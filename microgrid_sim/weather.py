"""Weather import, Waitsfield VT climate synthesis, and design-window extraction.

Two ways to get an hourly weather year:

1. ``load_epw(path)`` -- any EnergyPlus/TMY3 .epw file. For Waitsfield use the
   nearest TMYx station (e.g. Montpelier-Barre ``USA_VT_Barre-Montpelier.Knapp.State.AP.726145_TMYx.epw``
   or Burlington ``726170``) from https://climate.onebuilding.org, or an NSRDB PSM
   TMY download for 44.19N, -72.82W converted to EPW/CSV.
   ``load_csv(path)`` reads a simple CSV with columns timestamp,temp_c,ghi[,dni,dhi,wind_ms,snow_depth_cm].

2. ``synthesize_waitsfield()`` -- a stochastic typical year for the Mad River
   Valley calibrated to monthly climate normals (temperature max/min, GHI) and to
   ASHRAE-style design extremes. It is the default so the tool runs offline, and it
   is deterministic for a given seed.

``select_design_windows`` then picks the stress windows (cold snap, dark storm,
spring flood, summer heat, multi-week endurance) the sizing must survive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from .solar import erbs_decomposition, solar_position

# ---------------------------------------------------------------------------
# Site
# ---------------------------------------------------------------------------
WAITSFIELD = dict(name="Waitsfield, VT", lat=44.190, lon=-72.824, tz=-5.0, elevation_m=213.0)

# Monthly climate normals used to calibrate the synthetic year (deg C, kWh/m2/day).
# Temperatures: Mad River Valley floor, interpolated from NOAA 1991-2020 normals at
# Montpelier/Northfield/Waitsfield co-op stations. GHI: NREL NSRDB long-term means for
# central Vermont. Edit these if you have better local data.
NORMALS = pd.DataFrame({
    "tmax_c": [-3.6, -1.7, 3.3, 11.4, 18.9, 23.6, 26.0, 25.0, 20.6, 13.5, 6.1, -0.6],
    "tmin_c": [-15.4, -14.2, -8.6, -1.7, 4.3, 9.6, 12.3, 11.4, 7.0, 1.2, -3.6, -10.8],
    "ghi_kwh_m2_day": [1.75, 2.65, 3.75, 4.50, 5.25, 5.60, 5.70, 4.95, 3.95, 2.60, 1.55, 1.30],
    "temp_anom_sd": [5.8, 5.6, 4.8, 3.8, 3.4, 3.0, 2.6, 2.6, 3.0, 3.6, 4.4, 5.6],
}, index=range(1, 13))
DESIGN_HEATING_996_C = -25.0   # 99.6% heating dry bulb (approx. Montpelier-Barre)
DESIGN_COOLING_04_C = 30.0     # 0.4% cooling dry bulb


@dataclass
class WeatherYear:
    """Hourly weather year (8760 rows) plus derived solar geometry."""
    df: pd.DataFrame
    site: dict = field(default_factory=lambda: dict(WAITSFIELD))
    source: str = ""

    @property
    def n(self):
        return len(self.df)

    def add_solar_geometry(self):
        d = self.df
        doy = d.index.dayofyear.values
        hr = d.index.hour.values + 0.5
        zen, az, etr = solar_position(doy, hr, self.site["lat"], self.site["lon"], self.site["tz"])
        d["zenith"], d["azimuth"], d["etr_normal"] = zen, az, etr
        if "dni" not in d or d["dni"].isna().all():
            dni, dhi = erbs_decomposition(d["ghi"].values, zen, etr)
            d["dni"], d["dhi"] = dni, dhi
        if "albedo" not in d:
            snow = d.get("snow_depth_cm", pd.Series(0, index=d.index)).fillna(0)
            d["albedo"] = np.where(snow > 2, 0.65, 0.20)
        if "snowfall_event" not in d:
            snow = d.get("snow_depth_cm", pd.Series(0, index=d.index)).fillna(0)
            daily = snow.resample("D").max()
            new = (daily.diff().fillna(0) >= 1.0)
            ev = pd.Series(False, index=d.index)
            for day in daily.index[new.values]:
                ev.loc[day:day + pd.Timedelta(hours=5)] = True
            d["snowfall_event"] = ev.values
        return self

    def daily(self):
        d = self.df
        out = pd.DataFrame({
            "t_mean": d["temp_c"].resample("D").mean(),
            "t_min": d["temp_c"].resample("D").min(),
            "t_max": d["temp_c"].resample("D").max(),
            "ghi_kwh": d["ghi"].resample("D").sum() / 1000.0,
        })
        return out


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
EPW_COLS = ["year", "month", "day", "hour", "minute", "flags", "temp_c", "dewpoint_c", "rh",
            "pressure_pa", "etr_h", "etr_n", "ir_h", "ghi", "dni", "dhi", "gh_illum", "dn_illum",
            "dh_illum", "zen_lum", "wind_dir", "wind_ms", "sky_cover", "opaque_sky", "visibility",
            "ceiling", "wx_obs", "wx_codes", "precip_water", "aod", "snow_depth_cm", "days_since_snow",
            "albedo_epw", "precip_mm", "precip_qty"]


def load_epw(path: str | Path, year: int = 2023) -> WeatherYear:
    path = Path(path)
    with open(path, "r", encoding="latin-1") as f:
        header = f.readline().split(",")
    site = dict(WAITSFIELD)
    try:
        site.update(name=f"{header[1]}, {header[2]} (EPW)", lat=float(header[6]), lon=float(header[7]),
                    tz=float(header[8]), elevation_m=float(header[9]))
    except (IndexError, ValueError):
        pass
    raw = pd.read_csv(path, skiprows=8, header=None, names=EPW_COLS[:35], usecols=range(35))
    raw = raw.iloc[:8760]
    idx = pd.date_range(f"{year}-01-01", periods=len(raw), freq="h")
    df = pd.DataFrame(index=idx)
    df["temp_c"] = raw["temp_c"].values.astype(float)
    df["ghi"] = raw["ghi"].clip(lower=0).values.astype(float)
    df["dni"] = raw["dni"].clip(lower=0).values.astype(float)
    df["dhi"] = raw["dhi"].clip(lower=0).values.astype(float)
    df["wind_ms"] = raw["wind_ms"].values.astype(float)
    snow = raw["snow_depth_cm"].astype(float).where(lambda s: s < 900, 0.0)
    df["snow_depth_cm"] = snow.values
    wy = WeatherYear(df=df, site=site, source=f"EPW: {path.name}")
    return wy.add_solar_geometry()


def load_csv(path: str | Path, site: dict | None = None) -> WeatherYear:
    raw = pd.read_csv(path, parse_dates=[0], index_col=0)
    df = raw.rename(columns=str.lower).iloc[:8760].copy()
    df.index = pd.date_range(f"{df.index[0].year}-01-01", periods=len(df), freq="h")
    for c in ("wind_ms", "snow_depth_cm"):
        if c not in df:
            df[c] = 2.5 if c == "wind_ms" else 0.0
    wy = WeatherYear(df=df, site=site or dict(WAITSFIELD), source=f"CSV: {Path(path).name}")
    return wy.add_solar_geometry()


# ---------------------------------------------------------------------------
# Synthetic Waitsfield typical year
# ---------------------------------------------------------------------------
def _monthly_to_daily(values, idx_days):
    """Smoothly interpolate 12 mid-month values to every day of the year (periodic)."""
    mid = np.array([15, 45, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349], dtype=float)
    x = np.concatenate([mid - 365, mid, mid + 365])
    y = np.tile(np.asarray(values, float), 3)
    return np.interp(idx_days, x, y)


def synthesize_waitsfield(seed: int = 7, year: int = 2023) -> WeatherYear:
    rng = np.random.default_rng(seed)
    site = dict(WAITSFIELD)
    days = pd.date_range(f"{year}-01-01", periods=365, freq="D")
    doy = np.arange(1, 366, dtype=float)
    tmax_n = _monthly_to_daily(NORMALS.tmax_c, doy)
    tmin_n = _monthly_to_daily(NORMALS.tmin_c, doy)
    sd_n = _monthly_to_daily(NORMALS.temp_anom_sd, doy)
    winter_w = np.clip(np.cos(2 * np.pi * (doy - 15) / 365), 0, 1)  # 1 in mid-Jan, 0 in summer

    # Daily cloudiness: latent AR(1) Gaussian -> Beta-distributed clearness index
    z = np.zeros(365)
    for i in range(1, 365):
        z[i] = 0.45 * z[i - 1] + np.sqrt(1 - 0.45**2) * rng.standard_normal()
    u = stats.norm.cdf(z)
    kt_mean = _monthly_to_daily([0.42, 0.46, 0.48, 0.47, 0.48, 0.49, 0.51, 0.49, 0.48, 0.42, 0.36, 0.36], doy)
    conc = 5.0
    kt_d = stats.beta.ppf(u, kt_mean * conc, (1 - kt_mean) * conc)
    kt_d = np.clip(kt_d, 0.04, 0.76)

    # Daily temperature anomaly: AR(1) with seasonally varying sd, correlated with cloudiness
    a = np.zeros(365)
    phi = 0.72
    for i in range(1, 365):
        a[i] = phi * a[i - 1] + np.sqrt(1 - phi**2) * rng.standard_normal()
    clear_score = (kt_d - kt_mean) / 0.15
    # winter: clear = cold (radiative); summer: clear = hot
    anom = sd_n * a + clear_score * (-1.2 * winter_w + 0.9 * (1 - winter_w))
    t_mean_d = (tmax_n + tmin_n) / 2 + anom
    rng_scale = np.clip(0.55 + 1.6 * (kt_d - 0.12), 0.35, 1.35)
    t_range_d = (tmax_n - tmin_n) * rng_scale

    # Hourly temperature: smooth daily means + diurnal cosine (min ~06h, max ~15h)
    hidx = pd.date_range(f"{year}-01-01", periods=8760, freq="h")
    hour_of_year = np.arange(8760) + 0.5
    t_mean_h = np.interp(hour_of_year, np.arange(365) * 24 + 12, t_mean_d)
    t_range_h = np.interp(hour_of_year, np.arange(365) * 24 + 12, t_range_d)
    hod = hidx.hour.values + 0.5
    shape = np.where((hod >= 6) & (hod <= 15), -np.cos(np.pi * (hod - 6) / 9),
                     np.cos(np.pi * ((hod - 15) % 24) / 15))
    temp = t_mean_h + 0.5 * t_range_h * shape + rng.normal(0, 0.4, 8760)

    # Calibrate: monthly means -> normals (smooth offset), tails -> design extremes
    month_h = hidx.month.values
    normal_mean = ((NORMALS.tmax_c + NORMALS.tmin_c) / 2).values
    for _ in range(3):
        bias = np.array([temp[month_h == m].mean() for m in range(1, 13)]) - normal_mean
        temp = temp - np.interp(hour_of_year / 24, np.arange(365), _monthly_to_daily(bias, doy))
        q10, q004 = np.quantile(temp, [0.10, 0.004])
        lo = temp < q10
        temp[lo] = q10 + (temp[lo] - q10) * (DESIGN_HEATING_996_C - q10) / (q004 - q10)
        q90, q996 = np.quantile(temp, [0.90, 0.996])
        hi = temp > q90
        temp[hi] = q90 + (temp[hi] - q90) * (DESIGN_COOLING_04_C - q90) / (q996 - q90)

    # Solar: hourly clearness around the daily value, scaled per month to GHI normals
    zen, az, etr = solar_position(hidx.dayofyear.values, hod, site["lat"], site["lon"], site["tz"])
    etr_h = np.maximum(etr * np.cos(np.radians(zen)), 0)
    kt_h = np.repeat(kt_d, 24) * np.clip(1 + rng.normal(0, 0.12, 8760), 0.5, 1.4)
    # lower transmittance at low sun (air mass)
    kt_h *= np.clip(0.75 + 0.3 * np.cos(np.radians(np.minimum(zen, 89))), 0.6, 1.05)
    ghi = np.clip(kt_h, 0, 0.8) * etr_h
    month = hidx.month.values
    for _ in range(3):
        for m in range(1, 13):
            sel = month == m
            ndays = sel.sum() / 24
            cur = ghi[sel].sum() / 1000 / ndays
            ghi[sel] *= NORMALS.ghi_kwh_m2_day[m] / max(cur, 1e-6)
        ghi = np.minimum(ghi, 0.82 * etr_h)

    # Wind (Weibull, valley site), afternoon peak
    wind = rng.weibull(2.0, 8760) * 2.6 * (1 + 0.25 * np.sin(np.pi * (hod - 9) / 12).clip(0))

    # Snow: snowfall on cold overcast days, simple degree-day melt
    t_mean_hourly_daily = pd.Series(temp, index=hidx).resample("D").mean().values
    snow_day = (t_mean_hourly_daily < 0.5) & (kt_d < 0.32) & (rng.random(365) < 0.85)
    depth = np.zeros(365)
    for i in range(365):
        prev = depth[i - 1] if i else 25.0  # assume snowpack on Jan 1
        melt = 1.6 * max(t_mean_hourly_daily[i], 0)
        depth[i] = max(prev - melt, 0) + (8.0 if snow_day[i] else 0.0)
    snow_depth = np.repeat(depth, 24)
    snowfall_event = np.repeat(snow_day, 24) & (hod < 6)

    df = pd.DataFrame({
        "temp_c": temp, "ghi": ghi, "wind_ms": wind, "snow_depth_cm": snow_depth,
        "snowfall_event": snowfall_event,
    }, index=hidx)
    wy = WeatherYear(df=df, site=site, source=f"Synthetic Waitsfield VT typical year (seed {seed})")
    return wy.add_solar_geometry()


# ---------------------------------------------------------------------------
# Design windows
# ---------------------------------------------------------------------------
@dataclass
class DesignWindow:
    key: str
    label: str
    description: str
    start: pd.Timestamp
    days: int

    def slice(self, wy: WeatherYear) -> pd.DataFrame:
        i0 = wy.df.index.get_loc(self.start)
        return wy.df.iloc[i0:i0 + 24 * self.days]


def _best_window(score: pd.Series, days: int, months: list[int], mode: str) -> pd.Timestamp:
    roll = score.rolling(days).mean().shift(-(days - 1))  # window starting at each day
    ok = roll.index.month.isin(months) & roll.notna()
    cand = roll[ok]
    return cand.idxmin() if mode == "min" else cand.idxmax()


def select_design_windows(wy: WeatherYear, days: int = 3, endurance_days: int = 14) -> list[DesignWindow]:
    """Pick stress windows from the weather year.

    * cold_snap    - lowest mean temperature (Dec-Feb): peak heating, worst heat-pump COP
    * dark_storm   - lowest solar x heating need (Nov-Feb): least PV when heating still matters
    * spring_flood - lowest solar in Mar-May (rain-on-snow / spring flood season)
    * summer_heat  - highest mean temperature (Jun-Aug): cooling requirement (80-90 F)
    * endurance    - worst multi-week heating+low-solar stretch: tests fuel autonomy
    """
    d = wy.daily()
    hdd = (18.0 - d.t_mean).clip(lower=0)
    dark_cold = d.ghi_kwh / d.ghi_kwh.max() - 0.6 * hdd / hdd.max()
    windows = [
        DesignWindow("cold_snap", "Winter cold snap",
                     f"Coldest {days}-day stretch (Dec-Feb): peak heating, lowest heat-pump COP",
                     _best_window(d.t_mean, days, [12, 1, 2], "min"), days),
        DesignWindow("dark_storm", "Winter dark storm",
                     f"Least sun + high heating need over {days} days (Nov-Feb)",
                     _best_window(dark_cold, days, [11, 12, 1, 2], "min"), days),
        DesignWindow("spring_flood", "Spring flood (overcast)",
                     f"Darkest {days}-day spell Mar-May, typical of rain-on-snow flood events",
                     _best_window(d.ghi_kwh, days, [3, 4, 5], "min"), days),
        DesignWindow("summer_heat", "Summer heat wave",
                     f"Hottest {days}-day stretch (Jun-Aug): cooling to the 80-90 F target",
                     _best_window(d.t_mean, days, [6, 7, 8], "max"), days),
    ]
    if endurance_days and endurance_days > days:
        windows.append(DesignWindow(
            "endurance", f"{endurance_days}-day winter endurance",
            f"Worst {endurance_days}-day dark/cold stretch (Nov-Mar): fuel autonomy check",
            _best_window(dark_cold, endurance_days, [11, 12, 1, 2, 3], "min"), endurance_days))
    # windows must fit inside the year
    last_start = wy.df.index[-1] - pd.Timedelta(days=max(w.days for w in windows))
    for w in windows:
        if w.start > last_start:
            w.start = last_start.normalize()
    return windows
