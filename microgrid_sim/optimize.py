"""Design-space sweep, constraint screening, cost/weight evaluation and optimization.

Stage 1  coarse full-factorial sweep over PV kW x battery kWh x generator kW x heat-pump units
Stage 2  local refinement around the best feasible designs at purchasable increments
         (PV in 2-panel steps, battery in 5 kWh modules, catalog generator sizes, whole heat-pump heads)
Each candidate is simulated through every design window; a design is feasible only if it
holds the building between the temperature limits and serves the load in all of them,
while fitting the trailer payload and array footprint.
"""
from __future__ import annotations

import itertools
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .models import (Battery, Building, Constraints, Economics, EVSupport, Generator, HeatPump, Loads,
                     PVArray, Weights)
from .simulate import Scenario, build_scenario, simulate

DESIGN_COLS = ["pv_kw", "bat_kwh", "gen_kw", "hp_units", "tank_l"]
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)

GEN_CATALOG = [0, 3.5, 5, 6, 7.5, 8, 10, 12, 13, 15, 17, 20, 25, 30, 35, 40]


@dataclass
class SweepSpace:
    pv_kw: list = field(default_factory=lambda: [0, 3, 6, 9, 12, 15, 18, 21])
    bat_kwh: list = field(default_factory=lambda: [0, 10, 20, 30, 45, 60, 80, 100])
    gen_kw: list = field(default_factory=lambda: [0, 6, 8, 10, 12, 15, 20, 25, 30])
    hp_units: list = field(default_factory=lambda: list(range(2, 17)))
    tank_l: list = field(default_factory=lambda: [190.0, 380.0, 570.0])   # 50 / 100 / 150 gal
    refine_top_k: int = 6
    pv_step: float = 0.8                 # two 400 W modules
    bat_step: float = 5.0                # one rack module


@dataclass
class Study:
    building: Building = field(default_factory=Building)
    loads: Loads = field(default_factory=Loads)
    pv: PVArray = field(default_factory=PVArray)
    hp: HeatPump = field(default_factory=HeatPump)
    battery: Battery = field(default_factory=Battery)
    generator: Generator = field(default_factory=Generator)
    ev: EVSupport = field(default_factory=EVSupport)
    econ: Economics = field(default_factory=Economics)
    weights: Weights = field(default_factory=Weights)
    constraints: Constraints = field(default_factory=Constraints)
    space: SweepSpace = field(default_factory=SweepSpace)
    annual_pv_kwh_per_kw: float = 0.0    # filled in from the weather year by the runner


def make_scenarios(study: Study, wy, windows) -> list[Scenario]:
    return [build_scenario(w.key, w.label, w.slice(wy), study.building, study.loads, study.pv) for w in windows]


