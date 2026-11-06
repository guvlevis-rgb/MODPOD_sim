"""Visual outputs: a self-contained interactive HTML report (Plotly.js) and static PNGs (matplotlib)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

L_PER_GAL = 3.78541
KG_PER_LB = 0.453592


def c2f(c):
    return np.asarray(c, float) * 9 / 5 + 32


def _r(x, n=2):
    a = np.round(np.asarray(x, float), n)
    return np.where(np.isfinite(a), a, None).tolist()


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else round(float(o), 3)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _case_rows(df: pd.DataFrame):
    rows = []
    for _, r in df.iterrows():
        d = {k: (None if (isinstance(v, float) and not np.isfinite(v)) else v) for k, v in r.items()}
        rows.append(d)
    return rows


def build_payload(res: dict) -> dict:
    st, wy, best, sweep = res["study"], res["weather"], res["best"], res["sweep"]
    b = st.building
    tp = b.thermal_params()

    # weather year (daily)
    dly = wy.daily()
    windows = [dict(key=w.key, label=w.label, desc=w.description, start=str(w.start.date()),
                    end=str((w.start + pd.Timedelta(days=w.days)).date()), days=w.days) for w in res["windows"]]

    # dispatch series
    disp = {}
    for k, v in res["dispatch"].items():
        s = v["series"]
        mt = v["metrics"]
        disp[k] = dict(
            label=v["label"], time=[t.strftime("%Y-%m-%d %H:%M") for t in s["time"]],
            pv=_r(s["pv"]), bat_dis=_r(s["bat_dis"]), gen=_r(s["gen"]), ev=_r(s["ev"]), unmet=_r(s["unmet"]),
            load=_r(s["load"]), hvac=_r(s["hvac"]), bat_ch=_r(s["bat_ch"]), soc=_r(np.asarray(s["soc"]) * 100, 1),
            t_in=_r(c2f(s["t_in"]), 1), t_out=_r(c2f(s["t_out"]), 1), curtail=_r(s["curtail"]),
            m=dict(days=mt["days"], fuel_gal=mt["fuel_l"] / L_PER_GAL, gen_h=mt["gen_hours"], starts=mt["gen_starts"],
                   unmet=mt["unmet"], demand=mt["demand"], pv=mt["pv_used"], t_min=float(c2f(mt["t_min"])),
                   t_max=float(c2f(mt["t_max"])), heat=mt["heat_kwh"], cool=mt["cool_kwh"],
                   hvac=mt["hvac_kwh"], autonomy=mt["autonomy_h"] / 24, soc_end=mt["soc_end"] * 100,
                   fuel_left_gal=mt["fuel_left_l"] / L_PER_GAL, curtail=mt["curtail"]))

    # sweep (subsample infeasible for the scatter)
    feas = sweep[sweep.feasible]
    inf = sweep[~sweep.feasible]
    inf = inf.sample(min(len(inf), 4000), random_state=1) if len(inf) else inf
    cols = ["pv_kw", "bat_kwh", "gen_kw", "hp_kw", "tank_l", "capex_usd", "lifecycle_usd", "weight_kg",
            "exp_fuel_l_per_day"]
    sc_feas = {c: _r(feas[c], 2) for c in cols}
    sc_inf = {c: _r(inf[c], 2) for c in ["capex_usd", "exp_fuel_l_per_day", "weight_kg"]}
    sc_inf["reason"] = inf.fail_reason.tolist()
    pareto = res["pareto_cost_fuel"]

    # heatmap: warmest cold-snap low achievable per generator x heat-pump size (designs within payload),
    # annotated with the cheapest feasible lifecycle cost where one exists
    coarse = sweep[sweep.stage == "coarse"]
    gens = sorted(coarse.gen_kw.unique())
    hps = sorted(coarse.hp_kw.unique())
    light = sweep[sweep.weight_kg <= st.constraints.max_payload_kg]
    tcol = "t_min_cold_snap"
    z, txt, lab = [], [], []
    for h in hps:
        rz, rt, rl = [], [], []
        for g in gens:
            cell = light[(light.gen_kw == g) & (np.isclose(light.hp_kw, h))]
            fe = cell[cell.feasible]
            if len(cell):
                rz.append(round(float(c2f(cell[tcol].max())), 1))
            else:
                rz.append(None)
            if len(fe):
                r = fe.nsmallest(1, "objective").iloc[0]
                rl.append(f"${r.lifecycle_usd / 1000:.0f}k")
                rt.append(f"feasible from ${r.lifecycle_usd / 1000:.1f}k lifecycle: PV {r.pv_kw:g} kW, "
                          f"battery {r.bat_kwh:g} kWh, tank {r.tank_l / L_PER_GAL:.0f} gal")
            else:
                rl.append("")
                rt.append("no feasible design" if len(cell) else "every combination is over the trailer payload")
        z.append(rz)
        txt.append(rt)
        lab.append(rl)
    reasons = sweep[~sweep.feasible].fail_reason.value_counts()

    ann = res["annual"]
    a = dict(start=[str(t.date()) for t in ann.start], fuel_gpd=_r(ann.fuel_l_per_day / L_PER_GAL, 2),
             t_in_min=_r(c2f(ann.t_in_min), 1), t_in_max=_r(c2f(ann.t_in_max), 1),
             t_out_mean=_r(c2f(ann.t_out_mean), 1), unmet=_r(ann.unmet_kwh, 2))

    def caserows(df):
        out = []
        for r in _case_rows(df):
            out.append(_clean(r))
        return out

    econ = st.econ
    assumptions = [
        ("Site", f"{wy.site['name']}  {wy.site['lat']:.3f} N, {abs(wy.site['lon']):.3f} W"),
        ("Weather", wy.source),
        ("Shelter zone", f"{b.floor_area_m2 * 10.764:,.0f} ft2, {b.occupants} occupants, {b.env().name}"),
        ("Windows", (f"{tp['windows']['count']} x {b.window_width_m / 0.3048:.1f} x {b.window_height_m / 0.3048:.1f} ft"
                     if tp['windows']['count'] else "from envelope window-to-wall ratio")
                    + f" = {tp['windows']['area_ft2']:,.0f} ft2 ({tp['windows']['wwr']:.0%} of wall), "
                      f"{tp['windows']['name']}, U-{tp['windows']['u_ip']:.2f} Btu/h-ft2-F, SHGC {tp['windows']['shgc']:.2f}, "
                      f"split N/E/S/W {'/'.join(f'{v:.0%}' for v in b.window_facade_split.values())}"),
        ("Envelope UA", f"{tp['ua_total']:.0f} W/K ({tp['ua_total'] * 1.8956:,.0f} Btu/h-F) incl. ventilation"),
        ("Thermostat", f"heat to {c2f(b.heat_setpoint_c):.0f} F, cool to {c2f(b.cool_setpoint_c):.0f} F; "
                       f"limits {c2f(b.min_indoor_c):.0f}-{c2f(b.max_indoor_c):.0f} F"),
        ("Heat pumps", f"cold-climate, {st.hp.unit_kw} kW per head at 47 F; cutoff {c2f(st.hp.cutoff_c):.0f} F"),
        ("PV", f"tilt {st.pv.tilt_deg:g} deg, losses {st.pv.system_losses:.0%}, snow model "
               f"{'on' if st.pv.snow_model else 'off'}, staff clear snow at {st.pv.snow_clearing_hour}:00"),
        ("Battery", f"LFP, {st.battery.c_rate}C, {st.battery.eff_oneway ** 2:.0%} round trip, "
                    f"SOC floor {st.battery.soc_min:.0%}, deploys {st.battery.soc_initial:.0%} full"),
        ("Generator", f"{st.generator.fuel}, cycle charging {st.generator.soc_start:.0%}->{st.generator.soc_stop:.0%} SOC, "
                      f"min load {st.generator.min_load_frac:.0%}"),
        ("Costs", f"PV ${econ.pv_usd_per_kw:,.0f}/kW, battery ${econ.battery_usd_per_kwh:,.0f}/kWh, "
                  f"generator ${econ.gen_usd_fixed:,.0f} + ${econ.gen_usd_per_kw:,.0f}/kW, heat pump ${econ.hp_usd_per_kw:,.0f}/kW"),
        ("Lifecycle", f"{econ.deployment_days_per_year:g} deployment days/yr, {econ.lifetime_years:g} yr, "
                      f"{econ.discount_rate:.0%} discount, fuel ${econ.fuel_usd_per_l * L_PER_GAL:.2f}/gal, "
                      f"carbon ${econ.carbon_usd_per_t:g}/t"),
        ("Trailer", f"payload limit {st.constraints.max_payload_kg / KG_PER_LB:,.0f} lb, array area "
                    f"{st.constraints.max_pv_area_m2 * 10.764:,.0f} ft2"),
    ]
    gb_payload = _gen_bat_payload(res)
    win_payload = _windows_payload(res)
    payload = dict(
        gen_bat=gb_payload, windows_study=win_payload,
        win=dict(count=tp["windows"]["count"], area_ft2=tp["windows"]["area_ft2"], name=tp["windows"]["name"],
                 u_ip=tp["windows"]["u_ip"], shgc=tp["windows"]["shgc"], wwr=tp["windows"]["wwr"],
                 ua=tp["breakdown"]["Windows"], split=b.window_facade_split),
        site=wy.site, source=wy.source, windows=windows,
        weather=dict(date=[str(d.date()) for d in dly.index], tmin=_r(c2f(dly.t_min), 1), tmax=_r(c2f(dly.t_max), 1),
                     tmean=_r(c2f(dly.t_mean), 1), ghi=_r(dly.ghi_kwh, 2)),
        best=_clean({k: best[k] for k in best.index if isinstance(best[k], (int, float, np.floating, np.integer, np.bool_))}),
        hp_unit_kw=st.hp.unit_kw, dispatch=disp,
        sweep=dict(feas=sc_feas, inf=sc_inf, pareto=dict(capex=_r(pareto.capex_usd), fuel=_r(pareto.exp_fuel_l_per_day)),
                   n=int(len(sweep)), n_feas=int(sweep.feasible.sum()),
                   heat=dict(x=[float(g) for g in gens], y=[float(h) for h in hps], z=z, text=txt, lab=lab),
                   reasons=dict(labels=reasons.index.tolist(), counts=reasons.values.tolist())),
        annual=a, ladder=caserows(res["ladder"]),
        sensitivity=caserows(res["sensitivity"]) if "sensitivity" in res else [],
        matrix=caserows(res["matrix"]) if "matrix" in res else [],
        ua=dict(labels=list(tp["breakdown"].keys()), w_k=[round(v, 1) for v in tp["breakdown"].values()]),
        assumptions=assumptions,
        building=dict(area_ft2=b.floor_area_m2 * 10.764, occupants=b.occupants, envelope=b.env().name,
                      t_min_f=float(c2f(b.min_indoor_c)), t_max_f=float(c2f(b.max_indoor_c))),
        deploy_days=econ.deployment_days_per_year,
        limits=dict(payload_lb=st.constraints.max_payload_kg / KG_PER_LB),
    )
    return _clean(payload)


PLOTLY_TAG = '<script src="https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.35.2/plotly.min.js"></script>'
PLOTLY_LOCAL = Path(__file__).parent / "assets" / "plotly-2.35.2.min.js"


def _gen_bat_payload(res):
    gb = res.get("gen_bat")
    if gb is None:
        return None
    from .optimize import WEIGHT_KEYS
    gens = sorted(gb.gen_kw.unique())
    bats = sorted(gb.bat_kwh.unique())
    idx = {(g, b): i for i, (g, b) in enumerate(zip(gb.gen_kw, gb.bat_kwh))}

    def mat(col, f=float, nd=2):
        out = []
        for b in bats:
            row = []
            for g in gens:
                v = gb[col].iloc[idx[(g, b)]]
                row.append(None if v is None or (isinstance(v, float) and not np.isfinite(v)) else
                           (round(f(v), nd) if nd is not None else f(v)))
            out.append(row)
        return out

    short = [sc for sc in res["scenarios"] if sc.hours <= 72]
    views = [("worst", "Worst 3-day window"), ("expected", "Weighted average")] + [(sc.key, sc.label) for sc in short]
    run = {k: mat(f"run_h_day_{k}", nd=1) for k, _ in views}
    starts = {k: mat(f"starts_day_{k}", nd=1) for k, _ in views if f"starts_day_{k}" in gb}
    fuel = {k: mat(f"fuel_lpd_{k}", f=lambda v: v / L_PER_GAL, nd=1) for k, _ in views if f"fuel_lpd_{k}" in gb}
    wt_cols = [(lab, f"wt_{key}_kg") for lab, key in WEIGHT_KEYS.items()]
    opts = []
    for _, r in gb[gb.feasible].sort_values(["weight_kg"]).iterrows():
        opts.append(_clean(dict(
            gen_kw=r.gen_kw, bat_kwh=r.bat_kwh, best=bool(r.is_best), weight_lb=r.weight_kg / KG_PER_LB,
            capex=r.capex_usd, lifecycle=r.lifecycle_usd, autonomy=r.get("autonomy_d_endurance"),
            run={k: r[f"run_h_day_{k}"] for k, _ in views}, starts={k: r.get(f"starts_day_{k}") for k, _ in views},
            fuel={k: r[f"fuel_lpd_{k}"] / L_PER_GAL for k, _ in views if f"fuel_lpd_{k}" in r},
            wt={lab: r[c] / KG_PER_LB for lab, c in wt_cols})))
    best = res["best"]
    best_wt = {lab: float(best[c]) / KG_PER_LB for lab, c in wt_cols}
    return dict(gens=[float(g) for g in gens], bats=[float(b) for b in bats], views=views, run=run, starts=starts,
                fuel=fuel, weight_lb=mat("weight_kg", f=lambda v: v / KG_PER_LB, nd=0),
                feasible=mat("feasible", f=bool, nd=None), reason=mat("fail_reason", f=str, nd=None),
                capex=mat("capex_usd", nd=0), options=opts, best_wt=best_wt,
                best=dict(gen=float(best.gen_kw), bat=float(best.bat_kwh)),
                fixed=dict(pv=float(best.pv_kw), hp=float(best.hp_kw), tank_gal=float(best.tank_l) / L_PER_GAL))


def _windows_payload(res):
    w = res.get("windows_study")
    if w is None or not len(w):
        return None
    rows = []
    for _, r in w.iterrows():
        d = {k: r.get(k) for k in ("window_type", "window_name", "window_count", "window_area_ft2", "window_u_ip",
                                   "window_shgc", "window_ua_w_k", "ua_total_w_k", "design_heat_loss_kw", "feasible",
                                   "pv_kw", "bat_kwh", "gen_kw", "hp_kw", "capex_usd", "lifecycle_usd", "weight_kg",
                                   "fuel_l_per_day", "worst_fuel_l_per_day", "run_h_day_cold", "binding")}
        rows.append(_clean(d))
    return rows


def write_report(res: dict, path, standalone: bool = True) -> str:
    """Write the HTML report.

    standalone=True (default) embeds Plotly.js and wraps the page in a full HTML document, so the file
    works offline and in any viewer (VS Code Live Preview, Safari, Chrome, email attachment).
    standalone=False keeps the small CDN version (used when publishing as a web page).
    """
    payload = build_payload(res)
    html = TEMPLATE.replace("/*__DATA__*/null", json.dumps(payload, separators=(",", ":")))
    if standalone:
        if PLOTLY_LOCAL.exists():
            js = PLOTLY_LOCAL.read_text(encoding="utf-8").replace("</script", "<\\/script")
            html = html.replace(PLOTLY_TAG, "<script>" + js + "</script>")
        html = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1">\n</head>\n<body>\n'
                + html + "\n</body>\n</html>\n")
    Path(path).write_text(html, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Static PNGs (for slides / offline use)
# ---------------------------------------------------------------------------
def write_pngs(res: dict, out_dir) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir)
    files = []
    col = dict(pv="#eda100", bat="#2a78d6", gen="#eb6834", ev="#1baf7a", unmet="#d03b3b", ink="#1d2430", mute="#8a93a3")
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": "#c9ced6", "axes.grid": True, "grid.color": "#e6e9ee", "grid.linewidth": 0.6})
    st = res["study"]
    for k, v in res["dispatch"].items():
        s = v["series"]
        t = pd.DatetimeIndex(s["time"])
        fig, ax = plt.subplots(3, 1, figsize=(10, 7), sharex=True, gridspec_kw=dict(height_ratios=[3, 1.6, 1.2]))
        ax[0].stackplot(t, s["pv"], s["bat_dis"], s["gen"], s["ev"], s["unmet"],
                        labels=["PV", "Battery", "Generator", "EV", "Unserved"],
                        colors=[col["pv"], col["bat"], col["gen"], col["ev"], col["unmet"]], alpha=0.9, step="post")
        ax[0].step(t, s["load"] + s["bat_ch"], where="post", color=col["ink"], lw=1.2, label="Load + charging")
        ax[0].step(t, s["load"], where="post", color=col["ink"], lw=0.8, ls="--", label="Load")
        ax[0].set_ylabel("kW")
        ax[0].legend(ncol=7, fontsize=7, loc="upper left", frameon=False)
        ax[0].set_title(f"{v['label']} - recommended design dispatch", loc="left", fontsize=11)
        ax[1].plot(t, c2f(s["t_in"]), color=col["bat"], lw=1.6, label="Indoor")
        ax[1].plot(t, c2f(s["t_out"]), color=col["mute"], lw=1, ls="--", label="Outdoor")
        ax[1].axhspan(c2f(st.building.min_indoor_c), c2f(st.building.max_indoor_c), color="#2a78d6", alpha=0.06)
        ax[1].set_ylabel("deg F")
        ax[1].legend(ncol=2, fontsize=7, frameon=False)
        ax[2].plot(t, np.asarray(s["soc"]) * 100, color=col["bat"], lw=1.4)
        ax[2].set_ylabel("Battery SOC %")
        ax[2].set_ylim(0, 105)
        fig.tight_layout()
        f = out / f"dispatch_{k}.png"
        fig.savefig(f, dpi=140)
        plt.close(fig)
        files.append(str(f))

    sw = res["sweep"]
    fig, ax = plt.subplots(figsize=(8, 5))
    inf = sw[~sw.feasible]
    ax.scatter(inf.capex_usd / 1000, inf.exp_fuel_l_per_day / L_PER_GAL, s=3, color="#c9ced6", label="Infeasible")
    fe = sw[sw.feasible]
    ax.scatter(fe.capex_usd / 1000, fe.exp_fuel_l_per_day / L_PER_GAL, s=8, color=col["bat"], label="Feasible")
    p = res["pareto_cost_fuel"]
    ax.plot(p.capex_usd / 1000, p.exp_fuel_l_per_day / L_PER_GAL, color=col["gen"], lw=1.5, label="Pareto front")
    bst = res["best"]
    ax.scatter([bst.capex_usd / 1000], [bst.exp_fuel_l_per_day / L_PER_GAL], s=80, facecolor="none",
               edgecolor=col["ink"], lw=1.5, label="Optimum")
    ax.set_xlabel("Capital cost ($k)")
    ax.set_ylabel("Expected fuel use (gal/day)")
    ax.legend(frameon=False)
    ax.set_title("Design sweep: cost vs fuel", loc="left")
    fig.tight_layout()
    f = out / "sweep_cost_vs_fuel.png"
    fig.savefig(f, dpi=140)
    plt.close(fig)
    files.append(str(f))

    if "sensitivity" in res:
        sens = res["sensitivity"]
        fig, ax = plt.subplots(figsize=(8, 5))
        ok = sens[sens.feasible]
        ax.barh(ok.case, ok.capex_usd / 1000, color=col["bat"])
        for _, r in sens[~sens.feasible].iterrows():
            ax.barh(r.case, 0)
            ax.text(1, r.case, "no feasible design: " + str(r.get("binding", "")), va="center", fontsize=7, color=col["unmet"])
        ax.invert_yaxis()
        ax.set_xlabel("Capital cost of re-optimized design ($k)")
        ax.set_title("Sensitivity: re-optimized capex", loc="left")
        fig.tight_layout()
        f = out / "sensitivity_capex.png"
        fig.savefig(f, dpi=140)
        plt.close(fig)
        files.append(str(f))
    return files


TEMPLATE = r"""<title>MOD POD Microgrid Sizing</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Source+Sans+3:wght@400;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: engineering field report. Header band -> recommended-design spec strip -> numbered study sections, single 1180px column, charts full-width */
:root{
  --bg:#f3f5f4; --panel:#ffffff; --ink:#17201d; --ink2:#4b5753; --mute:#7d8884; --rule:#d9dfdc; --grid:#e8ecea;
  --accent:#1f6f5c; --accent-soft:#e1efe9;
  --pv:#eda100; --bat:#2a78d6; --gen:#eb6834; --ev:#1baf7a; --crit:#c8312f; --warn:#b36b00;
  --band:rgba(42,120,214,.08); --mag:#e87ba4;
  --f-display:"Barlow Condensed","Arial Narrow",system-ui,sans-serif;
  --f-body:"Source Sans 3",system-ui,-apple-system,"Segoe UI",sans-serif;
  --f-mono:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#121715; --panel:#1a201e; --ink:#e8eeeb; --ink2:#b4beba; --mute:#86918d; --rule:#2c3532; --grid:#252d2a;
  --accent:#5cc0a2; --accent-soft:#1d3029;
  --pv:#c98500; --bat:#3987e5; --gen:#d95926; --ev:#199e70; --crit:#ec6a68; --warn:#e0a03a; --band:rgba(57,135,229,.12); --mag:#d55181;
  color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#121715; --panel:#1a201e; --ink:#e8eeeb; --ink2:#b4beba; --mute:#86918d; --rule:#2c3532; --grid:#252d2a;
  --accent:#5cc0a2; --accent-soft:#1d3029;
  --pv:#c98500; --bat:#3987e5; --gen:#d95926; --ev:#199e70; --crit:#ec6a68; --warn:#e0a03a; --band:rgba(57,135,229,.12); --mag:#d55181;
  color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font:16px/1.5 var(--f-body);margin:0}
