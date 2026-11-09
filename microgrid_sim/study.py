"""End-to-end study runner: weather -> design windows -> sweep/optimize -> checks -> outputs."""
from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .models import ENVELOPE_PRESETS
from .optimize import binding_constraint, Study, WEIGHT_KEYS, weight_breakdown, SweepSpace, evaluate, make_scenarios, optimize, pareto_front
from .simulate import Scenario, build_scenario, simulate, window_solar_gains
from .models import occupant_gains_kw
from .weather import DesignWindow, WeatherYear, load_csv, load_epw, select_design_windows, synthesize_waitsfield

SECTIONS = ("building", "loads", "pv", "hp", "battery", "generator", "ev", "econ", "weights", "constraints", "space")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def study_from_config(cfg: dict | None) -> Study:
    st = Study()
    for sec, vals in (cfg or {}).items():
        if sec not in SECTIONS:
            continue
        obj = getattr(st, sec)
        for k, v in vals.items():
            if not hasattr(obj, k):
                close = [n for n in vars(obj) if k.lower()[:5] in n.lower()]
                raise ValueError(f'unknown setting "{sec}.{k}"' + (f". Did you mean {close}?" if close else
                                 f". Valid {sec} settings: {sorted(vars(obj))}"))
            cur = getattr(obj, k)
            setattr(obj, k, tuple(v) if isinstance(cur, tuple) else v)
    unknown = [k for k in (cfg or {}) if k not in SECTIONS and k not in ("weather", "design", "week", "base_config")
               and not k.startswith("_")]
    if unknown:
        raise ValueError(f"unknown config section(s) {unknown}. Valid sections: weather, {', '.join(SECTIONS)}")
    st.building.validate()
    return st


def study_to_dict(st: Study) -> dict:
    return {s: dataclasses.asdict(getattr(st, s)) for s in SECTIONS}


def load_weather(cfg: dict) -> WeatherYear:
    w = (cfg or {}).get("weather", {})
    src = w.get("file")
    if src:
        return load_epw(src) if str(src).lower().endswith(".epw") else load_csv(src)
    return synthesize_waitsfield(seed=w.get("seed", 7))


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def annual_readiness(st: Study, wy: WeatherYear, design: pd.Series, days: int = 3) -> pd.DataFrame:
    """Deploy the chosen design on every day of the year (vectorized over start dates)."""
    df = wy.df
    T = 24 * days
    starts = np.arange(0, len(df) - T + 1, 24)
    N = len(starts)
    idx = starts[None, :] + np.arange(T)[:, None]                       # (T, N)
    pvk = st.pv.per_kw(df)
    sol = window_solar_gains(df, st.building)
    hours = np.arange(T) % 24
    prof = st.loads.profiles(hours, st.building.occupants, st.building.floor_area_m2)
    occ = occupant_gains_kw(hours, st.building.occupants)
    internal = (prof["elec_gains_kw"] + occ)[:, None]
    t_out = df.temp_c.values[idx]
    sc = Scenario("annual", "Any-day deployment", df.index, t_out, pvk[idx], sol[idx],
                  prof["crit"], prof["ess"], prof["disc"], np.repeat(0.5 * internal, N, 1),
                  0.5 * internal + sol[idx], summer=t_out.mean(0) > 15.0)
    hp_kw = design.hp_units * st.hp.unit_kw
    r = simulate(sc, np.full(N, design.pv_kw), np.full(N, design.bat_kwh), np.full(N, design.gen_kw),
                 np.full(N, hp_kw), st.building, st.hp, st.battery, st.generator, st.ev, tank_l=design.tank_l)
    return pd.DataFrame({
        "start": df.index[starts], "t_out_mean": t_out.mean(0), "t_out_min": t_out.min(0),
        "fuel_l_per_day": r["fuel_l"] / days, "t_in_min": r["t_min"], "t_in_max": r["t_max"],
        "unmet_kwh": r["unmet"], "pv_kwh_per_day": r["pv_used"] / days, "demand_kwh_per_day": r["demand"] / days,
        "renew_frac": 1 - (r["gen_kwh"] - r["dump"]) / np.maximum(r["served"], 1e-9),
        "cold_dh": r["cold_dh"], "hot_dh": r["hot_dh"], "unmet_crit_kwh": r["unmet_crit"],
        "unmet_frac": r["unmet"] / np.maximum(r["demand"], 1e-9),
    })


