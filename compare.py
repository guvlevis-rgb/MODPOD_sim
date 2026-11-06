#!/usr/bin/env python3
"""Compare finished runs side by side.

Looks for every results*/results.pkl folder next to this file, prints one row per case and
writes comparison.xlsx.   Usage:  python3 compare.py
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import pandas as pd  # noqa: E402

rows = []
for f in sorted(glob.glob("results*/results.pkl")):
    r = pd.read_pickle(f)
    case = os.path.dirname(f).replace("results_", "") or "results"
    b = r["best"]
    if b is None:
        rows.append({"case": case, "result": "no feasible design"})
        continue
    b3 = [k for k in b.index if k.startswith("gen_h_") and not k.endswith("endurance")]
    w = r["study"].building.thermal_params()
    rows.append({
        "case": case,
        "windows": f"{w['windows']['count']} x {w['windows']['name']}",
        "occupants": r["study"].building.occupants,
        "PV kW": b.pv_kw, "battery kWh": b.bat_kwh, "generator kW": b.gen_kw,
        "tank gal": round(b.tank_l / 3.785), "heat pumps kW": b.hp_kw,
        "capex $": round(b.capex_usd), "lifecycle $": round(b.lifecycle_usd),
        "payload lb": round(b.weight_kg / 0.4536),
        "gen run h/day (worst)": round(max(b[k] for k in b3) / 3, 1),
        "cold-snap fuel gal/day": round(b.fuel_lpd_cold_snap / 3.785, 1),
        "autonomy days": round(b.autonomy_d_endurance, 1),
        "building UA W/K": round(w["ua_total"]),
    })

if not rows:
    sys.exit("No runs found. Run a case first, e.g.  python3 run_study.py -o results_base")
df = pd.DataFrame(rows)
print(df.to_string(index=False))
df.to_excel("comparison.xlsx", index=False)
print("\nSaved comparison.xlsx")