.wrap{max-width:1180px;margin:0 auto;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:28px 64px}
header.top{display:grid;gap:6px;padding-block:8px 22px;border-bottom:2px solid var(--ink)}
.eyebrow{font:500 12px/1 var(--f-mono);letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
h1{font:700 clamp(34px,5vw,52px)/1 var(--f-display);margin:0;letter-spacing:.01em;text-wrap:balance}
.sub{color:var(--ink2);max-width:72ch;margin:0}
h2{font:600 28px/1.1 var(--f-display);margin:0;letter-spacing:.01em;text-wrap:balance}
h2 .n{font:500 14px var(--f-mono);color:var(--mute);margin-right:10px;vertical-align:4px}
h3{font:600 19px/1.2 var(--f-display);margin:0;letter-spacing:.02em}
section{display:grid;gap:14px;padding-block:34px 6px}
.lede{color:var(--ink2);max-width:76ch;margin:0}
.spec{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:1px;background:var(--rule);border:1px solid var(--rule);margin-top:22px}
.spec .cell{background:var(--panel);padding:14px 16px;display:grid;gap:2px;align-content:start;min-width:0}
.spec .k{font:500 11px/1.2 var(--f-mono);letter-spacing:.07em;text-transform:uppercase;color:var(--mute)}
.spec .v{font:600 34px/1.05 var(--f-display);font-variant-numeric:tabular-nums}
.spec .v small{font:500 15px var(--f-body);color:var(--ink2);margin-left:3px}
.spec .d{font-size:13px;color:var(--ink2)}
.spec .sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:0}
.outcomes{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:18px;padding-block:16px 4px}
.outcomes div{display:grid;gap:1px;border-left:2px solid var(--rule);padding-left:12px}
.outcomes b{font:600 24px/1.1 var(--f-display);font-variant-numeric:tabular-nums}
.outcomes span{font-size:13px;color:var(--ink2)}
.findings{margin:0;padding-left:20px;display:grid;gap:6px;max-width:82ch}
.panel{background:var(--panel);border:1px solid var(--rule);padding:14px 14px 8px;min-width:0}
.chart{width:100%;height:380px}
.chart.tall{height:560px}.chart.short{height:300px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:16px}
.tabs{display:flex;flex-wrap:wrap;gap:6px}
.tabs button{font:500 14px var(--f-body);border:1px solid var(--rule);background:var(--panel);color:var(--ink);padding:7px 12px;border-radius:3px;cursor:pointer}
.tabs button[aria-selected="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.tabs button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.wdesc{color:var(--ink2);font-size:14px;margin:0}
.mrow{display:flex;flex-wrap:wrap;gap:8px 22px;font-size:14px;color:var(--ink2)}
.mrow b{color:var(--ink);font-variant-numeric:tabular-nums;font-weight:600}
.tbl{overflow-x:auto;border:1px solid var(--rule);background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
th,td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--grid);white-space:nowrap}
th{font:500 11px var(--f-mono);letter-spacing:.06em;text-transform:uppercase;color:var(--mute);background:var(--panel);position:sticky;top:0}
td:first-child,th:first-child{text-align:left}
tr.base td{background:var(--accent-soft)}
td.no{color:var(--crit);text-align:left;white-space:normal;min-width:220px}
.pill{display:inline-block;font:500 11px var(--f-mono);padding:2px 7px;border-radius:10px;letter-spacing:.04em}
.pill.ok{background:var(--accent-soft);color:var(--accent)}
.pill.bad{background:color-mix(in srgb,var(--crit) 14%,transparent);color:var(--crit)}
dl.assume{display:grid;grid-template-columns:minmax(110px,180px) 1fr;gap:6px 18px;margin:0;font-size:14px}
dl.assume dt{color:var(--mute);font:500 12px/1.6 var(--f-mono);text-transform:uppercase;letter-spacing:.05em}
dl.assume dd{margin:0;min-width:0}
.note{font-size:13px;color:var(--mute);margin:0}
footer{margin-top:40px;padding-top:14px;border-top:1px solid var(--rule);font-size:13px;color:var(--mute)}
@media (max-width:560px){.spec .cell:last-child:nth-child(odd){grid-column:1/-1}.spec .v{font-size:28px}.chart{height:320px}.chart.tall{height:520px}}
</style>