def readiness_violations(st: Study, ann: pd.DataFrame) -> pd.DataFrame:
    c = st.constraints
    bad = ((ann.cold_dh > c.max_cold_degree_hours) | (ann.hot_dh > c.max_hot_degree_hours)
           | (ann.unmet_crit_kwh > 0.01) | (ann.unmet_frac > c.max_unmet_frac))
    return ann[bad]


def dispatch_series(st: Study, scenarios, design: pd.Series) -> dict:
    out = {}
    hp_kw = design.hp_units * st.hp.unit_kw
    for sc in scenarios:
        r = simulate(sc, [design.pv_kw], [design.bat_kwh], [design.gen_kw], [hp_kw], st.building, st.hp,
                     st.battery, st.generator, st.ev, record=True, tank_l=design.tank_l)
        s = {k: v[:, 0] for k, v in r["series"].items()}
        s["t_out"] = np.asarray(sc.t_out, float)
        s["time"] = sc.index
        bal = s["pv"] + s["gen"] + s["bat_dis"] + s["ev"] - s["load"] - s["bat_ch"]
        s["balance_err"] = float(np.abs(bal).max())
        out[sc.key] = dict(label=sc.label, series=s, metrics={k: float(np.ravel(v)[0]) for k, v in r.items()
                                                              if k != "series"})
    return out


# ---------------------------------------------------------------------------
# Sensitivity cases (each is fully re-optimized)
# ---------------------------------------------------------------------------
def default_cases():
    def env(name):
        return lambda s: setattr(s.building, "envelope", name)

    def occ(n):
        return lambda s: setattr(s.building, "occupants", n)

    return [
        ("Base case", lambda s: None),
        ("Leaky 1950s envelope", env("leaky_1950s")),
        ("Typical 1980s envelope", env("typical_1980s")),
        ("Efficient modern envelope", env("efficient_modern")),
        ("20 occupants", occ(20)),
        ("80 occupants", occ(80)),
        ("Heat-recovery ventilator (70 %)", lambda s: setattr(s.building, "hrv_effectiveness", 0.7)),
        ("Hold 60 F instead of 55 F", lambda s: setattr(s.building, "heat_setpoint_c", 15.6)),
        ("EV pickup supplies 60 kWh", lambda s: setattr(s.ev, "available_kwh", 60.0)),
        ("Propane generator", lambda s: (setattr(s.generator, "fuel", "propane"),
                                         setattr(s.econ, "fuel_usd_per_l", 0.75))),
        ("PV net-metered when parked", lambda s: setattr(s.econ, "idle_pv_usd_per_kwh", 0.19)),
        ("Triple-pane low-e windows", lambda s: setattr(s.building, "window_type", "triple_lowe")),
        ("Double-pane low-e windows", lambda s: setattr(s.building, "window_type", "double_lowe")),
        ("Twice as many windows", lambda s: setattr(s.building, "window_count", 2 * (s.building.window_count or 7))),
        ("Minimise up-front cost", lambda s: setattr(s.constraints, "objective", "capex")),
    ]


# ---------------------------------------------------------------------------
# Generator x battery trade-off (other equipment held at the optimum)
# ---------------------------------------------------------------------------
GEN_OPTIONS = [0, 5, 6, 8, 10, 12, 15, 17, 20, 25, 30]
BAT_OPTIONS = [0, 5, 10, 15, 20, 30, 45, 60, 80, 100]


