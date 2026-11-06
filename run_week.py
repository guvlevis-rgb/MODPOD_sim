#!/usr/bin/env python3
"""One-week operating simulation with equipment sizes you choose, replayed in an HMI-style web page.

    python3 run_week.py                                         # configs/week_example.json, all four conditions
    python3 run_week.py -c configs/week_example.json --condition winter ice_storm
    python3 run_week.py --pv 9 --battery 30 --generator 12 --heat-pumps 31.5 --tank 150 -o week_big_battery

Open week_results/week_hmi.html in a browser. Works from any folder and with VS Code's Run button.
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
missing = [m for m in ("numpy", "pandas", "scipy") if importlib.util.find_spec(m) is None]
if missing:
    sys.exit(f"Missing Python packages: {', '.join(missing)}\nInstall with:\n  \"{sys.executable}\" -m pip install {' '.join(missing)}")

args = sys.argv[1:]
if "-c" not in args and "--config" not in args:
    args = ["-c", "configs/week_example.json"] + args
sys.argv = [sys.argv[0], "week"] + args
from microgrid_sim.__main__ import main  # noqa: E402

try:
    main()
except json.JSONDecodeError as e:
    sys.exit(f"\nConfig file is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}.")
except FileNotFoundError as e:
    sys.exit(f"\nFile not found: {e.filename}\nPaths are relative to {HERE}")
except (ValueError, KeyError) as e:
    sys.exit(f"\nInput problem: {e.args[0] if e.args else e}\nFix the config file and run again.")