<div class="wrap">
<header class="top">
  <div class="eyebrow" id="site"></div>
  <h1>MOD POD Microgrid Sizing</h1>
  <p class="sub" id="intro"></p>
</header>

<div class="spec" id="spec"></div>
<div class="outcomes" id="outcomes"></div>

<section>
  <h2><span class="n">01</span>What the study found</h2>
  <ul class="findings" id="findings"></ul>
</section>

<section>
  <h2><span class="n">02</span>Recommended design through each design window</h2>
  <p class="lede">Hour-by-hour dispatch of the optimum. Top: where each kW of load came from. Middle: shelter temperature against the 50&ndash;90&nbsp;&deg;F requirement band. Bottom: battery state of charge. Windows marked "annual check" were added automatically when a deployment on that date broke a limit.</p>
  <div class="tabs" role="tablist" id="wtabs"></div>
  <p class="wdesc" id="wdesc"></p>
  <div class="mrow" id="wmetrics"></div>
  <div class="panel"><div class="chart tall" id="c_dispatch"></div></div>
</section>

<section id="genbat">
  <h2><span class="n">03</span>Generator and battery options</h2>
  <p class="lede" id="gblede"></p>
  <div class="tabs" role="tablist" id="gbtabs"></div>
  <div class="grid2">
    <div class="panel"><h3>Generator run time (hours per day)</h3><div class="chart" id="c_gbrun"></div></div>
    <div class="panel"><h3>Trailer payload (lb)</h3><div class="chart" id="c_gbwt"></div></div>
  </div>
  <p class="note">Outlined cells meet every limit (temperature, load served, payload). The orange square is the recommended design. Hover a cell for fuel use, starts, cost and the reason a combination fails.</p>
  <div class="grid2">
    <div class="panel"><h3>Run time vs battery size, by generator</h3><div class="chart" id="c_gbline"></div></div>
    <div class="panel"><h3>Payload by component, options that meet every limit</h3><div class="chart" id="c_gbstack"></div></div>
  </div>
  <div class="tbl"><table id="t_gb"></table></div>
</section>

<section>
  <h2><span class="n">04</span>Waitsfield weather year and design windows</h2>
  <p class="lede" id="wxlede"></p>
  <div class="panel"><div class="chart short" id="c_temp"></div></div>
  <div class="panel"><div class="chart short" id="c_ghi"></div></div>
