"""Coupled thermal-electrical hourly simulation, vectorized across designs.

One call simulates N candidate equipment sizings through one weather window at
once: state (indoor air/mass temperature, battery SOC, fuel, generator on/off)
is an array of length N and the time loop runs once. Weather inputs may also be
(T, N) arrays, which is how the annual "any-day deployment" check runs one
design through 365 different start dates in a single pass.

Dispatch (per hour):
  1. Thermal controller computes the heat (or cooling) needed to hold the
     setpoint; the heat pump delivers what its temperature-dependent capacity allows.
  2. Electrical demand = critical + essential + discretionary + heat-pump power.
  3. PV serves load first; surplus charges the battery, then is curtailed.
  4. Deficit: generator in HOMER-style cycle charging (starts on low SOC or when
     battery+EV cannot cover the deficit, runs until SOC reaches the stop level),
     then battery, then EV vehicle-to-load, then unserved.
  5. Unserved energy is shed discretionary -> HVAC -> essential -> critical;
     shed HVAC reduces the heat actually delivered to the building.
  6. Building air/mass nodes are integrated with backward Euler (stable at 1 h).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .models import (Battery, Building, EVSupport, Generator, HeatPump, Loads, PVArray,
                     occupant_gains_kw)
from .solar import iam_ashrae, poa_irradiance


@dataclass
class Scenario:
    key: str
    label: str
    index: pd.DatetimeIndex
    t_out: np.ndarray          # (T,) or (T,N)
    pv_per_kw: np.ndarray
    solar_gain_kw: np.ndarray
    crit: np.ndarray
    ess: np.ndarray
    disc: np.ndarray
    gain_conv_kw: np.ndarray   # internal gains to air node (excl. HVAC)
    gain_mass_kw: np.ndarray   # radiative internal + solar to mass node
    summer: bool

    @property
    def hours(self):
        return self.t_out.shape[0]


def window_solar_gains(wdf: pd.DataFrame, b: Building) -> np.ndarray:
    """Solar heat gain (kW) through the windows on each facade (N/E/S/W)."""
    from .models import FACADE_AZIMUTH
    ws = b.thermal_params()["windows"]
    total = np.zeros(len(wdf))
    for fac, area in ws["facade_m2"].items():
        if area <= 0:
            continue
        poa, beam, aoi = poa_irradiance(wdf.ghi.values, wdf.dni.values, wdf.dhi.values,
                                        wdf.zenith.values, wdf.azimuth.values, 90.0, FACADE_AZIMUTH[fac],
                                        wdf.albedo.values)
        total += area * (poa - beam * (1 - iam_ashrae(aoi, 0.1)))
    return total * ws["shgc"] * b.window_shading / 1000.0


def build_scenario(key, label, wdf, building: Building, loads: Loads, pv: PVArray) -> Scenario:
    hours = wdf.index.hour.values
    prof = loads.profiles(hours, building.occupants, building.floor_area_m2)
    occ = occupant_gains_kw(hours, building.occupants)
    sol = window_solar_gains(wdf, building)
    internal = prof["elec_gains_kw"] + occ
    return Scenario(
        key=key, label=label, index=wdf.index, t_out=wdf.temp_c.values.astype(float),
        pv_per_kw=pv.per_kw(wdf), solar_gain_kw=sol,
        crit=prof["crit"], ess=prof["ess"], disc=prof["disc"],
        gain_conv_kw=0.5 * internal, gain_mass_kw=0.5 * internal + sol,
        summer=bool(wdf.temp_c.mean() > 15.0),
    )


def _thermal_coeffs(tp, dt=3600.0):
    ca, cm = tp["c_air"] / dt / 1000.0, tp["c_mass"] / dt / 1000.0  # kW/K
    hao, hmo, ham = tp["h_ao"] / 1000.0, tp["h_mo"] / 1000.0, tp["h_am"] / 1000.0
    return ca, cm, hao, hmo, ham


def simulate(sc: Scenario, pv_kw, bat_kwh, gen_kw, hp_kw, building: Building, hp: HeatPump,
             bat: Battery, gen: Generator, ev: EVSupport, record: bool = False, tank_l: float | None = None) -> dict:
    """Simulate N designs (arrays) through one scenario. Returns metrics dict of (N,) arrays."""
    pv_kw, bat_kwh, gen_kw, hp_kw = (np.asarray(x, float) for x in np.broadcast_arrays(pv_kw, bat_kwh, gen_kw, hp_kw))
    N = pv_kw.shape[0]
    T = sc.hours
    tp = building.thermal_params()
    ca, cm, hao, hmo, ham = _thermal_coeffs(tp)
    f0, f1 = gen.fuel_coeffs()
    eta = bat.eff_oneway

    t0 = np.where(np.asarray(sc.summer), building.initial_indoor_summer_c, building.initial_indoor_winter_c)
    ta = np.broadcast_to(t0, (N,)).astype(float).copy()
    tm = ta.copy()
    soc = bat.soc_initial * bat_kwh
    soc_min = bat.soc_min * bat_kwh
    pmax_bat = bat.c_rate * bat_kwh
    fuel = np.where(gen_kw > 0, gen.tank_l if tank_l is None else tank_l, 0.0)
    ev_e = np.full(N, ev.available_kwh)
    gen_on = np.zeros(N, bool)

    z = lambda: np.zeros(N)
    m = {k: z() for k in ("demand", "served", "unmet", "unmet_crit", "pv_avail", "pv_used", "curtail",
                          "gen_kwh", "fuel_l", "gen_hours", "gen_starts", "bat_dis", "bat_ch", "ev_kwh",
                          "hvac_kwh", "heat_kwh", "cool_kwh", "heat_short_kwh", "cold_dh", "hot_dh",
                          "peak_load", "dump")}
    m["t_min"] = np.full(N, np.inf)
    m["t_max"] = np.full(N, -np.inf)
    first_unmet = np.full(N, np.inf)
    REC_KEYS = ("pv", "gen", "bat_dis", "bat_ch", "ev", "unmet", "load", "hvac", "base", "soc", "t_in", "curtail",
                "q_heat", "q_cool",
                # detailed flows (used by the week HMI)
                "pv_avail", "pv_to_load", "pv_to_bat", "gen_out", "gen_to_load", "gen_to_bat", "dump", "gen_on",
                "fuel_rate", "fuel_left", "ev_left", "crit", "ess", "disc", "hvac_dem", "shed_crit", "shed_ess",
                "shed_disc", "shed_hvac", "cop", "hp_cap", "q_need", "t_mass", "q_loss", "soc_kwh")
    rec = {k: np.zeros((T, N)) for k in REC_KEYS} if record else None

    hs, cs = building.heat_setpoint_c, building.cool_setpoint_c
    for t in range(T):
        to = sc.t_out[t]
        qa_gain = sc.gain_conv_kw[t]
        qm_gain = sc.gain_mass_kw[t]

        # --- thermal controller: Q to air node that hits the target at end of the hour
        def q_needed(target):
            tm1 = (cm * tm + qm_gain + hmo * to + ham * target) / (cm + hmo + ham)
            return (ca + hao + ham) * target - ham * tm1 - ca * ta - hao * to - qa_gain

        qh_need = np.maximum(q_needed(hs), 0.0)
        qc_need = np.where(qh_need > 0, 0.0, np.maximum(-q_needed(cs), 0.0))
        capf_h, cop_h = hp.heat_perf(to)
        capf_c, cop_c = hp.cool_perf(to)
        cap_h, cap_c = hp_kw * capf_h, hp_kw * capf_c
        qh = np.minimum(qh_need, cap_h)
        qc = np.minimum(qc_need, cap_c)
        plr_h = np.divide(qh, cap_h, out=np.zeros(N), where=cap_h > 0)
        plr_c = np.divide(qc, cap_c, out=np.zeros(N), where=cap_c > 0)
        p_hvac = (qh / (cop_h * (1 + hp.part_load_bonus * (1 - plr_h)))
                  + qc / (cop_c * (1 + hp.part_load_bonus * (1 - plr_c))))
        p_hvac = np.where(np.isfinite(p_hvac), p_hvac, 0.0)
        cop_eff = np.where(qh > 0, cop_h * (1 + hp.part_load_bonus * (1 - plr_h)),
                           np.where(qc > 0, cop_c * (1 + hp.part_load_bonus * (1 - plr_c)), 0.0))

        crit, ess, disc = sc.crit[t], sc.ess[t], sc.disc[t]
        demand = crit + ess + disc + p_hvac
        pv = pv_kw * sc.pv_per_kw[t]
        net = demand - pv

        # --- storage limits (energy per 1 h step == kW)
        p_dis_max = np.minimum(pmax_bat, np.maximum(soc - soc_min, 0) * eta)
        p_ch_max = np.minimum(pmax_bat, np.maximum(bat_kwh - soc, 0) / eta)
        ev_max = np.minimum(ev.max_kw, ev_e * ev.eff)

        # --- generator: cycle charging
        deficit = np.maximum(net, 0)
        start = (deficit > 0) & ((soc < gen.soc_start * bat_kwh - 1e-9) | (deficit > p_dis_max + ev_max + 1e-9))
        keep = gen_on & (deficit > 0) & (soc < gen.soc_stop * bat_kwh - 1e-9)
        new_on = (gen_kw > 0) & (fuel > 1e-6) & (start | keep)
        m["gen_starts"] += new_on & ~gen_on
        gen_on = new_on
        g_out = np.where(gen_on, np.clip(deficit + p_ch_max, gen.min_load_frac * gen_kw, gen_kw), 0.0)
        # fuel limit
        f_rate = np.where(gen_on, f0 * gen_kw + f1 * g_out, 0.0)
        short = gen_on & (f_rate > fuel)
        if short.any():
            frac = np.where(short, fuel / np.maximum(f_rate, 1e-9), 1.0)
            g_out = g_out * frac
            f_rate = np.where(short, fuel, f_rate)
        fuel = fuel - f_rate

        g_load = np.minimum(g_out, deficit)
        g_surplus = g_out - g_load
        rem = deficit - g_load
        b_dis = np.minimum(rem, p_dis_max)
        rem = rem - b_dis
        e_dis = np.minimum(rem, ev_max)
        rem = rem - e_dis
        unmet = rem
        pv_surplus = np.maximum(-net, 0)
        b_ch = np.minimum(pv_surplus + g_surplus, p_ch_max)
        spill = pv_surplus + g_surplus - b_ch
        dump = np.minimum(spill, g_surplus)
        soc = soc + b_ch * eta - np.divide(b_dis, eta)
        ev_e = ev_e - e_dis / ev.eff

        # --- load shedding order
        shed_disc = np.minimum(unmet, disc)
        u = unmet - shed_disc
        shed_hvac = np.minimum(u, p_hvac)
        u = u - shed_hvac
        shed_ess = np.minimum(u, ess)
        shed_crit = u - shed_ess
        hvac_frac = np.divide(p_hvac - shed_hvac, p_hvac, out=np.ones(N), where=p_hvac > 1e-9)
        qh_d, qc_d = qh * hvac_frac, qc * hvac_frac

        # --- building temperatures (backward Euler, 2x2 solved in closed form)
        qa = qa_gain + qh_d - qc_d
        a11, a12, b1 = ca + hao + ham, -ham, ca * ta + qa + hao * to
        a21, a22, b2 = -ham, cm + hmo + ham, cm * tm + qm_gain + hmo * to
        det = a11 * a22 - a12 * a21
        ta = (b1 * a22 - a12 * b2) / det
        tm = (a11 * b2 - a21 * b1) / det

        # --- metrics
        served = demand - unmet
        m["demand"] += demand
        m["served"] += served
        m["unmet"] += unmet
        m["unmet_crit"] += shed_crit
        m["pv_avail"] += pv
        m["pv_used"] += np.minimum(pv, demand) + np.minimum(b_ch, pv_surplus)
        m["curtail"] += spill - dump
        m["dump"] += dump
        m["gen_kwh"] += g_out
        m["fuel_l"] += f_rate
        m["gen_hours"] += gen_on
        m["bat_dis"] += b_dis
        m["bat_ch"] += b_ch
        m["ev_kwh"] += e_dis
        m["hvac_kwh"] += p_hvac - shed_hvac
        m["heat_kwh"] += qh_d
        m["cool_kwh"] += qc_d
        m["heat_short_kwh"] += qh_need - qh_d
        m["cold_dh"] += np.maximum(building.min_indoor_c - ta, 0)
        m["hot_dh"] += np.maximum(ta - building.max_indoor_c, 0)
        m["t_min"] = np.minimum(m["t_min"], ta)
        m["t_max"] = np.maximum(m["t_max"], ta)
        m["peak_load"] = np.maximum(m["peak_load"], served)
        first_unmet = np.where((unmet > 1e-3) & ~np.isfinite(first_unmet), t, first_unmet)
        if record:
            for k, v in (("pv", pv - (spill - dump)), ("gen", g_out - dump), ("bat_dis", b_dis), ("bat_ch", b_ch),
                         ("ev", e_dis), ("unmet", unmet), ("load", served), ("hvac", p_hvac - shed_hvac),
                         ("base", served - (p_hvac - shed_hvac)),
                         ("soc", np.divide(soc, bat_kwh, out=np.zeros(N), where=bat_kwh > 0)),
                         ("t_in", ta), ("curtail", spill - dump), ("q_heat", qh_d), ("q_cool", qc_d),
                         ("pv_avail", pv), ("pv_to_load", np.minimum(pv, demand)),
                         ("pv_to_bat", np.minimum(b_ch, pv_surplus)), ("gen_out", g_out), ("gen_to_load", g_load),
                         ("gen_to_bat", b_ch - np.minimum(b_ch, pv_surplus)), ("dump", dump),
                         ("gen_on", gen_on.astype(float)), ("fuel_rate", f_rate), ("fuel_left", fuel),
                         ("ev_left", ev_e), ("crit", np.full(N, crit)), ("ess", np.full(N, ess)),
                         ("disc", np.full(N, disc)), ("hvac_dem", p_hvac), ("shed_crit", shed_crit),
                         ("shed_ess", shed_ess), ("shed_disc", shed_disc), ("shed_hvac", shed_hvac),
                         ("cop", cop_eff), ("hp_cap", np.where(qc_need > 0, cap_c, cap_h)),
                         ("q_need", np.where(qh_need > 0, qh_need, qc_need)), ("t_mass", tm),
                         ("q_loss", hao * (ta - to) + hmo * (tm - to)), ("soc_kwh", soc)):
                rec[k][t] = v

    m["fuel_left_l"] = fuel
    m["soc_end"] = np.divide(soc, bat_kwh, out=np.zeros(N), where=bat_kwh > 0)
    m["autonomy_h"] = np.where(np.isfinite(first_unmet), first_unmet, T)
    m["days"] = T / 24.0
    if record:
        m["series"] = rec
    return m