def evaluate(study: Study, scenarios: list[Scenario], designs: pd.DataFrame) -> pd.DataFrame:
    """Simulate every design through every scenario and attach cost / weight / feasibility."""
    d = designs.reset_index(drop=True).copy()
    hp_kw = d.hp_units.values * study.hp.unit_kw
    d["hp_kw"] = hp_kw
    c, e, w = study.constraints, study.econ, study.weights
    ok = np.ones(len(d), bool)
    reason = np.full(len(d), "", dtype=object)
    nviol = np.zeros(len(d), int)
    allreasons = np.full(len(d), "", dtype=object)

    def flag(mask, why):
        nonlocal ok, reason, nviol, allreasons
        reason = np.where(ok & mask, why, reason)
        nviol += mask
        allreasons = np.where(mask, allreasons + why + "; ", allreasons)
        ok &= ~mask
    peak = np.zeros(len(d))
    exp_fuel_day = np.zeros(len(d))
    exp_gen_h_day = np.zeros(len(d))
    wsum = 0.0
    for sc in scenarios:
        r = simulate(sc, d.pv_kw.values, d.bat_kwh.values, d.gen_kw.values, hp_kw, study.building, study.hp,
                     study.battery, study.generator, study.ev, tank_l=d.tank_l.values)
        k = sc.key
        days = sc.hours / 24
        d[f"fuel_l_{k}"] = r["fuel_l"]
        d[f"fuel_lpd_{k}"] = r["fuel_l"] / days
        d[f"unmet_kwh_{k}"] = r["unmet"]
        d[f"unmet_frac_{k}"] = r["unmet"] / np.maximum(r["demand"], 1e-9)
        d[f"t_min_{k}"] = r["t_min"]
        d[f"t_max_{k}"] = r["t_max"]
        d[f"gen_h_{k}"] = r["gen_hours"]
        d[f"gen_starts_{k}"] = r["gen_starts"]
        d[f"bat_cycles_{k}"] = np.divide(r["bat_dis"], d.bat_kwh.values, out=np.zeros(len(d)), where=d.bat_kwh.values > 0)
        d[f"renew_frac_{k}"] = 1 - (r["gen_kwh"] - r["dump"] + r["ev_kwh"]) / np.maximum(r["served"], 1e-9)
        d[f"autonomy_d_{k}"] = r["autonomy_h"] / 24
        d[f"demand_kwh_{k}"] = r["demand"]
        d[f"curtail_kwh_{k}"] = r["curtail"]
        peak = np.maximum(peak, r["peak_load"])
        if k == "endurance":
            # fuel the whole stretch would need with unlimited tank (-> refuel deliveries)
            r2 = simulate(sc, d.pv_kw.values, d.bat_kwh.values, d.gen_kw.values, hp_kw, study.building, study.hp,
                          study.battery, study.generator, study.ev, tank_l=1e7)
            d["fuel_need_l_endurance"] = r2["fuel_l"]
            d["unmet_unlim_endurance"] = r2["unmet"]
            flag(r["autonomy_h"] / 24 < c.min_autonomy_days - 1e-9, "fuel autonomy below target")
            if not c.require_endurance:
                continue
        fail_crit = r["unmet_crit"] > 0.01
        fail_unmet = r["unmet"] > c.max_unmet_frac * r["demand"]
        fail_cold = r["cold_dh"] > c.max_cold_degree_hours
        fail_hot = r["hot_dh"] > c.max_hot_degree_hours
        flag(fail_crit | fail_unmet, f"load unserved ({sc.label})")
        flag(fail_cold, f"too cold ({sc.label})")
        flag(fail_hot, f"too hot ({sc.label})")
        wk = e.scenario_weights.get(k, 0.0)
        exp_fuel_day += wk * r["fuel_l"] / days
        exp_gen_h_day += wk * r["gen_hours"] / days
        wsum += wk
    if wsum > 0:
        exp_fuel_day /= wsum
        exp_gen_h_day /= wsum

    inv_kw = np.maximum(3.0, np.ceil(peak * 1.15))
    d["inverter_kw"] = inv_kw
    d["peak_kw"] = peak
    capex = (d.pv_kw * e.pv_usd_per_kw + d.bat_kwh * e.battery_usd_per_kwh + inv_kw * e.inverter_usd_per_kw
             + np.where(d.gen_kw > 0, e.gen_usd_fixed + e.gen_usd_per_kw * d.gen_kw + e.tank_usd_per_l * d.tank_l, 0.0)
             + hp_kw * e.hp_usd_per_kw)
    d["capex_usd"] = capex
    d["exp_fuel_l_per_day"] = exp_fuel_day
    fuel_usd = e.fuel_usd_per_l + e.carbon_usd_per_t * study.generator.co2_kg_per_l / 1000.0
    annual = e.deployment_days_per_year * (exp_fuel_day * fuel_usd + exp_gen_h_day * e.gen_om_usd_per_hour)
    annual = annual - d.pv_kw.values * study.annual_pv_kwh_per_kw * (1 - e.deployment_days_per_year / 365) * e.idle_pv_usd_per_kwh
    d["exp_co2_kg_per_day"] = exp_fuel_day * study.generator.co2_kg_per_l
    d["annual_opex_usd"] = annual
    d["lifecycle_usd"] = capex + e.annuity_factor * annual
    wb = weight_breakdown(study, d.pv_kw.values, d.bat_kwh.values, d.gen_kw.values, hp_kw, d.tank_l.values, inv_kw)
    for k, v in wb.items():
        d[f"wt_{WEIGHT_KEYS[k]}_kg"] = v
    d["weight_kg"] = sum(wb.values())
    d["pv_area_m2"] = d.pv_kw * study.pv.m2_per_kw
    flag(d.weight_kg.values > c.max_payload_kg, "over trailer payload")
    flag(d.pv_area_m2.values > c.max_pv_area_m2, "array exceeds deployable area")
    d["feasible"] = ok
    d["fail_reason"] = reason
    d["n_violations"] = nviol
    d["all_reasons"] = allreasons
    d["objective"] = {"lifecycle": d.lifecycle_usd, "capex": d.capex_usd, "weight": d.weight_kg}[c.objective]
    return d


WEIGHT_KEYS = {"PV array": "pv", "Battery": "bat", "Inverter": "inv", "Generator": "gen", "Fuel + tank": "fuel",
               "Heat pumps": "hp", "Fixed pod systems": "fixed"}


