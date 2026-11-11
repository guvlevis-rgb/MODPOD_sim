"""Excel workbook output: the HTML report's content as native Excel tables and charts.

Sheets
  Summary            recommended design, outcomes, results per design window (linked by formula), findings
  <window sheets>    one per design window: hourly dispatch table, window totals (formulas), 3 charts
  Weather            daily weather year, design-window statistics (formulas), 2 charts
  Design sweep       sweep counts, failure reasons (COUNTIF on All designs), cost-vs-fuel scatter,
                     cold-snap heat map (generator x heat pumps)
  Feasible designs   every design that met all limits, cheapest first
  Autonomy           fuel-autonomy ladder + chart
  Sensitivity        re-optimized cases + chart, envelope x occupants matrix
  Annual check       optimum deployed on every day of the year + 2 charts
  Assumptions        unit constants and limits used by formulas (blue = input), every config parameter,
                     building heat-loss breakdown + chart
  All designs        every simulated design (filterable)

Simulation outputs are written as values; totals, unit conversions, limits and cross-sheet
summaries are Excel formulas so the workbook stays traceable.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import AreaChart, BarChart, LineChart, Reference, ScatterChart, Series
from openpyxl.chart.layout import Layout, ManualLayout
from openpyxl.chart.marker import Marker
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as COL
from openpyxl.workbook.defined_name import DefinedName

from .report import L_PER_GAL, KG_PER_LB, build_payload, c2f

FONT = "Arial"
INK, INK2, MUTE = "17201D", "4B5753", "7D8884"
ACCENT = "1F6F5C"
C = dict(pv="EDA100", bat="2A78D6", gen="EB6834", ev="1BAF7A", unmet="C8312F", ink="17201D", mute="7D8884",
         grid="D9DFDC", hp="4B5753")

F_TITLE = Font(name=FONT, size=16, bold=True, color=INK)
F_H2 = Font(name=FONT, size=12, bold=True, color=INK)
F_HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
F_BODY = Font(name=FONT, size=10, color=INK)
F_BOLD = Font(name=FONT, size=10, bold=True, color=INK)
F_NOTE = Font(name=FONT, size=9, italic=True, color=MUTE)
F_INPUT = Font(name=FONT, size=10, color="0000FF")
F_LINK = Font(name=FONT, size=10, color="008000")
F_BAD = Font(name=FONT, size=10, color=C["unmet"])
FILL_HEAD = PatternFill("solid", fgColor="1F3A33")
FILL_KEY = PatternFill("solid", fgColor="E1EFE9")
FILL_INPUT = PatternFill("solid", fgColor="FFF8D6")
THIN = Side(style="thin", color="D9DFDC")
B_BOTTOM = Border(bottom=THIN)

NF = dict(kw="0.0", kwh="#,##0", kwh1="#,##0.0", f="0.0", pct="0.0%", usd="$#,##0", usdk='$#,##0.0,"k"',
          gal="#,##0.0", int="#,##0", d1="0.0", d2="0.00", lb="#,##0")

WINDOW_SHEET = {"cold_snap": "Cold snap", "dark_storm": "Dark storm", "spring_flood": "Spring flood",
                "summer_heat": "Heat wave", "endurance": "14-day endurance"}


def q(name: str) -> str:
    """Quoted sheet reference prefix."""
    return f"'{name}'!"


# ---------------------------------------------------------------------------
# small writing helpers
# ---------------------------------------------------------------------------
def _hbar():
    ch = BarChart()
    ch.type = "bar"          # horizontal bars
    return ch


def put(ws, row, col, value, font=F_BODY, fmt=None, fill=None, align=None, border=None):
    c = ws.cell(row=row, column=col, value=value)
    c.font = font
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = fill
    if align:
        c.alignment = align
    if border:
        c.border = border
    return c


def title(ws, text, sub=None):
    put(ws, 1, 1, text, F_TITLE)
    if sub:
        put(ws, 2, 1, sub, F_NOTE)
    ws.sheet_view.showGridLines = False


def header_row(ws, row, col, headers, widths=None):
    for j, h in enumerate(headers):
        put(ws, row, col + j, h, F_HEAD, fill=FILL_HEAD,
            align=Alignment(horizontal="center", vertical="center", wrap_text=True))
        if widths and j < len(widths) and widths[j]:
            ws.column_dimensions[COL(col + j)].width = widths[j]
    ws.row_dimensions[row].height = 30


def table(ws, row, col, headers, rows, fmts=None, widths=None):
    """Write a header + rows; returns (first_data_row, last_data_row)."""
    header_row(ws, row, col, headers, widths)
    r0 = row + 1
    for i, rec in enumerate(rows):
        for j, v in enumerate(rec):
            if isinstance(v, (np.floating,)):
                v = float(v)
            if isinstance(v, float) and not np.isfinite(v):
                v = None
            if isinstance(v, (np.integer,)):
                v = int(v)
            if isinstance(v, np.bool_):
                v = bool(v)
            put(ws, r0 + i, col + j, v, fmt=(fmts[j] if fmts and j < len(fmts) else None), border=B_BOTTOM)
    return r0, r0 + len(rows) - 1


def style_chart(ch, title_text, x_title=None, y_title=None, w=24, h=8.5, legend="t"):
    ch.title = title_text
    ch.width, ch.height = w, h
    if x_title:
        ch.x_axis.title = x_title
    if y_title:
        ch.y_axis.title = y_title
    try:
        ch.x_axis.delete = False
        ch.y_axis.delete = False
    except AttributeError:
        pass
    if legend:
        ch.legend.position = legend
    else:
        ch.legend = None
    return ch


def color_series(s, hexcol, line=False, width=19050, dash=None, fill=True):
    if line:
        s.graphicalProperties.line.solidFill = hexcol
        s.graphicalProperties.line.width = width
        if dash:
            s.graphicalProperties.line.dashStyle = dash
        s.smooth = False
    else:
        if fill:
            s.graphicalProperties.solidFill = hexcol
        s.graphicalProperties.line.solidFill = hexcol
        s.graphicalProperties.line.width = 3175


# ---------------------------------------------------------------------------
# sheets
# ---------------------------------------------------------------------------
def _assumptions(wb, res, P):
    ws = wb.create_sheet("Assumptions")
    st = res["study"]
    title(ws, "Assumptions and parameters",
          "Blue cells are inputs used by formulas in this workbook. Simulation parameters are listed for reference; "
          "change them in the JSON config and re-run the tool to update results.")
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 70
    put(ws, 4, 1, "Workbook constants and limits", F_H2)
    consts = [
        ("Liters per US gallon", L_PER_GAL, "0.00000", "L_PER_GAL", "Exact conversion"),
        ("Kilograms per pound", KG_PER_LB, "0.000000", "KG_PER_LB", "Exact conversion"),
        ("Minimum indoor temperature (°F)", P["building"]["t_min_f"], NF["f"], "T_MIN_F",
         "Engineering requirement: shelter at or above 50 °F (MOD POD presentation)"),
        ("Maximum indoor temperature (°F)", P["building"]["t_max_f"], NF["f"], "T_MAX_F",
         "Engineering requirement: hold roughly 80-90 °F in summer (MOD POD presentation)"),
        ("Trailer payload limit (lb)", P["limits"]["payload_lb"], NF["lb"], "PAYLOAD_LB",
         "Model input constraints.max_payload_kg converted to lb (10,000 lb tow rating minus trailer)"),
        ("Deployment days per year", st.econ.deployment_days_per_year, "0", "DEPLOY_DAYS",
         "Model input econ.deployment_days_per_year (planning estimate)"),
    ]
    header_row(ws, 5, 1, ["Constant", "Value", "Source / note"])
    for i, (lab, v, fmt, name, note) in enumerate(consts):
        r = 6 + i
        put(ws, r, 1, lab, border=B_BOTTOM)
        put(ws, r, 2, v, F_INPUT, fmt=fmt, fill=FILL_INPUT, border=B_BOTTOM)
        put(ws, r, 3, note, F_NOTE, border=B_BOTTOM)
        wb.defined_names[name] = DefinedName(name, attr_text=f"{q('Assumptions')}$B${r}")

    r = 6 + len(consts) + 2
    put(ws, r, 1, "Key modelling assumptions", F_H2)
    r += 1
    header_row(ws, r, 1, ["Item", "", "Assumption"])
    for lab, txt in P["assumptions"]:
        r += 1
        put(ws, r, 1, lab, border=B_BOTTOM)
        put(ws, r, 3, txt, border=B_BOTTOM, align=Alignment(wrap_text=True, vertical="top"))

    # heat-loss breakdown
    r += 3
    put(ws, r, 1, "Base building heat loss by path", F_H2)
    r += 1
    ua_rows = sorted(zip(P["ua"]["labels"], P["ua"]["w_k"]), key=lambda t: -t[1])
    r0, r1 = table(ws, r, 1, ["Path", "UA (W/K)", "Share"], [(l, v, None) for l, v in ua_rows],
                   fmts=[None, "0.0", NF["pct"]])
    for rr in range(r0, r1 + 1):
        ws.cell(rr, 3).value = f"=B{rr}/SUM($B${r0}:$B${r1})"
        ws.cell(rr, 3).number_format = NF["pct"]
    put(ws, r1 + 1, 1, "Total", F_BOLD)
    put(ws, r1 + 1, 2, f"=SUM(B{r0}:B{r1})", F_BOLD, fmt="0.0")
    put(ws, r1 + 2, 1, "Total (Btu/h-°F)", F_BODY)
    put(ws, r1 + 2, 2, f"=B{r1 + 1}*1.8956", fmt="#,##0")
    put(ws, r1 + 2, 3, "1 W/K = 1.8956 Btu/h-°F", F_NOTE)
    ch = _hbar()
    ch.add_data(Reference(ws, min_col=2, min_row=r0 - 1, max_row=r1), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=r0, max_row=r1))
    style_chart(ch, "Heat loss by path (W/K)", y_title="W/K", w=16, h=7.5, legend=None)
    ch.x_axis.scaling.orientation = "maxMin"
    color_series(ch.series[0], ACCENT)
    ch.gapWidth = 60
    ws.add_chart(ch, f"A{r1 + 4}")

    # every parameter
    r = r1 + 21
    put(ws, r, 1, "All simulation parameters (from the run)", F_H2)
    r += 1
    header_row(ws, r, 1, ["Parameter", "Value", "Section"])
    from .study import SECTIONS
    for sec in SECTIONS:
        obj = getattr(st, sec)
        for k, v in dataclasses.asdict(obj).items():
            r += 1
            if isinstance(v, (list, tuple, dict)):
                v = str(v)
            put(ws, r, 1, f"{sec}.{k}", border=B_BOTTOM)
            put(ws, r, 2, v, F_INPUT, border=B_BOTTOM)
            put(ws, r, 3, sec, F_NOTE, border=B_BOTTOM)
    ws.freeze_panes = "A4"
    return ws


def _window_sheet(wb, key, d, desc):
    name = WINDOW_SHEET.get(key) or (d["label"].replace("Annual check ", "") + " window")[:31]
    ws = wb.create_sheet(name)
    title(ws, f"{d['label']}: recommended design, hour by hour", desc)
    n = len(d["time"])
    # data table
    hdr = ["Time", "PV (kW)", "Battery discharge (kW)", "Generator (kW)", "EV pickup (kW)", "Unserved (kW)",
           "Load served (kW)", "Battery charging (kW)", "HVAC draw (kW)", "Battery SOC (%)",
           "Indoor (°F)", "Outdoor (°F)", "Min limit (°F)", "Max limit (°F)", "PV curtailed (kW)",
           "Heat/cool unit heat (kW)", "Propane heat (kW)", "Cooling (kW)", "Propane burn (gal/h)"]
    R0 = 70
    rows = []
    for i in range(n):
        rows.append([pd.Timestamp(d["time"][i]).strftime("%b %d %H:%M"), d["pv"][i], d["bat_dis"][i], d["gen"][i],
                     d["ev"][i], d["unmet"][i], d["load"][i], d["bat_ch"][i], d["hvac"][i], d["soc"][i],
                     d["t_in"][i], d["t_out"][i], None, None, d["curtail"][i],
                     d["q_hp"][i], d["q_ph"][i], d["q_cool"][i], d["lp_gph"][i]])
    fm = [None] + [NF["kw"]] * 8 + ["0"] + [NF["f"]] * 4 + [NF["kw"]] * 4 + ["0.00"]
    a, b = table(ws, R0, 1, hdr, rows, fmts=fm, widths=[13] + [11] * 18)
    for rr in range(a, b + 1):
        ws.cell(rr, 13).value = "=T_MIN_F"
        ws.cell(rr, 14).value = "=T_MAX_F"
    put(ws, R0 - 1, 1, "Hourly results (each row is one hour, so kW summed over the rows = kWh)", F_H2)

    # window totals (formulas over the table)
    m = d["m"]
    rng = lambda c: f"{COL(c)}{a}:{COL(c)}{b}"
    put(ws, 4, 1, "Window totals", F_H2)
    header_row(ws, 5, 1, ["Metric", "Value", "Unit"])
    mets = [
        ("Fuel used", m["fuel_gal"], NF["gal"], "gal", "sim"),
        ("Generator run time", m["gen_h"], "0", "h", "sim"),
        ("Generator starts", m["starts"], "0", "", "sim"),
        ("Demand served", f"=SUM({rng(7)})", NF["kwh"], "kWh", "f"),
        ("Unserved energy", f"=SUM({rng(6)})", NF["kwh1"], "kWh", "f"),
        ("PV energy used", f"=SUM({rng(2)})", NF["kwh"], "kWh", "f"),
        ("Generator energy", f"=SUM({rng(4)})", NF["kwh"], "kWh", "f"),
        ("HVAC electricity", f"=SUM({rng(9)})", NF["kwh"], "kWh", "f"),
        ("Coldest indoor", f"=MIN({rng(11)})", NF["f"], "°F", "f"),
        ("Warmest indoor", f"=MAX({rng(11)})", NF["f"], "°F", "f"),
        ("Hours below minimum", f'=COUNTIF({rng(11)},"<"&T_MIN_F)', "0", "h", "f"),
        ("Lowest battery SOC", f"=MIN({rng(10)})", "0", "%", "f"),
        ("Fuel left in tank", m["fuel_left_gal"], NF["gal"], "gal", "sim"),
        ("Window length", m["days"], "0", "days", "sim"),
        ("Fuel per day", "=B6/B19", NF["gal"], "gal/day", "f"),
        ("Heat from heat/cool units", f"=SUM({rng(16)})", NF["kwh"], "kWh", "f"),
        ("Heat from propane", f"=SUM({rng(17)})", NF["kwh"], "kWh", "f"),
        ("Propane used", f"=SUM({rng(19)})", NF["gal"], "gal", "f"),
        ("Propane per day", "=B23/B19", NF["gal"], "gal/day", "f"),
    ]
    for i, (lab, v, fmt, unit, kind) in enumerate(mets):
        rr = 6 + i
        put(ws, rr, 1, lab, border=B_BOTTOM)
        put(ws, rr, 2, v, fmt=fmt, border=B_BOTTOM)
        put(ws, rr, 3, unit, F_NOTE, border=B_BOTTOM)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 11
    ws.column_dimensions["C"].width = 9

    skip = max(1, n // 12)
    # chart 1: supply stack + load
    ac = AreaChart(grouping="stacked")
    cats = Reference(ws, min_col=1, min_row=a, max_row=b)
    sup = [(2, C["pv"]), (3, C["bat"]), (4, C["gen"])]
    if any(v > 0 for v in d["ev"]):
        sup.append((5, C["ev"]))
    if any(v > 0.01 for v in d["unmet"]):
        sup.append((6, C["unmet"]))
    for col, hexc in sup:
        ac.add_data(Reference(ws, min_col=col, min_row=a - 1, max_row=b), titles_from_data=True)
        color_series(ac.series[-1], hexc)
    ac.set_categories(cats)
    lc = LineChart()
    lc.add_data(Reference(ws, min_col=7, min_row=a - 1, max_row=b), titles_from_data=True)
    color_series(lc.series[-1], C["ink"], line=True, width=15875)
    lc.add_data(Reference(ws, min_col=9, min_row=a - 1, max_row=b), titles_from_data=True)
    color_series(lc.series[-1], C["hp"], line=True, width=12700, dash="sysDot")
    for s in lc.series:
        s.marker = Marker(symbol="none")
    style_chart(ac, "Where each kW came from", y_title="kW", w=26, h=9)
    ac.x_axis.tickLblSkip = skip
    ac.x_axis.tickMarkSkip = skip
    ac += lc
    ws.add_chart(ac, "E4")

    # chart 2: temperatures
    tc = LineChart()
    for col, hexc, wdt, dash in ((11, C["bat"], 22225, None), (12, C["mute"], 12700, "dash"),
                                 (13, C["unmet"], 9525, "sysDot"), (14, C["gen"], 9525, "sysDot")):
        tc.add_data(Reference(ws, min_col=col, min_row=a - 1, max_row=b), titles_from_data=True)
        color_series(tc.series[-1], hexc, line=True, width=wdt, dash=dash)
        tc.series[-1].marker = Marker(symbol="none")
    tc.set_categories(cats)
    style_chart(tc, "Shelter temperature vs requirement band", y_title="°F", w=26, h=7)
    tc.x_axis.tickLblSkip = skip
    tc.x_axis.tickMarkSkip = skip
    ws.add_chart(tc, "E23")

    # chart 3: SOC
    sc = LineChart()
    sc.add_data(Reference(ws, min_col=10, min_row=a - 1, max_row=b), titles_from_data=True)
    color_series(sc.series[-1], C["bat"], line=True, width=19050)
    sc.series[-1].marker = Marker(symbol="none")
    sc.set_categories(cats)
    style_chart(sc, "Battery state of charge", y_title="%", w=26, h=6, legend=None)
    sc.y_axis.scaling.min = 0
    sc.y_axis.scaling.max = 100
    sc.x_axis.tickLblSkip = skip
    sc.x_axis.tickMarkSkip = skip
    ws.add_chart(sc, "E39")

    # chart 4: heat delivered (heat/cool units + propane)
    hc = AreaChart(grouping="stacked")
    for col, hexc in ((16, C["hp"]), (17, "9C3FB0")):
        hc.add_data(Reference(ws, min_col=col, min_row=a - 1, max_row=b), titles_from_data=True)
        color_series(hc.series[-1], hexc)
    hc.set_categories(cats)
    style_chart(hc, "Heat delivered: heat/cool units and propane heaters", y_title="kW heat", w=26, h=7)
    hc.x_axis.tickLblSkip = skip
    hc.x_axis.tickMarkSkip = skip
    ws.add_chart(hc, "E52")
    return name


def _weather(wb, res, P):
    ws = wb.create_sheet("Weather")
    w = P["weather"]
    title(ws, "Waitsfield weather year and design windows", P["source"])
    # daily table
    R0 = 14
    put(ws, R0 - 1, 1, "Daily weather", F_H2)
    wins = P["windows"]

    def in_window(date):
        names = [x["label"] for x in wins if x["start"] <= date < x["end"]]
        return ", ".join(names) if names else None

    rows = [[pd.Timestamp(w["date"][i]).to_pydatetime(), w["tmin"][i], w["tmax"][i], w["tmean"][i], w["ghi"][i],
             in_window(w["date"][i])] for i in range(len(w["date"]))]
    a, b = table(ws, R0, 1, ["Date", "Min (°F)", "Max (°F)", "Mean (°F)", "Solar GHI (kWh/m²/day)",
                             "Design window"], rows, fmts=["mmm d", NF["f"], NF["f"], NF["f"], NF["d2"], None],
                 widths=[12, 10, 10, 10, 13, 30])
    # window statistics via formulas
    put(ws, 4, 1, "Design windows (statistics computed from the daily table)", F_H2)
    header_row(ws, 5, 1, ["Window", "Start", "End", "Days", "Mean (°F)", "Coldest (°F)", "Avg GHI (kWh/m²/day)",
                          "Why it matters"])
    ws.column_dimensions["G"].width = 13
    ws.column_dimensions["H"].width = 70
    D_ = f"$A${a}:$A${b}"
    for i, x in enumerate(wins):
        r = 6 + i
        put(ws, r, 1, x["label"], border=B_BOTTOM)
        put(ws, r, 2, pd.Timestamp(x["start"]).to_pydatetime(), fmt="mmm d", border=B_BOTTOM)
        put(ws, r, 3, pd.Timestamp(x["end"]).to_pydatetime() - pd.Timedelta(days=1), fmt="mmm d", border=B_BOTTOM)
        put(ws, r, 4, f"=C{r}-B{r}+1", fmt="0", border=B_BOTTOM)
        crit = f'{D_},">="&B{r},{D_},"<="&C{r}'
        put(ws, r, 5, f"=AVERAGEIFS($D${a}:$D${b},{crit})", fmt=NF["f"], border=B_BOTTOM)
        put(ws, r, 6, f"=_xlfn.MINIFS($B${a}:$B${b},{crit})", fmt=NF["f"], border=B_BOTTOM)
        put(ws, r, 7, f"=AVERAGEIFS($E${a}:$E${b},{crit})", fmt=NF["d2"], border=B_BOTTOM)
        put(ws, r, 8, x["desc"], F_NOTE, border=B_BOTTOM)
    # charts
    lc = LineChart()
    for col, hexc, wdt in ((3, C["gen"], 9525), (4, C["bat"], 19050), (2, "86B6EF", 9525)):
        lc.add_data(Reference(ws, min_col=col, min_row=a - 1, max_row=b), titles_from_data=True)
        color_series(lc.series[-1], hexc, line=True, width=wdt)
        lc.series[-1].marker = Marker(symbol="none")
    lc.set_categories(Reference(ws, min_col=1, min_row=a, max_row=b))
    style_chart(lc, "Daily temperature, Waitsfield (°F)", y_title="°F", w=28, h=8.5)
    lc.x_axis.number_format = "mmm"
    lc.x_axis.tickLblSkip = 30
    lc.x_axis.tickMarkSkip = 30
    ws.add_chart(lc, f"H{R0 - 1}")
    bc = BarChart()
    bc.add_data(Reference(ws, min_col=5, min_row=a - 1, max_row=b), titles_from_data=True)
    bc.set_categories(Reference(ws, min_col=1, min_row=a, max_row=b))
    color_series(bc.series[0], C["pv"])
    bc.gapWidth = 30
    style_chart(bc, "Daily solar irradiance (kWh/m²/day)", y_title="kWh/m²/day", w=28, h=8.5, legend=None)
    bc.x_axis.number_format = "mmm"
    bc.x_axis.tickLblSkip = 30
    bc.x_axis.tickMarkSkip = 30
    ws.add_chart(bc, f"H{R0 + 17}")
    # highlight design-window days
    ws.conditional_formatting.add(f"A{a}:F{b}", _rowrule(a, b))
    ws.freeze_panes = ws.cell(R0 + 1, 1)


def _rowrule(a, b):
    from openpyxl.formatting.rule import FormulaRule
    return FormulaRule(formula=[f'LEN($F{a})>0'], fill=PatternFill("solid", fgColor="E3EEFB"))


def _all_designs(wb, res):
    ws = wb.create_sheet("All designs")
    sw = res["sweep"].sort_values(["feasible", "objective"], ascending=[False, True])
    title(ws, "All simulated designs",
          "Every combination simulated (coarse sweep + refinement). Use the filter arrows to explore. "
          "Fuel and temperature columns are for the named design window.")
    cols = [("pv_kw", "PV (kW)", NF["kw"]), ("bat_kwh", "Battery (kWh)", "0"), ("gen_kw", "Generator (kW)", NF["kw"]),
            ("tank_l", "Tank (L)", "0"), ("hp_units", "Heat/cool units", "0"), ("ph_kbtu", "Propane (kBtu/h)", "0"),
            ("propane_worst_gpd", "Propane worst (gal/day)", NF["d1"]), ("capex_usd", "Capex ($)", NF["usd"]),
            ("lifecycle_usd", "Lifecycle ($)", NF["usd"]), ("weight_kg", "Payload (kg)", "#,##0"),
            ("exp_fuel_l_per_day", "Expected fuel (L/day)", NF["d1"]),
            ("fuel_lpd_cold_snap", "Cold-snap fuel (L/day)", NF["d1"]),
            ("t_min_cold_snap", "Cold-snap low (°C)", NF["d1"]),
            ("autonomy_d_endurance", "Diesel autonomy (days)", NF["d1"]),
            ("feasible", "Feasible", None), ("fail_reason", "First failed check", None), ("stage", "Stage", None)]
    cols = [c for c in cols if c[0] in sw.columns]
    hdr = [c[1] for c in cols]
    R0 = 4
    header_row(ws, R0, 1, hdr, widths=[9, 10, 11, 9, 11, 11, 12, 11, 12, 12, 11, 11, 9, 44, 9])
    vals = sw[[c[0] for c in cols]].to_numpy()
    fmts = [c[2] for c in cols]
    for i, rec in enumerate(vals):
        r = R0 + 1 + i
        for j, v in enumerate(rec):
            if isinstance(v, (float, np.floating)):
                v = None if not np.isfinite(v) else float(round(v, 3))
            elif isinstance(v, np.bool_):
                v = bool(v)
            c = ws.cell(r, j + 1, v)
            if fmts[j]:
                c.number_format = fmts[j]
    last = R0 + len(vals)
    ws.auto_filter.ref = f"A{R0}:{COL(len(cols))}{last}"
    ws.freeze_panes = ws.cell(R0 + 1, 1)
    # Arial for the header only (body uses default font: keeps the file small and fast)
    return dict(sheet="All designs", first=R0 + 1, last=last,
                col={c[0]: COL(i + 1) for i, c in enumerate(cols)})


def _sweep(wb, res, P, alld):
    ws = wb.create_sheet("Design sweep")
    s = P["sweep"]
    title(ws, "Design sweep", "Coarse full-factorial sweep plus refinement at purchasable increments. "
                              "Counts and failure tallies below are live formulas on the All designs sheet.")
    AD = q("All designs")
    feas_col = f"{AD}${alld['col']['feasible']}${alld['first']}:${alld['col']['feasible']}${alld['last']}"
    reas_col = f"{AD}${alld['col']['fail_reason']}${alld['first']}:${alld['col']['fail_reason']}${alld['last']}"
    put(ws, 4, 1, "Designs simulated", F_BOLD)
    put(ws, 4, 2, f"=COUNTA({feas_col})", fmt=NF["int"])
    put(ws, 5, 1, "Feasible (met every limit)", F_BOLD)
    put(ws, 5, 2, f"=COUNTIF({feas_col},TRUE)", fmt=NF["int"])
    put(ws, 6, 1, "Share feasible", F_BOLD)
    put(ws, 6, 2, "=B5/B4", fmt="0.00%")
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 12

    # failure reasons
    put(ws, 8, 1, "Why designs failed (first failed check)", F_H2)
    labels = s["reasons"]["labels"][:10]
    a, b = table(ws, 9, 1, ["Reason", "Designs"], [(l, None) for l in labels], fmts=[None, NF["int"]])
    for i in range(len(labels)):
        ws.cell(a + i, 2).value = f"=COUNTIF({reas_col},A{a + i})"
    bc = _hbar()
    bc.add_data(Reference(ws, min_col=2, min_row=a - 1, max_row=b), titles_from_data=True)
    bc.set_categories(Reference(ws, min_col=1, min_row=a, max_row=b))
    color_series(bc.series[0], C["mute"])
    bc.gapWidth = 50
    style_chart(bc, "Why designs failed", x_title=None, y_title="Designs", w=18, h=8, legend=None)
    bc.x_axis.scaling.orientation = "maxMin"
    ws.add_chart(bc, "D4")

    # heat map: warmest cold-snap low per generator x heat-pump size
    h = s["heat"]
    r = max(b, 22) + 3
    put(ws, r, 1, "Warmest cold-snap indoor low (°F) by generator and heat-pump size", F_H2)
    put(ws, r + 1, 1, "Rows: heat pumps (kW). Columns: generator (kW). Designs over the trailer payload are excluded. "
                      "Cells at or above the 50 °F minimum are bold.", F_NOTE)
    hr = r + 2
    put(ws, hr, 1, "Heat pumps (kW) \\ Generator (kW)", F_HEAD, fill=FILL_HEAD)
    for j, g in enumerate(h["x"]):
        put(ws, hr, 2 + j, g, F_HEAD, fmt="0.#", fill=FILL_HEAD, align=Alignment(horizontal="center"))
        ws.column_dimensions[COL(2 + j)].width = max(ws.column_dimensions[COL(2 + j)].width or 0, 8)
    ys = list(reversed(range(len(h["y"]))))
    for k, i in enumerate(ys):
        put(ws, hr + 1 + k, 1, h["y"][i], F_BOLD, fmt="0.0")
        for j in range(len(h["x"])):
            put(ws, hr + 1 + k, 2 + j, h["z"][i][j], fmt="0.0", align=Alignment(horizontal="center"))
    rng = f"B{hr + 1}:{COL(1 + len(h['x']))}{hr + len(ys)}"
    ws.conditional_formatting.add(rng, ColorScaleRule(start_type="min", start_color="CDE2FB",
                                                      mid_type="percentile", mid_value=50, mid_color="6DA7EC",
                                                      end_type="max", end_color="1C5CAB"))
    ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThanOrEqual", formula=["T_MIN_F"],
                                                  font=Font(name=FONT, bold=True, color="FFFFFF")))
    # second grid: cheapest feasible lifecycle per cell
    r2 = hr + len(ys) + 3
    put(ws, r2, 1, "Cheapest feasible lifecycle cost in each cell (blank = no feasible design)", F_H2)
    put(ws, r2 + 1, 1, "Heat pumps (kW) \\ Generator (kW)", F_HEAD, fill=FILL_HEAD)
    for j, g in enumerate(h["x"]):
        put(ws, r2 + 1, 2 + j, g, F_HEAD, fmt="0.#", fill=FILL_HEAD, align=Alignment(horizontal="center"))
    for k, i in enumerate(ys):
        put(ws, r2 + 2 + k, 1, h["y"][i], F_BOLD, fmt="0.0")
        for j in range(len(h["x"])):
            lab = h["lab"][i][j]
            v = float(lab.strip("$k")) * 1000 if lab else None
            put(ws, r2 + 2 + k, 2 + j, v, fmt=NF["usdk"], align=Alignment(horizontal="center"),
                fill=FILL_KEY if v else None)

    # scatter: capex vs fuel (data block on the right)
    dc = 30
    put(ws, 3, dc, "Chart data: cost vs fuel", F_H2)
    f = s["feas"]
    feas_rows = sorted(zip([v / 1000 for v in f["capex_usd"]], [v / L_PER_GAL for v in f["exp_fuel_l_per_day"]]))
    inf = s["inf"]
    inf_rows = list(zip([v / 1000 for v in inf["capex_usd"]], [v / L_PER_GAL for v in inf["exp_fuel_l_per_day"]]))[:2500]
    par = list(zip([v / 1000 for v in s["pareto"]["capex"]], [v / L_PER_GAL for v in s["pareto"]["fuel"]]))
    B_ = P["best"]
    blocks = [("Infeasible (sample)", inf_rows, "C9CED6", 3), ("Feasible", feas_rows, C["bat"], 6),
              ("Pareto front", par, C["gen"], 0), ("Optimum", [(B_["capex_usd"] / 1000, B_["exp_fuel_l_per_day"] / L_PER_GAL)], C["ink"], 11)]
    sch = ScatterChart()
    col = dc
    for name, rows, hexc, size in blocks:
        a2, b2 = table(ws, 4, col, [f"{name}: capex ($k)", "Fuel (gal/day)"], rows, fmts=[NF["d1"], NF["d2"]],
                       widths=[13, 11])
        xs = Reference(ws, min_col=col, min_row=a2, max_row=b2)
        ys_ = Reference(ws, min_col=col + 1, min_row=a2, max_row=b2)
        se = Series(ys_, xs, title=name)
        if size == 0:
            se.marker = Marker(symbol="none")
            color_series(se, hexc, line=True, width=22225)
        else:
            se.marker = Marker(symbol="circle", size=size)
            se.marker.graphicalProperties.solidFill = hexc if name != "Optimum" else "FFFFFF"
            se.marker.graphicalProperties.line.solidFill = hexc
            se.graphicalProperties.line.noFill = True
        sch.series.append(se)
        col += 3
    sch.scatterStyle = "marker"
    style_chart(sch, "Capital cost vs expected fuel use", x_title="Capital cost ($k)",
                y_title="Expected fuel (gal/day)", w=18, h=10)
    ws.add_chart(sch, "N4")
    ws.freeze_panes = "A4"


def _feasible(wb, res):
    ws = wb.create_sheet("Feasible designs")
    sw = res["sweep"]
    fe = sw[sw.feasible].sort_values("objective")
    title(ws, "Feasible designs, cheapest lifecycle cost first",
          "Every design that held 50-90 °F, served the load in every design window and fits the trailer. "
          "Row 1 of the table is the recommended design.")
    keys = [c for c in fe.columns if c.startswith("fuel_lpd_")]
    wins = [k.replace("fuel_lpd_", "") for k in keys]
    hdr = ["Rank", "PV (kW)", "Battery (kWh)", "Generator (kW)", "Tank (gal)", "Heat/cool units", "Inverter (kW)",
           "Capex ($)", "Lifecycle ($)", "Payload (lb)", "Expected diesel (gal/day)", "Autonomy (days)",
           "Propane heaters (kBtu/h)", "Propane worst (gal/day)"] + \
          [f"{WINDOW_SHEET.get(w, w)} diesel (gal/day)" for w in wins] + \
          [f"{WINDOW_SHEET.get(w, w)} low (°F)" for w in wins]
    rows = []
    for i, (_, r) in enumerate(fe.iterrows()):
        rows.append([i + 1, r.pv_kw, r.bat_kwh, r.gen_kw, r.tank_l / L_PER_GAL, r.hp_units, r.inverter_kw, r.capex_usd,
                     r.lifecycle_usd, r.weight_kg / KG_PER_LB, r.exp_fuel_l_per_day / L_PER_GAL,
                     r.get("autonomy_d_endurance", np.nan), r.get("ph_kbtu", 0), r.get("propane_worst_gpd", 0)]
                    + [r[k] / L_PER_GAL for k in keys] + [float(c2f(r[f"t_min_{w}"])) for w in wins])
    fm = ["0", NF["kw"], "0", NF["kw"], "0", "0", "0", NF["usd"], NF["usd"], NF["lb"], NF["d1"], NF["d1"], "0",
          NF["d1"]] + [NF["d1"]] * len(wins) * 2
    a, b = table(ws, 4, 1, hdr, rows, fmts=fm, widths=[6] + [11] * (len(hdr) - 1))
    for j in range(1, len(hdr) + 1):
        ws.cell(a, j).fill = FILL_KEY
        ws.cell(a, j).font = F_BOLD
    ws.auto_filter.ref = f"A4:{COL(len(hdr))}{b}"
    ws.freeze_panes = "B5"


def _cases(ws, row, rows, first_label):
    hdr = [first_label, "PV (kW)", "Battery (kWh)", "Generator (kW)", "Tank (gal)", "Heat/cool units", "Capex ($)",
           "Lifecycle ($)", "Payload (lb)", "Diesel (gal/day)", "Autonomy (days)", "14-day diesel need (gal)",
           "Propane heaters (kBtu/h)", "Propane (gal/day)", "14-day propane need (gal)", "Note"]
    out = []
    for r in rows:
        if r.get("feasible"):
            out.append([r["case"], r["pv_kw"], r["bat_kwh"], r["gen_kw"], r["tank_l"] / L_PER_GAL, r.get("hp_units"),
                        r["capex_usd"], r["lifecycle_usd"], r["weight_kg"] / KG_PER_LB, r["fuel_l_per_day"] / L_PER_GAL,
                        r.get("autonomy_days"), (r.get("fuel_need_14d_l") or 0) / L_PER_GAL,
                        r.get("ph_kbtu"), r.get("propane_gal_per_day"), r.get("propane_need_14d_gal"), None])
        else:
            note = f"No feasible design. Binding limit: {r.get('binding') or '-'}"
            if r.get("max_autonomy_days") is not None:
                note += f"; best achievable {r['max_autonomy_days']:.1f} days"
            out.append([r["case"]] + [None] * 14 + [note])
    fm = [None, NF["kw"], "0", NF["kw"], "0", "0", NF["usd"], NF["usd"], NF["lb"], NF["d1"], NF["d1"], "#,##0",
          "0", NF["d1"], "#,##0", None]
    a, b = table(ws, row, 1, hdr, out, fmts=fm, widths=[32, 9, 10, 11, 9, 10, 11, 12, 11, 10, 10, 12, 12, 11, 12, 60])
    for i, r in enumerate(rows):
        if not r.get("feasible"):
            ws.cell(a + i, 16).font = F_BAD
    return a, b


def _autonomy(wb, P):
    ws = wb.create_sheet("Autonomy")
    title(ws, "Fuel autonomy without resupply",
          "Cheapest design that carries the worst multi-week winter stretch for N days on one tank, re-optimized "
          "for each target. Flood events can cut road access.")
    rows = [dict(r, case=f"{r['target_days']} days") for r in P["ladder"]]
    a, b = _cases(ws, 4, rows, "Autonomy target")
    ch = BarChart()
    ch.add_data(Reference(ws, min_col=7, min_row=a - 1, max_row=b), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=a, max_row=b))
    color_series(ch.series[0], C["bat"])
    style_chart(ch, "Capital cost by autonomy target (blank = not achievable)", y_title="$", w=18, h=8, legend=None)
    ch.y_axis.number_format = '$#,##0,"k"'
    ws.add_chart(ch, f"A{b + 3}")


def _sensitivity(wb, P):
    ws = wb.create_sheet("Sensitivity")
    title(ws, "Building and occupant sensitivity",
          "Each row changes one input and re-runs the full sweep and optimization; sizes are the new optimum.")
    a, b = _cases(ws, 4, P["sensitivity"], "Case")
    for j in range(1, 17):
        ws.cell(a, j).fill = FILL_KEY
    ch = _hbar()
    ch.add_data(Reference(ws, min_col=7, min_row=a - 1, max_row=b), titles_from_data=True)
    ch.add_data(Reference(ws, min_col=8, min_row=a - 1, max_row=b), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=a, max_row=b))
    color_series(ch.series[0], C["bat"])
    color_series(ch.series[1], C["gen"])
    style_chart(ch, "Re-optimized cost by case (blank = no feasible design)", y_title="$", w=20, h=11)
    ch.x_axis.scaling.orientation = "maxMin"
    ch.y_axis.number_format = '$#,##0,"k"'
    ch.gapWidth = 40
    ws.add_chart(ch, f"A{b + 3}")

    M = P["matrix"]
    if M:
        r = b + 27
        put(ws, r, 1, "Re-optimized capital cost by envelope and occupancy", F_H2)
        envs = list(dict.fromkeys(m["envelope"] for m in M))
        occs = list(dict.fromkeys(m["occupants"] for m in M))
        names = {"leaky_1950s": "Leaky 1950s", "typical_1980s": "Typical 1980s", "typical_2010s": "Typical 2010s", "efficient_modern": "Efficient modern"}
        put(ws, r + 1, 1, "Envelope \\ Occupants", F_HEAD, fill=FILL_HEAD)
        for j, o in enumerate(occs):
            put(ws, r + 1, 2 + j, o, F_HEAD, fill=FILL_HEAD, align=Alignment(horizontal="center"))
        for i, e in enumerate(envs):
            put(ws, r + 2 + i, 1, names.get(e, e), F_BOLD)
            for j, o in enumerate(occs):
                rec = next((m for m in M if m["envelope"] == e and m["occupants"] == o), None)
                if rec and rec.get("feasible"):
                    put(ws, r + 2 + i, 2 + j, rec["capex_usd"], fmt=NF["usdk"], align=Alignment(horizontal="center"))
                else:
                    put(ws, r + 2 + i, 2 + j, "none", F_BAD, align=Alignment(horizontal="center"))
        rng = f"B{r + 2}:{COL(1 + len(occs))}{r + 1 + len(envs)}"
        ws.conditional_formatting.add(rng, ColorScaleRule(start_type="min", start_color="CDE2FB",
                                                          end_type="max", end_color="3987E5"))
        put(ws, r + 2 + len(envs), 1, "\"none\" = no towable design meets every limit for that combination.", F_NOTE)
    ws.freeze_panes = "B5"


def _annual(wb, P):
    ws = wb.create_sheet("Annual check")
    an = P["annual"]
    title(ws, "Any-day deployment check",
          "The recommended design deployed for 3 days starting on every day of the year.")
    R0 = 12
    rows = [[pd.Timestamp(an["start"][i]).to_pydatetime(), an["fuel_gpd"][i], an["t_in_min"][i], an["t_in_max"][i],
             an["t_out_mean"][i], an["unmet"][i], None, None] for i in range(len(an["start"]))]
    a, b = table(ws, R0, 1, ["Start date", "Fuel (gal/day)", "Coldest indoor (°F)", "Warmest indoor (°F)",
                             "Outdoor mean (°F)", "Unserved (kWh)", "Min limit (°F)", "Max limit (°F)"], rows,
                 fmts=["mmm d", NF["d2"], NF["f"], NF["f"], NF["f"], NF["d2"], NF["f"], NF["f"]],
                 widths=[12, 11, 12, 12, 12, 11, 10, 10])
    for rr in range(a, b + 1):
        ws.cell(rr, 7).value = "=T_MIN_F"
        ws.cell(rr, 8).value = "=T_MAX_F"
    put(ws, R0 - 1, 1, "Results by deployment start date", F_H2)
    put(ws, 4, 1, "Summary", F_H2)
    summ = [("Start dates tested", f"=COUNT(A{a}:A{b})", "0"),
            ("Highest fuel use (gal/day)", f"=MAX(B{a}:B{b})", NF["d1"]),
            ("Average fuel use (gal/day)", f"=AVERAGE(B{a}:B{b})", NF["d1"]),
            ("Coldest indoor (°F)", f"=MIN(C{a}:C{b})", NF["f"]),
            ("Start dates below minimum", f'=COUNTIF(C{a}:C{b},"<"&T_MIN_F)', "0"),
            ("Start dates with unserved load > 1 kWh", f'=COUNTIF(F{a}:F{b},">1")', "0")]
    for i, (lab, fml, fmt) in enumerate(summ):
        put(ws, 5 + i, 1, lab, border=B_BOTTOM)
        put(ws, 5 + i, 2, fml, fmt=fmt, border=B_BOTTOM)
    ws.column_dimensions["A"].width = 34
    lc = LineChart()
    lc.add_data(Reference(ws, min_col=2, min_row=a - 1, max_row=b), titles_from_data=True)
    color_series(lc.series[0], C["gen"], line=True, width=19050)
    lc.series[0].marker = Marker(symbol="none")
    lc.set_categories(Reference(ws, min_col=1, min_row=a, max_row=b))
    style_chart(lc, "Fuel use by deployment start date", y_title="gal/day", w=24, h=8, legend=None)
    lc.x_axis.number_format = "mmm"
    lc.x_axis.tickLblSkip = 30
    lc.x_axis.tickMarkSkip = 30
    ws.add_chart(lc, "J4")
    tc = LineChart()
    for col, hexc, wdt, dash in ((4, C["gen"], 15875, None), (3, C["bat"], 19050, None), (5, C["mute"], 9525, "sysDot"),
                                 (7, C["unmet"], 9525, "dash"), (8, C["gen"], 9525, "dash")):
        tc.add_data(Reference(ws, min_col=col, min_row=a - 1, max_row=b), titles_from_data=True)
        color_series(tc.series[-1], hexc, line=True, width=wdt, dash=dash)
        tc.series[-1].marker = Marker(symbol="none")
    tc.set_categories(Reference(ws, min_col=1, min_row=a, max_row=b))
    style_chart(tc, "Coldest and warmest indoor temperature by start date", y_title="°F", w=24, h=9)
    tc.x_axis.number_format = "mmm"
    tc.x_axis.tickLblSkip = 30
    tc.x_axis.tickMarkSkip = 30
    ws.add_chart(tc, "J21")
    ws.freeze_panes = ws.cell(R0 + 1, 1)


def _findings(P):
    f = []
    d = P["dispatch"]
    B = P["best"]
    if "cold_snap" in d:
        m = d["cold_snap"]["m"]
        ph_share = m.get("ph_heat", 0) / max(m.get("ph_heat", 0) + m["heat"], 1e-9)
        f.append(f"Heating sets the size. In the cold snap the propane heaters supply {ph_share:.0%} of the heat, burning "
                 f"{m.get('propane_gal', 0) / m['days']:.1f} gal of propane a day, while the generator burns "
                 f"{m['fuel_gal'] / m['days']:.1f} gal/day of diesel; the shelter bottoms out at {m['t_min']:.1f} °F.")
    ua = sorted(zip(P["ua"]["labels"], P["ua"]["w_k"]), key=lambda t: -t[1])
    tot = sum(v for _, v in ua)
    f.append(f"{ua[0][0]} ({ua[0][1] / tot:.0%}) and {ua[1][0].lower()} ({ua[1][1] / tot:.0%}) are the largest heat-loss "
             f"paths. A heat-recovery ventilator or a tighter building shrinks every component (Sensitivity sheet).")
    if B["pv_kw"] < 2:
        f.append(f"PV {'does not enter' if B['pv_kw'] < 0.05 else 'barely enters'} the optimum: with about {P['deploy_days']:.0f} deployment days a year the fuel saved "
                 f"does not repay the array. PV earns its place when the array is net-metered while parked, or when "
                 f"fuel autonomy matters more than cost.")
    else:
        f.append(f"The optimum carries {B['pv_kw']:.1f} kW of PV.")
    if "summer_heat" in d:
        m = d["summer_heat"]["m"]
        f.append(f"Summer is easy: indoor temperature peaks at {m['t_max']:.1f} °F with {m['fuel_gal'] / m['days']:.1f} "
                 f"gal/day of fuel.")
    no = [r for r in P["ladder"] if not r.get("feasible")]
    if no:
        best_aut = max((r.get("max_autonomy_days") or 0) for r in P["ladder"])
        f.append(f"One tank carries the worst winter stretch for at most {best_aut:.1f} days within the trailer payload; "
                 f"{' and '.join(str(r['target_days']) + ' days' for r in no)} need a fuel delivery or a lighter heating load.")
    inf = [r for r in P["sensitivity"] if not r.get("feasible")]
    if inf:
        f.append("No towable design works for " + " or ".join(
            f"{r['case'].lower()} (binding limit: {r.get('binding')})" for r in inf) + ".")
    return f


def _summary(wb, res, P, window_sheets):
    ws = wb["Summary"]
    B = P["best"]
    st = res["study"]
    title(ws, "MOD POD Microgrid Sizing",
          f"{P['site']['name']} · {P['source']} · {P['building']['area_ft2']:,.0f} ft² shelter, "
          f"{P['building']['occupants']} occupants, {P['building']['envelope']}")
    ws.column_dimensions["A"].width = 30
    for c, wd in zip("BCDEFGHIJK", [13, 9, 44, 12, 12, 12, 12, 12, 12, 12]):
        ws.column_dimensions[c].width = wd

    put(ws, 4, 1, "Recommended design", F_H2)
    header_row(ws, 5, 1, ["Component", "Size", "Unit", "Detail"])
    comp = [("PV array", B["pv_kw"], "kW", f"{B['pv_kw'] / 0.4:.0f} x 400 W modules, tilt {st.pv.tilt_deg:g}°", NF["kw"]),
            ("Battery (LFP)", B["bat_kwh"], "kWh", f"usable {B['bat_kwh'] * (1 - st.battery.soc_min):.1f} kWh", "0"),
            ("Diesel generator", B["gen_kw"], "kW", f"cycle charging {st.generator.soc_start:.0%}->{st.generator.soc_stop:.0%} SOC", NF["kw"]),
            ("Fuel tank", B["tank_l"], "L", None, "0"),
            ("Heat/cool units", B["hp_units"], "units", f"{P['hp_label']}, {B['hp_kw']:.1f} kW heat", "0"),
            ("Propane heaters", B.get("ph_kbtu", 0), "kBtu/h", f"{P['ph_label']}; {B.get('propane_cylinders', 0):.0f} x 100-lb cylinders on board", "0"),
            ("Inverter / charger", B["inverter_kw"], "kW", f"peak load {B['peak_kw']:.1f} kW", "0")]
    for i, (lab, v, u, det, fmt) in enumerate(comp):
        r = 6 + i
        put(ws, r, 1, lab, F_BOLD, border=B_BOTTOM)
        put(ws, r, 2, v, fmt=fmt, border=B_BOTTOM)
        put(ws, r, 3, u, F_NOTE, border=B_BOTTOM)
        put(ws, r, 4, det, border=B_BOTTOM)
    ws["D9"] = '=TEXT(B9/L_PER_GAL,"0")&" gal on board"'
    ws["D9"].font = F_BODY

    put(ws, 13, 1, "Outcomes", F_H2)
    header_row(ws, 14, 1, ["Metric", "Value", "Unit", "Note"])
    outs = [
        ("Equipment capital cost", B["capex_usd"], NF["usd"], "$", "Equipment + inverter + tank (planning costs)"),
        ("15-year lifecycle cost", B["lifecycle_usd"], NF["usd"], "$", "Capex + discounted fuel, generator O&M and carbon"),
        ("Payload", B["weight_kg"], "#,##0", "kg", "Energy equipment + fixed systems + full fuel tank"),
        ("Payload", "=B17/KG_PER_LB", NF["lb"], "lb", None),
        ("Payload margin", "=PAYLOAD_LB-B18", NF["lb"], "lb", "Trailer limit minus payload"),
        ("Worst 3-day diesel use", None, NF["d1"], "gal/day", "Highest of the 3-day design windows (linked below)"),
        ("Fuel autonomy, worst winter stretch", B.get("autonomy_d_endurance"), NF["d1"], "days", "On one tank, no resupply"),
        ("Worst 3-day propane use", B.get("propane_worst_gpd", 0), NF["d1"], "gal/day", "Propane heaters, worst 3-day window"),
        ("Expected diesel per deployment day", "=" + str(B["exp_fuel_l_per_day"]) + "/L_PER_GAL", NF["d1"], "gal/day",
         "Weighted across design windows"),
    ]
    for i, (lab, v, fmt, u, note) in enumerate(outs):
        r = 15 + i
        put(ws, r, 1, lab, F_BOLD, border=B_BOTTOM)
        put(ws, r, 2, v, fmt=fmt, border=B_BOTTOM)
        put(ws, r, 3, u, F_NOTE, border=B_BOTTOM)
        put(ws, r, 4, note, F_NOTE, border=B_BOTTOM)

    # per-window results, linked to each window sheet
    r = 25
    put(ws, r - 1, 1, "Results by design window (linked to each window's sheet)", F_H2)
    header_row(ws, r, 1, ["Design window", "Days", "", "Sheet", "Fuel (gal/day)", "Generator (h)", "Coldest (°F)",
                          "Warmest (°F)", "Unserved (kWh)", "PV used (kWh)", "Demand (kWh)"])
    three_day = []
    for i, (key, sname) in enumerate(window_sheets):
        rr = r + 1 + i
        ref = q(sname)
        put(ws, rr, 1, P["dispatch"][key]["label"], F_BOLD, border=B_BOTTOM)
        put(ws, rr, 2, f"={ref}B19", fmt="0", border=B_BOTTOM)
        c = put(ws, rr, 4, sname, F_LINK, border=B_BOTTOM)
        c.hyperlink = f"#{ref}A1"
        put(ws, rr, 5, f"={ref}B20", F_LINK, fmt=NF["d1"], border=B_BOTTOM)
        put(ws, rr, 6, f"={ref}B7", F_LINK, fmt="0", border=B_BOTTOM)
        put(ws, rr, 7, f"={ref}B14", F_LINK, fmt=NF["f"], border=B_BOTTOM)
        put(ws, rr, 8, f"={ref}B15", F_LINK, fmt=NF["f"], border=B_BOTTOM)
        put(ws, rr, 9, f"={ref}B10", F_LINK, fmt=NF["d1"], border=B_BOTTOM)
        put(ws, rr, 10, f"={ref}B11", F_LINK, fmt="#,##0", border=B_BOTTOM)
        put(ws, rr, 11, f"={ref}B9", F_LINK, fmt="#,##0", border=B_BOTTOM)
        if P["dispatch"][key]["m"]["days"] <= 3:
            three_day.append(f"E{rr}")
    ws["B20"] = f"=MAX({','.join(three_day)})" if three_day else None
    ws["B20"].number_format = NF["d1"]
    ws["B20"].font = F_BODY
    last = r + len(window_sheets)

    # chart: fuel per day by window
    ch = BarChart()
    ch.add_data(Reference(ws, min_col=5, min_row=r, max_row=last), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=r + 1, max_row=last))
    color_series(ch.series[0], C["gen"])
    style_chart(ch, "Fuel use by design window (gal/day)", y_title="gal/day", w=16, h=7.5, legend=None)
    ch.gapWidth = 60
    ws.add_chart(ch, "F4")

    # fuel needed
    fr = last + 3
    F = P.get("fuel") or []
    if F:
        put(ws, fr, 1, "Fuel needed by the recommended design (unlimited fuel, full requirement)", F_H2)
        fa, fb = table(ws, fr + 1, 1, ["Period", "Days", "", "Diesel (gal)", "Diesel (gal/day)", "Propane (gal)",
                                       "Propane (gal/day)", "Propane (lb)", "100-lb cylinders"],
                       [[f["period"], f["days"], None, f["diesel_gal"], f["diesel_gal_per_day"], f["propane_gal"],
                         f["propane_gal_per_day"], f["propane_lb"], f["propane_cylinders"]] for f in F],
                       fmts=[None, "0", None, NF["d1"], NF["d1"], NF["d1"], NF["d1"], "#,##0", "0"])
        fr = fb + 3

    # findings
    put(ws, fr, 1, "What the study found", F_H2)
    for i, t in enumerate(_findings(P)):
        ws.merge_cells(start_row=fr + 1 + i, start_column=1, end_row=fr + 1 + i, end_column=11)
        put(ws, fr + 1 + i, 1, f"• {t}", align=Alignment(wrap_text=True, vertical="top"))
        ws.row_dimensions[fr + 1 + i].height = 30

    # sheet index
    ir = fr + 9
    put(ws, ir, 1, "Sheets in this workbook", F_H2)
    idx = [(s, d) for s, d in [
        ("Gen & battery", "Generator x battery sizes: run time, starts, fuel, payload and margin"),
        ("Windows", "Window count and type: heat loss and re-optimized design"),
        ("Weather", "Daily weather year and design-window statistics"),
        ("Design sweep", "Sweep counts, failure reasons, cost-vs-fuel chart, sizing heat map"),
        ("Feasible designs", "Every design that met all limits, cheapest first"),
        ("Autonomy", "Days on one tank without resupply"),
        ("Sensitivity", "Re-optimized designs for envelope, occupancy and other changes"),
        ("Annual check", "Recommended design deployed on every day of the year"),
        ("Assumptions", "Constants, limits, heat-loss breakdown and every model parameter"),
        ("All designs", "Every simulated design, filterable"),
    ] if s in wb.sheetnames]
    idx = [(s, f"Hourly dispatch: {P['dispatch'][k]['label']}") for k, s in window_sheets] + idx
    for i, (s, d) in enumerate(idx):
        c = put(ws, ir + 1 + i, 1, s, Font(name=FONT, size=10, color="0563C1", underline="single"))
        c.hyperlink = f"#{q(s)}A1"
        put(ws, ir + 1 + i, 4, d, F_NOTE)
    put(ws, ir + len(idx) + 2, 1, "Formatting: blue = input constant (Assumptions); green = linked from another sheet; "
                                  "black = value from the simulation or a formula on this sheet.", F_NOTE)


def _gen_battery(wb, res, P):
    G = P.get("gen_bat")
    if not G:
        return None
    ws = wb.create_sheet("Gen & battery")
    f = G["fixed"]
    title(ws, "Generator and battery options",
          f"Every generator x battery size simulated through the design windows with PV {f['pv']:.1f} kW, heat pumps "
          f"{f['hp']:.1f} kW and a {f['tank_gal']:.0f} gal tank held at the recommended design. "
          "Bigger batteries cut generator run time but add weight.")
    gens, bats = G["gens"], G["bats"]
    gb = res["gen_bat"]
    views = G["views"]

    # ---- options that meet every limit
    put(ws, 4, 1, "Options that meet every limit (lightest first; green row = recommended)", F_H2)
    hdr = ["Generator (kW)", "Battery (kWh)"] + [f"Run h/day: {lab}" for _, lab in views] + \
          ["Starts/day (worst)", "Fuel gal/day (worst)", "Payload (lb)", "Margin (lb)", "Capex ($)", "Lifecycle ($)",
           "Autonomy (days)"]
    rows = []
    for o in G["options"]:
        rows.append([o["gen_kw"], o["bat_kwh"]] + [o["run"][k] for k, _ in views] +
                    [o["starts"].get("worst"), o["fuel"].get("worst"), o["weight_lb"], None, o["capex"], o["lifecycle"],
                     o.get("autonomy")])
    nv = len(views)
    fm = [NF["kw"], "0"] + [NF["d1"]] * nv + [NF["d1"], NF["d1"], NF["lb"], NF["lb"], NF["usd"], NF["usd"], NF["d1"]]
    a, b = table(ws, 5, 1, hdr, rows, fmts=fm, widths=[11, 10] + [12] * nv + [11, 11, 11, 10, 11, 12, 11])
    mcol = 2 + nv + 4      # margin column
    for i, o in enumerate(G["options"]):
        r = a + i
        ws.cell(r, mcol).value = f"=PAYLOAD_LB-{COL(mcol - 1)}{r}"
        if o["best"]:
            for j in range(1, len(hdr) + 1):
                ws.cell(r, j).fill = FILL_KEY
                ws.cell(r, j).font = F_BOLD
    end_opts = max(b, a)

    # ---- matrices
    def matrix(r0, label, z, fmt, colors, note=None):
        put(ws, r0, 1, label, F_H2)
        if note:
            put(ws, r0 + 1, 1, note, F_NOTE)
        hr = r0 + 2
        put(ws, hr, 1, "Battery (kWh) \\ Generator (kW)", F_HEAD, fill=FILL_HEAD)
        for j, g in enumerate(gens):
            put(ws, hr, 2 + j, g, F_HEAD, fmt="0.#", fill=FILL_HEAD, align=Alignment(horizontal="center"))
        for i, bv in enumerate(bats):
            put(ws, hr + 1 + i, 1, bv, F_BOLD, fmt="0")
            for j in range(len(gens)):
                c = put(ws, hr + 1 + i, 2 + j, z[i][j], fmt=fmt, align=Alignment(horizontal="center"))
                if G["feasible"][i][j]:
                    c.border = Border(left=Side(style="medium", color=INK), right=Side(style="medium", color=INK),
                                      top=Side(style="medium", color=INK), bottom=Side(style="medium", color=INK))
                    c.font = F_BOLD
        bi, bj = bats.index(G["best"]["bat"]), gens.index(G["best"]["gen"])
        cb = ws.cell(hr + 1 + bi, 2 + bj)
        cb.border = Border(left=Side(style="thick", color=C["gen"]), right=Side(style="thick", color=C["gen"]),
                           top=Side(style="thick", color=C["gen"]), bottom=Side(style="thick", color=C["gen"]))
        rng = f"B{hr + 1}:{COL(1 + len(gens))}{hr + len(bats)}"
        ws.conditional_formatting.add(rng, ColorScaleRule(start_type="min", start_color=colors[0],
                                                          end_type="max", end_color=colors[1]))
        return hr + len(bats) + 2

    note = "Bold outlined cells meet every limit; orange outline = recommended design."
    r = end_opts + 3
    first_matrix = r
    for k, lab in views:
        r = matrix(r, f"Generator run time, hours per day: {lab}", G["run"][k], NF["d1"], ("FDE3D6", "D9662F"), note)
    r = matrix(r, "Generator starts per day: worst 3-day window", G["starts"]["worst"], NF["d1"], ("FDE3D6", "D9662F"), note)
    r = matrix(r, "Fuel use, gal/day: worst 3-day window", G["fuel"]["worst"], NF["d1"], ("FDE3D6", "D9662F"), note)
    wt_r0 = r
    r = matrix(r, "Trailer payload (lb), all equipment + full fuel tank", G["weight_lb"], NF["lb"], ("CDE2FB", "1C5CAB"),
               note + " Limit is PAYLOAD_LB on the Assumptions sheet.")
    # payload margin matrix as live formulas against the payload matrix
    put(ws, r, 1, "Payload margin (lb) = trailer limit - payload (formula; negative = over the limit)", F_H2)
    hr = r + 2
    put(ws, hr, 1, "Battery (kWh) \\ Generator (kW)", F_HEAD, fill=FILL_HEAD)
    for j, g in enumerate(gens):
        put(ws, hr, 2 + j, g, F_HEAD, fmt="0.#", fill=FILL_HEAD, align=Alignment(horizontal="center"))
    wt_hr = wt_r0 + 2
    for i, bv in enumerate(bats):
        put(ws, hr + 1 + i, 1, bv, F_BOLD, fmt="0")
        for j in range(len(gens)):
            put(ws, hr + 1 + i, 2 + j, f"=PAYLOAD_LB-{COL(2 + j)}{wt_hr + 1 + i}", fmt="#,##0;[Red]-#,##0",
                align=Alignment(horizontal="center"))
    r = hr + len(bats) + 3

    # ---- chart data: run time vs battery per generator (worst window) + payload by component
    put(ws, r, 1, "Chart data", F_H2)
    cd = r + 1
    cand = [g for g in gens if g > 0]
    bi = cand.index(G["best"]["gen"]) if G["best"]["gen"] in cand else 0
    pick = cand[max(0, bi - 3): max(0, bi - 3) + 6]
    header_row(ws, cd, 1, ["Battery (kWh)"] + [f"{g:g} kW generator" for g in pick])
    for i, bv in enumerate(bats):
        put(ws, cd + 1 + i, 1, bv, fmt="0")
        for k, g in enumerate(pick):
            put(ws, cd + 1 + i, 2 + k, G["run"]["worst"][i][gens.index(g)], fmt=NF["d1"])
    lc = LineChart()
    pal = [C["bat"], C["gen"], C["ev"], C["pv"], "E87BA4", "008300"]
    for k in range(len(pick)):
        lc.add_data(Reference(ws, min_col=2 + k, min_row=cd, max_row=cd + len(bats)), titles_from_data=True)
        color_series(lc.series[-1], pal[k % len(pal)], line=True, width=22225)
        lc.series[-1].marker = Marker(symbol="circle", size=6)
        lc.series[-1].marker.graphicalProperties.solidFill = pal[k % len(pal)]
    lc.set_categories(Reference(ws, min_col=1, min_row=cd + 1, max_row=cd + len(bats)))
    style_chart(lc, "Generator run time vs battery size (worst 3-day window)", x_title="Battery (kWh)",
                y_title="h/day", w=18, h=9)
    lc.y_axis.scaling.min = 0
    lc.y_axis.scaling.max = 24
    ws.add_chart(lc, f"{COL(len(hdr) + 2)}4")

    comps = list(P["gen_bat"]["best_wt"].keys())
    opts = G["options"][:12]
    pr = cd + len(bats) + 3
    header_row(ws, pr, 1, ["Option"] + comps + ["Total (lb)"])
    for i, o in enumerate(opts):
        rr = pr + 1 + i
        put(ws, rr, 1, f"{o['gen_kw']:g} kW + {o['bat_kwh']:g} kWh" + (" (rec.)" if o["best"] else ""))
        for k, cpt in enumerate(comps):
            put(ws, rr, 2 + k, o["wt"][cpt], fmt=NF["lb"])
        put(ws, rr, 2 + len(comps), f"=SUM(B{rr}:{COL(1 + len(comps))}{rr})", fmt=NF["lb"])
    if opts:
        bc = BarChart()
        bc.type = "bar"
        bc.grouping = "stacked"
        bc.overlap = 100
        ccol = {"PV array": C["pv"], "Battery": C["bat"], "Inverter": C["mute"], "Generator": C["gen"],
                "Fuel + tank": "E87BA4", "Heat pumps": ACCENT, "Propane heaters": "9C3FB0", "Propane cylinders": "F0B400",
                "Fixed pod systems": "B4BEBA"}
        for k, cpt in enumerate(comps):
            bc.add_data(Reference(ws, min_col=2 + k, min_row=pr, max_row=pr + len(opts)), titles_from_data=True)
            color_series(bc.series[-1], ccol.get(cpt, C["mute"]))
        bc.set_categories(Reference(ws, min_col=1, min_row=pr + 1, max_row=pr + len(opts)))
        style_chart(bc, "Payload by component (lb), options that meet every limit", y_title="lb", w=18, h=9)
        bc.x_axis.scaling.orientation = "maxMin"
        bc.gapWidth = 40
        ws.add_chart(bc, f"{COL(len(hdr) + 2)}23")
    ws.freeze_panes = "C6"
    return ws


def _windows(wb, res, P):
    W = P.get("windows_study")
    if not W:
        return None
    ws = wb.create_sheet("Windows")
    w = P["win"]
    title(ws, "Window count and type",
          f"Base building: {w['count']} windows, {w['area_ft2']:,.0f} ft2 ({w['wwr']:.0%} of wall), {w['name']}, "
          f"U-{w['u_ip']:.2f}, SHGC {w['shgc']:.2f}. Each row re-runs the full sweep and optimization. "
          "Inputs: building.window_count, window_width_m, window_height_m, window_type, window_u_ip, window_shgc, "
          "window_facade_split.")
    hdr = ["Window type", "Count", "Area (ft²)", "U (Btu/h·ft²·°F)", "SHGC", "Window UA (W/K)", "Total UA (W/K)",
           "Design heat loss (kW)", "Generator (kW)", "Battery (kWh)", "Heat pumps (kW)", "Cold-snap run (h/day)",
           "Fuel (gal/day)", "Payload (lb)", "Capex ($)", "Lifecycle ($)", "Note"]
    rows = []
    for r in W:
        ok = r.get("feasible")
        rows.append([r["window_name"], r["window_count"], r["window_area_ft2"], r["window_u_ip"], r["window_shgc"],
                     r["window_ua_w_k"], r["ua_total_w_k"], r["design_heat_loss_kw"],
                     r.get("gen_kw") if ok else None, r.get("bat_kwh") if ok else None, r.get("hp_kw") if ok else None,
                     r.get("run_h_day_cold") if ok else None,
                     (r.get("fuel_l_per_day") or 0) / L_PER_GAL if ok else None,
                     (r.get("weight_kg") or 0) / KG_PER_LB if ok else None,
                     r.get("capex_usd") if ok else None, r.get("lifecycle_usd") if ok else None,
                     None if ok else f"No feasible design. Binding limit: {r.get('binding')}"])
    fm = [None, "0", "#,##0", "0.00", "0.00", "0", "0", NF["d1"], NF["kw"], "0", NF["kw"], NF["d1"], NF["d1"], NF["lb"],
          NF["usd"], NF["usd"], None]
    a, b = table(ws, 4, 1, hdr, rows, fmts=fm, widths=[28, 7, 9, 10, 7, 10, 10, 11, 11, 10, 11, 11, 10, 11, 11, 12, 50])
    for i, r in enumerate(W):
        if r["window_name"] == w["name"] and r["window_count"] == w["count"]:
            for j in range(1, len(hdr) + 1):
                ws.cell(a + i, j).fill = FILL_KEY
        if not r.get("feasible"):
            ws.cell(a + i, 17).font = F_BAD
    # chart data: design heat loss by type for each count
    types = list(dict.fromkeys(r["window_name"] for r in W))
    counts = list(dict.fromkeys(r["window_count"] for r in W))
    cr = b + 3
    put(ws, cr, 1, "Design heat loss at -13 °F by window type and count (kW)", F_H2)
    header_row(ws, cr + 1, 1, ["Window type"] + [f"{c} windows" for c in counts])
    for i, t in enumerate(types):
        put(ws, cr + 2 + i, 1, t)
        for j, c in enumerate(counts):
            rec = next(r for r in W if r["window_name"] == t and r["window_count"] == c)
            put(ws, cr + 2 + i, 2 + j, rec["design_heat_loss_kw"], fmt=NF["d1"])
    ch = BarChart()
    pal = [C["bat"], C["gen"], C["ev"], C["pv"]]
    for j in range(len(counts)):
        ch.add_data(Reference(ws, min_col=2 + j, min_row=cr + 1, max_row=cr + 1 + len(types)), titles_from_data=True)
        color_series(ch.series[-1], pal[j % 4])
    ch.set_categories(Reference(ws, min_col=1, min_row=cr + 2, max_row=cr + 1 + len(types)))
    style_chart(ch, "Design heat loss by window type and count", y_title="kW", w=18, h=8.5)
    ws.add_chart(ch, f"A{cr + 3 + len(types)}")
    # capex matrix
    mr = cr + 3 + len(types)
    mc = 12
    put(ws, mr, mc, "Re-optimized capital cost ($)", F_H2)
    put(ws, mr + 1, mc, "Window type \\ Count", F_HEAD, fill=FILL_HEAD)
    for j, c in enumerate(counts):
        put(ws, mr + 1, mc + 1 + j, c, F_HEAD, fill=FILL_HEAD, align=Alignment(horizontal="center"))
    for i, t in enumerate(types):
        put(ws, mr + 2 + i, mc, t, F_BOLD)
        for j, c in enumerate(counts):
            rec = next(r for r in W if r["window_name"] == t and r["window_count"] == c)
            if rec.get("feasible"):
                put(ws, mr + 2 + i, mc + 1 + j, rec["capex_usd"], fmt=NF["usdk"], align=Alignment(horizontal="center"))
            else:
                put(ws, mr + 2 + i, mc + 1 + j, "none", F_BAD, align=Alignment(horizontal="center"))
    ws.conditional_formatting.add(f"{COL(mc + 1)}{mr + 2}:{COL(mc + len(counts))}{mr + 1 + len(types)}",
                                  ColorScaleRule(start_type="min", start_color="CDE2FB", end_type="max", end_color="3987E5"))
    ws.freeze_panes = "B5"


# ---------------------------------------------------------------------------
def write_excel(res: dict, path) -> str:
    P = build_payload(res)
    wb = Workbook()
    try:  # Arial as the workbook default font (cells written without an explicit font)
        wb._fonts[0] = Font(name=FONT, size=10)
        wb._named_styles["Normal"].font = Font(name=FONT, size=10)
    except Exception:
        pass
    ws = wb.active
    ws.title = "Summary"
    window_sheets = []
    for key, d in P["dispatch"].items():
        w = next((x for x in P["windows"] if x["key"] == key), None)
        desc = f"{w['desc']}. {w['start']} to {w['end']}." if w else ""
        window_sheets.append((key, _window_sheet(wb, key, d, desc)))
    _weather(wb, res, P)
    alld = None
    _feasible(wb, res)
    _gen_battery(wb, res, P)
    _windows(wb, res, P)
    _autonomy(wb, P)
    if P["sensitivity"]:
        _sensitivity(wb, P)
    _annual(wb, P)
    _assumptions(wb, res, P)
    alld = _all_designs(wb, res)
    _sweep(wb, res, P, alld)
    _summary(wb, res, P, window_sheets)
    # sheet order: Summary, windows, Weather, Design sweep, Feasible, Autonomy, Sensitivity, Annual, Assumptions, All
    order = ["Summary"] + [s for _, s in window_sheets] + ["Gen & battery", "Weather", "Design sweep",
                                                           "Feasible designs", "Windows", "Autonomy",
                                                           "Sensitivity", "Annual check", "Assumptions", "All designs"]
    wb._sheets = [wb[s] for s in order if s in wb.sheetnames]
    wb.active = 0
    tabcol = {"Summary": ACCENT, "Assumptions": "0000FF", "All designs": "7D8884", "Gen & battery": C["gen"]}
    for s in wb.worksheets:
        s.sheet_properties.tabColor = tabcol.get(s.title, "2A78D6" if s.title in [x for _, x in window_sheets] else "B4BEBA")
    wb.calculation.fullCalcOnLoad = True
    wb.save(path)
    return str(path)