def gen_battery_options(st: Study, scs, best: pd.Series, gens=None, bats=None) -> pd.DataFrame:
    """Every generator x battery size with PV, heat pumps and tank fixed at the optimum.

    Reports generator run time (h/day), starts, fuel, payload and feasibility for each combination,
    which shows directly how battery size trades against generator run time and trailer weight.
    """
    gens = sorted(set(gens or GEN_OPTIONS) | {float(best.gen_kw)})
    bats = sorted(set(bats or BAT_OPTIONS) | {float(best.bat_kwh)})
    rows = [(best.pv_kw, b, g, best.hp_units, best.tank_l if g > 0 else 0.0) for g in gens for b in bats]
    d = pd.DataFrame(rows, columns=["pv_kw", "bat_kwh", "gen_kw", "hp_units", "tank_l"]).astype(float)
    r = evaluate(st, scs, d)
    short = [sc for sc in scs if sc.hours <= 72]
    for sc in scs:
        days = sc.hours / 24
        r[f"run_h_day_{sc.key}"] = r[f"gen_h_{sc.key}"] / days
        r[f"starts_day_{sc.key}"] = r[f"gen_starts_{sc.key}"] / days
    r["run_h_day_worst"] = r[[f"run_h_day_{sc.key}" for sc in short]].max(axis=1)
    r["starts_day_worst"] = r[[f"starts_day_{sc.key}" for sc in short]].max(axis=1)
    r["fuel_lpd_worst"] = r[[f"fuel_lpd_{sc.key}" for sc in short]].max(axis=1)
    w = st.econ.scenario_weights
    ws = sum(w.get(sc.key, 0) for sc in short) or 1
    r["run_h_day_expected"] = sum(w.get(sc.key, 0) * r[f"run_h_day_{sc.key}"] for sc in short) / ws
    r["is_best"] = np.isclose(r.gen_kw, best.gen_kw) & np.isclose(r.bat_kwh, best.bat_kwh)
    return r


def window_study(st: Study, wy, windows, days=3, types=("single_pane", "double_clear", "double_lowe", "triple_lowe"),
                 counts=None) -> pd.DataFrame:
    """Re-optimize the system for each window type x window count."""
    from .models import WINDOW_TYPES
    if counts is None:   # half, base and double the base building's window count
        n0 = st.building.window_count or 7
        counts = sorted({max(1, round(n0 / 2)), n0, 2 * n0})
    rows = []
    for t in types:
        for n in counts:
            def mut(s, t=t, n=n):
                s.building.window_type = t
                s.building.window_count = n
            s2, r2, b2 = run_case(st, wy, windows, mut, days)
            tp = s2.building.thermal_params()
            wsp = tp["windows"]
            row = _summ(f"{WINDOW_TYPES[t].name}, {n} windows", b2, s2, r2)
            dt = s2.building.heat_setpoint_c - (-25.0)   # 99.6 % design temperature
            row.update(window_type=t, window_name=WINDOW_TYPES[t].name, window_count=n,
                       window_area_ft2=wsp["area_ft2"], window_u_ip=wsp["u_ip"], window_shgc=wsp["shgc"],
                       window_ua_w_k=tp["breakdown"]["Windows"], ua_total_w_k=tp["ua_total"],
                       design_heat_loss_kw=tp["ua_total"] * dt / 1000.0)
            rows.append(row)
    return pd.DataFrame(rows)


def _summ(label, best, st, res):
    n_feas, n_tot = int(res.feasible.sum()), len(res)
    if best is None:
        return dict(case=label, feasible=False, n_feasible=n_feas, n_designs=n_tot,
                    binding=binding_constraint(res), envelope=st.building.envelope,
                    occupants=st.building.occupants)
    return dict(case=label, feasible=True, pv_kw=best.pv_kw, bat_kwh=best.bat_kwh, gen_kw=best.gen_kw,
                hp_kw=best.hp_kw, hp_units=int(best.hp_units), tank_l=best.tank_l, inverter_kw=best.inverter_kw,
                capex_usd=best.capex_usd, lifecycle_usd=best.lifecycle_usd, weight_kg=best.weight_kg,
                fuel_l_per_day=best.exp_fuel_l_per_day, worst_fuel_l_per_day=max(
                    best[c] for c in best.index if c.startswith("fuel_lpd_") and not c.endswith("endurance")),
                autonomy_days=best.get("autonomy_d_endurance", np.nan),
                fuel_need_14d_l=best.get("fuel_need_l_endurance", np.nan),
                run_h_day_cold=best.get("gen_h_cold_snap", np.nan) / 3.0,
                n_feasible=n_feas, n_designs=n_tot, envelope=st.building.envelope, occupants=st.building.occupants)