</section>

<section>
  <h2><span class="n">05</span>Design sweep</h2>
  <p class="lede" id="sweeplede"></p>
  <div class="grid2">
    <div class="panel"><h3>Capital cost vs expected fuel use</h3><div class="chart" id="c_scatter"></div></div>
    <div class="panel"><h3>Warmest cold-snap indoor low (&deg;F) by generator and heat-pump size</h3><div class="chart" id="c_heat"></div></div>
  </div>
  <p class="note">Labels show the cheapest feasible lifecycle cost in that cell; blank cells have no design that meets every limit. Empty cells are combinations that are always over the trailer payload.</p>
  <div class="panel"><h3>Why designs failed</h3><div class="chart short" id="c_reasons"></div></div>
</section>

<section>
  <h2><span class="n">06</span>Fuel autonomy without resupply</h2>
  <p class="lede">Cheapest design that carries the worst multi-week winter stretch for N days on one tank, re-optimized for each target with larger batteries, arrays and tanks allowed. Flood events can cut road access, so this is the resilience ladder behind "ideally up to a few weeks".</p>
  <div class="tbl"><table id="t_ladder"></table></div>
</section>

<section>
  <h2><span class="n">07</span>Building, window and occupant sensitivity</h2>
  <p class="lede">Each row changes one input and re-runs the full sweep and optimization, so the sizes shown are the new optimum, not the base design re-checked.</p>
  <div class="tbl"><table id="t_sens"></table></div>
  <div class="grid2">
    <div class="panel"><h3>Re-optimized capital cost by envelope and occupancy ($k)</h3><div class="chart short" id="c_matrix"></div></div>
    <div class="panel"><h3>Base building heat loss by path (W/K)</h3><div class="chart short" id="c_ua"></div></div>
  </div>
  <h3 id="winh">Window count and type</h3>
  <p class="lede" id="winlede"></p>
  <div class="grid2">
    <div class="panel"><h3>Re-optimized capital cost ($k)</h3><div class="chart short" id="c_win"></div></div>
    <div class="panel"><h3>Design heat loss at &minus;13 &deg;F (kW)</h3><div class="chart short" id="c_winload"></div></div>
  </div>
  <div class="tbl"><table id="t_win"></table></div>
</section>

<section>
  <h2><span class="n">08</span>Any-day deployment check</h2>
  <p class="lede" id="annlede"></p>
  <div class="grid2">
    <div class="panel"><h3>Fuel use by deployment start date (gal/day)</h3><div class="chart short" id="c_annfuel"></div></div>
    <div class="panel"><h3>Coldest and warmest indoor temperature (&deg;F)</h3><div class="chart short" id="c_anntemp"></div></div>
  </div>
</section>

<section>
  <h2><span class="n">09</span>Assumptions</h2>
  <dl class="assume" id="assume"></dl>
  <p class="note">All parameters are editable in the JSON config passed to <code>python -m microgrid_sim run</code>. Results are screening-level: hourly time step, sensible loads only, rule-based dispatch.</p>
</section>

<footer>Generated by microgrid_sim for MOD POD (Group 498), VEIC community resilience pod.</footer>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.35.2/plotly.min.js"></script>
<script>
const D = /*__DATA__*/null;
const G = 3.78541, LB = 0.453592;
const $ = id => document.getElementById(id);
const fmt = (v, d=0) => v==null||isNaN(v) ? "–" : Number(v).toLocaleString("en-US",{minimumFractionDigits:d,maximumFractionDigits:d});
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const B = D.best;
const GB = D.gen_bat;
const WS = D.windows_study;

// ---------- static text ----------
$("site").textContent = `${D.site.name} · ${D.site.lat.toFixed(2)}° N ${Math.abs(D.site.lon).toFixed(2)}° W`;
$("intro").textContent = `Hybrid PV, battery, diesel generator and cold-climate heat-pump sizing for a towable resilience pod serving a ${fmt(D.building.area_ft2)} ft² shelter with ${D.building.occupants} occupants (${D.building.envelope}). ${fmt(D.sweep.n)} equipment combinations were simulated hour by hour through every design window; ${fmt(D.sweep.n_feas)} met all requirements.`;

const spec = [
  ["PV array", fmt(B.pv_kw,1), "kW", `${fmt(B.pv_kw/0.4)} × 400 W modules`, "--pv"],
  ["Battery", fmt(B.bat_kwh), "kWh", `LFP, usable ${fmt(B.bat_kwh*0.9)} kWh`, "--bat"],
  ["Generator", fmt(B.gen_kw,1), "kW", `${fmt(B.tank_l/G)} gal diesel tank`, "--gen"],
  ["Heat pumps", fmt(B.hp_kw,1), "kW", `${fmt(B.hp_units)} × ${D.hp_unit_kw} kW cold-climate heads`, "--accent"],
  ["Inverter", fmt(B.inverter_kw), "kW", `peak load ${fmt(B.peak_kw,1)} kW`, "--mute"],
];
$("spec").innerHTML = spec.map(s=>`<div class="cell"><span class="k"><span class="sw" style="background:var(${s[4]})"></span>${s[0]}</span><span class="v">${s[1]}<small>${s[2]}</small></span><span class="d">${s[3]}</span></div>`).join("");
const worstFuel = Math.max(...Object.keys(B).filter(k=>k.startsWith("fuel_lpd_")&&!k.endsWith("endurance")).map(k=>B[k]));
$("outcomes").innerHTML = [
  [`$${fmt(B.capex_usd/1000,1)}k`, "equipment capital cost"],
  [`$${fmt(B.lifecycle_usd/1000,1)}k`, "15-year lifecycle cost"],
  [`${fmt(B.weight_kg/LB)} lb`, `payload of ${fmt(D.limits.payload_lb)} lb allowed`],
  [`${fmt(worstFuel/G,1)} gal/day`, "fuel in the worst 3-day window"],
  [`${fmt(B.autonomy_d_endurance,1)} days`, "on one tank, worst winter stretch"],
].map(o=>`<div><b>${o[0]}</b><span>${o[1]}</span></div>`).join("");

// findings (generated from results)
(function(){
  const f = [];
  const disp = D.dispatch;
  const cs = disp.cold_snap, sh = disp.summer_heat;
  const ua = D.ua; const tot = ua.w_k.reduce((a,b)=>a+b,0);
  const big = ua.labels.map((l,i)=>[l,ua.w_k[i]]).sort((a,b)=>b[1]-a[1]);
  if (cs) f.push(`Heating sets the size. In the cold snap the heat pumps draw ${fmt(cs.m.hvac/cs.m.demand*100)}% of all electricity and the generator burns ${fmt(cs.m.fuel_gal/cs.m.days,1)} gal/day; the shelter bottoms out at ${fmt(cs.m.t_min,1)} °F.`);
  f.push(`${big[0][0]} (${fmt(big[0][1]/tot*100)}%) and ${big[1][0].toLowerCase()} (${fmt(big[1][1]/tot*100)}%) are the largest heat-loss paths. A heat-recovery ventilator or a tighter building shrinks every component; see section 07.`);
  if (B.pv_kw < 2) f.push(`PV barely enters the optimum. Waitsfield winter windows deliver ${fmt(Math.min(...D.windows.filter(w=>w.days<=3).map(w=>{const i0=D.weather.date.indexOf(w.start);return D.weather.ghi.slice(i0,i0+w.days).reduce((a,b)=>a+b,0)/w.days;})),2)} kWh/m²/day at worst, and with about ${fmt(D.deploy_days)} deployment days a year the fuel saved does not repay the array. PV earns its place when the pod's array is net-metered while parked, or when fuel autonomy matters more than cost.`);
  else f.push(`The optimum carries ${fmt(B.pv_kw,1)} kW of PV, supplying ${fmt(sh? sh.m.pv/sh.m.demand*100:0)}% of summer demand.`);
  if (sh) f.push(`Summer is easy: indoor temperature peaks at ${fmt(sh.m.t_max,1)} °F with ${fmt(sh.m.fuel_gal/sh.m.days,1)} gal/day of fuel.`);
  if (GB && GB.options.length){
    const o=GB.options, bestO=o.find(x=>x.best)||o[0];
    const quiet=[...o].sort((a,b)=>a.run.worst-b.run.worst)[0];
    const light=o[0];
    f.push(`Generator and battery trade-off: the recommended ${fmt(bestO.gen_kw)} kW generator with ${fmt(bestO.bat_kwh)} kWh battery runs ${fmt(bestO.run.worst,1)} h/day in the worst window at ${fmt(bestO.weight_lb)} lb. Among options that fit the trailer, the lightest (${fmt(light.gen_kw)} kW + ${fmt(light.bat_kwh)} kWh) weighs ${fmt(light.weight_lb)} lb and the quietest (${fmt(quiet.gen_kw)} kW + ${fmt(quiet.bat_kwh)} kWh) runs ${fmt(quiet.run.worst,1)} h/day. Cutting cold-weather run time much further needs a battery too heavy to tow; see section 03.`);
  }
  if (WS){ const ok=WS.filter(r=>r.feasible); if(ok.length){ const lo=ok.reduce((a,b)=>a.capex_usd<b.capex_usd?a:b), hi=ok.reduce((a,b)=>a.capex_usd>b.capex_usd?a:b);
    f.push(`Windows matter: across the window cases that can be served, the re-optimized pod ranges from $${fmt(lo.capex_usd/1000,1)}k (${lo.window_count} × ${lo.window_name.toLowerCase()}) to $${fmt(hi.capex_usd/1000,1)}k (${hi.window_count} × ${hi.window_name.toLowerCase()}).`); } }
  const lad = D.ladder.filter(r=>r.feasible); const ladNo = D.ladder.filter(r=>!r.feasible);
  if (ladNo.length) f.push(`One tank carries the worst winter stretch for at most ${fmt(Math.max(...D.ladder.map(r=>r.max_autonomy_days||0)),1)} days within the trailer payload. ${ladNo.map(r=>r.case).join(" and ")} without resupply need a fuel delivery or a lighter heating load.`);
  const inf = D.sensitivity.filter(r=>!r.feasible);
  if (inf.length) f.push(`No towable design works for ${inf.map(r=>`${r.case.toLowerCase()} (binding limit: ${r.binding})`).join(" or ")}.`);
  $("findings").innerHTML = f.map(x=>`<li>${x}</li>`).join("");
})();

