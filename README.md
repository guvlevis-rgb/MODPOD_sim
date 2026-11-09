# MOD POD microgrid sizing tool

Python simulation and optimization tool for the MOD POD community resilience pod (Group 498, VEIC). It sizes the PV array, LFP battery, diesel generator (and its fuel tank), and cold-climate air-source heat pumps for a towable pod that has to keep a municipal shelter in Waitsfield, VT between 50 °F and 90 °F and powered for at least 3 days.

## Base case: `configs/ucg40.json`

Every default in the tool is the **ucg40** base case: the launchers run it when no config is given, the model's built-in
defaults match it, and every other case config inherits it with `"base_config": "ucg40.json"` and changes one thing.

| Input | ucg40 value |
|---|---|
| Shelter | 180 m² (1,940 ft²), 10 ft ceilings, 40 occupants |
| Envelope | `typical_2010s` (IECC 2012 era): wall U-0.053, roof U-0.032 Btu/h·ft²·°F, 0.3 air changes/h, no heat-recovery ventilator |
| Windows | 7 single-pane windows, 44 × 72 in (154 ft² in total, 9 % of wall), half on the north wall, half on the south |
| Thermostat and limits | heat to 55 °F, cool to 80 °F; shelter must stay between 50 °F and 90 °F |
| Critical loads | communications 200 W, refrigeration 3.5 kWh/day, pod controls 60 W; no medical load |
| Other loads | LED lighting, water system, device charging, heat-pump water heater (20 L per person per day); no electric cooking |
| Equipment rules | PV 45° tilt, snow brushed off at 9 am; LFP battery 0.5C, 10 % floor; diesel generator cycle-charging 30 % → 85 %; 3.5 kW heat-pump heads |
| Limits and costs | trailer payload 6,393 lb, array area 1,184 ft², 12 deployment days a year, diesel $4.35/gal, carbon $190/t |
| Weather | synthetic Waitsfield typical year (seed 7) |

The building's heat loss is 442 W/K. Ventilation for 40 people is the largest part (186 W/K), then windows (83),
infiltration (54), walls (45), slab edge (41) and roof (32).

**Base-case result** (`python3 run_study.py`, synthetic weather):

| | |
|---|---|
| Recommended design | 1.4 kW PV, 10 kWh battery, 10 kW diesel generator with a 50 gal tank, 24.5 kW of heat pumps (7 × 3.5 kW), 15 kW inverter |
| Cost | $42.0k equipment, $49.5k over 15 years |
| Payload | 4,886 lb of the 6,393 lb trailer limit |
| Worst 3-day window | 15.3 gal of diesel a day, generator running 20.7 h a day, shelter low 52.6 °F |
| Fuel autonomy | 6.0 days on one tank through the worst 14-day winter stretch |

Simulated designs: 27,888, of which 1,214 met every limit.

What changes it most (each case re-optimized):

| Case | Equipment cost | What changes |
|---|---|---|
| Heat-recovery ventilator, 70 % | $25.8k | 14 kW of heat pumps, 7.5 kW generator |
| Better windows (double low-e or triple low-e) | $34.3k | 21 kW of heat pumps, 5 kWh battery |
| 80 occupants | $51.5k | 15 kW generator, 31.5 kW of heat pumps |
| Older 1980s envelope | $49.9k | 13 kW generator, 31.5 kW of heat pumps |
| Leaky 1950s envelope | none | no design fits on the trailer |

The base case only has to ride through 3 days without fuel resupply. Run for a full week at these sizes
(`python3 run_week.py`), the 50 gal tank runs dry in the winter and ice-storm weeks. The report's autonomy ladder
finds designs that last 10 and 14 days on one tank: a 12 kW generator with a 150 or 200 gal tank, no PV, 5 kWh
battery, at $40.6k and $41.3k of equipment.

## Quick start

```bash
cd path/to/MODPOD_sim                  # the folder that contains run_study.py
python3 -m pip install -r requirements.txt
python3 run_study.py                                                  # ucg40 base case -> results/
python3 run_study.py -c configs/triple_lowe.json -o results_triple_lowe --quick
python3 compare.py                                                    # side-by-side table + comparison.xlsx
python3 run_week.py                                                   # one-week HMI replay at the base-case sizes
```

`run_study.py`, `run_week.py` and `compare.py` work from any folder and with VS Code's Run button. A case config only
needs `"base_config": "ucg40.json"` and the settings it changes:

| Config | Case |
|---|---|
| `configs/ucg40.json` | base case (all settings written out) |
| `configs/triple_lowe.json` | base case with triple-pane low-e windows |
| `configs/60_people_hrv.json` | base case with 60 occupants and a 70 % heat-recovery ventilator |
| `configs/typical_1980s.json` | base case in an older 1980s envelope |
| `configs/week_example.json` | one-week simulation of the base case at the recommended sizes |

