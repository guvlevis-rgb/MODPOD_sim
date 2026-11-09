"""One-week operating simulation of a manually sized MOD POD microgrid.

Unlike the sizing study, nothing is optimized here: you enter the equipment sizes, pick one or more
conditions (winter, summer, flood, ice_storm) and the tool runs the pod hour by hour for a week with
the same building, load and equipment models, then writes an HMI-style HTML page that replays the
week with every energy flow between components.

Config (JSON): the same sections as the sizing study (building, loads, pv, hp, battery, generator, ev,
econ, weights, constraints, weather) plus

    "base_config": "ucg40.json",          # optional: inherit a building config, then override
    "design": {"pv_kw": 6, "bat_kwh": 20, "gen_kw": 15, "hp_kw": 35, "tank_gal": 100,
               "inverter_kw": null, "ev_kwh": 0},
    "week":   {"conditions": ["winter", "summer", "flood", "ice_storm"], "days": 7,
               "start_dates": {"winter": "2023-01-20"},              # optional overrides
               "ice_storm": {"storm_hours": 36, "ice_clear_hours": 24, "post_storm_cooling_c": 4}}
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .models import f_to_c
from .optimize import weight_breakdown
from .simulate import build_scenario, simulate
from .solar import erbs_decomposition
from .study import load_weather, study_from_config

L_PER_GAL = 3.78541
KG_PER_LB = 0.453592

CONDITIONS = {
    "winter": dict(label="Winter", title="Winter cold week",
                   desc="The coldest 7 days of December to February: peak heating, heat pumps at their least efficient, short days."),
    "summer": dict(label="Summer", title="Summer heat week",
                   desc="The hottest 7 days of June to August: cooling to keep the shelter under 90 °F, long sunny days."),
    "flood": dict(label="Flood", title="Spring flood week",
                  desc="The darkest 7 days of March to May, typical of rain-on-snow flooding: near-freezing, overcast, little solar. No fuel resupply."),
    "ice_storm": dict(label="Ice storm", title="Ice storm week",
                      desc="Freezing rain for the first 36 hours (temperatures pinned near 30 °F, heavy overcast, high wind) "
                           "glazes the PV array; ice stays on until crews can clear it a day after the storm, then colder "
                           "air follows."),
}
DEFAULT_DESIGN = dict(pv_kw=6.0, bat_kwh=20.0, gen_kw=15.0, hp_kw=35.0, tank_gal=100.0, inverter_kw=None, ev_kwh=None)
DEFAULT_ICE = dict(storm_hours=36, storm_start_hour=6, ice_clear_hours=24, post_storm_cooling_c=4.0, ghi_factor=0.15,
                   wind_factor=2.5)


def c2f(c):
    return np.asarray(c, float) * 9 / 5 + 32


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def _merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None) -> dict:
    """Read a week config; follows "base_config" (relative to the file) to inherit a building config."""
    if not path:
        return {}
    path = Path(path)
    cfg = json.loads(path.read_text())
    base = cfg.pop("base_config", None)
    if base:
        bp = (path.parent / base) if not Path(base).is_absolute() else Path(base)
        cfg = _merge(load_config(bp), cfg)
    return cfg


def resolve_design(cfg: dict, st) -> dict:
    d = dict(DEFAULT_DESIGN)
    d.update({k: v for k, v in (cfg.get("design") or {}).items()})
    unknown = set(d) - set(DEFAULT_DESIGN) - {"hp_units", "tank_l"}
    if unknown:
        raise ValueError(f"unknown design setting(s) {sorted(unknown)}. Valid: {sorted(DEFAULT_DESIGN)} "
                         f"(or hp_units instead of hp_kw, tank_l instead of tank_gal)")
    if d.get("hp_units") is not None:
        d["hp_kw"] = float(d["hp_units"]) * st.hp.unit_kw
    if d.get("tank_l") is not None:
        d["tank_gal"] = float(d["tank_l"]) / L_PER_GAL
    for k in ("pv_kw", "bat_kwh", "gen_kw", "hp_kw", "tank_gal"):
        d[k] = float(d[k])
        if d[k] < 0:
            raise ValueError(f"design.{k} cannot be negative")
    if d["gen_kw"] == 0:
        d["tank_gal"] = 0.0
    if d["pv_kw"] * st.pv.m2_per_kw > st.constraints.max_pv_area_m2 * 1.5:
        raise ValueError(f"design.pv_kw = {d['pv_kw']} needs {d['pv_kw'] * st.pv.m2_per_kw / 0.0929:,.0f} ft2 of panels")
    return d


# ---------------------------------------------------------------------------
# condition weeks
# ---------------------------------------------------------------------------
def _pick_start(wy, cond: str, days: int) -> pd.Timestamp:
    d = wy.daily()
    roll = lambda s: s.rolling(days).mean().shift(-(days - 1))
    if cond == "winter":
        sc, months, mode = roll(d.t_mean), [12, 1, 2], "min"
    elif cond == "summer":
        sc, months, mode = roll(d.t_mean), [6, 7, 8], "max"
    elif cond == "flood":
        sc, months, mode = roll(d.ghi_kwh), [3, 4, 5], "min"
    elif cond == "ice_storm":
        # a near-freezing, overcast 2-day start that freezing rain needs
        t2 = d.t_mean.rolling(2).mean().shift(-1)
        g2 = d.ghi_kwh.rolling(2).mean().shift(-1)
        sc = g2 + 0.35 * (t2 + 2.5).abs()
        sc[(t2 < -9) | (t2 > 1.5)] = np.inf
        months, mode = [12, 1, 2, 3], "min"
    else:
        raise ValueError(f'unknown condition "{cond}". Use one of: {", ".join(CONDITIONS)}')
    ok = sc.index.month.isin(months) & sc.notna() & np.isfinite(sc)
    last = d.index[-1] - pd.Timedelta(days=days - 1)
    cand = sc[ok & (sc.index <= last)]
    return cand.idxmin() if mode == "min" else cand.idxmax()


def condition_week(wy, cond: str, days: int, start: str | None, ice: dict):
    """Weather slice for the condition, with storm modifications. Returns (wdf, cover, notes)."""
    t0 = pd.Timestamp(start) if start else _pick_start(wy, cond, days)
    t0 = t0.normalize()
    i0 = wy.df.index.get_loc(t0)
    wdf = wy.df.iloc[i0:i0 + 24 * days].copy()
    if len(wdf) < 24 * days:
        raise ValueError(f"start date {t0.date()} leaves fewer than {days} days in the weather year")
    n = len(wdf)
    cover = np.zeros(n)
    notes = []
    if cond == "ice_storm":
        ice = {**DEFAULT_ICE, **(ice or {})}
        a = int(ice["storm_start_hour"])
        b = min(n, a + int(ice["storm_hours"]))
        t = wdf.temp_c.values.copy()
        seg = t[a:b]
        t[a:b] = np.clip(-1.5 + 0.35 * (seg - seg.mean()), -5.0, 0.4)
        h = np.arange(n)
        after = h >= b
        t[after] -= ice["post_storm_cooling_c"] * np.exp(-(h[after] - b) / 72.0)
        wdf["temp_c"] = t
        g = wdf.ghi.values.copy()
        g[a:b] *= ice["ghi_factor"]
        wdf["ghi"] = g
        w = wdf.wind_ms.values.copy()
        w[a:b] *= ice["wind_factor"]
        wdf["wind_ms"] = w
        wdf["snowfall_event"] = False
        dni, dhi = erbs_decomposition(wdf.ghi.values, wdf.zenith.values, wdf.etr_normal.values)
        wdf["dni"], wdf["dhi"] = dni, dhi
        # ice glaze on the array: total cover from storm start until crews clear it at the first
        # 9 am at least `ice_clear_hours` after the storm ends
        hod = wdf.index.hour.values
        clear = next((k for k in range(b + int(ice["ice_clear_hours"]), n) if hod[k] == 9), n)
        cover[a:clear] = 1.0
        notes.append(f"Freezing rain from hour {a} to {b}; array iced until {wdf.index[min(clear, n - 1)]:%a %H:%M}.")
    return wdf, cover, notes


# ---------------------------------------------------------------------------
# run one condition
# ---------------------------------------------------------------------------
def _events(S, st, design, times):
    """Operator event log from the hourly series."""
    ev = []
    n = len(times)

    def add(i, sev, msg):
        ev.append(dict(i=int(i), sev=sev, msg=msg))

    def edges(flag):
        f = np.asarray(flag, bool)
        prev = np.concatenate([[False], f[:-1]])
        return np.where(f & ~prev)[0], np.where(~f & prev)[0]

    on, off = edges(S["gen_on"] > 0.5)
    for i in on:
        add(i, "info", f"Generator started: battery {S['soc'][i]:.0f} %, bus load {S['load'][i]:.1f} kW")
    for i in off:
        run = i - max([k for k in on if k < i], default=0)
        add(i, "info", f"Generator stopped after {run} h; battery {S['soc'][i]:.0f} %")
    if design["bat_kwh"] > 0:
        lo, _ = edges(S["soc"] < 15)
        for i in lo:
            add(i, "warn", f"Battery low: {S['soc'][i]:.0f} %")
        full, _ = edges(S["soc"] > 99)
        for i in full:
            add(i, "info", "Battery full")
    for key, label, sev in (("shed_disc", "Discretionary loads (charging, hot water)", "warn"),
                            ("shed_hvac", "Heating/cooling curtailed by power shortage", "alarm"),
                            ("shed_ess", "Essential loads (lighting, water, cooking)", "alarm"),
                            ("shed_crit", "CRITICAL loads (comms, medical, refrigeration)", "alarm")):
        a, b = edges(S[key] > 0.01)
        for i in a:
            add(i, sev, f"{label} shed: {S[key][i]:.1f} kW unserved")
        for i in b:
            add(i, "info", f"{label.split(' (')[0]} restored")
    a, b = edges(S["t_in"] < S["t_min_f"])
    for i in a:
        add(i, "alarm", f"Shelter below {S['t_min_f']:.0f} °F: {S['t_in'][i]:.1f} °F")
    for i in b:
        add(i, "info", f"Shelter back above {S['t_min_f']:.0f} °F")
    a, b = edges(S["t_in"] > S["t_max_f"])
    for i in a:
        add(i, "alarm", f"Shelter above {S['t_max_f']:.0f} °F: {S['t_in'][i]:.1f} °F")
    for i in b:
        add(i, "info", f"Shelter back below {S['t_max_f']:.0f} °F")
    if design["gen_kw"] > 0:
        a, _ = edges(S["fuel_pct"] < 25)
        for i in a:
            add(i, "warn", f"Fuel below 25 %: {S['fuel_gal'][i]:.0f} gal left")
        a, _ = edges(S["fuel_pct"] < 0.5)
        for i in a:
            add(i, "alarm", "Fuel tank empty: generator unavailable")
    a, _ = edges((S["q_need"] - S["q_heat"] - S["q_cool"] > 0.3) & (S["shed_hvac"] < 0.01))
    for i in a:
        add(i, "warn", f"Heat pumps at full capacity: {S['q_need'][i] - S['q_heat'][i] - S['q_cool'][i]:.1f} kW short")
    if design["pv_kw"] > 0:
        a, b = edges(S["pv_cover"] > 0.5)
        for i in a:
            add(i, "warn", f"PV array {'iced over' if S['pv_cover_kind'] == 'ice' else 'covered by snow'}")
        for i in b:
            add(i, "info", "PV array clear")
    ev.sort(key=lambda e: (e["i"], {"alarm": 0, "warn": 1, "info": 2}[e["sev"]]))
    return ev


def run_condition(st, wy, design, cond, wcfg):
    days = int(wcfg.get("days", 7))
    start = (wcfg.get("start_dates") or {}).get(cond)
    wdf, cover, notes = condition_week(wy, cond, days, start, wcfg.get("ice_storm"))
    sc = build_scenario(cond, CONDITIONS[cond]["title"], wdf, st.building, st.loads, st.pv)
    pv_clean = copy.deepcopy(st.pv)
    pv_clean.snow_model = False
    clean = pv_clean.per_kw(wdf)
    sc.pv_per_kw = sc.pv_per_kw * (1 - cover)
    snow_cover = np.where(clean > 0.02, 1 - np.divide(sc.pv_per_kw, clean, out=np.ones_like(clean), where=clean > 0), 0.0)
    pv_cover = np.maximum(cover, np.clip(snow_cover, 0, 1) * (clean > 0.02))
    st2 = copy.deepcopy(st)
    if design.get("ev_kwh") is not None:
        st2.ev.available_kwh = float(design["ev_kwh"])
    tank_l = design["tank_gal"] * L_PER_GAL
    r = simulate(sc, [design["pv_kw"]], [design["bat_kwh"]], [design["gen_kw"]], [design["hp_kw"]], st2.building,
                 st2.hp, st2.battery, st2.generator, st2.ev, record=True, tank_l=tank_l)
    R = {k: v[:, 0] for k, v in r["series"].items()}
    T = len(wdf)
    tp = st.building.thermal_params()
    b = st.building
    S = {k: np.round(np.asarray(v, float), 3) for k, v in R.items()}
    S["soc"] = R["soc"] * 100
    S["t_in"] = c2f(R["t_in"])
    S["t_mass"] = c2f(R["t_mass"])
    S["t_out"] = c2f(wdf.temp_c.values)
    S["ghi"] = wdf.ghi.values / 1000.0
    S["wind_mph"] = wdf.wind_ms.values * 2.23694
    S["fuel_gal"] = R["fuel_left"] / L_PER_GAL
    S["fuel_gph"] = R["fuel_rate"] / L_PER_GAL
    S["fuel_pct"] = np.divide(R["fuel_left"], tank_l, out=np.zeros(T), where=tank_l > 0) * 100
    S["gen_load_pct"] = np.divide(R["gen_out"], design["gen_kw"], out=np.zeros(T), where=design["gen_kw"] > 0) * 100
    S["solar_gain"] = sc.solar_gain_kw
    S["internal_gain"] = sc.gain_conv_kw + sc.gain_mass_kw - sc.solar_gain_kw
    S["pv_cover"] = pv_cover * 100
    S["pv_cover_kind"] = "ice" if cond == "ice_storm" else "snow"
    S["t_min_f"] = float(c2f(b.min_indoor_c))
    S["t_max_f"] = float(c2f(b.max_indoor_c))
    events = _events({**S, "pv_cover": pv_cover}, st, design, wdf.index)

    # energy balance check (bus): sources = served load + battery charging
    bal = (R["pv_to_load"] + R["pv_to_bat"] + R["gen_to_load"] + R["gen_to_bat"] + R["bat_dis"] + R["ev"]
           - R["load"] - R["bat_ch"])
    gen_h = float(R["gen_on"].sum())
    tot = dict(
        demand=float(r["demand"][0]), served=float(r["served"][0]), unmet=float(r["unmet"][0]),
        unmet_crit=float(r["unmet_crit"][0]), pv_avail=float(R["pv_avail"].sum()),
        pv_used=float(R["pv_to_load"].sum() + R["pv_to_bat"].sum()), curtail=float(R["curtail"].sum()),
        gen_kwh=float(R["gen_out"].sum()), dump=float(R["dump"].sum()), gen_hours=gen_h,
        gen_starts=int(r["gen_starts"][0]), fuel_gal=float(R["fuel_rate"].sum() / L_PER_GAL),
        fuel_left_gal=float(R["fuel_left"][-1] / L_PER_GAL), bat_dis=float(R["bat_dis"].sum()),
        bat_ch=float(R["bat_ch"].sum()), ev_kwh=float(R["ev"].sum()), heat=float(R["q_heat"].sum()),
        cool=float(R["q_cool"].sum()), hvac=float(R["hvac"].sum()), t_min=float(S["t_in"].min()),
        t_max=float(S["t_in"].max()), hours_cold=int((S["t_in"] < S["t_min_f"]).sum()),
        hours_hot=int((S["t_in"] > S["t_max_f"]).sum()), soc_min=float(S["soc"].min()) if design["bat_kwh"] else None,
        balance_err=float(np.abs(bal).max()), peak_load=float(R["load"].max()),
        pv_share=float((R["pv_to_load"].sum() + R["pv_to_bat"].sum()) / max(
            R["pv_to_load"].sum() + R["pv_to_bat"].sum() + R["gen_out"].sum() - R["dump"].sum() + R["ev"].sum(), 1e-9)),
        days_of_fuel=(float(R["fuel_rate"].sum() / L_PER_GAL) and design["tank_gal"] / (R["fuel_rate"].sum() / L_PER_GAL) * days),
    )
    keep = ["pv_avail", "pv_to_load", "pv_to_bat", "curtail", "gen_out", "gen_to_load", "gen_to_bat", "dump",
            "gen_on", "gen_load_pct", "fuel_gph", "fuel_gal", "fuel_pct", "bat_dis", "bat_ch", "soc", "soc_kwh", "ev",
            "ev_left", "load", "crit", "ess", "disc", "hvac_dem", "hvac", "shed_crit", "shed_ess", "shed_disc",
            "shed_hvac", "unmet", "q_heat", "q_cool", "q_need", "hp_cap", "cop", "t_in", "t_mass", "t_out", "q_loss",
            "solar_gain", "internal_gain", "ghi", "wind_mph", "pv_cover"]
    series = {k: [round(float(x), 3) for x in np.asarray(S[k], float)] for k in keep}
    return dict(key=cond, **CONDITIONS[cond], notes=notes, start=str(wdf.index[0].date()),
                end=str((wdf.index[-1]).date()), time=[t.strftime("%Y-%m-%dT%H:%M") for t in wdf.index],
                series=series, events=events, totals=tot,
                t_min_f=S["t_min_f"], t_max_f=S["t_max_f"],
                heat_setpoint_f=float(c2f(b.heat_setpoint_c)), cool_setpoint_f=float(c2f(b.cool_setpoint_c)))


def design_summary(st, design, peak_kw):
    inv = design.get("inverter_kw") or max(3.0, float(np.ceil(peak_kw * 1.15)))
    wb = weight_breakdown(st, design["pv_kw"], design["bat_kwh"], design["gen_kw"], design["hp_kw"],
                          design["tank_gal"] * L_PER_GAL, inv)
    wb = {k: float(np.asarray(v)) / KG_PER_LB for k, v in wb.items()}
    e = st.econ
    capex = (design["pv_kw"] * e.pv_usd_per_kw + design["bat_kwh"] * e.battery_usd_per_kwh + inv * e.inverter_usd_per_kw
             + (e.gen_usd_fixed + e.gen_usd_per_kw * design["gen_kw"] + e.tank_usd_per_l * design["tank_gal"] * L_PER_GAL
                if design["gen_kw"] > 0 else 0) + design["hp_kw"] * e.hp_usd_per_kw)
    tp = st.building.thermal_params()
    return dict(design=dict(design, inverter_kw=inv, hp_units=design["hp_kw"] / st.hp.unit_kw),
                weight_lb=wb, payload_lb=sum(wb.values()), payload_limit_lb=st.constraints.max_payload_kg / KG_PER_LB,
                capex=capex,
                building=dict(area_ft2=st.building.floor_area_m2 * 10.764, occupants=st.building.occupants,
                              envelope=st.building.env().name, ua_w_k=tp["ua_total"],
                              windows=f"{tp['windows']['count']} × {tp['windows']['name']}",
                              t_min_f=float(c2f(st.building.min_indoor_c)), t_max_f=float(c2f(st.building.max_indoor_c))),
                battery=dict(soc_min=st.battery.soc_min * 100, c_rate=st.battery.c_rate),
                generator=dict(fuel=st.generator.fuel, soc_start=st.generator.soc_start * 100,
                               soc_stop=st.generator.soc_stop * 100, min_load=st.generator.min_load_frac * 100),
                pv=dict(tilt=st.pv.tilt_deg), hp=dict(unit_kw=st.hp.unit_kw))


def run_week(cfg: dict, out_dir="week_results", conditions=None, design_overrides=None, verbose=True) -> dict:
    log = print if verbose else (lambda *a, **k: None)
    cfg = copy.deepcopy(cfg or {})
    if design_overrides:
        cfg.setdefault("design", {}).update({k: v for k, v in design_overrides.items() if v is not None})
    st = study_from_config(cfg)
    wy = load_weather(cfg)
    design = resolve_design(cfg, st)
    wcfg = cfg.get("week") or {}
    conds = conditions or wcfg.get("conditions") or list(CONDITIONS)
    bad = [c for c in conds if c not in CONDITIONS]
    if bad:
        raise ValueError(f"unknown condition(s) {bad}. Use: {', '.join(CONDITIONS)}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log(f"Weather: {wy.source}")
    log(f"Design: PV {design['pv_kw']:g} kW | battery {design['bat_kwh']:g} kWh | generator {design['gen_kw']:g} kW "
        f"({design['tank_gal']:.0f} gal) | heat pumps {design['hp_kw']:g} kW")
    runs = []
    for c in conds:
        rc = run_condition(st, wy, design, c, wcfg)
        t = rc["totals"]
        log(f"  {rc['title']:20s} {rc['start']}  fuel {t['fuel_gal']:5.1f} gal | gen {t['gen_hours']:4.0f} h "
            f"{t['gen_starts']:3d} starts | indoor {t['t_min']:.1f}-{t['t_max']:.1f} F | unserved {t['unmet']:.1f} kWh"
            f" | balance err {t['balance_err']:.1e}")
        runs.append(rc)
        pd.DataFrame({"time": rc["time"], **rc["series"]}).to_csv(out / f"week_{c}_hourly.csv", index=False)
    peak = max(r["totals"]["peak_load"] for r in runs)
    summ = design_summary(st, design, peak)
    payload = dict(site=wy.site, source=wy.source, conditions=runs, order=[r["key"] for r in runs], **summ)
    (out / "week_results.json").write_text(json.dumps(payload, default=float))
    from .hmi import write_hmi
    path = write_hmi(payload, out / "week_hmi.html")
    log(f"HMI: {path}")
    return payload
