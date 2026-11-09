"""Command line entry point.

    python -m microgrid_sim run                      # defaults: synthetic Waitsfield year, base building
    python -m microgrid_sim run -c configs/ucg40.json -o results
    python -m microgrid_sim run --weather path/to/USA_VT_Montpelier.epw --quick
"""
import argparse
import json
import os
import sys

if __package__ in (None, ""):          # started as a plain file (e.g. VS Code's Run button)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "microgrid_sim"
from microgrid_sim.study import run


def main():
    ap = argparse.ArgumentParser(prog="microgrid_sim", description="MOD POD hybrid microgrid sizing study")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the sweep, optimization, checks and report")
    r.add_argument("-c", "--config", help="JSON config with parameter overrides")
    r.add_argument("-o", "--out", default="results", help="output folder")
    r.add_argument("-w", "--weather", help="EPW or CSV weather file (overrides config)")
    r.add_argument("--quick", action="store_true", help="skip the sensitivity re-optimizations")
    r.add_argument("--no-report", action="store_true")
    wk = sub.add_parser("week", help="simulate one week with sizes you enter; writes an HMI-style HTML page")
    wk.add_argument("-c", "--config", help="JSON config (building sections + design + week)")
    wk.add_argument("-o", "--out", default="week_results", help="output folder")
    wk.add_argument("-w", "--weather", help="EPW or CSV weather file (overrides config)")
    wk.add_argument("--condition", nargs="+", choices=["winter", "summer", "flood", "ice_storm", "all"],
                    help="condition(s) to run (default: from config, else all four)")
    wk.add_argument("--pv", type=float, help="PV array kW")
    wk.add_argument("--battery", type=float, help="battery kWh")
    wk.add_argument("--generator", type=float, help="generator kW (0 = none)")
    wk.add_argument("--heat-pumps", type=float, help="heat-pump heating capacity kW")
    wk.add_argument("--tank", type=float, help="fuel tank gallons")
    rp = sub.add_parser("report", help="rebuild the HTML report and PNGs from a saved results folder")
    rp.add_argument("-o", "--out", default="results")
    a = ap.parse_args()
    if a.cmd == "week":
        from microgrid_sim.week import load_config, run_week
        cfg = load_config(a.config)
        if a.weather:
            cfg.setdefault("weather", {})["file"] = a.weather
        conds = None if not a.condition or "all" in a.condition else a.condition
        run_week(cfg, out_dir=a.out, conditions=conds,
                 design_overrides=dict(pv_kw=a.pv, bat_kwh=a.battery, gen_kw=a.generator, hp_kw=a.heat_pumps,
                                       tank_gal=a.tank))
        return
    if a.cmd == "report":
        import pandas as pd
        from pathlib import Path
        from microgrid_sim.report import write_pngs, write_report
        res = pd.read_pickle(Path(a.out) / "results.pkl")
        print(write_report(res, Path(a.out) / "microgrid_report.html"))
        write_pngs(res, a.out)
        from microgrid_sim.excel import write_excel
        print(write_excel(res, Path(a.out) / "microgrid_results.xlsx"))
        return
    from microgrid_sim.week import load_config
    cfg = load_config(a.config) if a.config else {}
    if a.weather:
        cfg.setdefault("weather", {})["file"] = a.weather
    run(cfg, out_dir=a.out, sensitivity=not a.quick, make_report=not a.no_report)


if __name__ == "__main__":
    main()