// ---------- plotting helpers ----------
function baseLayout(extra={}){
  const ink=css("--ink"), ink2=css("--ink2"), grid=css("--grid"), rule=css("--rule");
  const ax = {gridcolor:grid, linecolor:rule, zerolinecolor:rule, tickfont:{color:ink2,size:11}, title:{font:{color:ink2,size:12}}};
  return Object.assign({paper_bgcolor:"rgba(0,0,0,0)", plot_bgcolor:"rgba(0,0,0,0)", font:{family:css("--f-body"),color:ink,size:12},
    margin:{l:56,r:16,t:16,b:44}, hovermode:"x unified", legend:{orientation:"h",y:1.08,x:0,font:{size:12,color:ink2}},
    hoverlabel:{bgcolor:css("--panel"),bordercolor:rule,font:{color:ink}},
    xaxis:Object.assign({},ax), yaxis:Object.assign({},ax)}, extra, {_ax:ax});
}
const isDark = () => { const t=document.documentElement.getAttribute("data-theme"); return t==="dark" || (t!=="light" && matchMedia("(prefers-color-scheme: dark)").matches); };
const CFG = {displaylogo:false, responsive:true, modeBarButtonsToRemove:["lasso2d","select2d","autoScale2d"]};
function plot(id, data, layout){ const ax=layout._ax; delete layout._ax; Plotly.react($(id), data, layout, CFG); }

// ---------- 02 dispatch ----------
const wkeys = Object.keys(D.dispatch);
let wsel = wkeys[0];
try{ const s = localStorage.getItem("modpod-window"); if (s && D.dispatch[s]) wsel = s; }catch(e){}
$("wtabs").innerHTML = wkeys.map(k=>`<button role="tab" id="tab-${k}" aria-selected="${k===wsel}" data-k="${k}">${D.dispatch[k].label}</button>`).join("");
$("wtabs").addEventListener("click", e=>{ const k=e.target.dataset.k; if(!k) return; wsel=k; try{localStorage.setItem("modpod-window",k)}catch(e){}
  [...$("wtabs").children].forEach(b=>b.setAttribute("aria-selected", b.dataset.k===k)); drawDispatch(); });

function drawDispatch(){
  const d = D.dispatch[wsel], w = D.windows.find(x=>x.key===wsel), m=d.m;
  $("wdesc").textContent = w ? `${w.desc}. ${w.start} to ${w.end}.` : "";
  $("wmetrics").innerHTML = [
    ["Fuel", `${fmt(m.fuel_gal,1)} gal`], ["Generator", `${fmt(m.gen_h)} h, ${fmt(m.starts)} starts`],
    ["PV used", `${fmt(m.pv)} kWh`], ["Demand", `${fmt(m.demand)} kWh`], ["Indoor", `${fmt(m.t_min,1)}–${fmt(m.t_max,1)} °F`],
    ["Unserved", `${fmt(m.unmet,1)} kWh`], ["Fuel left", `${fmt(m.fuel_left_gal)} gal`], ["Load carried", `${fmt(m.autonomy,1)} of ${fmt(m.days)} days`]
  ].map(x=>`<span>${x[0]} <b>${x[1]}</b></span>`).join("");
  const t = d.time, L = baseLayout();
  const st = (name, y, c) => ({x:t, y, name, type:"scatter", mode:"lines", stackgroup:"s", line:{width:0.5,color:css(c),shape:"hv"}, fillcolor:css(c), hovertemplate:"%{y:.1f} kW"});
  const sup = [st("PV", d.pv, "--pv"), st("Battery", d.bat_dis, "--bat"), st("Generator", d.gen, "--gen")];
  if (d.ev.some(v=>v>0)) sup.push(st("EV pickup", d.ev, "--ev"));
  if (d.unmet.some(v=>v>0.01)) sup.push(st("Unserved", d.unmet, "--crit"));
  const loadC = d.load.map((v,i)=>v + d.bat_ch[i]);
  sup.push({x:t,y:loadC,name:"Load + battery charging",type:"scatter",mode:"lines",line:{color:css("--ink"),width:1.5,shape:"hv"},hovertemplate:"%{y:.1f} kW"});
  sup.push({x:t,y:d.hvac,name:"Heat-pump draw",type:"scatter",mode:"lines",line:{color:css("--ink2"),width:1.2,dash:"dot",shape:"hv"},hovertemplate:"%{y:.1f} kW"});
  const tr = sup.map(s=>Object.assign(s,{xaxis:"x",yaxis:"y"}));
  const lo=D.building.t_min_f, hi=D.building.t_max_f;
  tr.push({x:t,y:d.t_out,name:"Outdoor",type:"scatter",mode:"lines",line:{color:css("--mute"),width:1.2,dash:"dash"},xaxis:"x",yaxis:"y2",hovertemplate:"%{y:.1f} °F"});
  tr.push({x:t,y:d.t_in,name:"Indoor",type:"scatter",mode:"lines",line:{color:css("--bat"),width:2},xaxis:"x",yaxis:"y2",hovertemplate:"%{y:.1f} °F"});
  tr.push({x:t,y:d.soc,name:"Battery SOC",type:"scatter",mode:"lines",line:{color:css("--bat"),width:1.6},fill:"tozeroy",fillcolor:css("--band"),xaxis:"x",yaxis:"y3",hovertemplate:"%{y:.0f}%",showlegend:false});
  const ax = L._ax;
  Object.assign(L, {
    margin:{l:60,r:16,t:30,b:40},
    xaxis:Object.assign({},ax,{anchor:"y3"}),
    yaxis:Object.assign({},ax,{domain:[0.47,1],title:{text:"kW",font:{color:css("--ink2"),size:12}},rangemode:"tozero"}),
    yaxis2:Object.assign({},ax,{domain:[0.2,0.42],title:{text:"°F",font:{color:css("--ink2"),size:12}}}),
    yaxis3:Object.assign({},ax,{domain:[0,0.14],range:[0,105],title:{text:"SOC %",font:{color:css("--ink2"),size:12}}}),
    shapes:[{type:"rect",xref:"paper",yref:"y2",x0:0,x1:1,y0:lo,y1:hi,fillcolor:css("--band"),line:{width:0},layer:"below"},
            {type:"line",xref:"paper",yref:"y2",x0:0,x1:1,y0:lo,y1:lo,line:{color:css("--crit"),width:1,dash:"dot"}}],
    annotations:[{xref:"paper",yref:"y2",x:1,y:lo,text:`${lo.toFixed(0)} °F minimum`,showarrow:false,xanchor:"right",yanchor:"bottom",font:{size:11,color:css("--crit")}}],
  });
  plot("c_dispatch", tr, L);
}

// ---------- 03 weather ----------
function windowShapes(yref){
  return D.windows.map(w=>({type:"rect",xref:"x",yref:"paper",x0:w.start,x1:w.end,y0:0,y1:1,fillcolor:css("--band"),line:{width:1,color:css("--bat")},layer:"below"}));
}
function drawWeather(){
  const w=D.weather;
  const coldest = Math.min(...w.tmin), hottest=Math.max(...w.tmax);
  $("wxlede").textContent = `${D.source}. Daily range ${fmt(coldest)} °F to ${fmt(hottest)} °F. Shaded spans are the design windows the sizing must survive; the optimizer picks them automatically from the year.`;
  let L = baseLayout({hovermode:"x unified"});
  L.yaxis = Object.assign({}, L._ax, {title:{text:"°F",font:{color:css("--ink2"),size:12}}});
  L.shapes = windowShapes();
  const short={cold_snap:"cold snap",dark_storm:"dark storm",spring_flood:"spring flood",summer_heat:"heat wave",endurance:"14-day"};
  const groups=[]; [...D.windows].sort((a,b)=>a.start<b.start?-1:1).forEach(w=>{const g=groups[groups.length-1];
    if(g && (new Date(w.start)-new Date(g[0].start))/864e5<25) g.push(w); else groups.push([w]);});
  L.annotations = groups.map(g=>({x:g[0].start,y:1.02,xref:"x",yref:"paper",text:g.map(w=>short[w.key]||"annual check").join(" · "),showarrow:false,xanchor:g[0].start>"2023-10"?"right":"left",yanchor:"bottom",font:{size:11,color:css("--ink2")}}));
  L.margin.t=30; L.showlegend=false;
  plot("c_temp",[
    {x:w.date,y:w.tmax,name:"Daily max",type:"scatter",mode:"lines",line:{width:0},showlegend:false,hovertemplate:"max %{y:.0f} °F"},
    {x:w.date,y:w.tmin,name:"Daily range",type:"scatter",mode:"lines",line:{width:0},fill:"tonexty",fillcolor:css("--band"),hovertemplate:"min %{y:.0f} °F"},
    {x:w.date,y:w.tmean,name:"Daily mean",type:"scatter",mode:"lines",line:{color:css("--bat"),width:1.6},hovertemplate:"mean %{y:.0f} °F"}],L);
  L = baseLayout();
  L.yaxis = Object.assign({}, L._ax, {title:{text:"kWh/m²/day",font:{color:css("--ink2"),size:12}}});
  L.shapes = windowShapes(); L.showlegend=false;
  plot("c_ghi",[{x:w.date,y:w.ghi,type:"bar",name:"Global horizontal irradiance",marker:{color:css("--pv")},hovertemplate:"%{y:.2f} kWh/m²"}],L);
}