def optimize_with_readiness(st: Study, wy, windows, days=3, max_iter=3, log=None):
    """Optimize on the design windows, then deploy the winner on every day of the year.
    If some start date breaks a constraint, add that window as a new design window and re-optimize."""
    windows = list(windows)
    for it in range(max_iter + 1):
        scs = make_scenarios(st, wy, windows)
        res, best = optimize(st, scs, verbose=bool(log) and it == 0)
        if best is None:
            return res, None, windows, scs, None
        ann = annual_readiness(st, wy, best, days)
        bad = readiness_violations(st, ann)
        if bad.empty or it == max_iter:
            return res, best, windows, scs, ann
        worst = bad.sort_values(["cold_dh", "unmet_frac", "hot_dh"], ascending=False).iloc[0]
        start = pd.Timestamp(worst.start)
        windows.append(DesignWindow(f"check_{it + 1}", f"Annual check {start:%b %d}",
                                    f"Added automatically: the {days}-day window starting {start:%b %d} "
                                    f"broke a constraint for the previous optimum", start, days))
        if log:
            log(f"  annual check: {len(bad)} start dates fail -> adding window {start:%b %d} and re-optimizing")


def run_case(base: Study, wy, windows, mutate, days=3):
    st = copy.deepcopy(base)
    mutate(st)
    res, best, _, _, _ = optimize_with_readiness(st, wy, windows, days, max_iter=2)
    return st, res, best


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------
def run(cfg: dict | None = None, out_dir: str | Path = "results", sensitivity: bool = True,
        make_report: bool = True, verbose: bool = True) -> dict:
    cfg = cfg or {}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log = print if verbose else (lambda *a, **k: None)

    st = study_from_config(cfg)
    wy = load_weather(cfg)
    wcfg = cfg.get("weather", {})
    days = wcfg.get("design_days", 3)
    windows = select_design_windows(wy, days=days, endurance_days=wcfg.get("endurance_days", 14))
    st.annual_pv_kwh_per_kw = float(st.pv.per_kw(wy.df).sum())
    log(f"Weather: {wy.source}")
    for w in windows:
        s = w.slice(wy)
        log(f"  {w.label:28s} {w.start:%b %d}  Tmean {s.temp_c.mean():6.1f} C  Tmin {s.temp_c.min():6.1f} C  "
            f"GHI {s.ghi.sum() / 1000 / w.days:4.2f} kWh/m2/d")
    tp = st.building.thermal_params()
    log(f"Building: {st.building.floor_area_m2:.0f} m2, {st.building.occupants} occupants, "
        f"{st.building.env().name}, UA {tp['ua_total']:.0f} W/K")

    log("Optimizing base case...")
    res, best, windows, scs, ann = optimize_with_readiness(st, wy, windows, days, log=log)
    results = dict(study=st, weather=wy, windows=windows, scenarios=scs, sweep=res, best=best, cfg=cfg)
    res.to_csv(out / "sweep_results.csv", index=False)
    if best is None:
        log("No feasible design in the sweep space. Widen SweepSpace or relax constraints.")
        log(res.fail_reason.value_counts().head().to_string())
        return results
    log(f"  OPTIMUM  PV {best.pv_kw:.1f} kW | battery {best.bat_kwh:.0f} kWh | generator {best.gen_kw:.1f} kW "
        f"({best.tank_l:.0f} L tank) | heat pumps {best.hp_kw:.1f} kW ({int(best.hp_units)} x {st.hp.unit_kw} kW)")
    log(f"           capex ${best.capex_usd:,.0f} | lifecycle ${best.lifecycle_usd:,.0f} | "
        f"payload {best.weight_kg:,.0f} kg | autonomy {best.autonomy_d_endurance:.1f} d")

    results["dispatch"] = dispatch_series(st, scs, best)
    results["annual"] = ann
    ann.to_csv(out / "annual_readiness.csv", index=False)
    results["pareto_cost_fuel"] = pareto_front(res, "capex_usd", "exp_fuel_l_per_day")
    gb = gen_battery_options(st, scs, best)
    results["gen_bat"] = gb
    keep = ["gen_kw", "bat_kwh", "feasible", "fail_reason", "run_h_day_worst", "run_h_day_expected", "starts_day_worst",
            "fuel_lpd_worst", "weight_kg", "capex_usd", "lifecycle_usd", "autonomy_d_endurance"]
    keep += [c for c in gb.columns if c.startswith(("run_h_day_", "starts_day_", "wt_")) and c not in keep]
    gb[keep].to_csv(out / "generator_battery_options.csv", index=False)
    log(f"Generator x battery options: {len(gb)} combinations, {int(gb.feasible.sum())} meet every limit")

    # autonomy ladder: cheapest design that carries the worst multi-week stretch N days on one tank
    ladder = []
    for target in (3, 5, 7, 10, 14):
        s2 = copy.deepcopy(st)
        s2.constraints.min_autonomy_days = target
        s2.space.pv_kw = sorted(set(st.space.pv_kw) | {24.0})
        s2.space.bat_kwh = sorted(set(st.space.bat_kwh) | {120.0, 150.0})
        s2.space.tank_l = [190.0, 380.0, 570.0, 760.0, 950.0, 1140.0]
        r2, b2 = optimize(s2, scs, verbose=False)
        row = _summ(f"{target} days", b2, s2, r2)
        row["target_days"] = target
        near = r2[(r2.n_violations == 0) | (r2.all_reasons == "fuel autonomy below target; ")]
        row["max_autonomy_days"] = float(near.autonomy_d_endurance.max()) if len(near) else np.nan
        ladder.append(row)
    results["ladder"] = pd.DataFrame(ladder)
    results["ladder"].to_csv(out / "autonomy_ladder.csv", index=False)
    log("Autonomy ladder done.")

    if sensitivity:
        rows = []
        for label, fn in default_cases():
            s2, r2, b2 = run_case(st, wy, windows, fn, days) if label != "Base case" else (st, res, best)
            rows.append(_summ(label, b2, s2, r2))
        results["sensitivity"] = pd.DataFrame(rows)
        results["sensitivity"].to_csv(out / "sensitivity.csv", index=False)
        grid_rows = []
        for env in ENVELOPE_PRESETS:
            for n in (20, 40, 60, 80):
                def mut(s, env=env, n=n):
                    s.building.envelope = env
                    s.building.occupants = n
                s2, r2, b2 = run_case(st, wy, windows, mut, days)
                grid_rows.append(_summ(f"{env}/{n}", b2, s2, r2))
        results["matrix"] = pd.DataFrame(grid_rows)
        results["matrix"].to_csv(out / "envelope_occupant_matrix.csv", index=False)
        results["windows_study"] = window_study(st, wy, windows, days)
        results["windows_study"].to_csv(out / "window_study.csv", index=False)
        log("Sensitivity runs done.")

    with open(out / "study_config_used.json", "w") as f:
        json.dump({"weather": cfg.get("weather", {}), **study_to_dict(st)}, f, indent=2, default=str)

    pd.to_pickle(results, out / "results.pkl")   # lets `python -m microgrid_sim report` rebuild visuals
    if make_report:
        from .report import write_pngs, write_report
        path = write_report(results, out / "microgrid_report.html")
        write_pngs(results, out)
        log(f"Report: {path}")
        from .excel import write_excel
        log(f"Excel:  {write_excel(results, out / 'microgrid_results.xlsx')}")
        results["report_path"] = path
    return results
