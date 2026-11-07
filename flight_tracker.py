"""A local, scenario-driven aircraft tracker for the gate-assignment project.

Run ``python flight_tracker.py --serve`` and open http://127.0.0.1:8766.
The map is an illustrative simulation; the challenge data has no live aircraft
coordinates, so aircraft move along scheduled routes between airport markers.
"""

import argparse
import glob
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from evaluator import run_sim
from gms.config import CATEGORY_NAME
from gms.loader import load_schedule, load_static
from gms.messages import FlightStore
from gms.profile import flight_profile


AIRPORTS = {
    "YYZ": (-79.63, 43.68), "YVR": (-123.18, 49.20), "YUL": (-73.74, 45.47),
    "YOW": (-75.67, 45.32), "YYC": (-114.02, 51.12), "YHZ": (-63.51, 44.88),
    "YYJ": (-123.43, 48.65), "YEG": (-113.58, 53.31), "YWG": (-97.24, 49.91),
    "YQB": (-71.39, 46.79), "LHR": (-0.45, 51.47), "CDG": (2.55, 49.01),
    "FRA": (8.56, 50.04), "NRT": (140.39, 35.77), "DXB": (55.36, 25.25),
    "JFK": (-73.78, 40.64), "LAX": (-118.41, 33.94), "SFO": (-122.38, 37.62),
    "AMS": (4.76, 52.31), "ORD": (-87.90, 41.98),
}


