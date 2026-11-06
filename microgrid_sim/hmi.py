"""HMI-style HTML replay of a one-week microgrid simulation (self-contained, works offline)."""
from __future__ import annotations

import json
from pathlib import Path


def write_hmi(payload: dict, path, standalone: bool = True) -> str:
    html = TEMPLATE.replace("/*__DATA__*/null", json.dumps(payload, separators=(",", ":"), default=float))
    if standalone:
        html = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n</head>\n<body>\n'
                + html + "\n</body>\n</html>\n")
    Path(path).write_text(html, encoding="utf-8")
    return str(path)


TEMPLATE = r"""<title>MOD POD Microgrid HMI</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
/* Layout: ISA-101 style operator screen. Header bar (condition tabs + sim clock) -> alarm strip ->
   one-line diagram beside a faceplate -> transport/scrubber -> four synced trends -> event log + week totals.
   Single deliberate light-grey look: grey equipment, colour reserved for energy flows and abnormal states. */
:root{
  color-scheme: light;
  --bg:#d3d7da; --panel:#e3e6e8; --panel2:#eef0f1; --eq:#f3f4f5; --line:#9aa1a8; --edge:#6f777e;
  --ink:#1c2126; --ink2:#46505a; --mute:#69737c;
  --pv:#b8860b; --bat:#2f6db5; --gen:#7b4f2c; --ev:#2e8b57; --elec:#5b4aa2; --heat:#d2541c; --cool:#1f8fa8;
  --alarm:#c62828; --warn:#f0b400; --ok:#2e7d32;
  --f-ui:"Segoe UI","Roboto","Helvetica Neue",Arial,system-ui,sans-serif;
  --f-num:ui-monospace,"SF Mono","Cascadia Mono",Consolas,"Roboto Mono",monospace;
}
*{box-sizing:border-box}
html,body{background:var(--bg)}
body{margin:0;color:var(--ink);font:14px/1.4 var(--f-ui)}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:10;background:#2b3238}
.num{font-family:var(--f-num);font-variant-numeric:tabular-nums}
.bar{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:#2b3238;color:#e8ecef;
  padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:10px}
.bar-in{max-width:1400px;margin:0 auto;display:flex;flex-wrap:wrap;align-items:center;gap:10px 24px}
.brand{display:grid;gap:0}
.brand .eyebrow{font:600 11px/1.2 var(--f-ui);letter-spacing:.08em;text-transform:uppercase;color:#9fb0bd}
.brand h1{margin:0;font:600 19px/1.2 var(--f-ui);letter-spacing:.01em}
.conds{display:flex;flex-wrap:wrap;gap:4px}
.conds button{font:600 13px var(--f-ui);color:#cfd8de;background:#3a434a;border:1px solid #4d575f;padding:7px 14px;border-radius:3px;cursor:pointer}
.conds button[aria-selected="true"]{background:#e8ecef;color:#1c2126;border-color:#e8ecef}
.conds button:focus-visible,.tbtn:focus-visible,.eqhit:focus-visible{outline:2px solid #6fb3ff;outline-offset:2px}
.clock{margin-left:auto;display:flex;align-items:baseline;gap:10px;color:#cfd8de}
.clock b{font:600 22px var(--f-num);color:#fff}
.wrap{max-width:1400px;margin:0 auto;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:12px 40px;display:grid;gap:12px}
.alarmbar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-height:34px;padding:5px 10px;background:var(--panel);border:1px solid var(--line);border-radius:4px}
.alarmbar .lbl{font:600 11px var(--f-ui);letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin-right:6px}
.chip{display:inline-flex;align-items:center;gap:6px;font:600 12px var(--f-ui);padding:3px 9px;border-radius:3px;border:1px solid transparent}
.chip.alarm{background:var(--alarm);color:#fff}
.chip.warn{background:var(--warn);color:#1c2126}
.chip.info{background:var(--panel2);color:var(--ink2);border-color:var(--line)}
.chip .ic{font:700 11px var(--f-ui)}
.cond-desc{font-size:13px;color:var(--ink2);margin:0}
.grid{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:12px}
@media (max-width:1050px){.grid{grid-template-columns:minmax(0,1fr)}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:4px;min-width:0}
.ph{display:flex;justify-content:space-between;gap:12px;align-items:baseline;padding:8px 12px;border-bottom:1px solid var(--line);
  font:600 12px var(--f-ui);letter-spacing:.06em;text-transform:uppercase;color:var(--ink2)}
.ph span{font-weight:400;text-transform:none;letter-spacing:0;color:var(--mute)}
.svgwrap{overflow-x:auto}
#sld{display:block;width:100%;min-width:640px;height:auto}
/* one-line diagram */
.eqbox{fill:var(--eq);stroke:var(--edge);stroke-width:1.2}
.eqhit{cursor:pointer}
.eqhit:hover .eqbox{stroke:var(--ink);stroke-width:2}
.eqhit.sel .eqbox{stroke:#1d5fa6;stroke-width:2.5}
.t-name{font:600 12px var(--f-ui);letter-spacing:.06em;fill:var(--ink2)}
.t-big{font:600 22px var(--f-num);fill:var(--ink)}
.t-mid{font:500 13px var(--f-num);fill:var(--ink)}
.t-sm{font:400 11.5px var(--f-ui);fill:var(--mute)}
.t-flow{font:600 12.5px var(--f-num)}
.t-st{font:700 10.5px var(--f-ui);letter-spacing:.06em}
.track{fill:none;stroke:#bfc5ca;stroke-width:10;stroke-linecap:round}
.flow{fill:none;stroke-linecap:round;stroke-dasharray:10 9;animation:dash 1s linear infinite}
.flow.idle{stroke:#a9b0b6 !important;stroke-width:2 !important;stroke-dasharray:3 6;animation:none;opacity:.7}
@keyframes dash{to{stroke-dashoffset:-38}}
@media (prefers-reduced-motion:reduce){.flow{animation:none}}
.bus{stroke:#2b3238;stroke-width:7;stroke-linecap:round}
/* faceplate */
.face{display:flex;flex-direction:column}
.fp-body{padding:10px 12px;display:grid;gap:12px}
.fp-title{display:flex;justify-content:space-between;align-items:center;gap:8px}
.fp-title h2{margin:0;font:600 17px var(--f-ui)}
.fp-tab{width:100%;border-collapse:collapse;font-size:13px}
.fp-tab td{padding:5px 0;border-bottom:1px solid #d3d7da}
.fp-tab td:last-child{text-align:right;font-family:var(--f-num);font-variant-numeric:tabular-nums}
.fp-tab tr.h td{font:600 11px var(--f-ui);letter-spacing:.06em;text-transform:uppercase;color:var(--mute);border-bottom:1px solid var(--line);padding-top:10px}
.fp-pick{display:flex;flex-wrap:wrap;gap:4px}
.fp-pick button{font:12px var(--f-ui);background:var(--panel2);border:1px solid var(--line);border-radius:3px;padding:3px 8px;cursor:pointer;color:var(--ink2)}
.fp-pick button[aria-pressed="true"]{background:#2b3238;color:#fff;border-color:#2b3238}
/* transport */
.transport{display:grid;gap:8px;padding:10px 12px}
.trow{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px}
.tbtn{font:600 13px var(--f-ui);background:var(--eq);border:1px solid var(--edge);border-radius:3px;padding:6px 12px;cursor:pointer;color:var(--ink);min-width:44px}
.tbtn.play{background:#2b3238;color:#fff;border-color:#2b3238;min-width:84px}
select{font:13px var(--f-ui);padding:5px 6px;border:1px solid var(--edge);border-radius:3px;background:var(--eq);color:var(--ink)}
.scrub{position:relative;padding-top:12px}
.scrub input{width:100%;margin:0;accent-color:#2b3238}
.ticks{position:absolute;left:8px;right:8px;top:0;height:10px}
.ticks i{position:absolute;top:0;width:2px;height:10px;border-radius:1px}
.ticks i.alarm{background:var(--alarm)}.ticks i.warn{background:#c79400}
.days{display:flex;justify-content:space-between;font-size:11.5px;color:var(--mute);padding:0 4px}
.legend-k{font-size:12px;color:var(--mute)}
/* trends */
.trends{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,560px),1fr));gap:12px;padding:12px}
.trend h3{margin:0 0 4px;font:600 13px var(--f-ui);color:var(--ink)}
.lg{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px;color:var(--ink2);margin-bottom:4px}
.lg i{display:inline-block;width:14px;height:8px;border-radius:2px;margin-right:5px;vertical-align:0}
.lg i.ln{height:3px;vertical-align:2px}
canvas{display:block;width:100%;height:170px;background:var(--panel2);border:1px solid #c5cacf;border-radius:3px;cursor:crosshair}
/* bottom */
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,480px),1fr));gap:12px}
.evlist{max-height:420px;overflow:auto}
table.ev{width:100%;border-collapse:collapse;font-size:13px}
table.ev td{padding:6px 10px;border-bottom:1px solid #d3d7da;vertical-align:top}
table.ev td.tm{white-space:nowrap;font-family:var(--f-num);color:var(--ink2)}
table.ev tr{cursor:pointer}
table.ev tr:hover td{background:var(--panel2)}
table.ev tr.future td{opacity:.45}
table.ev tr.now td{background:#dfe9f5}
.sev{display:inline-block;min-width:52px;text-align:center;font:700 10.5px var(--f-ui);letter-spacing:.05em;padding:2px 6px;border-radius:2px}
.sev.alarm{background:var(--alarm);color:#fff}.sev.warn{background:var(--warn);color:#1c2126}.sev.info{background:#c9ced3;color:var(--ink2)}
table.kpi{width:100%;border-collapse:collapse;font-size:13px}
table.kpi td,table.kpi th{padding:6px 12px;border-bottom:1px solid #d3d7da;text-align:left}
table.kpi th{font:600 11px var(--f-ui);letter-spacing:.06em;text-transform:uppercase;color:var(--mute)}
table.kpi td.v{text-align:right;font-family:var(--f-num);font-variant-numeric:tabular-nums;white-space:nowrap}
table.kpi tr.bad td.v{color:var(--alarm);font-weight:700}
.note{font-size:12px;color:var(--mute);padding:8px 12px;margin:0}
.tbl{overflow-x:auto}
</style>

<header class="bar"><div class="bar-in">
  <div class="brand"><span class="eyebrow" id="site"></span><h1>MOD POD Microgrid HMI</h1></div>
  <div class="conds" role="tablist" id="conds"></div>
  <div class="clock"><span id="clk-day"></span><b id="clk-time" class="num"></b><span id="clk-h" class="num"></span></div>
</div></header>

<div class="wrap">
  <div class="alarmbar" id="alarmbar" aria-live="polite"></div>
  <p class="cond-desc" id="cdesc"></p>

  <div class="grid">
    <section class="panel">
      <div class="ph">One-line diagram <span id="designline"></span></div>
      <div class="svgwrap">
      <svg id="sld" viewBox="0 0 1000 660" role="img" aria-label="One-line diagram of the pod microgrid with live power flows">
        <!-- tracks -->
        <path class="track" d="M125 150V230"/><path class="track" d="M365 150V230"/><path class="track" d="M605 150V230"/><path class="track" d="M895 150V230"/>
        <path class="track" d="M725 95H700"/>
        <path class="track" d="M135 230V310"/><path class="track" d="M365 230V310"/><path class="track" d="M605 230V310"/><path class="track" d="M860 230V310"/>
        <path class="track" d="M135 410V490"/><path class="track" d="M590 560H680"/>
        <!-- flows -->
        <path id="f-pv" class="flow" d="M125 150V230" stroke="var(--pv)"/>
        <path id="f-bat" class="flow" d="M365 150V230" stroke="var(--bat)"/>
        <path id="f-gen" class="flow" d="M605 150V230" stroke="var(--gen)"/>
        <path id="f-ev" class="flow" d="M895 150V230" stroke="var(--ev)"/>
        <path id="f-fuel" class="flow" d="M725 95H700" stroke="var(--gen)"/>
        <path id="f-hp" class="flow" d="M135 230V310" stroke="var(--elec)"/>
        <path id="f-crit" class="flow" d="M365 230V310" stroke="var(--elec)"/>
        <path id="f-ess" class="flow" d="M605 230V310" stroke="var(--elec)"/>
        <path id="f-disc" class="flow" d="M860 230V310" stroke="var(--elec)"/>
        <path id="f-heat" class="flow" d="M135 410V490" stroke="var(--heat)"/>
        <path id="f-loss" class="flow" d="M590 560H680" stroke="var(--heat)"/>
        <!-- bus -->
        <line class="bus" x1="40" y1="230" x2="960" y2="230"/>
        <text class="t-name" x="160" y="221">AC BUS · 240 V</text>
        <text class="t-mid" x="956" y="221" text-anchor="end" id="bus-kw"></text>
        <text class="t-flow" x="133" y="196" id="v-pv" fill="var(--pv)"></text>
        <text class="t-flow" x="373" y="196" id="v-bat" fill="var(--bat)"></text>
        <text class="t-flow" x="613" y="196" id="v-gen" fill="var(--gen)"></text>
        <text class="t-flow" x="887" y="196" id="v-ev" fill="var(--ev)" text-anchor="end"></text>
        <text class="t-flow" x="143" y="276" id="v-hp" fill="var(--elec)"></text>
        <text class="t-flow" x="373" y="276" id="v-crit" fill="var(--elec)"></text>
        <text class="t-flow" x="613" y="276" id="v-ess" fill="var(--elec)"></text>
        <text class="t-flow" x="868" y="276" id="v-disc" fill="var(--elec)"></text>
        <text class="t-flow" x="143" y="456" id="v-heat" fill="var(--heat)"></text>
        <text class="t-flow" x="635" y="550" id="v-loss" fill="var(--heat)" text-anchor="middle"></text>

        <!-- PV -->
        <g class="eqhit" data-eq="pv" tabindex="0" role="button" aria-label="PV array details">
          <rect class="eqbox" x="30" y="30" width="190" height="120" rx="6"/>
          <text class="t-name" x="44" y="52">PV ARRAY</text>
          <text class="t-st" x="206" y="52" text-anchor="end" id="pv-st"></text>
          <text class="t-big" x="44" y="86" id="pv-kw"></text>
          <text class="t-sm" x="44" y="108" id="pv-sub"></text>
          <text class="t-sm" x="44" y="126" id="pv-sub2"></text>
          <text class="t-sm" x="44" y="142" id="pv-rate"></text>
        </g>
        <!-- Battery -->
        <g class="eqhit" data-eq="bat" tabindex="0" role="button" aria-label="Battery details">
          <rect class="eqbox" x="270" y="30" width="190" height="120" rx="6"/>
          <text class="t-name" x="284" y="52">BATTERY</text>
          <text class="t-big" x="284" y="86" id="bat-soc"></text>
          <text class="t-mid" x="284" y="108" id="bat-kw"></text>
          <text class="t-sm" x="284" y="126" id="bat-kwh"></text>
          <text class="t-st" x="284" y="142" id="bat-st"></text>
          <rect x="414" y="44" width="30" height="94" rx="3" fill="#fff" stroke="var(--edge)"/>
          <rect x="424" y="39" width="10" height="5" fill="var(--edge)"/>
          <rect id="bat-fill" x="417" y="47" width="24" height="88" fill="var(--bat)"/>
          <line id="bat-floor" x1="414" x2="444" y1="128" y2="128" stroke="var(--alarm)" stroke-dasharray="3 2"/>
        </g>
        <!-- Generator -->
        <g class="eqhit" data-eq="gen" tabindex="0" role="button" aria-label="Generator details">
          <rect class="eqbox" x="510" y="30" width="190" height="120" rx="6"/>
          <text class="t-name" x="524" y="52">DIESEL GENERATOR</text>
          <circle id="gen-lamp" cx="684" cy="48" r="7" stroke="var(--edge)"/>
          <text class="t-big" x="524" y="86" id="gen-kw"></text>
          <text class="t-sm" x="524" y="108" id="gen-sub"></text>
          <text class="t-sm" x="524" y="126" id="gen-sub2"></text>
          <text class="t-st" x="524" y="142" id="gen-st"></text>
        </g>
        <!-- Fuel tank -->
        <g class="eqhit" data-eq="tank" tabindex="0" role="button" aria-label="Fuel tank details">
          <rect class="eqbox" x="725" y="30" width="72" height="120" rx="6"/>
          <text class="t-name" x="761" y="48" text-anchor="middle">FUEL</text>
          <rect x="743" y="56" width="36" height="66" rx="3" fill="#fff" stroke="var(--edge)"/>
          <rect id="tank-fill" x="746" y="59" width="30" height="60" fill="var(--gen)" fill-opacity=".75"/>
          <text class="t-mid" x="761" y="141" text-anchor="middle" id="tank-gal"></text>
        </g>
        <!-- EV -->
        <g class="eqhit" data-eq="ev" tabindex="0" role="button" aria-label="Electric pickup details">
          <rect class="eqbox" x="820" y="30" width="150" height="120" rx="6"/>
          <text class="t-name" x="834" y="52">EV PICKUP (V2L)</text>
          <text class="t-big" x="834" y="86" id="ev-kw"></text>
          <text class="t-sm" x="834" y="108" id="ev-sub"></text>
          <text class="t-st" x="834" y="142" id="ev-st"></text>
        </g>
        <!-- loads -->
        <g class="eqhit" data-eq="hp" tabindex="0" role="button" aria-label="Heat pump details">
          <rect class="eqbox" x="30" y="310" width="210" height="100" rx="6"/>
          <text class="t-name" x="44" y="331">HEAT PUMPS</text>
          <text class="t-st" x="226" y="331" text-anchor="end" id="hp-st"></text>
          <text class="t-big" x="44" y="362" id="hp-kw"></text>
          <text class="t-sm" x="44" y="382" id="hp-sub"></text>
          <text class="t-sm" x="44" y="399" id="hp-sub2"></text>
        </g>
        <g class="eqhit" data-eq="crit" tabindex="0" role="button" aria-label="Critical loads details">
          <rect class="eqbox" x="270" y="310" width="190" height="100" rx="6"/>
          <text class="t-name" x="284" y="331">CRITICAL</text>
          <text class="t-st" x="446" y="331" text-anchor="end" id="crit-st"></text>
          <text class="t-big" x="284" y="362" id="crit-kw"></text>
          <text class="t-sm" x="284" y="382">comms, medical,</text>
          <text class="t-sm" x="284" y="399">refrigeration, controls</text>
        </g>
        <g class="eqhit" data-eq="ess" tabindex="0" role="button" aria-label="Essential loads details">
          <rect class="eqbox" x="510" y="310" width="190" height="100" rx="6"/>
          <text class="t-name" x="524" y="331">ESSENTIAL</text>
          <text class="t-st" x="686" y="331" text-anchor="end" id="ess-st"></text>
          <text class="t-big" x="524" y="362" id="ess-kw"></text>
          <text class="t-sm" x="524" y="382">lighting, water system,</text>
          <text class="t-sm" x="524" y="399">cooking</text>
        </g>
        <g class="eqhit" data-eq="disc" tabindex="0" role="button" aria-label="Discretionary loads details">
          <rect class="eqbox" x="750" y="310" width="220" height="100" rx="6"/>
          <text class="t-name" x="764" y="331">DISCRETIONARY</text>
          <text class="t-st" x="956" y="331" text-anchor="end" id="disc-st"></text>
          <text class="t-big" x="764" y="362" id="disc-kw"></text>
          <text class="t-sm" x="764" y="382">device charging,</text>
          <text class="t-sm" x="764" y="399">heat-pump water heater</text>
        </g>
        <!-- building -->
        <g class="eqhit" data-eq="bldg" tabindex="0" role="button" aria-label="Shelter building details">
          <rect class="eqbox" x="30" y="490" width="560" height="145" rx="6"/>
          <text class="t-name" x="44" y="512" id="b-name">SHELTER</text>
          <text class="t-st" x="576" y="512" text-anchor="end" id="b-st"></text>
          <text class="t-sm" x="44" y="534">Indoor air</text>
          <text class="t-big" x="44" y="562" id="b-tin" style="font-size:30px"></text>
          <text class="t-sm" x="44" y="584" id="b-tm"></text>
          <text class="t-sm" x="214" y="534">Heat in</text>
          <text class="t-mid" x="214" y="552" id="b-qin"></text>
          <text class="t-sm" x="214" y="574">Solar · people &amp; equipment</text>
          <text class="t-mid" x="214" y="592" id="b-gain"></text>
          <text class="t-sm" x="410" y="534">Heat lost to outdoors</text>
          <text class="t-mid" x="410" y="552" id="b-loss"></text>
          <text class="t-sm" x="410" y="574">Thermostat heat · cool</text>
          <text class="t-mid" x="410" y="592" id="b-sp"></text>
          <!-- thermometer scale 30..100 F -->
          <rect x="44" y="606" width="530" height="10" rx="2" fill="#fff" stroke="var(--edge)" stroke-width=".8"/>
          <rect id="b-band" y="606" height="10" fill="var(--ok)" fill-opacity=".18"/>
          <line id="b-mark" y1="601" y2="621" stroke="var(--ink)" stroke-width="3"/>
          <text class="t-sm" x="44" y="630" id="b-s0"></text>
          <text class="t-sm" x="574" y="630" text-anchor="end" id="b-s1"></text>
        </g>
        <!-- outdoor -->
        <g class="eqhit" data-eq="out" tabindex="0" role="button" aria-label="Outdoor conditions details">
          <rect class="eqbox" x="680" y="490" width="290" height="145" rx="6" style="stroke-dasharray:5 4"/>
          <text class="t-name" x="694" y="512">OUTDOOR · WAITSFIELD</text>
          <text class="t-big" x="694" y="550" id="o-t" style="font-size:28px"></text>
          <text class="t-sm" x="694" y="574" id="o-sun"></text>
          <text class="t-sm" x="694" y="592" id="o-wind"></text>
          <text class="t-st" x="694" y="616" id="o-cond"></text>
        </g>
      </svg>
      </div>
    </section>

    <aside class="panel face">
      <div class="ph">Faceplate <span>click any equipment</span></div>
      <div class="fp-body">
        <div class="fp-pick" id="fp-pick"></div>
        <div class="fp-title"><h2 id="fp-name"></h2><span id="fp-chip"></span></div>
        <table class="fp-tab" id="fp-tab"></table>
      </div>
    </aside>
  </div>

  <section class="panel">
    <div class="ph">Replay <span>Space plays or pauses · ← → step one hour</span></div>
    <div class="transport">
      <div class="trow">
        <button class="tbtn" id="b-start" aria-label="Go to start">⏮</button>
        <button class="tbtn" id="b-back" aria-label="Back one hour">−1 h</button>
        <button class="tbtn play" id="b-play">▶ Play</button>
        <button class="tbtn" id="b-fwd" aria-label="Forward one hour">+1 h</button>
        <button class="tbtn" id="b-prev" aria-label="Previous alarm">◀ alarm</button>
        <button class="tbtn" id="b-next" aria-label="Next alarm">alarm ▶</button>
        <label class="legend-k" for="speed">Speed</label>
        <select id="speed"><option value="1">1 h/s</option><option value="3" selected>3 h/s</option><option value="6">6 h/s</option><option value="12">12 h/s</option><option value="24">1 day/s</option></select>
        <span class="legend-k">Ticks above the slider: <b style="color:var(--alarm)">alarm</b> · <b style="color:#a07800">warning</b> events</span>
      </div>
      <div class="scrub"><div class="ticks" id="ticks"></div><input type="range" id="slider" min="0" max="167" value="0" aria-label="Simulation hour"></div>
      <div class="days" id="days"></div>
    </div>
  </section>

  <section class="panel">
    <div class="ph">Trends <span>click a chart to jump to that hour</span></div>
    <div class="trends">
      <div class="trend"><h3>Bus power by source (kW)</h3><div class="lg" id="lg0"></div><canvas id="c0"></canvas></div>
      <div class="trend"><h3>Temperatures (°F)</h3><div class="lg" id="lg1"></div><canvas id="c1"></canvas></div>
      <div class="trend"><h3>Battery charge and fuel in tank (%)</h3><div class="lg" id="lg2"></div><canvas id="c2"></canvas></div>
      <div class="trend"><h3>Building heat flows (kW)</h3><div class="lg" id="lg3"></div><canvas id="c3"></canvas></div>
    </div>
  </section>

  <div class="grid2">
    <section class="panel"><div class="ph">Event log <span id="evcount"></span></div><div class="evlist"><table class="ev" id="evtab"></table></div></section>
    <section class="panel"><div class="ph">Week totals <span id="wk-dates"></span></div>
      <div class="tbl"><table class="kpi" id="kpi"></table></div>
      <div class="ph">Design and payload</div>
      <div class="tbl"><table class="kpi" id="dsg"></table></div>
      <p class="note" id="foot"></p>
    </section>
  </div>
</div>

<script>
const D = /*__DATA__*/null;
const $ = id => document.getElementById(id);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const f1 = (v, d = 1) => (v == null || isNaN(v)) ? "–" : Number(v).toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
const DOW = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"], MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const day = s => { const y = +s.slice(0,4), m = +s.slice(5,7), d = +s.slice(8,10); const w = new Date(Date.UTC(y, m-1, d)).getUTCDay(); return `${DOW.at(w)} ${MON.at(m-1)} ${d}`; };
const ds = D.design;
let ci = 0, C = D.conditions[0], S = C.series, N = C.time.length, t = 0, playing = false, sel = "gen", acc = 0, last = 0;
try { const k = localStorage.getItem("hmi-cond"); const j = D.order.indexOf(k); if (j >= 0) ci = j; const e = localStorage.getItem("hmi-eq"); if (e) sel = e; } catch (e) {}

// ---------- static text ----------
$("site").textContent = `${D.site.name} · ${f1(D.building.area_ft2,0)} ft² shelter · ${D.building.occupants} occupants`;
$("designline").textContent = `PV ${f1(ds.pv_kw)} kW · battery ${f1(ds.bat_kwh,0)} kWh · generator ${f1(ds.gen_kw)} kW, ${f1(ds.tank_gal,0)} gal · heat pumps ${f1(ds.hp_kw)} kW`;
$("conds").innerHTML = D.conditions.map((c, i) => `<button role="tab" data-i="${i}" aria-selected="${i===ci}">${c.label}</button>`).join("");
$("conds").addEventListener("click", e => { const i = e.target.dataset.i; if (i == null) return; setCond(+i); });
$("b-name").textContent = `SHELTER · ${f1(D.building.area_ft2,0)} FT² · ${D.building.occupants} PEOPLE`;
$("pv-rate").textContent = `Rated ${f1(ds.pv_kw)} kW · ${f1(D.pv.tilt,0)}° tilt`;

// ---------- flows ----------
function flow(id, kw, reverse) {
  const el = $(id), a = Math.abs(kw);
  if (!(a > 0.05)) { el.classList.add("idle"); el.style.strokeWidth = ""; el.style.animationDuration = ""; return; }
  el.classList.remove("idle");
  el.style.strokeWidth = (2.5 + Math.min(7.5, a * 0.45)).toFixed(1);
  el.style.animationDuration = Math.max(0.22, 2.2 / Math.sqrt(a)).toFixed(2) + "s";
  el.style.animationDirection = reverse ? "reverse" : "normal";
}
function txt(id, s) { $(id).textContent = s; }
function stat(id, s, col) { const e = $(id); e.textContent = s; e.setAttribute("fill", col || css("--mute")); }
const v = k => S[k][t];

function alarmsAt(i) {
  const a = [], g = k => S[k][i];
  if (g("shed_crit") > 0.01) a.push(["alarm", `Critical loads unserved ${f1(g("shed_crit"))} kW`]);
  if (g("shed_ess") > 0.01) a.push(["alarm", `Essential loads shed ${f1(g("shed_ess"))} kW`]);
  if (g("shed_hvac") > 0.01) a.push(["alarm", `Heating/cooling curtailed ${f1(g("shed_hvac"))} kW`]);
  if (g("t_in") < C.t_min_f) a.push(["alarm", `Shelter ${f1(g("t_in"))} °F, below ${f1(C.t_min_f,0)} °F`]);
  if (g("t_in") > C.t_max_f) a.push(["alarm", `Shelter ${f1(g("t_in"))} °F, above ${f1(C.t_max_f,0)} °F`]);
  if (ds.gen_kw > 0 && g("fuel_pct") < 0.5) a.push(["alarm", "Fuel tank empty"]);
  else if (ds.gen_kw > 0 && g("fuel_pct") < 25) a.push(["warn", `Fuel low ${f1(g("fuel_gal"),0)} gal`]);
  if (g("shed_disc") > 0.01) a.push(["warn", `Discretionary loads shed ${f1(g("shed_disc"))} kW`]);
  if (ds.bat_kwh > 0 && g("soc") < 15) a.push(["warn", `Battery low ${f1(g("soc"),0)} %`]);
  if (g("q_need") - g("q_heat") - g("q_cool") > 0.3 && g("shed_hvac") < 0.01) a.push(["warn", `Heat pumps at capacity, ${f1(g("q_need")-g("q_heat")-g("q_cool"))} kW short`]);
  if (ds.pv_kw > 0 && g("pv_cover") > 50) a.push(["warn", C.key === "ice_storm" ? "PV array iced" : "PV array snow-covered"]);
  return a;
}

function update() {
  const tm = C.time[t];
  txt("clk-day", day(tm)); txt("clk-time", tm.slice(11, 16)); txt("clk-h", `Hour ${t+1} / ${N}`);
  $("slider").value = t;
  // alarm bar
  const al = alarmsAt(t);
  $("alarmbar").innerHTML = `<span class="lbl">Active alarms</span>` + (al.length
    ? al.map(([s, m]) => `<span class="chip ${s}"><span class="ic">${s === "alarm" ? "!" : "▲"}</span>${m}</span>`).join("")
    : `<span class="chip info">None. All loads served, shelter within limits</span>`);
  // PV
  const pvU = v("pv_to_load") + v("pv_to_bat");
  flow("f-pv", pvU); txt("v-pv", pvU > 0.05 ? `${f1(pvU)} kW ↓` : "");
  txt("pv-kw", `${f1(pvU)} kW`);
  txt("pv-sub", `available ${f1(v("pv_avail"))} · curtailed ${f1(v("curtail"))}`);
  txt("pv-sub2", `to loads ${f1(v("pv_to_load"))} · to battery ${f1(v("pv_to_bat"))}`);
  if (ds.pv_kw <= 0) stat("pv-st", "NOT FITTED");
  else if (v("pv_cover") > 50) stat("pv-st", C.key === "ice_storm" ? "ICED" : "SNOW", "#a07800");
  else if (v("ghi") < 0.01) stat("pv-st", "NIGHT");
  else stat("pv-st", "PRODUCING", css("--ok"));
  // battery
  const bd = v("bat_dis"), bc = v("bat_ch"), bn = bd - bc;
  flow("f-bat", bn, bn < 0); txt("v-bat", Math.abs(bn) > 0.05 ? `${f1(Math.abs(bn))} kW ${bn > 0 ? "↓" : "↑"}` : "");
  txt("bat-soc", ds.bat_kwh > 0 ? `${f1(v("soc"),0)} %` : "–");
  txt("bat-kw", bd > 0.05 ? `out ${f1(bd)} kW` : bc > 0.05 ? `in ${f1(bc)} kW` : "0.0 kW");
  txt("bat-kwh", `${f1(v("soc_kwh"))} of ${f1(ds.bat_kwh,0)} kWh`);
  if (ds.bat_kwh <= 0) stat("bat-st", "NOT FITTED");
  else if (v("soc") < 15) stat("bat-st", "LOW", css("--alarm"));
  else stat("bat-st", bd > 0.05 ? "DISCHARGING" : bc > 0.05 ? "CHARGING" : "STANDBY", bd > 0.05 || bc > 0.05 ? css("--bat") : css("--mute"));
  const h = 88 * Math.max(0, Math.min(100, v("soc"))) / 100;
  $("bat-fill").setAttribute("y", 47 + 88 - h); $("bat-fill").setAttribute("height", h);
  $("bat-fill").setAttribute("fill", v("soc") < 15 ? css("--alarm") : css("--bat"));
  $("bat-floor").setAttribute("y1", 135 - 88 * D.battery.soc_min / 100); $("bat-floor").setAttribute("y2", 135 - 88 * D.battery.soc_min / 100);
  // generator
  const gon = v("gen_on") > 0.5, go = v("gen_out") - v("dump");
  flow("f-gen", go); txt("v-gen", go > 0.05 ? `${f1(go)} kW ↓` : "");
  flow("f-fuel", v("fuel_gph") * 5);
  txt("gen-kw", `${f1(v("gen_out"))} kW`);
  txt("gen-sub", gon ? `${f1(v("gen_load_pct"),0)} % load · ${f1(v("fuel_gph"),2)} gal/h` : `rated ${f1(ds.gen_kw)} kW`);
  txt("gen-sub2", gon ? `loads ${f1(v("gen_to_load"))} · battery ${f1(v("gen_to_bat"))}${v("dump") > 0.05 ? " · dump " + f1(v("dump")) : ""}` : `starts below ${f1(D.generator.soc_start,0)} % battery`);
  const empty = ds.gen_kw > 0 && v("fuel_pct") < 0.5;
  if (ds.gen_kw <= 0) { stat("gen-st", "NOT FITTED"); $("gen-lamp").setAttribute("fill", "#c9ced3"); }
  else if (empty) { stat("gen-st", "OUT OF FUEL", css("--alarm")); $("gen-lamp").setAttribute("fill", css("--alarm")); }
  else { stat("gen-st", gon ? "RUNNING" : "STOPPED · AUTO", gon ? css("--ok") : css("--mute")); $("gen-lamp").setAttribute("fill", gon ? css("--ok") : "#c9ced3"); }
  // tank
  const fp = Math.max(0, Math.min(100, v("fuel_pct"))), th = 60 * fp / 100;
  $("tank-fill").setAttribute("y", 59 + 60 - th); $("tank-fill").setAttribute("height", th);
  $("tank-fill").setAttribute("fill", fp < 25 ? css("--alarm") : css("--gen"));
  txt("tank-gal", ds.gen_kw > 0 ? `${f1(v("fuel_gal"),0)} gal` : "–");
  // EV
  flow("f-ev", v("ev")); txt("v-ev", v("ev") > 0.05 ? `${f1(v("ev"))} kW ↓` : "");
  txt("ev-kw", `${f1(v("ev"))} kW`);
  txt("ev-sub", `budget left ${f1(v("ev_left"))} kWh`);
  stat("ev-st", v("ev") > 0.05 ? "SUPPLYING" : (S.ev_left[0] > 0 ? "STANDBY" : "NOT ALLOCATED"), v("ev") > 0.05 ? css("--ev") : css("--mute"));
  // loads
  const hpE = v("hvac");
  flow("f-hp", hpE); txt("v-hp", hpE > 0.05 ? `${f1(hpE)} kW ↓` : "");
  txt("hp-kw", `${f1(hpE)} kW in`);
  const heating = v("q_heat") > 0.05, cooling = v("q_cool") > 0.05;
  txt("hp-sub", heating ? `${f1(v("q_heat"))} kW heat out · COP ${f1(v("cop"),2)}` : cooling ? `${f1(v("q_cool"))} kW cooling · COP ${f1(v("cop"),2)}` : "thermostat satisfied");
  txt("hp-sub2", `capacity ${f1(v("hp_cap"))} of ${f1(ds.hp_kw)} kW`);
  if (v("shed_hvac") > 0.01) stat("hp-st", "CURTAILED", css("--alarm"));
  else if (v("q_need") - v("q_heat") - v("q_cool") > 0.3) stat("hp-st", "AT CAPACITY", "#a07800");
  else stat("hp-st", heating ? "HEATING" : cooling ? "COOLING" : "IDLE", heating ? css("--heat") : cooling ? css("--cool") : css("--mute"));
  for (const [k, sk] of [["crit","shed_crit"],["ess","shed_ess"],["disc","shed_disc"]]) {
    const served = v(k) - v(sk);
    flow("f-" + k, served); txt("v-" + k, served > 0.05 ? `${f1(served)} kW ↓` : "");
    txt(k + "-kw", `${f1(served)} kW`);
    if (v(sk) > 0.01) stat(k + "-st", `SHED ${f1(v(sk))} kW`, k === "disc" ? "#a07800" : css("--alarm"));
    else stat(k + "-st", "SERVED", css("--ok"));
  }
  txt("bus-kw", `Bus load ${f1(v("load") + v("bat_ch"))} kW`);
  // building
  const qnet = v("q_heat") - v("q_cool");
  flow("f-heat", qnet, qnet < 0); $("f-heat").setAttribute("stroke", qnet < 0 ? css("--cool") : css("--heat"));
  $("v-heat").setAttribute("fill", qnet < 0 ? css("--cool") : css("--heat"));
  txt("v-heat", Math.abs(qnet) > 0.05 ? `${f1(Math.abs(qnet))} kW ${qnet > 0 ? "heat ↓" : "removed ↑"}` : "");
  flow("f-loss", v("q_loss"), v("q_loss") < 0);
  txt("v-loss", `${f1(Math.abs(v("q_loss")))} kW ${v("q_loss") >= 0 ? "→" : "←"}`);
  txt("b-tin", `${f1(v("t_in"))} °F`);
  txt("b-tm", `structure ${f1(v("t_mass"))} °F`);
  txt("b-qin", heating ? `${f1(v("q_heat"))} kW from heat pumps` : cooling ? `${f1(v("q_cool"))} kW removed` : "0.0 kW");
  txt("b-gain", `${f1(v("solar_gain"))} · ${f1(v("internal_gain"))} kW`);
  txt("b-loss", `${f1(v("q_loss"))} kW`);
  txt("b-sp", `${f1(C.heat_setpoint_f,0)} · ${f1(C.cool_setpoint_f,0)} °F`);
  const X = f => 44 + 530 * (Math.max(30, Math.min(100, f)) - 30) / 70;
  $("b-band").setAttribute("x", X(C.t_min_f)); $("b-band").setAttribute("width", X(C.t_max_f) - X(C.t_min_f));
  $("b-mark").setAttribute("x1", X(v("t_in"))); $("b-mark").setAttribute("x2", X(v("t_in")));
  $("b-mark").setAttribute("stroke", v("t_in") < C.t_min_f || v("t_in") > C.t_max_f ? css("--alarm") : css("--ink"));
  txt("b-s0", "30 °F"); txt("b-s1", "100 °F");
  if (v("t_in") < C.t_min_f) stat("b-st", "TOO COLD", css("--alarm"));
  else if (v("t_in") > C.t_max_f) stat("b-st", "TOO HOT", css("--alarm"));
  else stat("b-st", `WITHIN ${f1(C.t_min_f,0)}–${f1(C.t_max_f,0)} °F`, css("--ok"));
  // outdoor
  txt("o-t", `${f1(v("t_out"))} °F`);
  txt("o-sun", `Sun ${f1(v("ghi"),2)} kW/m² on the ground`);
  txt("o-wind", `Wind ${f1(v("wind_mph"),0)} mph`);
  stat("o-cond", C.title.toUpperCase());
  faceplate(); drawTrends(); evHighlight();
}

// ---------- faceplate ----------
const EQ = {pv:"PV array", bat:"Battery", gen:"Generator", tank:"Fuel tank", ev:"EV pickup", hp:"Heat pumps", crit:"Critical loads", ess:"Essential loads", disc:"Discretionary loads", bldg:"Shelter", out:"Outdoor"};
$("fp-pick").innerHTML = Object.entries(EQ).map(([k, n]) => `<button data-eq="${k}" aria-pressed="${k===sel}">${n}</button>`).join("");
$("fp-pick").addEventListener("click", e => { if (e.target.dataset.eq) select(e.target.dataset.eq); });
document.querySelectorAll(".eqhit").forEach(g => {
  g.addEventListener("click", () => select(g.dataset.eq));
  g.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(g.dataset.eq); } });
});
function select(k) { sel = k; try { localStorage.setItem("hmi-eq", k); } catch (e) {}
  document.querySelectorAll(".eqhit").forEach(g => g.classList.toggle("sel", g.dataset.eq === k));
  [...$("fp-pick").children].forEach(b => b.setAttribute("aria-pressed", b.dataset.eq === k)); faceplate(); }
const sum = k => S[k].reduce((a, b) => a + b, 0);
function faceplate() {
  const T = C.totals, rows = [];
  const H = s => rows.push(`<tr class="h"><td colspan="2">${s}</td></tr>`), R = (a, b) => rows.push(`<tr><td>${a}</td><td>${b}</td></tr>`);
  let chip = "";
  H(`Now · ${day(C.time[t])} ${C.time[t].slice(11,16)}`);
  if (sel === "pv") { R("Available", `${f1(v("pv_avail"))} kW`); R("To loads", `${f1(v("pv_to_load"))} kW`); R("To battery", `${f1(v("pv_to_bat"))} kW`); R("Curtailed", `${f1(v("curtail"))} kW`); R("Covered (snow/ice)", `${f1(v("pv_cover"),0)} %`); R("Sun on ground", `${f1(v("ghi"),2)} kW/m²`);
    H("Week"); R("Energy available", `${f1(T.pv_avail,0)} kWh`); R("Energy used", `${f1(T.pv_used,0)} kWh`); R("Curtailed", `${f1(T.curtail,0)} kWh`); R("Share of supply", `${f1(T.pv_share*100,0)} %`); H("Rating"); R("Array", `${f1(ds.pv_kw)} kW, ${f1(D.pv.tilt,0)}° tilt`); }
  if (sel === "bat") { R("Charge", `${f1(v("soc"),0)} % · ${f1(v("soc_kwh"))} kWh`); R("Discharge", `${f1(v("bat_dis"))} kW`); R("Charge from PV", `${f1(v("pv_to_bat"))} kW`); R("Charge from generator", `${f1(v("gen_to_bat"))} kW`);
    H("Week"); R("Energy discharged", `${f1(T.bat_dis,0)} kWh`); R("Energy charged", `${f1(T.bat_ch,0)} kWh`); R("Lowest charge", T.soc_min == null ? "–" : `${f1(T.soc_min,0)} %`); R("Full cycles", ds.bat_kwh > 0 ? f1(T.bat_dis / ds.bat_kwh, 1) : "–");
    H("Settings"); R("Capacity", `${f1(ds.bat_kwh,0)} kWh`); R("Max power", `${f1(ds.bat_kwh*D.battery.c_rate)} kW (${D.battery.c_rate}C)`); R("Floor", `${f1(D.battery.soc_min,0)} %`); R("Generator start / stop", `${f1(D.generator.soc_start,0)} % / ${f1(D.generator.soc_stop,0)} %`); }
  if (sel === "gen") { R("State", v("gen_on") > 0.5 ? "Running" : "Stopped"); R("Output", `${f1(v("gen_out"))} kW · ${f1(v("gen_load_pct"),0)} % load`); R("To loads", `${f1(v("gen_to_load"))} kW`); R("To battery", `${f1(v("gen_to_bat"))} kW`); R("Wasted at minimum load", `${f1(v("dump"))} kW`); R("Fuel burn", `${f1(v("fuel_gph"),2)} gal/h`);
    H("Week"); R("Run time", `${f1(T.gen_hours,0)} h (${f1(T.gen_hours/C.time.length*24,1)} h/day)`); R("Starts", f1(T.gen_starts,0)); R("Energy produced", `${f1(T.gen_kwh,0)} kWh`); R("Fuel used", `${f1(T.fuel_gal,1)} gal`); R("Fuel left", `${f1(T.fuel_left_gal,1)} gal`);
    H("Rating"); R("Generator", `${f1(ds.gen_kw)} kW ${D.generator.fuel}`); R("Minimum load", `${f1(D.generator.min_load,0)} %`); }
  if (sel === "tank") { R("Fuel left", `${f1(v("fuel_gal"),1)} gal · ${f1(v("fuel_pct"),0)} %`); R("Burn rate now", `${f1(v("fuel_gph"),2)} gal/h`); R("Hours left at this rate", v("fuel_gph") > 0.01 ? f1(v("fuel_gal")/v("fuel_gph"),0) : "–");
    H("Week"); R("Fuel used", `${f1(T.fuel_gal,1)} gal`); R("Average", `${f1(T.fuel_gal/(N/24),1)} gal/day`); R("One tank lasts at this average", T.fuel_gal > 0 ? `${f1(ds.tank_gal/(T.fuel_gal/(N/24)),1)} days` : "–"); H("Rating"); R("Tank", `${f1(ds.tank_gal,0)} gal`); }
  if (sel === "ev") { R("Output", `${f1(v("ev"))} kW`); R("Budget left", `${f1(v("ev_left"))} kWh`); H("Week"); R("Energy supplied", `${f1(T.ev_kwh,0)} kWh`); }
  if (sel === "hp") { R("Electric input", `${f1(v("hvac"))} kW`); R("Heat out", `${f1(v("q_heat"))} kW`); R("Cooling", `${f1(v("q_cool"))} kW`); R("COP", v("cop") > 0 ? f1(v("cop"),2) : "–"); R("Capacity at this temperature", `${f1(v("hp_cap"))} kW`); R("Building needs", `${f1(v("q_need"))} kW`); R("Shortfall", `${f1(Math.max(0, v("q_need")-v("q_heat")-v("q_cool")))} kW`);
    H("Week"); R("Heat delivered", `${f1(T.heat,0)} kWh`); R("Cooling delivered", `${f1(T.cool,0)} kWh`); R("Electricity used", `${f1(T.hvac,0)} kWh`); R("Average COP", T.hvac > 0 ? f1((T.heat + T.cool)/T.hvac, 2) : "–"); H("Rating"); R("Installed", `${f1(ds.hp_kw)} kW (${f1(ds.hp_units,0)} × ${D.hp.unit_kw} kW)`); }
  if (["crit","ess","disc"].includes(sel)) { const sk = "shed_" + sel; R("Demand", `${f1(v(sel),2)} kW`); R("Served", `${f1(v(sel)-v(sk),2)} kW`); R("Shed", `${f1(v(sk),2)} kW`);
    H("Week"); R("Energy demand", `${f1(sum(sel),1)} kWh`); R("Energy shed", `${f1(sum(sk),1)} kWh`); R("Hours with shedding", f1(S[sk].filter(x => x > 0.01).length, 0));
    H("Priority"); R("Shed order", {crit:"last (4 of 4)", ess:"third (3 of 4)", disc:"first (1 of 4)"}[sel]); }
  if (sel === "bldg") { R("Indoor air", `${f1(v("t_in"))} °F`); R("Structure", `${f1(v("t_mass"))} °F`); R("Heat-pump heat", `${f1(v("q_heat"))} kW`); R("Cooling", `${f1(v("q_cool"))} kW`); R("Solar gain", `${f1(v("solar_gain"))} kW`); R("People and equipment", `${f1(v("internal_gain"))} kW`); R("Lost to outdoors", `${f1(v("q_loss"))} kW`);
    H("Week"); R("Coldest / warmest", `${f1(T.t_min)} / ${f1(T.t_max)} °F`); R(`Hours below ${f1(C.t_min_f,0)} °F`, f1(T.hours_cold,0)); R(`Hours above ${f1(C.t_max_f,0)} °F`, f1(T.hours_hot,0));
    H("Building"); R("Heat loss coefficient", `${f1(D.building.ua_w_k,0)} W/K`); R("Windows", D.building.windows); }
  if (sel === "out") { R("Temperature", `${f1(v("t_out"))} °F`); R("Sun", `${f1(v("ghi"),2)} kW/m²`); R("Wind", `${f1(v("wind_mph"),0)} mph`);
    H("Week"); R("Coldest", `${f1(Math.min(...S.t_out))} °F`); R("Warmest", `${f1(Math.max(...S.t_out))} °F`); R("Average sun", `${f1(sum("ghi")/(N/24),2)} kWh/m²/day`); }
  $("fp-name").textContent = EQ[sel]; $("fp-chip").innerHTML = chip; $("fp-tab").innerHTML = rows.join("");
}

// ---------- trends ----------
const TR = [
  {id:"c0", lg:"lg0", stack:[["PV","--pv",i=>S.pv_to_load[i]+S.pv_to_bat[i]],["Battery","--bat",i=>S.bat_dis[i]],["Generator","--gen",i=>S.gen_out[i]-S.dump[i]],["EV pickup","--ev",i=>S.ev[i]],["Unserved","--alarm",i=>S.unmet[i]]],
   lines:[["Load + battery charging","--ink",i=>S.load[i]+S.bat_ch[i],1.6],["Heat-pump draw","--elec",i=>S.hvac[i],1.2,[4,3]]], min0:true},
  {id:"c1", lg:"lg1", band:true, lines:[["Indoor","--bat",i=>S.t_in[i],2.2],["Structure","--elec",i=>S.t_mass[i],1.1],["Outdoor","--mute",i=>S.t_out[i],1.3,[5,4]]]},
  {id:"c2", lg:"lg2", gen:true, lines:[["Battery charge","--bat",i=>S.soc[i],2],["Fuel in tank","--gen",i=>S.fuel_pct[i],2]], fixed:[0,100]},
  {id:"c3", lg:"lg3", stack:[["Heat-pump heat","--heat",i=>S.q_heat[i]],["Solar gain","--pv",i=>S.solar_gain[i]],["People & equipment","--elec",i=>S.internal_gain[i]]],
   lines:[["Heat to hold thermostat (capped)","--ink",i=>Math.min(S.q_need[i]*(S.q_cool[i]>0?0:1), ds.hp_kw*1.25),1.4,[4,3]],["Lost to outdoors","--alarm",i=>S.q_loss[i],1.4],["Cooling","--cool",i=>S.q_cool[i],1.6]], min0:true},
];
function legends() {
  for (const c of TR) {
    let h = (c.stack||[]).filter(s => s[0] !== "EV pickup" || S.ev.some(x=>x>0)).filter(s => s[0] !== "Unserved" || S.unmet.some(x=>x>0.01))
      .map(s => `<span><i style="background:var(${s[1]})"></i>${s[0]}</span>`).join("");
    h += (c.lines||[]).filter(s => s[0] !== "Cooling" || S.q_cool.some(x=>x>0)).map(s => `<span><i class="ln" style="background:var(${s[1]})"></i>${s[0]}</span>`).join("");
    if (c.band) h += `<span><i style="background:rgba(46,125,50,.18)"></i>${f1(C.t_min_f,0)}–${f1(C.t_max_f,0)} °F requirement</span>`;
    if (c.gen) h += `<span><i style="background:rgba(123,79,44,.22)"></i>Generator running</span>`;
    $(c.lg).innerHTML = h;
  }
}
function drawTrends() {
  for (const c of TR) {
    const cv = $(c.id), dpr = window.devicePixelRatio || 1, W = cv.clientWidth, Hh = cv.clientHeight;
    if (!W) continue;
    if (cv.width !== Math.round(W*dpr) || cv.height !== Math.round(Hh*dpr)) { cv.width = Math.round(W*dpr); cv.height = Math.round(Hh*dpr); }
    const g = cv.getContext("2d"); g.setTransform(dpr,0,0,dpr,0,0); g.clearRect(0,0,W,Hh);
    const L = 44, Rm = 10, Tp = 8, B = 20, pw = W - L - Rm, ph = Hh - Tp - B;
    const cum = (c.stack||[]).map(() => new Float64Array(N));
    for (let i = 0; i < N; i++) { let a = 0; (c.stack||[]).forEach((s, k) => { a += Math.max(0, s[2](i)); cum[k][i] = a; }); }
    let lo = Infinity, hi = -Infinity;
    if (c.fixed) [lo, hi] = c.fixed;
    else { for (let i = 0; i < N; i++) { if (cum.length) hi = Math.max(hi, cum.at(-1)[i]); for (const l of (c.lines||[])) { const y = l[2](i); hi = Math.max(hi, y); lo = Math.min(lo, y); } }
      if (c.min0) lo = Math.min(0, lo); if (c.band) { lo = Math.min(lo, C.t_min_f - 5); hi = Math.max(hi, C.t_max_f + 2); }
      const pad = (hi - lo) * 0.06 || 1; hi += pad; if (!c.min0) lo -= pad; }
    const X = i => L + pw * i / (N - 1), Y = y => Tp + ph * (1 - (y - lo) / (hi - lo));
    // grid
    g.font = "11px " + css("--f-ui"); g.fillStyle = css("--mute"); g.strokeStyle = "#d3d8dc"; g.lineWidth = 1;
    const step = niceStep((hi - lo) / 4);
    for (let y = Math.ceil(lo / step) * step; y <= hi; y += step) { g.beginPath(); g.moveTo(L, Y(y)); g.lineTo(L + pw, Y(y)); g.stroke(); g.textAlign = "right"; g.fillText(fmtTick(y), L - 6, Y(y) + 4); }
    g.textAlign = "left";
    for (let i = 0; i < N; i++) if (C.time[i].slice(11,13) === "00") { g.strokeStyle = "#b9c0c6"; g.beginPath(); g.moveTo(X(i), Tp); g.lineTo(X(i), Tp + ph); g.stroke(); g.fillText(day(C.time[i]).slice(0, 3), X(i) + 3, Hh - 6); }
    if (c.band) { g.fillStyle = "rgba(46,125,50,.14)"; g.fillRect(L, Y(C.t_max_f), pw, Y(C.t_min_f) - Y(C.t_max_f)); g.strokeStyle = css("--alarm"); g.setLineDash([3,3]); g.beginPath(); g.moveTo(L, Y(C.t_min_f)); g.lineTo(L+pw, Y(C.t_min_f)); g.stroke(); g.setLineDash([]); }
    if (c.gen) { g.fillStyle = "rgba(123,79,44,.18)"; for (let i = 0; i < N; i++) if (S.gen_on[i] > 0.5) g.fillRect(X(i) - pw/(N-1)/2, Tp, pw/(N-1) + 0.6, ph); }
    // stacked areas (step)
    (c.stack||[]).forEach((s, k) => {
      g.beginPath(); g.moveTo(X(0), Y(k ? cum[k-1][0] : 0));
      for (let i = 0; i < N; i++) g.lineTo(X(i), Y(cum[k][i]));
      for (let i = N - 1; i >= 0; i--) g.lineTo(X(i), Y(k ? cum[k-1][i] : 0));
      g.closePath(); g.fillStyle = css(s[1]); g.globalAlpha = .78; g.fill(); g.globalAlpha = 1;
    });
    for (const l of (c.lines||[])) {
      if (l[0] === "Cooling" && !S.q_cool.some(x => x > 0)) continue;
      g.beginPath(); for (let i = 0; i < N; i++) { const y = Y(l[2](i)); i ? g.lineTo(X(i), y) : g.moveTo(X(i), y); }
      g.strokeStyle = css(l[1]); g.lineWidth = l[3]; g.setLineDash(l[4] || []); g.stroke(); g.setLineDash([]);
    }
    // cursor
    g.strokeStyle = css("--ink"); g.lineWidth = 1.5; g.beginPath(); g.moveTo(X(t), Tp); g.lineTo(X(t), Tp + ph); g.stroke();
    g.fillStyle = css("--ink"); g.beginPath(); g.moveTo(X(t) - 5, Tp); g.lineTo(X(t) + 5, Tp); g.lineTo(X(t), Tp + 6); g.fill();
    cv._map = x => Math.round((x - L) / pw * (N - 1));
  }
}
function niceStep(r) { const p = Math.pow(10, Math.floor(Math.log10(r || 1))), m = r / p; return (m < 1.5 ? 1 : m < 3 ? 2 : m < 7 ? 5 : 10) * p; }
function fmtTick(y) { return Math.abs(y) >= 100 || Number.isInteger(y) ? y.toFixed(0) : y.toFixed(1); }
for (const c of TR) { const cv = $(c.id); let down = false;
  const go = e => { const r = cv.getBoundingClientRect(); seek(cv._map(e.clientX - r.left)); };
  cv.addEventListener("pointerdown", e => { down = true; cv.setPointerCapture(e.pointerId); go(e); });
  cv.addEventListener("pointermove", e => { if (down) go(e); });
  cv.addEventListener("pointerup", () => down = false); }

// ---------- events ----------
function evTable() {
  const E = C.events, n = {alarm:0, warn:0, info:0}; E.forEach(e => n[e.sev]++);
  $("evcount").textContent = `${n.alarm} alarms · ${n.warn} warnings · ${n.info} info`;
  $("evtab").innerHTML = E.map((e, k) => `<tr data-i="${e.i}" data-k="${k}"><td class="tm">${day(C.time[e.i])} ${C.time[e.i].slice(11,16)}</td><td><span class="sev ${e.sev}">${e.sev === "warn" ? "WARN" : e.sev.toUpperCase()}</span></td><td>${e.msg}</td></tr>`).join("")
    || `<tr><td>No events this week.</td></tr>`;
  $("ticks").innerHTML = E.filter(e => e.sev !== "info").map(e => `<i class="${e.sev}" style="left:${(e.i/(N-1)*100).toFixed(2)}%" title="${e.msg}"></i>`).join("");
}
$("evtab").addEventListener("click", e => { const tr = e.target.closest("tr"); if (tr && tr.dataset.i) seek(+tr.dataset.i); });
function evHighlight() { let cur = null;
  for (const tr of $("evtab").rows) { const i = +tr.dataset.i; tr.classList.toggle("future", i > t); tr.classList.toggle("now", i === t); if (i <= t) cur = tr; }
  const box = $("evtab").parentElement;
  if (cur && !box.matches(":hover")) box.scrollTop = Math.max(0, cur.offsetTop - box.clientHeight + cur.offsetHeight + 4); }

// ---------- totals ----------
function totals() {
  const T = C.totals, rows = [];
  const R = (a, b, bad) => rows.push(`<tr class="${bad ? "bad" : ""}"><td>${a}</td><td class="v">${b}</td></tr>`);
  $("wk-dates").textContent = `${day(C.start)} to ${day(C.end)}`;
  R("Energy demand", `${f1(T.demand,0)} kWh`); R("Unserved energy", `${f1(T.unmet,1)} kWh`, T.unmet > 0.5); R("Critical loads unserved", `${f1(T.unmet_crit,1)} kWh`, T.unmet_crit > 0.01);
  R("Coldest / warmest indoor", `${f1(T.t_min)} / ${f1(T.t_max)} °F`, T.hours_cold > 0 || T.hours_hot > 0);
  R(`Hours outside ${f1(C.t_min_f,0)}–${f1(C.t_max_f,0)} °F`, f1(T.hours_cold + T.hours_hot, 0), T.hours_cold + T.hours_hot > 0);
  R("Fuel used / left", `${f1(T.fuel_gal,1)} / ${f1(T.fuel_left_gal,1)} gal`, ds.gen_kw > 0 && T.fuel_left_gal < 0.5);
  R("Generator run time · starts", `${f1(T.gen_hours,0)} h · ${f1(T.gen_starts,0)}`);
  R("PV used / curtailed", `${f1(T.pv_used,0)} / ${f1(T.curtail,0)} kWh`);
  R("Share of supply from PV", `${f1(T.pv_share*100,0)} %`);
  R("Peak bus load", `${f1(T.peak_load,1)} kW`);
  $("kpi").innerHTML = rows.join("");
  const d = [], P = (a, b, bad) => d.push(`<tr class="${bad ? "bad" : ""}"><td>${a}</td><td class="v">${b}</td></tr>`);
  P("PV array", `${f1(ds.pv_kw)} kW`); P("Battery", `${f1(ds.bat_kwh,0)} kWh`); P("Generator · tank", `${f1(ds.gen_kw)} kW · ${f1(ds.tank_gal,0)} gal`);
  P("Heat pumps", `${f1(ds.hp_kw)} kW`); P("Inverter", `${f1(ds.inverter_kw,0)} kW`); P("Equipment cost (planning)", `$${f1(D.capex/1000,1)}k`);
  P("Trailer payload", `${f1(D.payload_lb,0)} of ${f1(D.payload_limit_lb,0)} lb`, D.payload_lb > D.payload_limit_lb);
  $("dsg").innerHTML = d.join("");
  $("foot").textContent = `${D.source}. Hourly simulation with the sizing tool's building, load and equipment models. Sizes are entered by hand, not optimized.`;
}

// ---------- transport ----------
function seek(i) { t = Math.max(0, Math.min(N - 1, i | 0)); update(); }
function setCond(i) { ci = i; C = D.conditions[i]; S = C.series; N = C.time.length; t = Math.min(t, N - 1);
  try { localStorage.setItem("hmi-cond", C.key); } catch (e) {}
  [...$("conds").children].forEach((b, k) => b.setAttribute("aria-selected", k === i));
  $("slider").max = N - 1; $("cdesc").textContent = `${C.title}, ${day(C.start)} to ${day(C.end)}. ${C.desc} ${C.notes.join(" ")}`;
  const dd = []; for (let k = 0; k < N; k += 24) dd.push(`<span>${day(C.time[k])}</span>`); $("days").innerHTML = dd.join("");
  legends(); evTable(); totals(); update(); }
function play(on) { playing = on; $("b-play").textContent = on ? "❚❚ Pause" : "▶ Play"; if (on) { if (t >= N - 1) t = 0; last = performance.now(); acc = 0; requestAnimationFrame(tick); } }
function tick(now) { if (!playing) return; acc += (now - last) / 1000 * +$("speed").value; last = now;
  if (acc >= 1) { const s = Math.floor(acc); acc -= s; if (t + s >= N - 1) { seek(N - 1); play(false); return; } seek(t + s); }
  requestAnimationFrame(tick); }
$("b-play").onclick = () => play(!playing);
$("b-start").onclick = () => { play(false); seek(0); };
$("b-back").onclick = () => { play(false); seek(t - 1); };
$("b-fwd").onclick = () => { play(false); seek(t + 1); };
$("b-next").onclick = () => { play(false); const e = C.events.find(e => e.sev !== "info" && e.i > t); if (e) seek(e.i); };
$("b-prev").onclick = () => { play(false); const e = [...C.events].reverse().find(e => e.sev !== "info" && e.i < t); if (e) seek(e.i); };
$("slider").addEventListener("input", e => { play(false); seek(+e.target.value); });
document.addEventListener("keydown", e => { if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT" && e.target.type !== "range") return;
  if (e.code === "Space" && !e.target.closest(".eqhit")) { e.preventDefault(); play(!playing); }
  if (e.key === "ArrowRight" && e.target.type !== "range") { play(false); seek(t + 1); }
  if (e.key === "ArrowLeft" && e.target.type !== "range") { play(false); seek(t - 1); } });
window.addEventListener("resize", () => drawTrends());
select(sel); setCond(ci);
</script>
"""
