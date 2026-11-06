"""Sanity checks: run with  python -m pytest tests  (or python tests/test_physics.py)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microgrid_sim.models import Battery, Building, EVSupport, Generator, HeatPump, Loads, PVArray  # noqa: E402
from microgrid_sim.simulate import Scenario, simulate  # noqa: E402
from microgrid_sim.weather import select_design_windows, synthesize_waitsfield  # noqa: E402


def _flat_scenario(t_out, hours=24 * 10, gains=0.0, base=0.0, pv=0.0):
    z = np.zeros(hours)
    return Scenario("t", "test", pd.date_range("2023-01-01", periods=hours, freq="h"), np.full(hours, t_out),
                    np.full(hours, pv), z, np.full(hours, base), z, z, np.full(hours, gains / 2),
                    np.full(hours, gains / 2), summer=False)


def test_steady_state_heating_matches_ua():
    """At constant outdoor temperature the delivered heat must converge to UA x dT - gains."""
    b = Building()
    tp = b.thermal_params()
    sc = _flat_scenario(-10.0, gains=2.0)
    r = simulate(sc, [0], [0], [50], [200], b, HeatPump(), Battery(), Generator(), EVSupport(), record=True, tank_l=1e6)
    q_last = r["series"]["q_heat"][-24:, 0].mean()
    expected = tp["ua_total"] / 1000 * (b.heat_setpoint_c + 10.0) - 2.0
    assert abs(q_last - expected) / expected < 0.02, (q_last, expected)


def test_free_float_decays_to_outdoor():
    b = Building()
    sc = _flat_scenario(0.0, hours=24 * 60)
    r = simulate(sc, [0], [0], [0], [0], b, HeatPump(), Battery(), Generator(), EVSupport(), record=True)
    assert abs(r["series"]["t_in"][-1, 0]) < 0.3


def test_energy_balance_and_battery_limits():
    wy = synthesize_waitsfield()
    from microgrid_sim.simulate import build_scenario
    b, L, pv = Building(), Loads(), PVArray()
    for w in select_design_windows(wy):
        sc = build_scenario(w.key, w.label, w.slice(wy), b, L, pv)
        r = simulate(sc, [12, 0, 6], [40, 20, 0], [12, 15, 20], [31.5, 35, 28], b, HeatPump(), Battery(),
                     Generator(), EVSupport(available_kwh=30), record=True)
        s = r["series"]
        bal = s["pv"] + s["gen"] + s["bat_dis"] + s["ev"] - s["load"] - s["bat_ch"]
        assert np.abs(bal).max() < 1e-6
        assert s["soc"][:, :2].min() >= 0.1 - 1e-9 and s["soc"].max() <= 1 + 1e-9
        assert (r["fuel_l"] <= 380 + 1e-6).all()


def test_window_inputs():
    """More / worse windows raise heat loss; better windows lower it; facade split moves solar gain."""
    base = Building().thermal_params()["breakdown"]["Windows"]
    assert Building(window_type="triple_lowe").thermal_params()["breakdown"]["Windows"] < base
    assert Building(window_count=48).thermal_params()["breakdown"]["Windows"] > 1.9 * base
    assert abs(Building(window_u_ip=0.30).thermal_params()["windows"]["u"] - 0.30 * 5.678) < 1e-9
    wy = synthesize_waitsfield()
    jan = wy.df.loc["2023-01-10":"2023-01-12"]
    from microgrid_sim.simulate import window_solar_gains
    south = window_solar_gains(jan, Building(window_facade_split={"S": 1.0})).sum()
    north = window_solar_gains(jan, Building(window_facade_split={"N": 1.0})).sum()
    assert south > 2 * north


def test_heat_pump_curve_monotonic():
    hp = HeatPump()
    t = np.linspace(-30, 15, 50)
    cap, cop = hp.heat_perf(t)
    assert np.all(np.diff(cap) >= -1e-9) and np.all(np.diff(cop) > 0)


def test_weather_calibration():
    wy = synthesize_waitsfield()
    d = wy.df
    assert abs(np.quantile(d.temp_c, 0.004) - (-25.0)) < 0.5
    assert 1200 < d.ghi.sum() / 1000 < 1450          # kWh/m2/yr, central Vermont


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