// ---------- 04 sweep ----------
function drawSweep(){
  const s=D.sweep;
  $("sweeplede").textContent = `Coarse full-factorial sweep plus a refinement around the best designs at purchasable increments (2-module PV steps, 5 kWh battery modules, catalog generators, whole heat-pump heads, 50/100/150 gal tanks). ${fmt(s.n)} designs, ${fmt(s.n_feas)} feasible. The optimum minimizes 15-year lifecycle cost (equipment + discounted fuel, maintenance and carbon).`;
  let L = baseLayout({hovermode:"closest"});
  L.xaxis = Object.assign({},L._ax,{title:{text:"Capital cost ($k)",font:{color:css("--ink2"),size:12}}});
  L.yaxis = Object.assign({},L._ax,{title:{text:"Expected fuel (gal/day)",font:{color:css("--ink2"),size:12}}});
  const f=s.feas;
  plot("c_scatter",[
    {x:s.inf.capex_usd.map(v=>v/1000),y:s.inf.exp_fuel_l_per_day.map(v=>v/G),type:"scattergl",mode:"markers",name:"Infeasible (sample)",marker:{size:4,color:css("--rule")},text:s.inf.reason,hovertemplate:"%{text}<extra></extra>"},
    {x:f.capex_usd.map(v=>v/1000),y:f.exp_fuel_l_per_day.map(v=>v/G),type:"scattergl",mode:"markers",name:"Feasible",marker:{size:7,color:css("--bat"),line:{width:1,color:css("--panel")}},
     customdata:f.pv_kw.map((p,i)=>[p,f.bat_kwh[i],f.gen_kw[i],f.hp_kw[i],f.tank_l[i]/G,f.lifecycle_usd[i]/1000,f.weight_kg[i]/LB]),
     hovertemplate:"PV %{customdata[0]} kW · battery %{customdata[1]} kWh<br>generator %{customdata[2]} kW · %{customdata[4]:.0f} gal<br>heat pumps %{customdata[3]} kW<br>lifecycle $%{customdata[5]:.1f}k · %{customdata[6]:,.0f} lb<extra></extra>"},
    {x:s.pareto.capex.map(v=>v/1000),y:s.pareto.fuel.map(v=>v/G),type:"scatter",mode:"lines",name:"Pareto front",line:{color:css("--gen"),width:2,shape:"hv"},hoverinfo:"skip"},
    {x:[B.capex_usd/1000],y:[B.exp_fuel_l_per_day/G],type:"scatter",mode:"markers",name:"Optimum",marker:{size:15,color:"rgba(0,0,0,0)",line:{width:2.5,color:css("--ink")}},hoverinfo:"skip"},
  ],L);
  const h=s.heat;
  L = baseLayout({hovermode:"closest"});
  const xi=h.x.map((_,i)=>i), yi=h.y.map((_,i)=>i);
  L.xaxis=Object.assign({},L._ax,{title:{text:"Generator (kW)",font:{color:css("--ink2"),size:12}},tickvals:xi,ticktext:h.x.map(v=>fmt(v,v%1?1:0)),showgrid:false,zeroline:false});
  L.yaxis=Object.assign({},L._ax,{title:{text:"Heat pumps (kW)",font:{color:css("--ink2"),size:12}},tickvals:yi,ticktext:h.y.map(v=>fmt(v,1)),showgrid:false,zeroline:false});
  L.margin.r=10;
  const scale = isDark() ? [[0,"#9ec5f4"],[0.5,"#3987e5"],[1,"#184f95"]] : [[0,"#cde2fb"],[0.5,"#3987e5"],[1,"#104281"]];
  const bx = h.x.findIndex(v=>v>=B.gen_kw), by = h.y.findIndex(v=>v>=B.hp_kw-1e-6);
  const zf=h.z.flat().filter(v=>v!=null), zmid=(Math.max(...zf)+Math.min(...zf))/2;
  L.annotations=[]; h.lab.forEach((row,i)=>row.forEach((t,j)=>{ if(t) L.annotations.push({x:j,y:i,text:t,showarrow:false,font:{size:9,color:h.z[i][j]>zmid?"#ffffff":"#0b1a33"}}); }));
  plot("c_heat",[{type:"heatmap",x:xi,y:yi,z:h.z,text:h.text,colorscale:scale,xgap:2,ygap:2,
     colorbar:{thickness:10,tickfont:{color:css("--ink2"),size:10},outlinewidth:0,title:{text:"°F",font:{color:css("--ink2"),size:11}}},hoverongaps:false,
     customdata:h.y.map(hy=>h.x.map(gx=>[gx,hy])),
     hovertemplate:"generator %{customdata[0]} kW · heat pumps %{customdata[1]} kW<br>warmest cold-snap low %{z:.1f} °F<br>%{text}<extra></extra>"},
     {type:"scatter",mode:"markers",x:[bx<0?xi.length-1:bx],y:[by<0?yi.length-1:by],marker:{size:22,symbol:"square-open",color:css("--gen"),line:{width:2.5,color:css("--gen")}},name:"Optimum",hoverinfo:"skip",showlegend:false}],L);
  L = baseLayout({hovermode:"closest"});
  L.margin.l=240; L.showlegend=false;
  L.yaxis=Object.assign({},L._ax,{autorange:"reversed",automargin:true});
  L.xaxis=Object.assign({},L._ax,{title:{text:"Designs (first failed check)",font:{color:css("--ink2"),size:12}}});
  const top = s.reasons.labels.slice(0,8);
  plot("c_reasons",[{type:"bar",orientation:"h",y:top,x:s.reasons.counts.slice(0,8),marker:{color:css("--mute")},hovertemplate:"%{x:,} designs<extra></extra>"}],L);
}

// ---------- 05 / 06 tables ----------
function caseTable(id, rows, first){
  const head = `<tr><th>${first}</th><th>PV kW</th><th>Battery kWh</th><th>Generator kW</th><th>Tank gal</th><th>Heat pumps kW</th><th>Capex</th><th>Lifecycle</th><th>Payload lb</th><th>Fuel gal/day</th><th>Autonomy days</th><th>14-day fuel gal</th></tr>`;
  const body = rows.map((r,i)=> r.feasible
    ? `<tr class="${i===0&&id==="t_sens"?"base":""}"><td>${r.case}</td><td>${fmt(r.pv_kw,1)}</td><td>${fmt(r.bat_kwh)}</td><td>${fmt(r.gen_kw,1)}</td><td>${fmt(r.tank_l/G)}</td><td>${fmt(r.hp_kw,1)}</td><td>$${fmt(r.capex_usd/1000,1)}k</td><td>$${fmt(r.lifecycle_usd/1000,1)}k</td><td>${fmt(r.weight_kg/LB)}</td><td>${fmt(r.fuel_l_per_day/G,1)}</td><td>${fmt(r.autonomy_days,1)}</td><td>${fmt(r.fuel_need_14d_l/G)}</td></tr>`
    : `<tr><td>${r.case}</td><td class="no" colspan="11"><span class="pill bad">no feasible design</span> binding limit: ${r.binding||"–"}${r.max_autonomy_days!=null?` · best achievable ${fmt(r.max_autonomy_days,1)} days`:""}</td></tr>`).join("");
  $(id).innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
}
caseTable("t_ladder", D.ladder.map(r=>Object.assign({},r,{case:`${r.target_days} days`})), "Autonomy target");
caseTable("t_sens", D.sensitivity, "Case");

