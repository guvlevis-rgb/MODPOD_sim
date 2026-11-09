#!/usr/bin/env python3
"""Run the MOD POD microgrid study from anywhere, including VS Code's Run button.

    python3 run_study.py                                   # base case -> results/
    python3 run_study.py -c configs/triple_lowe.json -o results_triple_lowe --quick
    python3 run_study.py report -o results                 # rebuild HTML/Excel/PNGs from a saved run

Relative paths (configs/..., results...) are taken from this folder, so it doesn't matter
which folder Terminal is in.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

missing = [m for m in ("numpy", "pandas", "scipy", "matplotlib", "openpyxl") if importlib.util.find_spec(m) is None]
if missing:
    print("Missing Python packages: " + ", ".join(missing))
    print("Install them for THIS Python with:\n")
    print(f'  "{sys.executable}" -m pip install ' + " ".join(missing) + "\n")
    sys.exit(1)

args = sys.argv[1:]
if not args:
    args = ["run", "-c", "configs/ucg40.json", "-o", "results"]
elif args[0] not in ("run", "report", "-h", "--help"):
    args = ["run"] + args
sys.argv = [sys.argv[0]] + args

from microgrid_sim.__main__ import main  # noqa: E402

print(f"Python {sys.version.split()[0]} at {sys.executable}")
print(f"Working folder: {HERE}\n")
import json  # noqa: E402

try:
    main()
except json.JSONDecodeError as e:
    sys.exit(f"\nConfig file is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}.\n"
             "Common causes: a trailing comma, a missing quote, comments, or None/True instead of null/true.")
except FileNotFoundError as e:
    sys.exit(f"\nFile not found: {e.filename}\nPaths are relative to {HERE}")
except (ValueError, KeyError) as e:
    msg = e.args[0] if e.args else str(e)
    sys.exit(f"\nInput problem: {msg}\nFix the config file and run again.")