## Quick start (module form)

```bash
pip install numpy pandas scipy matplotlib openpyxl
python -m microgrid_sim run -c configs/ucg40.json -o results            # full study, ~5 min
python -m microgrid_sim run -o results --quick                           # skip sensitivity re-runs (~30 s)
python -m microgrid_sim run -w USA_VT_Barre-Montpelier.Knapp.State.AP.726145_TMYx.epw -o results_epw
python -m microgrid_sim report -o results                               # rebuild HTML, Excel and PNGs from saved results
python tests/test_physics.py                                             # sanity checks
```

Outputs in the results folder:

| File | Contents |
|---|---|
| `microgrid_report.html` | Interactive report: optimum, dispatch per design window, weather year, sweep explorer, autonomy ladder, sensitivity, any-day deployment check, assumptions |
| `microgrid_results.xlsx` | Excel version of the report: Summary, one sheet per design window (hourly data + charts), Weather, Design sweep, Feasible designs, Autonomy, Sensitivity, Annual check, Assumptions, All designs. 28 native Excel charts; totals, unit conversions, limits and cross-sheet summaries are live formulas |
| `dispatch_*.png`, `sweep_cost_vs_fuel.png`, `sensitivity_capex.png` | Static figures for slides |
| `sweep_results.csv` | Every simulated design with per-window metrics, cost, weight, feasibility and failure reason |
| `annual_readiness.csv` | Optimum deployed on every day of the year |
| `generator_battery_options.csv`, `window_study.csv`, `autonomy_ladder.csv`, `sensitivity.csv`, `envelope_occupant_matrix.csv` | Re-optimized designs for each case |
| `study_config_used.json` | Every parameter value used, for reproducibility |

## Weather

* **Default:** a synthetic typical year for Waitsfield (44.19 N, 72.82 W) generated from monthly climate normals (temperature max/min and GHI) and calibrated to the 99.6 % heating (-13 °F) and 0.4 % cooling (86 °F) design temperatures. It is deterministic for a given seed. Edit `NORMALS` in `weather.py` if you have better local data.
* **Real data:** pass any EnergyPlus `.epw` file with `-w`. The nearest TMYx stations are Barre-Montpelier (726145) and Burlington (726170) from climate.onebuilding.org. An NSRDB PSM TMY download for the Waitsfield coordinates also works once converted to EPW, or as a CSV with columns `timestamp,temp_c,ghi[,dni,dhi,wind_ms,snow_depth_cm]`.

The tool picks the design windows from whichever year you load: winter cold snap, winter dark storm, spring flood (overcast), summer heat wave, and a 14-day winter endurance stretch. After optimizing, it deploys the winner on every day of the year. If any start date breaks a limit, that window is added as a design window and the optimization runs again.

## One-week operating simulation (HMI replay)

A second tool runs equipment sizes **you choose** (nothing is optimized) through one week of weather and writes an
operator-screen style web page.

```bash
python3 run_week.py                                           # configs/week_example.json: ucg40 at the recommended sizes
python3 run_week.py --condition winter ice_storm              # just these
python3 run_week.py --pv 9 --battery 30 --generator 12 --heat-pumps 31.5 --tank 150 -o week_big_battery
```

Open `week_results/week_hmi.html` (works offline). It shows a one-line diagram with animated power, fuel and heat
flows (line thickness and speed scale with kW), status lamps and gauges, a faceplate for any component you click,
an hour-by-hour replay with play/step/alarm-jump controls, four synced trend charts, an event and alarm log, and
week totals with trailer payload. Hourly CSVs and a JSON file are written alongside.

The config uses the same sections as the sizing study. `"base_config": "ucg40.json"` inherits the base case;
then add:

| Section | Settings |
|---|---|
| `design` | `pv_kw`, `bat_kwh`, `gen_kw` (0 = none), `hp_kw` (or `hp_units`), `tank_gal` (or `tank_l`), `inverter_kw` (null = sized to peak), `ev_kwh` |
| `week` | `conditions` (any of `winter`, `summer`, `flood`, `ice_storm`), `days` (default 7), `start_dates` (e.g. `{"winter": "2023-01-20"}`), `ice_storm` (`storm_hours`, `ice_clear_hours`, `post_storm_cooling_c`) |