function drawSens(){
  const M=D.matrix; if(!M.length) return;
  const envs=[...new Set(M.map(r=>r.envelope))], occs=[...new Set(M.map(r=>r.occupants))];
  const names={leaky_1950s:"Leaky 1950s",typical_1980s:"Typical 1980s",typical_2010s:"Typical 2010s",efficient_modern:"Efficient modern"};
  const z=envs.map(e=>occs.map(o=>{const r=M.find(x=>x.envelope===e&&x.occupants===o);return r&&r.feasible?+(r.capex_usd/1000).toFixed(1):null;}));
  const tx=envs.map(e=>occs.map(o=>{const r=M.find(x=>x.envelope===e&&x.occupants===o);return r&&r.feasible?`PV ${fmt(r.pv_kw,1)} kW · bat ${fmt(r.bat_kwh)} kWh · gen ${fmt(r.gen_kw,1)} kW · HP ${fmt(r.hp_kw,1)} kW`:`no feasible design: ${r?r.binding:""}`;}));
  const lbl=envs.map((e,i)=>occs.map((o,j)=>z[i][j]==null?"none":`$${z[i][j].toFixed(0)}k`));
  let L=baseLayout({hovermode:"closest"});
  const ox=occs.map((_,i)=>i), ey=envs.map((_,i)=>i);
  L.xaxis=Object.assign({},L._ax,{title:{text:"Occupants",font:{color:css("--ink2"),size:12}},tickvals:ox,ticktext:occs.map(String),showgrid:false,zeroline:false});
  L.yaxis=Object.assign({},L._ax,{tickvals:ey,ticktext:envs.map(e=>names[e]||e),automargin:true,showgrid:false,zeroline:false});
  const zf=z.flat().filter(v=>v!=null), mid=(Math.max(...zf)+Math.min(...zf))/2;
  L.annotations=[]; envs.forEach((e,i)=>occs.forEach((o,j)=>L.annotations.push({x:j,y:i,text:lbl[i][j],showarrow:false,
     font:{size:12,color:z[i][j]==null?css("--crit"):(z[i][j]>mid?"#ffffff":"#0b1a33")}})));
  plot("c_matrix",[{type:"heatmap",x:ox,y:ey,z,text:tx,xgap:2,ygap:2,
    colorscale: isDark() ? [[0,"#9ec5f4"],[1,"#184f95"]] : [[0,"#cde2fb"],[1,"#1c5cab"]],showscale:false,hoverongaps:true,
    customdata:envs.map(e=>occs.map(o=>[names[e]||e,o])),
    hovertemplate:"%{customdata[0]}, %{customdata[1]} occupants<br>%{text}<extra></extra>"}],L);
  L=baseLayout({hovermode:"closest",showlegend:false});
  L.yaxis=Object.assign({},L._ax,{autorange:"reversed",automargin:true});
  L.xaxis=Object.assign({},L._ax,{title:{text:"W/K",font:{color:css("--ink2"),size:12}}});
  const ord = D.ua.labels.map((l,i)=>[l,D.ua.w_k[i]]).sort((a,b)=>b[1]-a[1]);
  plot("c_ua",[{type:"bar",orientation:"h",y:ord.map(o=>o[0]),x:ord.map(o=>o[1]),marker:{color:css("--accent")},text:ord.map(o=>o[1].toFixed(0)),textposition:"outside",textfont:{color:css("--ink2")},cliponaxis:false,hovertemplate:"%{x:.0f} W/K<extra></extra>"}],L);
}

// ---------- 07 annual ----------
function drawAnnual(){
  const a=D.annual;
  const fails=a.t_in_min.filter(v=>v<D.building.t_min_f-0.1).length;
  const maxF=Math.max(...a.fuel_gpd), iMax=a.fuel_gpd.indexOf(maxF);
  $("annlede").textContent = `The optimum deployed for 3 days starting on every day of the year (${a.start.length} runs). ${fails? fails+" start dates fall below the 50 °F minimum." : "Every start date holds the shelter at or above 50 °F."} Fuel peaks at ${fmt(maxF,1)} gal/day for a deployment starting ${a.start[iMax]}.`;
  let L=baseLayout(); L.showlegend=false;
  L.yaxis=Object.assign({},L._ax,{rangemode:"tozero",title:{text:"gal/day",font:{color:css("--ink2"),size:12}}});
  plot("c_annfuel",[{x:a.start,y:a.fuel_gpd,type:"scatter",mode:"lines",line:{color:css("--gen"),width:1.6},fill:"tozeroy",fillcolor:"rgba(235,104,52,.12)",hovertemplate:"%{y:.1f} gal/day<extra></extra>"}],L);
  L=baseLayout();
  L.yaxis=Object.assign({},L._ax,{title:{text:"°F",font:{color:css("--ink2"),size:12}}});
  L.shapes=[{type:"rect",xref:"paper",yref:"y",x0:0,x1:1,y0:D.building.t_min_f,y1:D.building.t_max_f,fillcolor:css("--band"),line:{width:0},layer:"below"}];
  plot("c_anntemp",[
    {x:a.start,y:a.t_in_max,name:"Warmest indoor",type:"scatter",mode:"lines",line:{color:css("--gen"),width:1.4},hovertemplate:"%{y:.1f} °F"},
    {x:a.start,y:a.t_in_min,name:"Coldest indoor",type:"scatter",mode:"lines",line:{color:css("--bat"),width:1.6},hovertemplate:"%{y:.1f} °F"},
    {x:a.start,y:a.t_out_mean,name:"Outdoor mean",type:"scatter",mode:"lines",line:{color:css("--mute"),width:1,dash:"dot"},hovertemplate:"%{y:.1f} °F"}],L);
}

$("assume").innerHTML = D.assumptions.map(a=>`<dt>${a[0]}</dt><dd>${a[1]}</dd>`).join("");

// ---------- 03 generator x battery ----------
let gbView = "worst";
function gbInit(){
  if(!GB){ $("genbat").hidden = true; return; }
  const f = GB.fixed;
  $("gblede").textContent = `Every generator and battery size from the catalog, simulated through the design windows with the rest of the system held at the recommended design (PV ${fmt(f.pv,1)} kW, heat pumps ${fmt(f.hp,1)} kW, ${fmt(f.tank_gal)} gal tank). A bigger battery lets a bigger generator charge it quickly and shut off, so run time falls, but both add weight. ${GB.options.length} of ${GB.gens.length*GB.bats.length} combinations meet every limit.`;
  $("gbtabs").innerHTML = GB.views.map(([k,l])=>`<button role="tab" data-k="${k}" aria-selected="${k===gbView}">${l}</button>`).join("");
  $("gbtabs").addEventListener("click", e=>{ const k=e.target.dataset.k; if(!k) return; gbView=k;
    [...$("gbtabs").children].forEach(b=>b.setAttribute("aria-selected", b.dataset.k===k)); drawGenBat(); });
}
function gbHeat(id, z, unit, dec, scaleKey){
  const xi=GB.gens.map((_,i)=>i), yi=GB.bats.map((_,i)=>i);
  const L=baseLayout({hovermode:"closest"});
  L.xaxis=Object.assign({},L._ax,{title:{text:"Generator (kW)",font:{color:css("--ink2"),size:12}},tickvals:xi,ticktext:GB.gens.map(v=>fmt(v,v%1?1:0)),showgrid:false,zeroline:false});
  L.yaxis=Object.assign({},L._ax,{title:{text:"Battery (kWh)",font:{color:css("--ink2"),size:12}},tickvals:yi,ticktext:GB.bats.map(v=>fmt(v)),showgrid:false,zeroline:false});
  L.margin.r=10;
  const zf=z.flat().filter(v=>v!=null), mid=(Math.max(...zf)+Math.min(...zf))/2;
  const light = scaleKey==="orange" ? [[0,"#fde3d6"],[1,"#b8461b"]] : [[0,"#cde2fb"],[1,"#1c5cab"]];
  const dark  = scaleKey==="orange" ? [[0,"#f6c2a9"],[1,"#a33d14"]] : [[0,"#9ec5f4"],[1,"#184f95"]];
  L.annotations=[]; z.forEach((row,i)=>row.forEach((v,j)=>{ if(v!=null) L.annotations.push({x:j,y:i,text:fmt(v,dec),showarrow:false,
     font:{size:10,color:v>mid?"#ffffff":"#0b1a33"}}); }));
  const shapes=[]; GB.feasible.forEach((row,i)=>row.forEach((ok,j)=>{ if(ok) shapes.push({type:"rect",x0:j-0.5,x1:j+0.5,y0:i-0.5,y1:i+0.5,line:{color:css("--ink"),width:2},fillcolor:"rgba(0,0,0,0)"}); }));
  const bx=GB.gens.indexOf(GB.best.gen), by=GB.bats.indexOf(GB.best.bat);
  shapes.push({type:"rect",x0:bx-0.42,x1:bx+0.42,y0:by-0.42,y1:by+0.42,line:{color:css("--gen"),width:3},fillcolor:"rgba(0,0,0,0)"});
  L.shapes=shapes;
  const cd = GB.bats.map((b,i)=>GB.gens.map((g,j)=>[g,b,GB.feasible[i][j]?"meets every limit":"fails: "+GB.reason[i][j],
      (GB.fuel[gbView]||GB.fuel.worst)[i][j], (GB.starts[gbView]||GB.starts.worst)[i][j], GB.capex[i][j]/1000, GB.weight_lb[i][j]]));
  plot(id,[{type:"heatmap",x:xi,y:yi,z,customdata:cd,colorscale:isDark()?dark:light,showscale:false,xgap:2,ygap:2,
     hovertemplate:"generator %{customdata[0]} kW · battery %{customdata[1]} kWh<br>%{z:.1f} "+unit+"<br>fuel %{customdata[3]:.1f} gal/day · %{customdata[4]:.1f} starts/day<br>payload %{customdata[6]:,.0f} lb · capex $%{customdata[5]:.1f}k<br>%{customdata[2]}<extra></extra>"}],L);
}
function drawGenBat(){
  if(!GB) return;
  gbHeat("c_gbrun", GB.run[gbView], "h/day", 1, "orange");
  gbHeat("c_gbwt", GB.weight_lb, "lb", 0, "blue");
  // lines: run time vs battery for generator sizes near the recommended one
  const order=["--bat","--gen","--ev","--pv","--crit","--accent"];
  const cand = GB.gens.filter(g=>g>0);
  const bi = cand.indexOf(GB.best.gen);
  const pick = cand.slice(Math.max(0,bi-3), Math.max(0,bi-3)+6);
  const tr = pick.map((g,k)=>{ const j=GB.gens.indexOf(g); return {x:GB.bats, y:GB.run[gbView].map(r=>r[j]), name:`${fmt(g,g%1?1:0)} kW`, type:"scatter", mode:"lines+markers",
     line:{color:css(order[k%order.length]),width:2}, marker:{size:7, color:GB.feasible.map(r=>r[j]?css(order[k%order.length]):css("--panel")), line:{width:1.5,color:css(order[k%order.length])}},
     hovertemplate:`${fmt(g,g%1?1:0)} kW generator · %{x} kWh battery<br>%{y:.1f} h/day<extra></extra>`}; });
  let L=baseLayout({hovermode:"closest"});
  L.xaxis=Object.assign({},L._ax,{title:{text:"Battery (kWh)",font:{color:css("--ink2"),size:12}}});
  L.yaxis=Object.assign({},L._ax,{title:{text:"Generator run time (h/day)",font:{color:css("--ink2"),size:12}},range:[0,24.5]});
  L.legend=Object.assign({},L.legend,{orientation:"v",x:1.02,y:1,xanchor:"left",title:{text:"Generator",font:{color:css("--ink2"),size:12}}});
  L.margin.r=90;
  plot("c_gbline", tr, L);
  // stacked payload bars for feasible options (+ limit line)
  const opts = GB.options.slice(0, 12);
  const labels = opts.map(o=>`${fmt(o.gen_kw,o.gen_kw%1?1:0)} kW + ${fmt(o.bat_kwh)} kWh${o.best?" ★":""}`);
  const comps = Object.keys(GB.best_wt);
  const ccol = {"PV array":"--pv","Battery":"--bat","Inverter":"--mute","Generator":"--gen","Fuel + tank":"--mag","Heat pumps":"--accent","Fixed pod systems":"--rule"};
  const st = comps.map(c=>({type:"bar",orientation:"h",name:c,y:labels,x:opts.map(o=>o.wt[c]),marker:{color:css(ccol[c]||"--mute"),line:{color:css("--panel"),width:1}},
     hovertemplate:`${c}: %{x:,.0f} lb<extra></extra>`}));
  L=baseLayout({hovermode:"closest",barmode:"stack"});
  L.margin.l=150; L.margin.b=90; L.yaxis=Object.assign({},L._ax,{autorange:"reversed",automargin:true});
  L.legend=Object.assign({},L.legend,{orientation:"h",x:0,y:-0.22,yanchor:"top"});
  L.xaxis=Object.assign({},L._ax,{title:{text:"lb",font:{color:css("--ink2"),size:12}}});
  L.shapes=[{type:"line",x0:D.limits.payload_lb,x1:D.limits.payload_lb,yref:"paper",y0:0,y1:1,line:{color:css("--crit"),width:2,dash:"dash"}}];
  L.annotations=[{x:D.limits.payload_lb,y:1.0,yref:"paper",text:"trailer limit",showarrow:false,xanchor:"right",yanchor:"bottom",font:{size:11,color:css("--crit")}}];
  plot("c_gbstack", st, L);
  // table
  const vk = GB.views.map(v=>v[0]);
  const lab = Object.fromEntries(GB.views);
  const head = `<tr><th>Generator</th><th>Battery</th>${vk.map(k=>`<th>Run h/day<br>${lab[k]}</th>`).join("")}<th>Starts/day<br>worst</th><th>Fuel gal/day<br>worst</th><th>Payload lb</th><th>Margin lb</th><th>Capex</th><th>Lifecycle</th><th>Autonomy days</th></tr>`;
  const body = GB.options.map(o=>`<tr class="${o.best?"base":""}"><td>${fmt(o.gen_kw,o.gen_kw%1?1:0)} kW${o.best?" (recommended)":""}</td><td>${fmt(o.bat_kwh)} kWh</td>${vk.map(k=>`<td>${fmt(o.run[k],1)}</td>`).join("")}<td>${fmt(o.starts.worst,1)}</td><td>${fmt(o.fuel.worst,1)}</td><td>${fmt(o.weight_lb)}</td><td>${fmt(D.limits.payload_lb-o.weight_lb)}</td><td>$${fmt(o.capex/1000,1)}k</td><td>$${fmt(o.lifecycle/1000,1)}k</td><td>${fmt(o.autonomy,1)}</td></tr>`).join("");
  $("t_gb").innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
}
gbInit();