def weight_breakdown(study: Study, pv_kw, bat_kwh, gen_kw, hp_kw, tank_l, inv_kw) -> dict:
    """Payload by component (kg). Works on scalars or arrays."""
    w, g = study.weights, study.generator
    gen_kw = np.asarray(gen_kw, float)
    has_gen = gen_kw > 0
    return {
        "PV array": np.asarray(pv_kw, float) * w.pv_kg_per_kw,
        "Battery": np.asarray(bat_kwh, float) * w.battery_kg_per_kwh,
        "Inverter": np.asarray(inv_kw, float) * w.inverter_kg_per_kw,
        "Generator": np.where(has_gen, w.gen_kg_fixed + w.gen_kg_per_kw * gen_kw, 0.0),
        "Fuel + tank": np.where(has_gen, np.asarray(tank_l, float) * (g.fuel_density + w.tank_kg_per_l), 0.0),
        "Heat pumps": np.asarray(hp_kw, float) * w.hp_kg_per_kw,
        "Fixed pod systems": np.full(np.shape(gen_kw), w.fixed_payload_kg, dtype=float),
    }


def grid(space: SweepSpace) -> pd.DataFrame:
    rows = list(itertools.product(space.pv_kw, space.bat_kwh, space.gen_kw, space.hp_units, space.tank_l))
    df = pd.DataFrame(rows, columns=DESIGN_COLS).astype(float)
    # no generator -> no tank; collapse duplicates
    df.loc[df.gen_kw == 0, "tank_l"] = 0.0
    return df.drop_duplicates().reset_index(drop=True)


def _neighbors(val, step, lo=0.0, n=3):
    return sorted({max(lo, round(val + i * step, 3)) for i in range(-n, n + 1)})


def _gen_neighbors(g):
    i = int(np.argmin(np.abs(np.array(GEN_CATALOG) - g)))
    return GEN_CATALOG[max(0, i - 2): i + 3]


def refine_grid(coarse: pd.DataFrame, space: SweepSpace) -> pd.DataFrame:
    top = coarse[coarse.feasible].nsmallest(space.refine_top_k, "objective")
    rows = set()
    for _, r in top.iterrows():
        tanks = sorted(space.tank_l)
        ti = int(np.argmin(np.abs(np.array(tanks) - (r.tank_l or tanks[0]))))
        for p, b, g, h, tk in itertools.product(_neighbors(r.pv_kw, space.pv_step, n=4),
                                                _neighbors(r.bat_kwh, space.bat_step, n=3),
                                                _gen_neighbors(r.gen_kw),
                                                _neighbors(r.hp_units, 1.0, lo=1.0, n=1),
                                                tanks[max(0, ti - 1): ti + 2]):
            rows.add((p, b, g, h, 0.0 if g == 0 else tk))
    return pd.DataFrame(sorted(rows), columns=DESIGN_COLS).astype(float)


def optimize(study: Study, scenarios: list[Scenario], verbose=True):
    coarse = evaluate(study, scenarios, grid(study.space))
    coarse["stage"] = "coarse"
    if verbose:
        print(f"  coarse sweep: {len(coarse):,} designs, {coarse.feasible.sum():,} feasible")
    if not coarse.feasible.any():
        return coarse, None
    fine = evaluate(study, scenarios, refine_grid(coarse, study.space))
    fine["stage"] = "refined"
    if verbose:
        print(f"  refinement : {len(fine):,} designs, {fine.feasible.sum():,} feasible")
    allr = pd.concat([coarse, fine], ignore_index=True)
    allr = allr.drop_duplicates(subset=DESIGN_COLS, keep="last").reset_index(drop=True)
    best = allr[allr.feasible].nsmallest(1, "objective").iloc[0]
    return allr, best


def binding_constraint(df: pd.DataFrame) -> str:
    """Most common single reason among near-miss designs (those failing exactly one check)."""
    near = df[df.n_violations == 1]
    if near.empty:
        near = df[df.n_violations == df.n_violations[df.n_violations > 0].min()] if (df.n_violations > 0).any() else df
    vc = near.all_reasons.str.split("; ").explode().replace("", np.nan).dropna().value_counts()
    return vc.index[0] if len(vc) else ""


def pareto_front(df: pd.DataFrame, x: str, y: str) -> pd.DataFrame:
    f = df[df.feasible].sort_values([x, y])
    keep, best_y = [], np.inf
    for i, r in f.iterrows():
        if r[y] < best_y - 1e-9:
            keep.append(i)
            best_y = r[y]
    return f.loc[keep]