def build_payload(scenario="simple", solution="solution_scored", static_path="static_info.json"):
    """Run a scenario and return gate trace plus aircraft route metadata."""
    scenario_path = Path("flight_data") / f"{scenario}.json"
    if not scenario_path.is_file():
        raise FileNotFoundError(f"Unknown scenario: {scenario}")

    result = run_sim(static_path, str(scenario_path), solution, verbose=False, record=True)
    trace = result["trace"]
    gates, ac_info, stations = load_static(static_path)
    schedule = load_schedule(str(scenario_path))
    store = FlightStore()
    for minute in sorted(schedule):
        for message in schedule[minute]:
            if message.get("action") != "GateOutage":
                store.apply(message, minute)

    flights = []
    for fid, state in store.flights.items():
        if state.get("cancelled"):
            continue
        profile = flight_profile(state, ac_info, stations)
        if not profile.has_yyz:
            continue
        legs = []
        for leg in profile.active_legs:
            dep, arr = leg["departureStation"], leg["arrivalStation"]
            legs.append({
                "from": dep, "to": arr,
                "departure": leg["dep_abs"], "arrival": leg["arr_abs"],
                "aircraft": leg["aircraftType"],
                "from_pos": AIRPORTS.get(dep, AIRPORTS["YYZ"]),
                "to_pos": AIRPORTS.get(arr, AIRPORTS["YYZ"]),
            })
        yyz_leg = profile.yyz_legs[0]
        flights.append({
            "id": fid, "carrier": state.get("carrier", ""),
            "number": state.get("flight_number", ""),
            "aircraft": yyz_leg["aircraftType"],
            "category": CATEGORY_NAME.get(profile.category, "unknown"),
            "wingspan": profile.wingspan,
            "jetbridge": profile.jetbridge_required,
            "diversion": profile.diversion,
            "intervals": profile.intervals,
            "legs": legs,
        })

    trace["result"] = {k: result.get(k) for k in
                        ("status", "score", "reason", "assigned", "unassigned", "breakdown")}
    flight_by_id = {f["id"]: f for f in flights}
    gate_changes = []
    previous_gate = {}
    for tick in trace.get("ticks", []):
        for fid, new_gate in tick.get("decisions", {}).get("reassignments", []):
            old_gate = previous_gate.get(fid)
            intervals = flight_by_id.get(fid, {}).get("intervals", [])
            outage_hit = old_gate and any(
                start < outage[1] and outage[0] < end
                for start, end in intervals
                for outage in tick.get("outages", {}).get(old_gate, [])
            )
            gate_changes.append({
                "flight_id": fid, "from_gate": old_gate or "Unknown",
                "to_gate": new_gate, "time": tick.get("time_label", ""),
                "reason": (f"Gate {old_gate} outage overlaps this flight's occupancy"
                           if outage_hit else "Flight timing or equipment update made the current gate invalid"),
            })
        previous_gate = {
            booking["flight_id"]: gate_id
            for gate_id, items in tick.get("plan", {}).items()
            for booking in items
        }
    return {"trace": trace, "flights": flights, "scenario": scenario,
            "airport_positions": AIRPORTS, "gate_changes": gate_changes}


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>YYZ Flight Tracker Simulation</title>
<style>
:root{color-scheme:dark;--bg:#07111a;--panel:#111e29;--line:#253746;--muted:#91a4b2;--cyan:#41d9e8;--green:#59e0a2}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#e6f0f5;font:14px system-ui,Segoe UI,sans-serif}
header{height:60px;display:flex;align-items:center;gap:16px;padding:0 20px;background:#0d1821;border-bottom:1px solid var(--line)}
h1{font-size:17px;margin:0;letter-spacing:.04em}.brand{color:var(--cyan);font-weight:800}.sub{color:var(--muted);font-size:12px}
.tools{margin-left:auto;display:flex;align-items:center;gap:10px}button,select{background:#172733;border:1px solid #355060;color:#e8f4f8;border-radius:6px;padding:8px 11px}button{cursor:pointer}
main{padding:14px;display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:12px}.mapbox,.side,.bottom{background:var(--panel);border:1px solid var(--line);border-radius:9px;overflow:hidden}
.mapbox{position:relative;min-height:560px}.maphead{position:absolute;z-index:2;top:12px;left:14px;right:14px;display:flex;justify-content:space-between;pointer-events:none}.pill{background:#08151edc;border:1px solid #314958;border-radius:99px;padding:7px 11px;font-size:11px;color:#c5d5de}.maptools{position:absolute;z-index:3;top:58px;left:14px;display:flex;gap:6px}.maptools button{background:#08151eea}.maptools button.on{border-color:var(--cyan);color:var(--cyan)}
svg{width:100%;height:100%;min-height:560px;display:block;background:#0a1923}.graticule{stroke:#28404c;stroke-width:.6;opacity:.55;vector-effect:non-scaling-stroke}.land{fill:#17313b;stroke:#31515d;stroke-width:1.3;vector-effect:non-scaling-stroke}.route{fill:none;stroke:#3dbac7;stroke-width:1.2;opacity:.22;vector-effect:non-scaling-stroke}.airport{fill:#9db6c2;stroke:#d7edf2;stroke-width:1;vector-effect:non-scaling-stroke}.airport-label{fill:#9eb3bd;font-size:10px;letter-spacing:.08em}.plane{cursor:pointer;filter:drop-shadow(0 0 7px #59ecff)}.plane circle{fill:#101e29;stroke:#55e7f5;stroke-width:2;vector-effect:non-scaling-stroke}.plane text{font-size:13px;fill:#d9fcff;text-anchor:middle;dominant-baseline:central;pointer-events:none}.plane.selected circle{stroke:#ffe284;stroke-width:3}.trail{stroke:#62e2ed;stroke-width:2;opacity:.6;vector-effect:non-scaling-stroke}
.tooltip{position:fixed;z-index:8;display:none;min-width:230px;max-width:300px;padding:12px;background:#07121df2;border:1px solid #48c9d8;border-radius:8px;box-shadow:0 8px 28px #0009;pointer-events:none}.tooltip strong{font-size:15px;color:#70e7f1}.tooltip .dim{color:var(--muted);font-size:12px}.kv{display:flex;justify-content:space-between;gap:12px;margin-top:6px;font-size:12px}.kv span:last-child{text-align:right;color:#e8f3f7}
.side{padding:14px;max-height:760px;overflow:auto}.side h2,.bottom h2{font-size:13px;text-transform:uppercase;letter-spacing:.1em;color:#adc0ca;margin:0 0 12px}.stat{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #20313c}.stat span:first-child{color:var(--muted)}.flightlist{margin-top:18px}.flightrow{padding:9px 8px;border-radius:6px;border-bottom:1px solid #20313c;cursor:pointer}.flightrow:hover,.flightrow.active{background:#1b3541}.flightrow b{display:block}.flightrow small{color:var(--muted)}.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);margin-right:7px}.tabbar{display:flex;gap:5px;margin:8px 0}.tabbar button{font-size:11px;padding:6px 8px;flex:1}.tabbar button.active{border-color:var(--cyan);color:var(--cyan)}.boardrow{padding:8px;border-bottom:1px solid #20313c;font-size:12px}.boardrow b{display:block;color:#e5f4f8}.boardrow small{color:var(--muted)}.change-reason{border-bottom:1px dotted #8ab6c2;cursor:help;color:#f1d58a}.bottom{grid-column:1/-1;padding:13px 16px}.timeline{display:flex;gap:4px;align-items:center}.timeline button{padding:7px 10px}input[type=range]{flex:1;accent-color:var(--cyan)}#time{font-variant-numeric:tabular-nums;color:var(--cyan);min-width:58px;text-align:center}.notice{color:#a8bbc4;font-size:11px}.error{color:#ff9f8f;padding:20px}
@media(max-width:850px){main{grid-template-columns:1fr}.side{max-height:none}.mapbox,svg{min-height:440px}.bottom{grid-column:auto}header{flex-wrap:wrap;height:auto;padding:12px}.tools{margin-left:0}}
</style></head><body>
<header><div class="brand">◉ YYZ OPS</div><h1>Aircraft Tracker</h1><span class="sub">SCENARIO SIMULATION · NOT LIVE AIRCRAFT DATA</span>
<div class="tools"><select id="scenario"></select><button id="load">Load scenario</button><button id="play">▶ Play</button></div></header>
<main><section class="mapbox"><div class="maphead"><span class="pill" id="scenarioLabel">Loading…</span><span class="pill" id="airCount">0 tracked</span></div><div class="maptools"><button id="zoomOut" aria-label="Zoom out">−</button><button id="zoomIn" aria-label="Zoom in">+</button><button id="groundOnly">Ground aircraft only</button></div><svg id="map" viewBox="0 0 1000 560" role="img" aria-label="Illustrative map zoomed around Toronto Pearson"></svg></section>
<aside class="side"><h2>Operations snapshot</h2><div class="stat"><span>Simulation time</span><b id="clock">--:--</b></div><div class="stat"><span>Aircraft airborne</span><b id="airborne">0</b></div><div class="stat"><span>Aircraft at YYZ</span><b id="atYYZ">0</b></div><div class="stat"><span>Gate plan score</span><b id="score">—</b></div><div class="flightlist"><h2>Flight board</h2><div class="tabbar"><button data-tab="incoming" class="active">Incoming</button><button data-tab="departing">Departing</button><button data-tab="changes">Gate changes</button></div><div id="board"></div><h2 style="margin-top:18px">Aircraft at current time</h2><div id="flights"></div></div></aside>
<section class="bottom"><h2>Simulation controls</h2><div class="timeline"><button id="back">− 15m</button><input id="slider" type="range" min="0" max="1000" value="0"><button id="forward">+ 15m</button><span id="time">--:--</span><select id="speed"><option value="1">1×</option><option value="4" selected>4×</option><option value="12">12×</option><option value="30">30×</option></select></div><div class="notice">Hover over an aircraft for its route, aircraft type, gate and timing. Coordinates are illustrative airport locations; aircraft positions are interpolated from scenario schedules.</div></section></main>
<div class="tooltip" id="tip"></div>
<script>
const EMBEDDED_DATA=__EMBEDDED_DATA__;
const NS='http://www.w3.org/2000/svg';let DATA=null,T=0,playing=false,last=0,raf=0,selected=null;
const map=document.querySelector('#map'),tip=document.querySelector('#tip');let world=null,mapZoom=6,mapMode='regional',boardMode='incoming';
function el(tag,attrs={}){let x=document.createElementNS(NS,tag);for(let k in attrs)x.setAttribute(k,attrs[k]);return x}
function xy(pos){let lon=+pos[0],lat=+pos[1];return [(lon+180)/360*1000,(90-lat)/180*560]}
function drawBase(){map.innerHTML='';world=el('g',{id:'world'});map.appendChild(world);
// Longitude/latitude graticule and stylized continent silhouettes for orientation.
for(let lat=-60;lat<=60;lat+=30){let y=xy([0,lat])[1];world.appendChild(el('path',{d:`M0 ${y}H1000`,class:'graticule'}))}for(let lon=-150;lon<=150;lon+=30){let x=xy([lon,0])[0];world.appendChild(el('path',{d:`M${x} 0V560`,class:'graticule'}))}
const shapes=[[[ -168,70],[-140,72],[-125,58],[-112,52],[-100,50],[-83,47],[-67,50],[-55,45],[-63,25],[-81,8],[-96,15],[-107,30],[-118,33],[-127,48],[-150,58]], [[-80,12],[-69,8],[-51,-2],[-44,-20],[-53,-42],[-68,-55],[-77,-34]], [[-11,72],[24,70],[44,58],[37,45],[29,35],[10,36],[-10,48]], [[-17,36],[7,37],[30,30],[42,12],[35,-10],[20,-35],[4,-34],[-10,-7]], [[35,65],[70,70],[110,62],[145,50],[155,30],[135,10],[112,20],[95,8],[75,18],[60,35],[40,45]], [[112,-11],[153,-12],[150,-39],[130,-44],[114,-30]]];
for(const s of shapes){let pts=s.map(p=>xy(p));world.appendChild(el('polygon',{points:pts.map(p=>p.join(',')).join(' '),class:'land'}))}for(const [code,pos] of Object.entries(DATA?.airport_positions||{})){let [x,y]=xy(pos),marker=el('g',{class:'airport-marker'});marker.dataset.pos=`${x},${y}`;marker.setAttribute('transform',`translate(${x} ${y}) scale(${1/mapZoom})`);marker.appendChild(el('circle',{cx:0,cy:0,r:2.5,class:'airport'}));let label=el('text',{x:5,y:-4,class:'airport-label'});label.textContent=code;marker.appendChild(label);world.appendChild(marker)}applyMapView()}
function applyMapView(){if(!world)return;let z=mapMode==='ground'?Math.max(mapZoom,11):mapZoom,[cx,cy]=xy(DATA?.airport_positions?.YYZ||[-79.63,43.68]);world.setAttribute('transform',`translate(${500-cx*z} ${280-cy*z}) scale(${z})`);world.querySelectorAll('.airport-marker').forEach(m=>{const p=m.dataset.pos.split(',').map(Number);m.setAttribute('transform',`translate(${p[0]} ${p[1]}) scale(${1/z})`)})}
function flightState(f,t){for(const iv of f.intervals||[])if(t>=iv[0]&&t<iv[1])return {kind:'ground',pos:DATA.airport_positions.YYZ,gate:gateAt(f.id,t),airport:'YYZ'};
for(const l of f.legs){if(t>=l.departure&&t<=l.arrival){let span=Math.max(1,l.arrival-l.departure),p=(t-l.departure)/span;let a=l.from_pos,b=l.to_pos,lon1=+a[0],lon2=+b[0];if(Math.abs(lon2-lon1)>180)lon2+=lon1>0?-360:360;let lon=lon1+(lon2-lon1)*p;if(lon>180)lon-=360;if(lon< -180)lon+=360;return {kind:'air',pos:[lon,+a[1]+(+b[1]-+a[1])*p],leg:l,p}}}
let prev=f.legs.filter(l=>l.arrival<=t).at(-1);if(prev&&t-prev.arrival<90)return {kind:'ground',pos:prev.to_pos,airport:prev.to,gate:prev.to==='YYZ'?gateAt(f.id,t):null};let next=f.legs.find(l=>l.departure>t);if(next&&next.departure-t<90)return {kind:'ground',pos:next.from_pos,airport:next.from,gate:next.from==='YYZ'?gateAt(f.id,t):null};return null}
function gateAt(fid,t){let tick=DATA.trace.ticks[0];for(const x of DATA.trace.ticks){if(x.time<=t)tick=x;else break}for(const g in tick.plan)if(tick.plan[g].some(b=>b.flight_id===fid))return g;return 'Unassigned'}
function timeFmt(v){v=Math.round(v);let day=Math.floor(v/1440),m=((v%1440)+1440)%1440,s=String(Math.floor(m/60)).padStart(2,'0')+':'+String(m%60).padStart(2,'0');return day?s+` +${day}d`:s}
function gateForFlight(fid){for(let i=DATA.trace.ticks.length-1;i>=0;i--){for(const [gid,bs] of Object.entries(DATA.trace.ticks[i].plan))if(bs.some(b=>b.flight_id===fid))return gid}return 'Unassigned'}
function escapeHtml(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function renderBoard(){let board=document.querySelector('#board');if(boardMode==='changes'){let rows=(DATA.gate_changes||[]).map(c=>`<div class="boardrow"><b>${c.flight_id}</b><small>${c.time} · ${c.from_gate} → ${c.to_gate} · <span class="change-reason" title="${escapeHtml(c.reason)}">reason</span></small></div>`);board.innerHTML=rows.join('')||'<div class="boardrow"><small>No changed gate assignments in this scenario.</small></div>';return}let rows=[];for(const f of DATA.flights)for(const leg of f.legs){let gate=gateForFlight(f.id);if(boardMode==='incoming'&&leg.to==='YYZ')rows.push({f,leg,gate});if(boardMode==='departing'&&leg.from==='YYZ')rows.push({f,leg,gate})}rows.sort((a,b)=>(boardMode==='incoming'?a.leg.arrival-b.leg.arrival:a.leg.departure-b.leg.departure));board.innerHTML=rows.map(({f,leg,gate})=>`<div class="boardrow"><b>${f.carrier}${f.number} · ${leg.from} → ${leg.to}</b><small>${boardMode==='incoming'?'Arrives':'Departs'} ${timeFmt(boardMode==='incoming'?leg.arrival:leg.departure)} · ${gate==='Unassigned'?'Gate pending':'Gate '+gate}</small></div>`).join('')||'<div class="boardrow"><small>No flights in this list.</small></div>'}
function showTip(f,state,event){let l=state.leg;let route=l?`${l.from} → ${l.to}`:(state.airport||'YYZ apron');tip.innerHTML=`<strong>${f.carrier}${f.number}</strong><div class="dim">${f.id}</div><div class="kv"><span>Aircraft</span><span>${f.aircraft}</span></div><div class="kv"><span>Route</span><span>${route}</span></div><div class="kv"><span>Status</span><span>${state.kind==='air'?'En route':'On ground'}</span></div><div class="kv"><span>Gate</span><span>${state.kind==='ground'?state.gate||'—':'—'}</span></div><div class="kv"><span>Category</span><span>${f.category}</span></div>${l?`<div class="kv"><span>Scheduled</span><span>${timeFmt(l.departure)}–${timeFmt(l.arrival)}</span></div>`:''}<div class="kv"><span>Wingspan / bridge</span><span>${f.wingspan}m / ${f.jetbridge?'required':'not required'}</span></div>`;tip.style.display='block';tip.style.left=Math.min(innerWidth-310,event.clientX+14)+'px';tip.style.top=Math.min(innerHeight-210,event.clientY+14)+'px'}
function render(){if(!DATA)return;applyMapView();const active=[];for(const f of DATA.flights){let s=flightState(f,T);if(s&&!(mapMode==='ground'&&!(s.kind==='ground'&&s.airport==='YYZ')))active.push({f,s})}document.querySelector('#airCount').textContent=active.length+' tracked';document.querySelector('#airborne').textContent=active.filter(x=>x.s.kind==='air').length;document.querySelector('#atYYZ').textContent=active.filter(x=>x.s.kind==='ground'&&x.s.airport==='YYZ').length;document.querySelector('#clock').textContent=timeFmt(T);document.querySelector('#time').textContent=timeFmt(T);document.querySelector('#slider').value=Math.round((T-DATA.trace.time_min)/Math.max(1,DATA.trace.time_max-DATA.trace.time_min)*1000);
map.querySelectorAll('.dynamic').forEach(n=>n.remove());for(const {f,s} of active){let [x,y]=xy(s.pos);if(s.leg){let a=xy(s.leg.from_pos),b=xy(s.leg.to_pos),d=`M${a[0]} ${a[1]} Q${(a[0]+b[0])/2} ${Math.min(a[1],b[1])-24} ${b[0]} ${b[1]}`;world.appendChild(el('path',{d,class:'route dynamic'}))}let z=mapMode==='ground'?Math.max(mapZoom,11):mapZoom,g=el('g',{class:'plane dynamic'+(selected===f.id?' selected':'')});g.setAttribute('transform',`translate(${x} ${y}) scale(${1/z})`);let c=el('circle',{r:10});g.appendChild(c);let tx=el('text',{y:1});tx.textContent='✈';g.appendChild(tx);g.addEventListener('pointerenter',e=>showTip(f,s,e));g.addEventListener('pointermove',e=>{tip.style.left=Math.min(innerWidth-310,e.clientX+14)+'px';tip.style.top=Math.min(innerHeight-210,e.clientY+14)+'px'});g.addEventListener('pointerleave',()=>tip.style.display='none');g.addEventListener('click',()=>{selected=f.id;render();document.querySelectorAll('.flightrow').forEach(r=>r.classList.toggle('active',r.dataset.id===f.id))});world.appendChild(g)}
document.querySelector('#flights').innerHTML=active.map(({f,s})=>`<div class="flightrow ${selected===f.id?'active':''}" data-id="${f.id}" onclick="selected='${f.id}';render()"><b><i class="dot"></i>${f.carrier}${f.number} <small>${f.aircraft}</small></b><small>${s.kind==='air'?'En route '+s.leg.from+' → '+s.leg.to:'Ground · '+(s.gate||s.airport||'—')}</small></div>`).join('')||'<div class="sub">No aircraft at this simulation time.</div>';renderBoard()}
async function loadScenario(name){document.querySelector('#scenarioLabel').textContent='Running '+name+'…';let r=await fetch('/api/run?scenario='+encodeURIComponent(name));let j=await r.json();if(!r.ok){document.querySelector('#scenarioLabel').textContent=j.error||'Simulation error';return}DATA=j;drawBase();T=DATA.trace.time_min;selected=null;document.querySelector('#scenarioLabel').textContent=name+' · '+DATA.trace.solution;document.querySelector('#score').textContent=DATA.trace.result.status==='OK'?Number(DATA.trace.result.score).toFixed(2):'FAILED';render()}
function frame(ts){if(!playing)return;if(!last)last=ts;T+=((ts-last)/1000)*Number(document.querySelector('#speed').value);last=ts;if(T>=DATA.trace.time_max){T=DATA.trace.time_max;playing=false;document.querySelector('#play').textContent='▶ Play'}render();raf=requestAnimationFrame(frame)}
document.querySelector('#play').onclick=()=>{playing=!playing;last=0;document.querySelector('#play').textContent=playing?'❚❚ Pause':'▶ Play';if(playing)raf=requestAnimationFrame(frame)};
document.querySelector('#slider').oninput=e=>{if(DATA){T=DATA.trace.time_min+(e.target.value/1000)*(DATA.trace.time_max-DATA.trace.time_min);render()}};
document.querySelector('#back').onclick=()=>{T=Math.max(DATA.trace.time_min,T-15);render()};document.querySelector('#forward').onclick=()=>{T=Math.min(DATA.trace.time_max,T+15);render()};document.querySelector('#load').onclick=()=>loadScenario(document.querySelector('#scenario').value);
document.querySelector('#zoomIn').onclick=()=>{mapZoom=Math.min(16,mapZoom+1);render()};document.querySelector('#zoomOut').onclick=()=>{mapZoom=Math.max(2,mapZoom-1);if(mapMode==='ground'&&mapZoom<11){mapMode='regional';document.querySelector('#groundOnly').classList.remove('on')}render()};document.querySelector('#groundOnly').onclick=()=>{mapMode=mapMode==='ground'?'regional':'ground';document.querySelector('#groundOnly').classList.toggle('on',mapMode==='ground');render()};document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{boardMode=b.dataset.tab;document.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('active',x===b));renderBoard()});
async function init(){if(EMBEDDED_DATA){DATA=EMBEDDED_DATA;drawBase();let sel=document.querySelector('#scenario');sel.innerHTML=`<option>${DATA.scenario}</option>`;T=DATA.trace.time_min;document.querySelector('#scenarioLabel').textContent=DATA.scenario+' · '+DATA.trace.solution;document.querySelector('#score').textContent=DATA.trace.result.status==='OK'?Number(DATA.trace.result.score).toFixed(2):'FAILED';render();return}let r=await fetch('/api/scenarios'),j=await r.json();let sel=document.querySelector('#scenario');sel.innerHTML=j.scenarios.map(s=>`<option>${s}</option>`).join('');if(j.scenarios.includes('busy_day'))sel.value='busy_day';await loadScenario(sel.value||'simple')};init().catch(e=>document.querySelector('#scenarioLabel').textContent='Could not load: '+e)
</script></body></html>'''


def make_handler(solution, static_path):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE.replace("__EMBEDDED_DATA__", "null").encode("utf-8"))
            elif parsed.path == "/api/scenarios":
                names = sorted(Path(p).stem for p in glob.glob("flight_data/*.json"))
                self._send(200, "application/json", json.dumps({"scenarios": names}).encode())
            elif parsed.path == "/api/run":
                name = (parse_qs(parsed.query).get("scenario") or [""])[0]
                try:
                    payload = build_payload(name, solution, static_path)
                except Exception as exc:  # Send simulation errors to the page.
                    self._send(400, "application/json", json.dumps({"error": str(exc)}).encode())
                    return
                self._send(200, "application/json", json.dumps(payload).encode())
            else:
                self._send(404, "text/plain", b"Not found")

        def log_message(self, fmt, *args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description="Scenario-driven YYZ aircraft tracker")
    parser.add_argument("--serve", action="store_true", help="run the local tracker website")
    parser.add_argument("--scenario", default="busy_day")
    parser.add_argument("--solution", default="solution_scored")
    parser.add_argument("--static", default="static_info.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--out", default="flight_tracker_demo.html")
    args = parser.parse_args()
    if args.serve:
        server = ThreadingHTTPServer((args.host, args.port), make_handler(args.solution, args.static))
        print(f"Flight tracker running at http://{args.host}:{args.port}/ (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    else:
        payload = build_payload(args.scenario, args.solution, args.static)
        # Static exports embed the scenario and run without a server.
        html = PAGE.replace("__EMBEDDED_DATA__", json.dumps(payload))
        Path(args.out).write_text(html, encoding="utf-8")
        print(f"Simulation: {payload['scenario']} with {args.solution}")
        print(f"Result: {payload['trace']['result']['status']} | score {payload['trace']['result']['score']}")
        print(f"Tracked flights: {len(payload['flights'])} | output: {args.out}")


if __name__ == "__main__":
    main()