// ---------- windows study ----------
function drawWindows(){
  if(!WS){ ["winh","winlede"].forEach(i=>$(i).hidden=true); return; }
  const types=[...new Set(WS.map(r=>r.window_name))], counts=[...new Set(WS.map(r=>r.window_count))];
  const W=D.win;
  $("winlede").textContent = `Base building: ${W.count??"–"} windows, ${fmt(W.area_ft2)} ft² (${fmt(W.wwr*100)}% of wall), ${W.name}, U-${fmt(W.u_ip,2)}, SHGC ${fmt(W.shgc,2)}; windows are ${fmt(W.ua)} W/K of the heat loss. Each combination below re-runs the full sweep and optimization. Set window_count, window_type (or window_u_ip / window_shgc) and window_facade_split in the config.`;
  const get=(t,c)=>WS.find(r=>r.window_name===t&&r.window_count===c);
  const z=types.map(t=>counts.map(c=>{const r=get(t,c);return r&&r.feasible?+(r.capex_usd/1000).toFixed(1):null;}));
  const xi=counts.map((_,i)=>i), yi=types.map((_,i)=>i);
  let L=baseLayout({hovermode:"closest"});
  L.xaxis=Object.assign({},L._ax,{title:{text:"Number of windows",font:{color:css("--ink2"),size:12}},tickvals:xi,ticktext:counts.map(String),showgrid:false,zeroline:false});
  L.yaxis=Object.assign({},L._ax,{tickvals:yi,ticktext:types,automargin:true,showgrid:false,zeroline:false});
  const zf=z.flat().filter(v=>v!=null), mid=zf.length?(Math.max(...zf)+Math.min(...zf))/2:0;
  L.annotations=[]; types.forEach((t,i)=>counts.forEach((c,j)=>{const v=z[i][j]; L.annotations.push({x:j,y:i,text:v==null?"none":`$${v.toFixed(0)}k`,showarrow:false,font:{size:12,color:v==null?css("--crit"):(v>mid?"#ffffff":"#0b1a33")}});}));
  const tx=types.map(t=>counts.map(c=>{const r=get(t,c); return r&&r.feasible?`gen ${fmt(r.gen_kw,1)} kW · battery ${fmt(r.bat_kwh)} kWh · HP ${fmt(r.hp_kw,1)} kW<br>${fmt(r.weight_kg/LB)} lb · cold-snap run ${fmt(r.run_h_day_cold,1)} h/day`:`no feasible design: ${r?r.binding:""}`;}));
  plot("c_win",[{type:"heatmap",x:xi,y:yi,z,text:tx,showscale:false,xgap:2,ygap:2,hoverongaps:true,
     colorscale:isDark()?[[0,"#9ec5f4"],[1,"#184f95"]]:[[0,"#cde2fb"],[1,"#1c5cab"]],
     customdata:types.map(t=>counts.map(c=>[t,c])),hovertemplate:"%{customdata[0]}, %{customdata[1]} windows<br>%{text}<extra></extra>"}],L);
  const order=["--bat","--gen","--ev","--pv"];
  const tr=counts.map((c,k)=>({type:"bar",name:`${c} windows`,x:types,y:types.map(t=>get(t,c).design_heat_loss_kw),marker:{color:css(order[k%4]),line:{color:css("--panel"),width:1}},hovertemplate:`${c} windows: %{y:.1f} kW<extra></extra>`}));
  L=baseLayout({hovermode:"closest",barmode:"group"});
  L.yaxis=Object.assign({},L._ax,{title:{text:"kW",font:{color:css("--ink2"),size:12}},rangemode:"tozero"});
  plot("c_winload",tr,L);
  const head=`<tr><th>Window type</th><th>Count</th><th>Area ft²</th><th>U (Btu/h·ft²·°F)</th><th>SHGC</th><th>Window UA W/K</th><th>Design loss kW</th><th>Generator kW</th><th>Battery kWh</th><th>Heat pumps kW</th><th>Cold-snap run h/day</th><th>Payload lb</th><th>Capex</th></tr>`;
  const body=WS.map(r=>r.feasible?`<tr class="${r.window_name===W.name&&r.window_count===W.count?"base":""}"><td>${r.window_name}</td><td>${r.window_count}</td><td>${fmt(r.window_area_ft2)}</td><td>${fmt(r.window_u_ip,2)}</td><td>${fmt(r.window_shgc,2)}</td><td>${fmt(r.window_ua_w_k)}</td><td>${fmt(r.design_heat_loss_kw,1)}</td><td>${fmt(r.gen_kw,1)}</td><td>${fmt(r.bat_kwh)}</td><td>${fmt(r.hp_kw,1)}</td><td>${fmt(r.run_h_day_cold,1)}</td><td>${fmt(r.weight_kg/LB)}</td><td>$${fmt(r.capex_usd/1000,1)}k</td></tr>`
    :`<tr><td>${r.window_name}</td><td>${r.window_count}</td><td>${fmt(r.window_area_ft2)}</td><td>${fmt(r.window_u_ip,2)}</td><td>${fmt(r.window_shgc,2)}</td><td>${fmt(r.window_ua_w_k)}</td><td>${fmt(r.design_heat_loss_kw,1)}</td><td class="no" colspan="6"><span class="pill bad">no feasible design</span> ${r.binding||""}</td></tr>`).join("");
  $("t_win").innerHTML=`<thead>${head}</thead><tbody>${body}</tbody>`;
}

function drawAll(){
  if(!window.Plotly){
    document.querySelectorAll(".chart").forEach(el=>{ el.style.height="auto"; el.innerHTML='<p class="note">Charts could not load (the Plotly library is missing). Re-generate the report with <code>python3 -m microgrid_sim report -o results</code>, or open the PNG files in the results folder.</p>'; });
    return;
  }
  drawDispatch(); drawGenBat(); drawWindows(); drawWeather(); drawSweep(); drawSens(); drawAnnual(); }
drawAll();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawAll);
new MutationObserver(drawAll).observe(document.documentElement,{attributes:true,attributeFilter:["data-theme"]});
</script>
"""
