"""Parameter sets and component models for the MOD POD hybrid microgrid.

Everything a user would want to change lives in a dataclass here, and every
dataclass can be overridden from a JSON config (see ``config.py``).

Units: kW, kWh, deg C, liters, kg, USD unless noted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .solar import iam_ashrae, poa_irradiance

F_PER_C = 9 / 5


def f_to_c(f):
    return (f - 32) / F_PER_C


# ---------------------------------------------------------------------------
# Building envelope + occupants
# ---------------------------------------------------------------------------
@dataclass
class Envelope:
    name: str
    u_wall: float          # W/m2K opaque wall
    u_roof: float          # W/m2K
    u_window: float        # W/m2K
    window_frac: float     # window-to-wall ratio
    shgc: float
    ach_infiltration: float
    slab_f_factor: float   # W/m.K of slab perimeter (ASHRAE F-factor)
    mass_kj_m2k: float     # internal heat capacity per floor area (ISO 13790 classes: 80/110/165/260/370)


ENVELOPE_PRESETS = {
    "leaky_1950s": Envelope("Leaky 1950s masonry", 1.10, 0.55, 4.0, 0.18, 0.70, 1.00, 1.25, 260),
    "typical_1980s": Envelope("Typical 1980s municipal", 0.45, 0.28, 2.8, 0.15, 0.55, 0.50, 0.90, 165),
    # IECC 2012 climate zone 6 era: walls ~U-0.053, roof ~U-0.032 Btu/h-ft2-F, low-e glazing, tighter air barrier
    "typical_2010s": Envelope("Typical 2010s (IECC 2012)", 0.30, 0.18, 2.0, 0.15, 0.40, 0.30, 0.75, 165),
    "efficient_modern": Envelope("Efficient modern", 0.22, 0.14, 1.5, 0.15, 0.40, 0.20, 0.50, 165),
}


@dataclass
class WindowType:
    name: str
    u_w_m2k: float         # whole-window U-factor (NFRC), W/m2K  (Btu/h-ft2-F = W/m2K / 5.678)
    shgc: float            # solar heat gain coefficient


# Whole-window values typical of NFRC-rated products (ASHRAE Fundamentals ch. 15 ranges)
WINDOW_TYPES = {
    "single_pane": WindowType("Single pane, metal frame", 5.8, 0.78),
    "single_storm": WindowType("Single pane + storm window", 3.4, 0.65),
    "double_clear": WindowType("Double pane, clear", 2.8, 0.58),
    "double_lowe": WindowType("Double pane, low-e, argon", 1.7, 0.40),
    "triple_lowe": WindowType("Triple pane, low-e", 1.0, 0.30),
}
ENVELOPE_DEFAULT_WINDOW = {"leaky_1950s": "single_storm", "typical_1980s": "double_clear", "typical_2010s": "double_lowe",
                           "efficient_modern": "double_lowe"}
W_M2K_PER_BTU = 5.678
M2_PER_FT2 = 0.092903
FACADE_AZIMUTH = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}


@dataclass
class Building:
    floor_area_m2: float = 180.0          # 1,940 ft2 shelter zone served by the pod (ucg40 base case)
    ceiling_height_m: float = 3.0
    aspect_ratio: float = 1.5
    envelope: str = "typical_2010s"
    envelope_overrides: dict = field(default_factory=dict)
    occupants: int = 40
    vent_lps_per_person: float = 2.5      # ASHRAE 62.1 people component (5 cfm/person)
    vent_lps_per_m2: float = 0.3          # area component (0.06 cfm/ft2)
    hrv_effectiveness: float = 0.0        # 0 = no heat recovery ventilator
    furniture_factor: float = 5.0         # air-node capacitance multiplier for furnishings
    heat_setpoint_c: float = f_to_c(55)   # controller target in winter
    cool_setpoint_c: float = f_to_c(80)   # controller target in summer
    min_indoor_c: float = f_to_c(50)      # requirement: >= 50 F
    max_indoor_c: float = f_to_c(90)      # requirement: <= ~90 F
    initial_indoor_winter_c: float = f_to_c(59)  # building state when pod arrives
    initial_indoor_summer_c: float = f_to_c(82)
    window_shading: float = 0.7           # frame + blinds/overhang factor on solar gains
    # --- windows (set window_count=None to fall back to the envelope's window-to-wall ratio)
    window_count: int | None = 7
    window_width_m: float = 1.1176        # 44 in
    window_height_m: float = 1.8288       # 72 in -> 22 ft2 per window
    window_type: str | None = "single_pane"  # key of WINDOW_TYPES; None = the envelope preset's typical window
    window_u_ip: float | None = None      # optional NFRC U-factor override, Btu/h-ft2-F (wins over type)
    window_shgc: float | None = None      # optional SHGC override
    window_facade_split: dict = field(default_factory=lambda: {"N": 0.5, "E": 0.0, "S": 0.5, "W": 0.0})

    def validate(self):
        """Check building inputs and raise a ValueError that says what to fix."""
        if self.envelope not in ENVELOPE_PRESETS:
            raise ValueError(f'building.envelope = "{self.envelope}" is not a preset. '
                             f'Use one of: {", ".join(ENVELOPE_PRESETS)}')
        bad = set(self.envelope_overrides) - set(Envelope.__dataclass_fields__) - {"name"}
        if bad:
            raise ValueError(f"building.envelope_overrides has unknown key(s) {sorted(bad)}. "
                             f"Allowed: {[k for k in Envelope.__dataclass_fields__ if k != 'name']}")
        if self.window_type is not None and self.window_type not in WINDOW_TYPES:
            raise ValueError(f'building.window_type = "{self.window_type}" is not a window type. '
                             f'Use one of: {", ".join(WINDOW_TYPES)} (or null to follow the envelope)')
        # accept "South", "south", "s" etc. for facade names
        split = {}
        for k, v in dict(self.window_facade_split).items():
            kk = str(k).strip().upper()[:1]
            if kk not in FACADE_AZIMUTH:
                raise ValueError(f'building.window_facade_split key "{k}" must be N, E, S or W')
            split[kk] = split.get(kk, 0.0) + float(v)
        if sum(split.values()) <= 0:
            raise ValueError("building.window_facade_split shares must add up to more than 0")
        self.window_facade_split = split
        for name in ("floor_area_m2", "ceiling_height_m", "window_width_m", "window_height_m"):
            if float(getattr(self, name)) <= 0:
                raise ValueError(f"building.{name} must be greater than 0")
        if self.window_count is not None:
            if float(self.window_count) < 0 or float(self.window_count) != int(float(self.window_count)):
                raise ValueError("building.window_count must be a whole number of windows (or null)")
            self.window_count = int(float(self.window_count))
        if int(self.occupants) < 0:
            raise ValueError("building.occupants cannot be negative")
        if not 0 <= float(self.hrv_effectiveness) < 1:
            raise ValueError("building.hrv_effectiveness must be between 0 and 0.95 (e.g. 0.7 for 70 %)")
        self.window_spec()  # area vs wall check
        return self

    def window_spec(self) -> dict:
        """Resolved window inputs: count, area per facade, U (W/m2K) and SHGC."""
        e = self.env()
        A = self.floor_area_m2
        width = np.sqrt(A / self.aspect_ratio)
        perim = 2 * (A / width + width)
        wall_gross = perim * self.ceiling_height_m
        key = self.window_type or ENVELOPE_DEFAULT_WINDOW.get(self.envelope)
        wt = WINDOW_TYPES.get(key) if key else None
        if key and wt is None:
            raise KeyError(f"unknown window_type '{key}'; choose from {list(WINDOW_TYPES)}")
        u = wt.u_w_m2k if wt else e.u_window
        shgc = wt.shgc if wt else e.shgc
        name = wt.name if wt else "Envelope preset window"
        if self.window_u_ip is not None:
            u, name = self.window_u_ip * W_M2K_PER_BTU, f"Custom (U-{self.window_u_ip:.2f})"
        if self.window_shgc is not None:
            shgc = self.window_shgc
        if self.window_count is None:
            total = e.window_frac * wall_gross
            count = None
        else:
            count = int(self.window_count)
            total = count * self.window_width_m * self.window_height_m
        if total > 0.8 * wall_gross:
            raise ValueError(f"windows total {total / M2_PER_FT2:,.0f} ft2, more than 80 % of the "
                             f"{wall_gross / M2_PER_FT2:,.0f} ft2 of wall. Reduce building.window_count or the window size")
        split = {k: float(v) for k, v in self.window_facade_split.items() if k in FACADE_AZIMUTH}
        ssum = sum(split.values()) or 1.0
        facade = {k: total * v / ssum for k, v in split.items()}
        return dict(count=count, area_m2=total, area_ft2=total / M2_PER_FT2, facade_m2=facade, u=u,
                    u_ip=u / W_M2K_PER_BTU, shgc=shgc, type=key, name=name, wall_gross_m2=wall_gross,
                    wwr=total / wall_gross)

    def env(self) -> Envelope:
        e = ENVELOPE_PRESETS[self.envelope]
        if self.envelope_overrides:
            e = Envelope(**{**e.__dict__, **self.envelope_overrides})
        return e

    def thermal_params(self) -> dict:
        """2R2C (air + mass) network parameters, ISO 13790-style."""
        e = self.env()
        A = self.floor_area_m2
        width = np.sqrt(A / self.aspect_ratio)
        length = A / width
        perim = 2 * (length + width)
        wall_gross = perim * self.ceiling_height_m
        ws = self.window_spec()
        a_win = ws["area_m2"]
        a_wall = wall_gross - a_win
        vol = A * self.ceiling_height_m
        rho_cp = 1.204 * 1005.0  # J/m3K
        h_inf = rho_cp * vol * e.ach_infiltration / 3600.0
        vent_m3s = (self.occupants * self.vent_lps_per_person + A * self.vent_lps_per_m2) / 1000.0
        h_vent = rho_cp * vent_m3s * (1 - self.hrv_effectiveness)
        h_win = ws["u"] * a_win
        h_slab = e.slab_f_factor * perim
        h_opaque = e.u_wall * a_wall + e.u_roof * A
        a_m = 2.5 * A                      # effective mass area (ISO medium)
        h_am = 9.1 * a_m                   # mass <-> air coupling
        h_mo = 1 / (1 / max(h_opaque, 1e-6) - 1 / h_am)  # mass -> outdoor through opaque envelope
        return dict(
            c_air=rho_cp * vol * self.furniture_factor,          # J/K
            c_mass=e.mass_kj_m2k * 1000.0 * A,                   # J/K
            h_ao=h_win + h_inf + h_vent + h_slab,                # W/K air -> outdoor
            h_mo=h_mo, h_am=h_am,
            ua_total=h_win + h_inf + h_vent + h_slab + h_opaque,
            breakdown={"Windows": h_win, "Walls": e.u_wall * a_wall, "Roof": e.u_roof * A,
                       "Slab edge": h_slab, "Infiltration": h_inf, "Ventilation": h_vent},
            a_window=a_win, perimeter=perim, volume=vol, windows=ws,
        )


# ---------------------------------------------------------------------------
# Electrical loads (non-HVAC), by priority tier
# ---------------------------------------------------------------------------
@dataclass
class Loads:
    # critical (never shed)
    comms_w: float = 200.0               # satellite internet, radios, router, phone charging kiosk base
    medical_w: float = 0.0               # oxygen concentrator / CPAP / med fridge
    refrigeration_kwh_day: float = 3.5   # food-service fridge + freezer
    controls_w: float = 60.0             # pod controller, BMS, remote monitoring, inverter idle
    # essential
    lighting_w_m2: float = 3.0           # LED; full 06-22, 10 % overnight
    water_system_w: float = 150.0        # pressure pump + UV treatment
    cooking_kwh_person_day: float = 0.0   # induction / electric food service
    # discretionary (shed first)
    charging_wh_person_day: float = 25.0  # phones, laptops
    dhw_l_person_day: float = 20.0        # hot water use
    dhw_delta_t: float = 40.0
    dhw_cop: float = 2.8                  # heat-pump water heater (1.0 = resistance)
    internal_gain_fraction: float = 0.8   # share of non-HVAC electricity that ends up as heat indoors

    def profiles(self, hours: np.ndarray, occupants: int, floor_area: float) -> dict:
        """Hourly kW profiles for critical / essential / discretionary tiers."""
        h = hours % 24
        crit = np.full(h.shape, (self.comms_w + self.medical_w + self.controls_w) / 1000.0
                       + self.refrigeration_kwh_day / 24.0)
        light = np.where((h >= 6) & (h < 22), 1.0, 0.1) * self.lighting_w_m2 * floor_area / 1000.0
        meal = np.zeros(24)
        meal[[7]] = 0.25
        meal[[12]] = 0.25
        meal[[17, 18]] = 0.25
        cook = meal[h] * self.cooking_kwh_person_day * occupants
        ess = light + self.water_system_w / 1000.0 + cook
        charge_shape = np.where((h >= 8) & (h < 22), 1 / 14, 0.0)
        charge = charge_shape * self.charging_wh_person_day / 1000.0 * occupants
        dhw_shape = np.zeros(24)
        dhw_shape[[6, 7, 8]] = 0.18
        dhw_shape[[18, 19, 20, 21]] = 0.115
        dhw_kwh_th = self.dhw_l_person_day * 4.186 * self.dhw_delta_t / 3600.0 * occupants
        dhw = dhw_shape[h] * dhw_kwh_th / self.dhw_cop
        disc = charge + dhw
        gains = self.internal_gain_fraction * (crit + light + 0.5 * cook + charge)
        return dict(crit=crit, ess=ess, disc=disc, elec_gains_kw=gains)


def occupant_gains_kw(hours: np.ndarray, occupants: int) -> np.ndarray:
    h = hours % 24
    night = (h >= 22) | (h < 7)
    present = np.where(night, 1.0, 0.75)
    w_per = np.where(night, 55.0, 75.0)   # sensible W/person sleeping / seated
    return present * w_per * occupants / 1000.0


# ---------------------------------------------------------------------------
# Air-source heat pump (cold-climate, inverter driven)
# ---------------------------------------------------------------------------
@dataclass
class HeatPump:
    unit_kw: float = 3.5                  # one ~12 kBtu/h cold-climate head (rated max heat at 47 F)
    cutoff_c: float = -32.0               # below this the outdoor unit stops
    # performance map at maximum capacity, vs outdoor dry bulb
    heat_t: tuple = (-32, -25, -15, -8.3, 0.0, 8.3, 15.0)
    heat_cap_frac: tuple = (0.45, 0.60, 0.76, 0.86, 0.94, 1.00, 1.00)
    heat_cop: tuple = (1.25, 1.50, 1.90, 2.25, 2.70, 3.30, 3.90)
    cool_t: tuple = (20.0, 27.0, 35.0, 40.0, 46.0)
    cool_cap_frac: tuple = (0.95, 0.92, 0.85, 0.80, 0.70)   # of rated heating capacity
    cool_cop: tuple = (5.0, 4.3, 3.3, 2.8, 2.2)
    part_load_bonus: float = 0.15         # COP gain at low part-load for inverter compressors

    def heat_perf(self, t_out):
        cap = np.interp(t_out, self.heat_t, self.heat_cap_frac)
        cop = np.interp(t_out, self.heat_t, self.heat_cop)
        cap = np.where(t_out < self.cutoff_c, 0.0, cap)
        return cap, cop

    def cool_perf(self, t_out):
        return np.interp(t_out, self.cool_t, self.cool_cap_frac), np.interp(t_out, self.cool_t, self.cool_cop)


# ---------------------------------------------------------------------------
# PV array
# ---------------------------------------------------------------------------
@dataclass
class PVArray:
    tilt_deg: float = 45.0               # steep tilt for winter sun and snow shedding
    azimuth_deg: float = 180.0
    system_losses: float = 0.14          # wiring, mismatch, soiling, deployment
    temp_coeff: float = -0.0037          # 1/K
    inverter_eff: float = 0.96
    dc_ac_ratio: float = 1.2
    snow_model: bool = True              # Marion et al. (2013) sliding-snow coverage
    snow_clearing_hour: int | None = 9   # pod staff brush the array each morning (None = never)
    m2_per_kw: float = 4.9               # ~21 % efficient modules

    def per_kw(self, wdf: pd.DataFrame) -> np.ndarray:
        """AC kW per installed kW_dc for each hour of ``wdf``."""
        poa, beam, aoi = poa_irradiance(wdf.ghi.values, wdf.dni.values, wdf.dhi.values,
                                        wdf.zenith.values, wdf.azimuth.values,
                                        self.tilt_deg, self.azimuth_deg, wdf.albedo.values)
        poa_eff = poa - beam * (1 - iam_ashrae(aoi))
        t_cell = wdf.temp_c.values + poa / (25.0 + 6.84 * wdf.wind_ms.values)   # Faiman
        dc = poa_eff / 1000.0 * (1 + self.temp_coeff * (t_cell - 25.0)) * (1 - self.system_losses)
        if self.snow_model and "snowfall_event" in wdf:
            dc *= 1 - self.snow_coverage(wdf, poa)
        ac = np.minimum(dc * self.inverter_eff, 1.0 / self.dc_ac_ratio)
        return np.maximum(ac, 0.0)

    def snow_coverage(self, wdf: pd.DataFrame, poa: np.ndarray) -> np.ndarray:
        cov = np.zeros(len(wdf))
        c = 0.0
        slide = 0.197 * np.sin(np.radians(self.tilt_deg))   # fraction of panel per hour
        ev = wdf.snowfall_event.values.astype(bool)
        ta = wdf.temp_c.values
        hod = wdf.index.hour.values
        for i in range(len(wdf)):
            if ev[i]:
                c = 1.0
            elif self.snow_clearing_hour is not None and hod[i] == self.snow_clearing_hour:
                c = 0.0
            elif c > 0 and ta[i] > -poa[i] / 80.0:          # Marion sliding criterion
                c = max(c - slide, 0.0)
            cov[i] = c
        return cov


# ---------------------------------------------------------------------------
# Storage, generation, EV
# ---------------------------------------------------------------------------
@dataclass
class Battery:
    soc_min: float = 0.10
    soc_initial: float = 1.0             # pod deploys fully charged
    c_rate: float = 0.5                  # max charge/discharge power per kWh
    eff_oneway: float = 0.95             # ~90 % round trip


@dataclass
class Generator:
    fuel: str = "diesel"                 # diesel | propane
    tank_l: float = 380.0                # ~100 gal on-board tank
    min_load_frac: float = 0.30
    soc_start: float = 0.30              # cycle-charging start threshold
    soc_stop: float = 0.85               # stop once battery recharged to this
    f0_l_per_kw_h: float = 0.08145       # HOMER diesel no-load intercept (per kW rated)
    f1_l_per_kwh: float = 0.246          # HOMER diesel slope

    def fuel_coeffs(self):
        if self.fuel == "propane":
            # same thermal efficiency curve, propane has ~70 % of diesel's volumetric energy, ~10 % worse efficiency
            k = 35.8 / 25.3 * 1.10
            return self.f0_l_per_kw_h * k, self.f1_l_per_kwh * k
        return self.f0_l_per_kw_h, self.f1_l_per_kwh

    @property
    def co2_kg_per_l(self):
        return 1.51 if self.fuel == "propane" else 2.68

    @property
    def fuel_density(self):
        return 0.51 if self.fuel == "propane" else 0.84


@dataclass
class EVSupport:
    """Electric pickup used to tow the pod, discharging through vehicle-to-load."""
    available_kwh: float = 0.0           # energy the town is willing to draw (keep range for return trip)
    max_kw: float = 9.6                  # e.g. 240 V / 40 A V2L/V2H outlet
    eff: float = 0.92


# ---------------------------------------------------------------------------
# Costs, weights and constraints
# ---------------------------------------------------------------------------
@dataclass
class Economics:
    pv_usd_per_kw: float = 1400.0        # modules + deployable racking + MPPT
    battery_usd_per_kwh: float = 450.0   # LFP rack incl. BMS
    inverter_usd_per_kw: float = 350.0   # hybrid inverter/charger, sized to peak load
    gen_usd_fixed: float = 3000.0
    gen_usd_per_kw: float = 450.0
    hp_usd_per_kw: float = 900.0         # ccASHP heads + line sets, per kW heating capacity
    tank_usd_per_l: float = 4.0          # double-wall fuel tank + transfer pump
    fuel_usd_per_l: float = 1.15         # diesel ~$4.35/gal (propane ~ $0.75/L)
    gen_om_usd_per_hour: float = 0.60
    carbon_usd_per_t: float = 190.0      # social cost of CO2 (EPA 2023 central, 2020$); 0 to ignore
    idle_pv_usd_per_kwh: float = 0.0     # value of PV energy when the pod is parked (e.g. net-metered at
                                         # the town garage, ~0.19 $/kWh); 0 = pod stored idle
    deployment_days_per_year: float = 12.0
    lifetime_years: float = 15.0
    discount_rate: float = 0.04
    scenario_weights: dict = field(default_factory=lambda: {
        "cold_snap": 0.2, "dark_storm": 0.3, "spring_flood": 0.3, "summer_heat": 0.2})

    @property
    def annuity_factor(self):
        r, n = self.discount_rate, self.lifetime_years
        return n if r == 0 else (1 - (1 + r) ** -n) / r


@dataclass
class Weights:
    pv_kg_per_kw: float = 75.0           # panels + deployable racking + cabling
    battery_kg_per_kwh: float = 11.0
    inverter_kg_per_kw: float = 8.0
    gen_kg_fixed: float = 180.0
    gen_kg_per_kw: float = 28.0
    hp_kg_per_kw: float = 22.0
    tank_kg_per_l: float = 0.12          # empty tank mass per liter of capacity (fuel mass added separately)
    fixed_payload_kg: float = 700.0      # comms, water, food service, fit-out


@dataclass
class Constraints:
    max_payload_kg: float = 2900.0       # trailer payload (10,000 lb tow rating minus trailer)
    max_pv_area_m2: float = 110.0        # deployable array footprint
    max_unmet_frac: float = 0.005        # share of total electrical demand allowed unserved
    max_cold_degree_hours: float = 1.0   # deg C-h below min_indoor allowed per window
    max_hot_degree_hours: float = 2.0
    require_endurance: bool = False      # if True the multi-week window must also be fully served
    min_autonomy_days: float = 0.0       # days the worst multi-week stretch must be carried on one tank
    objective: str = "lifecycle"         # lifecycle | capex | weight