| Condition | Week used |
|---|---|
| winter | coldest 7 days, Dec–Feb |
| summer | hottest 7 days, Jun–Aug |
| flood | darkest 7 days, Mar–May (rain-on-snow season), no fuel resupply |
| ice_storm | a near-freezing overcast start, Dec–Mar; 36 h of freezing rain (temperature held near 30 °F, sun cut 85 %, wind ×2.5), PV iced until crews clear it a day after the storm, then colder air |

## Window inputs

Windows are explicit inputs in the `building` section of the config:

| Parameter | Meaning | Default |
|---|---|---|
| `window_count` | number of windows (`null` = use the envelope preset's window-to-wall ratio) | 7 |
| `window_width_m`, `window_height_m` | size of each window | 1.1176 × 1.8288 m (44 × 72 in) |
| `window_type` | `single_pane`, `single_storm`, `double_clear`, `double_lowe`, `triple_lowe` (`null` = the envelope preset's typical window) | `single_pane` |
| `window_u_ip` | optional NFRC U-factor in Btu/h·ft²·°F, overrides the type | `null` |
| `window_shgc` | optional solar heat gain coefficient, overrides the type | `null` |
| `window_facade_split` | share of windows on each wall, e.g. `{"S": 0.5, "E": 0.2, "W": 0.2, "N": 0.1}` | `{"N": 0.5, "S": 0.5}` |

Window U-factor sets conduction loss; SHGC and facade orientation set solar gain, computed hour by hour for each wall.

## Generator and battery options

After optimizing, the tool simulates every generator size (0–30 kW) against every battery size (0–100 kWh) with the PV, heat pumps and tank held at the optimum. For each pair it reports generator run hours per day (by design window), starts per day, fuel use, payload by component, payload margin, cost, and whether it meets every limit. It appears in the HTML report (section 03), the Excel sheet "Gen & battery", and `generator_battery_options.csv`.

## Model

| Component | Model |
|---|---|
| Building | 2-node (air + mass) RC network, ISO 13790 style: walls, roof, windows, slab edge, infiltration, ventilation (ASHRAE 62.1 people + area rates, optional HRV); solar gains through windows on four façades; occupant and equipment gains. Backward-Euler, hourly. Envelope presets: `leaky_1950s`, `typical_1980s`, `typical_2010s` (IECC 2012 era), `efficient_modern`, plus per-parameter overrides. Inputs are checked before a run starts; a misspelled preset or setting stops with a message listing the valid choices. |
| Occupants and loads | Critical (comms, medical, refrigeration, controls), essential (lighting, water system, cooking), and discretionary (device charging, heat-pump water heater) profiles, scaled by occupant count. Shed in reverse priority when energy runs short; shed HVAC lowers the heat actually delivered. |
| Heat pumps | Cold-climate inverter units: capacity fraction and COP vs outdoor temperature, part-load COP bonus, low-temperature cutoff. Heating and cooling. |
| PV | Erbs decomposition, isotropic-sky transposition, IAM, Faiman cell temperature, temperature coefficient, system losses, inverter clipping. Marion sliding-snow coverage, with an option for staff to brush the array each morning. |
| Battery | LFP, C-rate limit, one-way efficiency, SOC floor. The pod deploys fully charged. |
| Generator | HOMER-style fuel curve (no-load + slope), minimum load, cycle-charging control (start on low SOC or when the deficit exceeds what the battery can supply, stop at the SOC set point), finite fuel tank. Propane option. |
| EV pickup | Optional vehicle-to-load source with an energy budget and power limit. |
| Economics | Capital cost (equipment + inverter sized to peak load + tank), lifecycle cost (fuel, generator O&M, social cost of carbon per deployment day, discounted over the lifetime), optional value of PV energy while the pod is parked. |
| Constraints | Indoor temperature limits (degree-hour tolerance), unserved energy (critical = 0), trailer payload including fuel, deployable array area, optional minimum fuel autonomy. |

## Optimization

1. Coarse full-factorial sweep over PV kW × battery kWh × generator kW × heat-pump heads × tank size (about 24,000 designs). The simulator is vectorized across designs, so each window runs as a single time loop.
2. Refinement around the six best feasible designs at purchasable increments.
3. Any-day deployment check with automatic design-window augmentation.
4. Autonomy ladder, sensitivity cases and a window study (four window types × half, base and double the window count), each fully re-optimized.

The objective is `lifecycle` (default), `capex` or `weight` (`constraints.objective`).

## Limitations

Screening-level design tool. The time step is hourly and loads are sensible only (no latent or humidity loads). There is no defrost penalty beyond what the COP curve implies, and no generator waste-heat recovery. Dispatch is rule-based, not predictive. The ucg40 base case uses envelope presets, not measured U-values; replace them with the real building's values when a site is chosen. Cost and weight coefficients are 2026 planning estimates.
