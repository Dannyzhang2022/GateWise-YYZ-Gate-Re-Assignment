"""
Gate Management System -- visualizer.

Two ways to use it:

1. Interactive server (recommended for students):

       python visualize.py --serve

   Opens a page with scenario and solution dropdowns (defaults to
   "solution_scored" when available). Press Run and it executes your ACTUAL
   code -- freshly imported, so saved edits are picked up without restarting --
   against the chosen scenario, then renders gates as rows, flight occupancy as
   bars on a time axis, gate outages as red blocks, a play/pause "operational
   clock" playhead, and a decision-tick stepper. Switch scenarios from the same
   page; nothing is regenerated to disk.

2. Static export (for sharing a fixed result, e.g. in a report):

       python visualize.py --scenario flight_data/cascade_2.json --solution solution_kd --open
       python visualize.py --all --open

   Writes one self-contained HTML file per scenario into visualization/.

Both modes share the same renderer; the server just feeds it live data instead
of a value baked in at generation time.
"""

import argparse
import glob
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from evaluator import run_sim

OUT_DIR = "visualization"


# ---------------------------------------------------------------------------
# Shared trace + page generation
# ---------------------------------------------------------------------------
def list_scenarios(pattern: str = "flight_data/*.json") -> list:
    return sorted(Path(p).stem for p in glob.glob(pattern))


def list_solutions(pattern: str = "solution*.py") -> list:
    """Return importable solution modules available in the project root."""
    return sorted(Path(p).stem for p in glob.glob(pattern))


def _run_trace(static_path: str, scenario_path: str, solution_module: str) -> dict:
    result = run_sim(static_path, scenario_path, solution_module, verbose=False, record=True)
    trace = result["trace"]
    trace["result"] = {k: result.get(k) for k in
                       ("status", "score", "reason", "assigned", "unassigned", "breakdown")}
    trace["scenario_name"] = Path(scenario_path).stem
    return trace


def _render_html(mode: dict) -> str:
    return _TEMPLATE.replace("__MODE_JSON__", json.dumps(mode))


def generate(static_path: str, scenario_path: str, solution_module: str, out_path: str) -> tuple:
    trace = _run_trace(static_path, scenario_path, solution_module)
    html = _render_html({"server": False, "trace": trace})
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path, trace["result"]


# ---------------------------------------------------------------------------
# Interactive server
# ---------------------------------------------------------------------------
def _make_handler(static_path: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, content_type: str, body: bytes):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: dict, status: int = 200):
            self._send(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))

        def do_GET(self):  # noqa: N802 - stdlib method name
            parsed = urlsplit(self.path)
            qs = parse_qs(parsed.query)

            if parsed.path in ("/", "/index.html"):
                html = _render_html({"server": True, "trace": None})
                self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))
                return

            if parsed.path == "/api/scenarios":
                self._send_json({"scenarios": list_scenarios()})
                return

            if parsed.path == "/api/solutions":
                self._send_json({"solutions": list_solutions()})
                return

            if parsed.path == "/api/run":
                scenario = (qs.get("scenario") or [""])[0]
                solution = (qs.get("solution") or ["solution"])[0]
                scenario_path = f"flight_data/{scenario}.json"

                if not scenario or not os.path.exists(scenario_path):
                    self._send_json({"error": f"Unknown scenario '{scenario}'."}, status=404)
                    return

                # Force a fresh import so saved edits to the student's module are
                # picked up without restarting this server.
                import sys
                sys.modules.pop(solution, None)

                try:
                    trace = _run_trace(static_path, scenario_path, solution)
                except ModuleNotFoundError as exc:
                    self._send_json({"error": f"Could not import '{solution}.py': {exc}"})
                except SyntaxError as exc:
                    self._send_json({"error": f"Syntax error in '{solution}.py': {exc}"})
                except Exception as exc:  # noqa: BLE001 - surface any failure to the page
                    self._send_json({"error": f"{type(exc).__name__}: {exc}"})
                else:
                    self._send_json({"trace": trace})
                return

            self._send(404, "text/plain; charset=utf-8", b"not found")

        def log_message(self, fmt, *args):  # quieter than the default access log
            pass

    return Handler


def serve(static_path: str = "static_info.json", host: str = "127.0.0.1",
         port: int = 8765, open_browser: bool = True):
    handler = _make_handler(static_path)
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"GMS visualizer serving at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="GMS visualizer")
    parser.add_argument("--scenario", default="flight_data/simple.json")
    parser.add_argument("--static", default="static_info.json")
    parser.add_argument("--solution", default="solution_kd")
    parser.add_argument("--out", default=None, help="output HTML path")
    parser.add_argument("--all", action="store_true", help="render every flight_data/*.json to disk")
    parser.add_argument("--open", dest="open_browser", action="store_true",
                        help="open the result(s) in a browser")
    parser.add_argument("--serve", action="store_true",
                        help="launch the interactive server instead of writing files")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    if args.serve:
        serve(args.static, args.host, args.port, open_browser=True)
        return 0

    scenarios = sorted(glob.glob("flight_data/*.json")) if args.all else [args.scenario]
    produced = []
    for scenario in scenarios:
        name = Path(scenario).stem
        out = args.out if (args.out and not args.all) else os.path.join(OUT_DIR, f"{name}__{args.solution}.html")
        path, result = generate(args.static, scenario, args.solution, out)
        print(f"  {name:<18} {result['status']:<8} -> {path}")
        produced.append(path)

    if args.open_browser:
        for path in produced:
            webbrowser.open(Path(path).resolve().as_uri())
    return 0


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gate Management Dashboard</title>
<style>
  :root { --labelw: 168px; --ruler: 30px; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #0e1116; color: #e6edf3;
         font: 14px/1.45 -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; }
  header { position: sticky; top: 0; z-index: 1000; background: #0e1116;
           padding: 16px 22px; border-bottom: 1px solid #232a33;
           display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
  header h1 { font-size: 18px; margin: 0; font-weight: 650; }
  header .sub { color: #8b949e; font-size: 13px; }
  .badge { padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; }
  .badge.ok { background: #1a3326; color: #3fb950; }
  .badge.fail { background: #3a1d22; color: #f85149; }
  .badge.manual { background: #3a2a12; color: #f0b34a; }
  .wrap { padding: 18px 22px 40px; }
  .toolbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
             background: #161b22; border: 1px solid #232a33; border-radius: 10px;
             padding: 12px 16px; margin-bottom: 16px; }
  .toolbar button { background: #21262d; color: #e6edf3; border: 1px solid #30363d;
                    border-radius: 7px; padding: 7px 13px; cursor: pointer; font-size: 13px; }
  .toolbar button:hover { background: #2b333c; }
  .toolbar button:disabled { opacity: .5; cursor: default; }
  .toolbar button.primary { background: #238636; border-color: #2ea043; font-weight: 650; }
  .toolbar select, .toolbar input[type=text] {
    background: #0d1117; color: #e6edf3; border: 1px solid #30363d; border-radius: 7px;
    padding: 6px 10px; font-size: 13px; font-family: ui-monospace, Consolas, monospace;
  }
  .toolbar .group { display: flex; align-items: center; gap: 8px; }
  .toolbar .sep { width: 1px; height: 26px; background: #30363d; }
  .clock { font-variant-numeric: tabular-nums; font-weight: 700; font-size: 16px; min-width: 70px; }
  .muted { color: #8b949e; font-size: 12px; }
  input[type=range] { accent-color: #2ea043; }

  .runbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
            background: #161b22; border: 1px solid #2ea043; border-radius: 10px;
            padding: 12px 16px; margin-bottom: 16px; }
  .tabs { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 8px; margin: 0 0 14px; border-bottom: 1px solid #30363d; }
  .tab-button { border: 1px solid #30363d; border-bottom: 0; border-radius: 8px 8px 0 0;
                padding: 9px 16px; background: #161b22; color: #8b949e; font-weight: 700;
                letter-spacing: .04em; cursor: pointer; }
  .tab-button.active { background: #238636; border-color: #2ea043; color: #fff; }
  .manual-override-button { margin-left: auto; align-self: center; padding: 8px 12px;
                            border: 1px solid #9e6a03; border-radius: 7px; background: #2b2111;
                            color: #f0b34a; font-size: 11px; font-weight: 800; letter-spacing: .04em; cursor: pointer; }
  .manual-override-button:hover, .manual-override-button.active { background: #5a3b0b; color: #fff; }
  .manual-override-button:disabled { opacity: .45; cursor: not-allowed; }
  .tab-pane { display: none; }
  .tab-pane.active { display: block; }
  .flight-boards { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 16px; }
  .flight-board { overflow: hidden; background: #0d1117; border: 1px solid #30363d; border-radius: 9px; }
  .flight-board h2 { margin: 0; padding: 12px 14px; background: #1b2530; color: #e6edf3;
                     font-size: 16px; letter-spacing: .07em; }
  .flight-board table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  .flight-board th { padding: 8px 10px; color: #8b949e; text-align: left; font-size: 10px;
                     letter-spacing: .06em; border-bottom: 1px solid #30363d; }
  .flight-board td { padding: 9px 10px; border-bottom: 1px solid #1b222b; white-space: nowrap; }
  .flight-board tr:last-child td { border-bottom: 0; }
  .flight-board .board-time { color: #e6edf3; font-weight: 750; font-size: 15px; }
  .flight-board .board-flight { font-weight: 750; }
  .flight-board .board-status { font-size: 11px; font-weight: 750; }
  .flight-board .on-time { color: #3fb950; }
  .flight-board .landed { color: #58a6ff; }
  .flight-board .departed { color: #a371f7; }
  .flight-board .delayed, .flight-board .gate-changed, .flight-board .diverted { color: #f0883e; }
  .flight-board .cancelled { color: #f85149; }
  .flight-board .board-empty { color: #8b949e; text-align: center; padding: 18px; }
  .compare-caption { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap;
                     margin: 12px 0; color: #8b949e; font-size: 12px; }
  .solution-score-strip { display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
                          margin: -7px 2px 14px; color: #8b949e; font-size: 12px; }
  .compare-completions { display: flex; flex-wrap: wrap; gap: 10px; margin: 0 0 14px; }
  .compare-completion { display: inline-flex; align-items: center; gap: 8px; padding: 7px 10px;
                        background: #161b22; border: 1px solid #30363d; border-radius: 8px; }
  .compare-timelines { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
  .compare-timeline-pane { min-width: 0; background: #0d1117; border: 1px solid #30363d; border-radius: 9px; overflow: hidden; }
  .compare-timeline-pane h4 { margin: 0; padding: 10px 12px; background: #1b2530; color: #e6edf3; font-size: 13px; }
  .compare-timeline-scroll { overflow-x: auto; }
  .compare-timeline-chart { position: relative; min-width: 680px; padding-bottom: 5px; }
  .compare-time-ruler, .compare-timeline-row { display: grid; grid-template-columns: 72px minmax(0, 1fr); }
  .compare-time-ruler { height: 30px; border-bottom: 1px solid #30363d; }
  .compare-time-label { position: absolute; top: 8px; transform: translateX(-50%); color: #8b949e; font-size: 9px; white-space: nowrap; }
  .compare-time-label:first-child { transform: none; }
  .compare-timeline-row { min-height: 30px; }
  .compare-timeline-gate { display: flex; align-items: center; padding-left: 8px; color: #8b949e; font-size: 10px; border-bottom: 1px solid #1b222b; }
  .compare-timeline-track { position: relative; min-width: 0; min-height: 30px; border-bottom: 1px solid #1b222b; }
  .compare-time-gridline { position: absolute; top: 0; bottom: 0; width: 1px; background: #26313c; pointer-events: none; }
  .compare-time-gridline.major { background: #364453; }
  .compare-timeline-bar { position: absolute; top: 5px; height: 19px; box-sizing: border-box; overflow: hidden; white-space: nowrap;
                          border-radius: 3px; padding: 2px 3px; color: #0d1117; font-size: 9px; font-weight: 750; }
  .compare-timeline-bar.compare-bar-different { outline: 2px solid #f0883e; z-index: 2; }
  .compare-playhead { position: absolute; top: 30px; bottom: 5px; width: 2px; background: #f85149; z-index: 3; pointer-events: none; }
  @media (max-width: 950px) { .compare-timelines { grid-template-columns: 1fr; } }
  .runbar label { font-size: 12px; color: #8b949e; font-weight: 650; text-transform: uppercase; letter-spacing: .04em; }
  .errorBox { display: none; background: #2a1216; border: 1px solid #f85149; color: #ffb4ad;
              border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; font-size: 13px;
              font-family: ui-monospace, Consolas, monospace; white-space: pre-wrap; }

  .chart { position: relative; background: #0d1117; border: 1px solid #232a33;
           border-radius: 10px; padding: 0 0 8px; overflow: hidden; min-height: 80px; }
  .placeholder { padding: 30px; text-align: center; color: #6e7681; }
  .ruler { display: flex; height: var(--ruler); border-bottom: 1px solid #232a33; position: relative; }
  .ruler .spacer { width: var(--labelw); flex: 0 0 var(--labelw); }
  .ruler .track { flex: 1; position: relative; }
  .ruler .hr { position: absolute; top: 0; bottom: 0; transform: translateX(-50%);
               font-size: 11px; color: #6e7681; padding-top: 8px; }
  .row { display: flex; height: 38px; border-bottom: 1px solid #161b22; }
  .row .label { width: var(--labelw); flex: 0 0 var(--labelw); display: flex; align-items: center;
                gap: 8px; padding: 0 10px; border-right: 1px solid #232a33; border-left: 4px solid transparent; }
  .row .label .gid { font-weight: 700; }
  .row .label .chip { font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 4px; }
  .row .label .status { margin-left: auto; font-size: 11px; color: #6e7681; white-space: nowrap; }
  .row.inuse { background: #11161d; }
  .row.down  { background: #1c1416; }
  .track { flex: 1; position: relative; }
  .bar { position: absolute; top: 6px; height: 26px; border-radius: 5px; overflow: hidden;
         display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 650;
         color: #0d1117; white-space: nowrap; box-shadow: inset 0 0 0 1px rgba(0,0,0,.25); }
  .bar.live { outline: 2px solid #fff; outline-offset: -1px; }
  .outage { position: absolute; top: 4px; height: 30px; border-radius: 5px;
            background: repeating-linear-gradient(45deg, #5a1d22, #5a1d22 6px, #7a262d 6px, #7a262d 12px);
            border: 1px solid #f85149; color: #ffb4ad; font-size: 10px; font-weight: 700;
            display: flex; align-items: center; justify-content: center; }

  .overlay { position: absolute; left: 0; right: 0; top: var(--ruler); bottom: 8px; pointer-events: none; }
  .grid { position: absolute; top: 0; bottom: 0; width: 1px; background: #1b222b; }
  .playhead { position: absolute; top: 0; bottom: 0; width: 2px; background: #58a6ff;
              box-shadow: 0 0 8px #58a6ff; }

  .panels { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-top: 16px; }
  .panel { background: #161b22; border: 1px solid #232a33; border-radius: 10px; padding: 12px 16px; }
  .panel h3 { margin: 0 0 8px; font-size: 13px; color: #8b949e; font-weight: 650; text-transform: uppercase; letter-spacing: .04em; }
  .log { font-family: ui-monospace, Consolas, monospace; font-size: 12px; white-space: pre-wrap; color: #c9d1d9; }
  .log .o { color: #f0883e; } .log .a { color: #3fb950; } .log .r { color: #58a6ff; }
  .log .c { color: #f85149; } .log .u { color: #8b949e; }
  .kv { display: flex; justify-content: space-between; padding: 3px 0; border-bottom: 1px solid #1b222b; }
  .scroll { max-height: 260px; overflow: auto; }
  .count { font-size: 11px; color: #8b949e; font-weight: 600; margin-bottom: 6px; }
  .qrow { display: flex; align-items: center; gap: 8px; padding: 4px 0; border-bottom: 1px solid #1b222b; font-size: 12px; }
  .fchip { color: #0d1117; font-weight: 700; border-radius: 4px; padding: 1px 7px; min-width: 46px; text-align: center; }
  .qcat { font-size: 10px; font-weight: 700; width: 36px; }
  .qarrow { font-weight: 600; flex: 1; }
  .qarrow.a { color: #3fb950; } .qarrow.r { color: #58a6ff; } .qarrow.u { color: #6e7681; }
  .qgate { font-weight: 700; padding: 1px 8px; border-radius: 4px; background: #21262d; }

  .banner { background: #161b22; border: 1px solid #30363d; border-left: 4px solid #f0883e;
            border-radius: 8px; padding: 10px 14px; margin-bottom: 12px; font-size: 13px;
            display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }
  .banner .ev { font-weight: 650; }
  .banner .ev.o { color: #f0883e; } .banner .ev.r { color: #58a6ff; } .banner .ev.c { color: #f85149; }
  @keyframes pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(88,166,255,.85); } 50% { box-shadow: 0 0 0 5px rgba(88,166,255,0); } }
  .bar.moved { outline: 2px solid #58a6ff; outline-offset: -1px; animation: pulse 1.4s infinite; z-index: 3; }
  .bar.ghost { opacity: .4; background: #30363d !important; border: 1px dashed #8b949e; color: #8b949e; z-index: 1; }
  .bar.emergency { box-shadow: inset 0 0 0 2px #f85149, 0 0 7px rgba(248,81,73,.55); font-weight: 800; }
  .sim-shell { overflow-x: auto; border: 1px solid #232a33; border-radius: 10px; background: #0d1117; }
  .sim-map { position: relative; min-width: 1200px; height: auto; min-height: 624px;
             aspect-ratio: 1500 / 780; overflow: hidden;
             background: radial-gradient(ellipse at 54% 50%, #15202a 0, #0d1117 76%); }
  .airport-footprint { position: absolute; inset: 0; width: 100%; height: 100%; }
  .airport-footprint .building { fill: #1c2732; stroke: #465566; stroke-width: 3; }
  .airport-footprint .pier { fill: #202d39; stroke: #526274; stroke-width: 2; }
  .airport-footprint .apron { fill: #111a22; stroke: #354250; stroke-width: 2; }
  .airport-footprint .taxi { fill: none; stroke: #526274; stroke-width: 14; stroke-dasharray: 10 8; }
  .airport-footprint .runway-center { fill: none; stroke: #d7dee6; stroke-width: 4; stroke-dasharray: 12 12; opacity: .8; }
  .airport-footprint text { fill: #aab6c2; font: 600 12px -apple-system,Segoe UI,Roboto,Arial,sans-serif; letter-spacing: .1px; }
  .airport-footprint .terminal-label { fill: #c9d1d9; font-size: 14px; font-weight: 650; }
  .airport-footprint .small-label { fill: #8b949e; font-size: 10px; letter-spacing: .1px; }
  .airport-footprint .runway-label { fill: #c9d1d9; font-size: 12px; font-weight: 700; letter-spacing: .1px; }
  .airport-footprint .terminal-label, .airport-footprint .runway-label { text-anchor: middle; }
  .sim-gate { position: absolute; transform: translate(-50%,-50%) rotate(90deg); width: 38px; height: 24px;
              border: 1px solid #44515f; border-radius: 5px; background: #202833; color: #c9d1d9;
              display: grid; place-items: center; font: 650 12px -apple-system,Segoe UI,Roboto,Arial,sans-serif; }
  .sim-gate-label { transform: rotate(-90deg); white-space: nowrap; }
  .sim-gate.occupied { border-color: #f0f6fc; box-shadow: 0 0 8px rgba(240,246,252,.35); }
  .sim-gate.down { border-color: #f85149; color: #ffb4ad; background: #35191d; }
  .sim-gate.manual-available { border-color: #3fb950; background: #173522; color: #b7f0c2; cursor: copy;
                               box-shadow: 0 0 10px rgba(63,185,80,.5); }
  .sim-gate.manual-current { border-color: #58a6ff; background: #172f49; color: #d2e7ff; }
  .sim-gate.manual-blocked { opacity: .55; }
  .sim-gate.manual-drop-target { outline: 3px solid #fff; outline-offset: 3px; }
  .sim-plane { position: absolute; z-index: 5; transform: translate(-50%,-50%); min-width: 78px;
               display: flex; flex-direction: column; align-items: center; pointer-events: auto;
               transition: left .22s linear, top .22s linear; }
  .sim-plane.manual-draggable { cursor: grab; }
  .sim-plane.manual-draggable:active { cursor: grabbing; }
  .sim-plane.manual-dragging { opacity: .4; }
  .manual-override-notice { margin: 0 0 14px; padding: 10px 13px; border: 1px solid #9e6a03;
                            border-left: 4px solid #f0b34a; border-radius: 8px; background: #241d12;
                            color: #f0d9a5; font-size: 12px; }
  .manual-override-notice strong { color: #f0b34a; }
  .manual-override-feedback { display: block; margin-top: 4px; color: #c9d1d9; }
  .sim-plane-label { max-width: 170px; margin-bottom: 3px; padding: 2px 6px; border-radius: 4px;
                     background: #0d1117; color: #e6edf3; border: 1px solid #303a46;
                     font: 650 11px -apple-system,Segoe UI,Roboto,Arial,sans-serif; white-space: nowrap; }
  .sim-plane-svg { display: block; height: 40px; overflow: visible; transform-origin: 50% 50%;
                   transition: transform .3s linear; filter: drop-shadow(0 3px 3px rgba(0,0,0,.55)); }
  .sim-plane-shape { stroke: rgba(255,255,255,.9); stroke-width: 2; stroke-linejoin: round; }
  .sim-plane-highlight { fill: none; stroke: rgba(255,255,255,.58); stroke-width: 2; stroke-linecap: round; }
  .sim-plane-priority { position: absolute; right: -4px; top: -8px; padding: 0 4px; border-radius: 8px;
                        background: #f85149; color: white; font-size: 9px; font-weight: 800; }
  .sim-plane.bridge .sim-plane-svg { filter: drop-shadow(0 0 4px #58a6ff) drop-shadow(0 3px 3px rgba(0,0,0,.55)); }
  .sim-plane-detail { color: #c9d1d9; background: rgba(13,17,23,.88); border-radius: 4px;
                      padding: 2px 5px; margin-top: 3px; font: 10px -apple-system,Segoe UI,Roboto,Arial,sans-serif; white-space: nowrap; }
  @media (max-width: 600px) { .sim-map { min-height: 624px; } }
  .legend { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 14px; color: #8b949e; font-size: 12px; align-items: center; }
  .legend .sw { display: inline-block; width: 14px; height: 14px; border-radius: 3px; vertical-align: -2px; margin-right: 5px; }
  .dis-entry { padding: 6px 0; border-bottom: 1px solid #1b222b; }
  .dis-entry .dis-time { color: #8b949e; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 2px; }
  .dis-entry .dis-line { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px;
                         font-size: 13px; padding: 5px 0; color: #c9d1d9; }
  .dis-entry .dis-kind { font-weight: 750; letter-spacing: .02em; }
  .dis-entry .dis-line.disruption .dis-kind { color: #f0883e; }
  .dis-entry .dis-line.solution .dis-kind { color: #58a6ff; }
  .dis-entry .dis-reason { color: #8b949e; }
  .gate-explanation { margin: 6px 0 10px 18px; padding: 8px 10px; background: #111820;
                      border: 1px solid #30363d; border-radius: 7px; font-size: 12px; }
  .gate-explanation summary { color: #c9d1d9; cursor: pointer; font-weight: 700; }
  .gate-explanation p { margin: 7px 0; color: #8b949e; }
  .gate-candidates { display: grid; gap: 5px; }
  .gate-candidate { padding: 6px 8px; border-left: 3px solid #484f58; background: #0d1117; }
  .gate-candidate.selected { border-left-color: #3fb950; }
  .gate-candidate.blocked { border-left-color: #f85149; color: #f0883e; }
  .gate-candidate strong { color: #e6edf3; }
  .gate-candidate .gate-cost { color: #8b949e; }
</style>
</head>
<body>
<header>
  <h1 id="title">Gate Management Dashboard</h1>
</header>

<div class="wrap">
  <div class="tabs" role="tablist" aria-label="Dashboard sections">
    <button class="tab-button active" id="visualTabButton" role="tab" aria-selected="true" aria-controls="visualTab">VISUAL SIMULATION</button>
    <button class="tab-button" id="boardsTabButton" role="tab" aria-selected="false" aria-controls="boardsTab">DEPARTURES AND ARRIVALS</button>
    <button class="tab-button" id="compareTabButton" role="tab" aria-selected="false" aria-controls="compareTab">COMPARE SOLUTIONS</button>
    <button class="tab-button" id="moreInfoTabButton" role="tab" aria-selected="false" aria-controls="moreInfoTab">MORE INFO</button>
    <button type="button" id="manualOverrideButton" class="manual-override-button" disabled>MANUAL OVERRIDE</button>
  </div>

  <div id="errorBox" class="errorBox"></div>

  <section id="visualTab" class="tab-pane active" role="tabpanel" aria-labelledby="visualTabButton">
  <div id="runbar" class="runbar" style="display:none">
    <div class="group">
      <label>Scenario</label>
      <select id="scenarioSelect" class="scenario-select"></select>
    </div>
    <div class="group">
      <label>Solution</label>
      <select id="solutionSelect" class="solution-select"></select>
    </div>
    <button id="runBtn" class="primary run-button">&#9654; Run</button>
    <span class="muted run-status" id="runStatus"></span>
  </div>

  <div class="toolbar">
    <div class="group">
      <button id="play" class="primary">&#9654; Play</button>
      <button id="reset">&#8634; Reset</button>
      <span class="clock" id="clock">--:--</span>
    </div>
    <div class="group" style="flex:1; min-width:240px;">
      <span class="muted">clock</span>
      <input type="range" id="scrub" min="0" max="1000" value="0" style="flex:1;">
    </div>
    <div class="group">
      <span class="muted">speed</span>
      <select id="speed" class="playback-speed">
        <option value="4">0.1x</option>
        <option value="10">0.25x</option>
        <option value="20">0.5x</option>
        <option value="40" selected>1x</option>
        <option value="80">2x</option>
        <option value="160">4x</option>
      </select>
    </div>
    <div class="sep"></div>
    <div class="group">
      <span class="muted">plan&nbsp;@</span>
      <button id="tprev">&#9664;</button>
      <span class="clock" id="tickLabel" style="font-size:14px; min-width:90px; text-align:center;"></span>
      <button id="tnext">&#9654;</button>
      <button id="playDec" class="primary">&#9654; Decisions</button>
    </div>
  </div>

  <div class="solution-score-strip" data-score-strip>
    <span class="solution-score-name" data-score-name>Solution: —</span>
    <span class="badge" data-score-result>NOT RUN</span>
  </div>

  <div id="manualOverrideNotice" class="manual-override-notice" hidden>
    <strong id="manualOverrideHeading">Manual override.</strong>
    <span id="manualOverrideInstructions">Playback is stopped. Drag an active airplane onto a green available gate.</span>
    <span id="manualOverrideFeedback" class="manual-override-feedback" role="status" aria-live="polite"></span>
  </div>

  <div class="panel" style="margin-top:16px">
    <h3>YYZ gate simulation <span class="muted" id="simTime"></span></h3>
    <div class="sim-shell"><div class="sim-map" id="simMap"></div></div>
  </div>

  <div class="panel" style="margin-top:16px">
    <h3>Disruption &amp; response timeline</h3>
    <div class="scroll" id="disruptionLog" style="max-height: 260px"></div>
  </div>
  </section>

  <section id="moreInfoTab" class="tab-pane" role="tabpanel" aria-labelledby="moreInfoTabButton">
  <div id="runbarMore" class="runbar" style="display:none">
    <div class="group">
      <label>Scenario</label>
      <select id="scenarioSelectMore" class="scenario-select"></select>
    </div>
    <div class="group">
      <label>Solution</label>
      <select id="solutionSelectMore" class="solution-select"></select>
    </div>
    <button id="runBtnMore" class="primary run-button">&#9654; Run</button>
    <span class="muted run-status" id="runStatusMore"></span>
  </div>

  <div class="toolbar">
    <div class="group">
      <button id="playMore" class="primary">&#9654; Play</button>
      <button id="resetMore">&#8634; Reset</button>
      <span class="clock" id="clockMore">--:--</span>
    </div>
    <div class="group" style="flex:1; min-width:240px;">
      <span class="muted">clock</span>
      <input type="range" id="scrubMore" min="0" max="1000" value="0" style="flex:1;">
    </div>
    <div class="group">
      <span class="muted">speed</span>
      <select id="speedMore" class="playback-speed">
        <option value="4">0.1x</option><option value="10">0.25x</option>
        <option value="20">0.5x</option><option value="40" selected>1x</option>
        <option value="80">2x</option><option value="160">4x</option>
      </select>
    </div>
    <div class="sep"></div>
    <div class="group">
      <span class="muted">plan&nbsp;@</span>
      <button id="tprevMore">&#9664;</button>
      <span class="clock" id="tickLabelMore" style="font-size:14px; min-width:90px; text-align:center;"></span>
      <button id="tnextMore">&#9654;</button>
      <button id="playDecMore" class="primary">&#9654; Decisions</button>
    </div>
  </div>

  <div class="solution-score-strip" data-score-strip>
    <span class="solution-score-name" data-score-name>Solution: —</span>
    <span class="badge" data-score-result>NOT RUN</span>
  </div>

  <div id="banner" class="banner" style="display:none"></div>
  <div class="chart" id="chart"><div class="placeholder" id="chartPlaceholder">Pick a scenario and press Run.</div></div>
  <div class="legend" id="legend"></div>

  <div class="panel" style="margin-top:16px">
    <h3>Gate Choice <span class="muted" id="gateExplanationTick"></span></h3>
    <div class="scroll" id="gateExplanations" style="max-height: 320px"></div>
  </div>

  <div class="panels">
    <div class="panel">
      <h3>Flight queue @ <span id="tickLabel3"></span></h3>
      <div class="scroll" id="queue"></div>
    </div>
    <div class="panel">
      <h3>Events at plan tick <span id="tickLabel2"></span></h3>
      <div class="log scroll" id="log"></div>
    </div>
    <div class="panel">
      <h3>Gate status at <span id="clock2">--:--</span></h3>
      <div class="scroll" id="status"></div>
    </div>
  </div>
  </section>

  <section id="boardsTab" class="tab-pane" role="tabpanel" aria-labelledby="boardsTabButton">
  <div id="runbarBoards" class="runbar" style="display:none">
    <div class="group">
      <label>Scenario</label>
      <select id="scenarioSelectBoards" class="scenario-select"></select>
    </div>
    <div class="group">
      <label>Solution</label>
      <select id="solutionSelectBoards" class="solution-select"></select>
    </div>
    <button id="runBtnBoards" class="primary run-button">&#9654; Run</button>
    <span class="muted run-status" id="runStatusBoards"></span>
  </div>

  <div class="toolbar">
    <div class="group">
      <button id="playBoards" class="primary">&#9654; Play</button>
      <button id="resetBoards">&#8634; Reset</button>
      <span class="clock" id="clockBoards">--:--</span>
    </div>
    <div class="group" style="flex:1; min-width:240px;">
      <span class="muted">clock</span>
      <input type="range" id="scrubBoards" min="0" max="1000" value="0" style="flex:1;">
    </div>
    <div class="group">
      <span class="muted">speed</span>
      <select id="speedBoards" class="playback-speed">
        <option value="4">0.1x</option><option value="10">0.25x</option>
        <option value="20">0.5x</option><option value="40" selected>1x</option>
        <option value="80">2x</option><option value="160">4x</option>
      </select>
    </div>
    <div class="sep"></div>
    <div class="group">
      <span class="muted">plan&nbsp;@</span>
      <button id="tprevBoards">&#9664;</button>
      <span class="clock" id="tickLabelBoards" style="font-size:14px; min-width:90px; text-align:center;"></span>
      <button id="tnextBoards">&#9654;</button>
      <button id="playDecBoards" class="primary">&#9654; Decisions</button>
    </div>
  </div>

  <div class="solution-score-strip" data-score-strip>
    <span class="solution-score-name" data-score-name>Solution: —</span>
    <span class="badge" data-score-result>NOT RUN</span>
  </div>

  <div class="flight-boards">
    <div class="flight-board" aria-labelledby="arrivalsBoardTitle">
      <h2 id="arrivalsBoardTitle">ARRIVALS</h2>
      <table><thead><tr><th>TIME</th><th>FLIGHT</th><th>FROM</th><th>GATE</th><th>STATUS</th></tr></thead>
        <tbody id="arrivalsBoard"></tbody></table>
    </div>
    <div class="flight-board" aria-labelledby="departuresBoardTitle">
      <h2 id="departuresBoardTitle">DEPARTURES</h2>
      <table><thead><tr><th>TIME</th><th>FLIGHT</th><th>TO</th><th>GATE</th><th>STATUS</th></tr></thead>
        <tbody id="departuresBoard"></tbody></table>
    </div>
  </div>
  </section>

  <section id="compareTab" class="tab-pane" role="tabpanel" aria-labelledby="compareTabButton">
  <div id="runbarCompare" class="runbar" style="display:none">
    <div class="group">
      <label>Scenario</label>
      <select id="scenarioSelectCompare" class="scenario-select"></select>
    </div>
    <div class="group">
      <label>Solution A</label>
      <select id="solutionSelectCompareA" class="compare-solution compare-solution-a"></select>
    </div>
    <div class="group">
      <label>Solution B</label>
      <select id="solutionSelectCompareB" class="compare-solution compare-solution-b"></select>
    </div>
    <button id="compareBtn" class="primary">Compare</button>
    <span class="muted" id="compareStatus"></span>
  </div>

  <div class="toolbar">
    <div class="group">
      <button id="playCompare" class="primary">&#9654; Play</button>
      <button id="resetCompare">&#8634; Reset</button>
      <span class="clock" id="clockCompare">--:--</span>
    </div>
    <div class="group" style="flex:1; min-width:240px;">
      <span class="muted">clock</span>
      <input type="range" id="scrubCompare" min="0" max="1000" value="0" style="flex:1;">
    </div>
    <div class="group">
      <span class="muted">speed</span>
      <select id="speedCompare" class="playback-speed">
        <option value="4">0.1x</option><option value="10">0.25x</option>
        <option value="20">0.5x</option><option value="40" selected>1x</option>
        <option value="80">2x</option><option value="160">4x</option>
      </select>
    </div>
    <div class="sep"></div>
    <div class="group">
      <span class="muted">plan&nbsp;@</span>
      <button id="tprevCompare">&#9664;</button>
      <span class="clock" id="tickLabelCompare" style="font-size:14px; min-width:90px; text-align:center;"></span>
      <button id="tnextCompare">&#9654;</button>
      <button id="playDecCompare" class="primary">&#9654; Decisions</button>
    </div>
  </div>

  <div class="compare-completions" aria-live="polite">
    <div class="compare-completion"><span class="muted" id="compareSolutionLabelA">Solution A</span>
      <span class="badge" id="compareCompletionA">NOT RUN</span></div>
    <div class="compare-completion"><span class="muted" id="compareSolutionLabelB">Solution B</span>
      <span class="badge" id="compareCompletionB">NOT RUN</span></div>
  </div>

  <div class="panel">
    <h3>Gate assignment comparison</h3>
    <div class="compare-timelines">
      <div class="compare-timeline-pane">
        <h4 id="compareTimelineHeadingA">SOLUTION A</h4>
        <div class="compare-timeline-scroll"><div id="compareTimelineA"></div></div>
      </div>
      <div class="compare-timeline-pane">
        <h4 id="compareTimelineHeadingB">SOLUTION B</h4>
        <div class="compare-timeline-scroll"><div id="compareTimelineB"></div></div>
      </div>
    </div>
    <div class="compare-caption">
      <span id="compareClock">Choose two solutions and run the comparison.</span>
      <span id="compareSummary"></span>
    </div>
  </div>
  </section>
</div>

<script>
const MODE = __MODE_JSON__;
const SERVER_MODE = !!MODE.server;

let TRACE = null;
let TRACE_COMPARE_B = null;
let COMPARE_TICK_A = -1, COMPARE_TICK_B = -1;
let TMIN = 0, TMAX = 1, SPAN = 1, OPEN = Infinity;
const TYPE_COLOR = {0: "#8b949e", 1: "#58a6ff", 2: "#3fb950"};
const TYPE_SHORT = {0: "CARGO", 1: "DOM", 2: "INTL"};
const TYPE_NAME = {0: "Cargo Terminal", 1: "Domestic Terminal", 2: "International Terminal"};
const MOVE_MINUTES = 10;
const DEPARTURE_ROLL_MINUTES = 5;
const DEPARTURE_CLIMB_MINUTES = 3;
const DEPARTURE_TOTAL_MINUTES = MOVE_MINUTES + DEPARTURE_ROLL_MINUTES + DEPARTURE_CLIMB_MINUTES;
const DEPARTURE_RUNWAY_ENTRY = {x:90, y:48.7};
const DEPARTURE_ROLL_END = {x:97.2, y:48.7};
const DEPARTURE_EXIT = {x:101.5, y:43};
let simGatePositions = {};

let planIdx = 0;   // start on the initial plan; press "Decisions" to play the day forward
let clock = 0;
let playing = false;
let raf = null, lastTs = null;
let decTimer = null;
let manualOverrideMode = false, manualPlanInvalidated = false;
let draggedFlightId = null, manualDragTargets = new Map(), activeRunController = null;

function pad(n){ return String(n).padStart(2,"0"); }
function fmt(min){ min = Math.round(min); const d = Math.floor(min/1440), m = ((min%1440)+1440)%1440;
  const s = pad(Math.floor(m/60))+":"+pad(m%60); return d>0 ? s+"+"+d+"d" : s; }
function frac(t){ return (t - TMIN) / SPAN; }
function shortId(fid){ const p = fid.split("-"); return p.length>=2 ? p[0]+" "+p[1] : fid; }
function colorFor(fid){ let h=0; for(const c of fid) h=(h*31 + c.charCodeAt(0))>>>0; return `hsl(${h%360} 62% 58%)`; }
function clampEnd(e){ return e >= OPEN ? TMAX : e; }

// ---- load a trace (fresh page-load, or a new Run) --------------------------
function loadTrace(trace){
  TRACE = trace;
  manualOverrideMode = false; manualPlanInvalidated = false;
  draggedFlightId = null; manualDragTargets.clear();
  COMPARE_TICK_A = -1; COMPARE_TICK_B = -1;
  TMIN = trace.time_min; TMAX = trace.time_max + DEPARTURE_TOTAL_MINUTES; SPAN = Math.max(1, TMAX - TMIN);
  OPEN = trace.outage_open_end;
  planIdx = 0; clock = TMIN; playing = false;
  if(decTimer){ clearInterval(decTimer); decTimer = null; }
  document.getElementById("playDec").innerHTML = "&#9654; Decisions";
  document.getElementById("playDecMore").innerHTML = "&#9654; Decisions";
  document.getElementById("playDecBoards").innerHTML = "&#9654; Decisions";
  document.getElementById("playDecCompare").innerHTML = "&#9654; Decisions";
  document.getElementById("chart").innerHTML = "";
  buildStatic();
  updateManualOverrideUI();
  renderPlan();
  setClockTime(TMIN);
}

function scoreText(result){
  const score = result && result.score;
  return score == null || !Number.isFinite(Number(score)) ? "—" : Number(score).toFixed(2);
}

function renderSingleSolutionScores(){
  for(const strip of document.querySelectorAll("[data-score-strip]")){
    const trace = TRACE;
    const label = strip.querySelector("[data-score-name]");
    const badge = strip.querySelector("[data-score-result]");
    label.textContent = `Solution: ${trace?.solution || "—"}`;
    badge.className = "badge";
    if(!trace){ badge.textContent = "NOT RUN"; continue; }
    const completed = trace.result?.status === "OK";
    badge.textContent = completed
      ? `COMPLETED · score ${scoreText(trace.result)}` : "FAILED";
    badge.classList.add(completed ? "ok" : "fail");
  }
}

// ---- static scaffold (rebuilt for every trace) ------------------------------
function buildStatic(){
  document.getElementById("title").textContent = "Gate Management Dashboard";
  renderSingleSolutionScores();

  const chart = document.getElementById("chart");
  // ruler
  const ruler = document.createElement("div"); ruler.className = "ruler";
  const sp = document.createElement("div"); sp.className = "spacer"; ruler.appendChild(sp);
  const rtrack = document.createElement("div"); rtrack.className = "track";
  for(let h = Math.ceil(TMIN/60)*60; h <= TMAX; h += 60){
    const hr = document.createElement("div"); hr.className = "hr";
    hr.style.left = (frac(h)*100) + "%"; hr.textContent = fmt(h); rtrack.appendChild(hr);
  }
  ruler.appendChild(rtrack); chart.appendChild(ruler);

  // gate rows
  for(const g of TRACE.gates){
    const row = document.createElement("div"); row.className = "row"; row.dataset.gid = g.id;
    const label = document.createElement("div"); label.className = "label";
    label.style.borderLeftColor = TYPE_COLOR[g.type];
    label.title = `max wingspan ${g.max_wingspan}m · jetbridge ${g.jetbridge?"yes":"no"} · dist ${g.dist}`;
    label.innerHTML = `<span class="gid">${g.id}</span>`
      + `<span class="chip" style="background:${TYPE_COLOR[g.type]}22;color:${TYPE_COLOR[g.type]}">${TYPE_SHORT[g.type]}</span>`
      + `<span class="status" data-status></span>`;
    const track = document.createElement("div"); track.className = "track"; track.dataset.track = g.id;
    row.appendChild(label); row.appendChild(track); chart.appendChild(row);
  }

  // overlay (gridlines + playhead)
  const overlay = document.createElement("div"); overlay.className = "overlay"; overlay.id = "overlay";
  for(let h = Math.ceil(TMIN/60)*60; h <= TMAX; h += 60){
    const gl = document.createElement("div"); gl.className = "grid";
    gl.style.left = `calc(var(--labelw) + (100% - var(--labelw)) * ${frac(h)})`;
    overlay.appendChild(gl);
  }
  const ph = document.createElement("div"); ph.className = "playhead"; ph.id = "playhead";
  overlay.appendChild(ph); chart.appendChild(overlay);

  // legend
  document.getElementById("legend").innerHTML =
    `<span><span class="sw" style="background:#58a6ff"></span>domestic gate</span>`
    + `<span><span class="sw" style="background:#3fb950"></span>international gate</span>`
    + `<span><span class="sw" style="background:#8b949e"></span>cargo gate</span>`
    + `<span><span class="sw" style="background:repeating-linear-gradient(45deg,#7a262d,#7a262d 4px,#5a1d22 4px,#5a1d22 8px);border:1px solid #f85149"></span>gate outage</span>`
    + `<span><span class="sw" style="outline:2px solid #fff;background:#444"></span>occupied right now</span>`
    + `<span>&#9879; emergency / diverted flight</span>`;
  buildSimulation();
}

function manualCategoryCode(value){
  if(value == null || typeof value === "boolean") return null;
  if(typeof value === "string"){
    const name = value.trim().toUpperCase();
    if(name === "CARGO") return 0;
    if(name === "DOMESTIC") return 1;
    if(name === "INTERNATIONAL") return 2;
  }
  const code = Number(value);
  return Number.isInteger(code) && code >= 0 && code <= 2 ? code : null;
}

function findFlightBooking(plan, flightId){
  for(const [gateId, bookings] of Object.entries(plan || {})){
    const index = (bookings || []).findIndex(booking => booking.flight_id === flightId);
    if(index >= 0) return {gateId, bookings, index, booking: bookings[index]};
  }
  return null;
}

function intervalsOverlap(left, right){
  return left[0] < right[1] && right[0] < left[1];
}

function manualMoveValidity(flightId, targetGateId){
  if(!TRACE) return {ok:false, reason:"Run a solution before editing gates."};
  const currentTick = TRACE.ticks[planIdx];
  const current = findFlightBooking(currentTick.plan, flightId);
  if(!current) return {ok:false, reason:"This airplane has no active gate assignment."};
  if(current.gateId === targetGateId) return {ok:false, reason:"This is already the assigned gate."};
  const target = TRACE.gates.find(gate => gate.id === targetGateId);
  if(!target) return {ok:false, reason:"Gate not found."};

  const category = manualCategoryCode(current.booking.category);
  const gateType = manualCategoryCode(target.type);
  if(category === null || gateType === null) return {ok:false, reason:"Flight or gate type is unknown."};
  if(category === 0 && gateType !== 0) return {ok:false, reason:"Cargo flights require a cargo gate."};
  if(category === 2 && gateType !== 2) return {ok:false, reason:"International flights require an international gate."};
  if(category === 1 && gateType === 0) return {ok:false, reason:"Domestic passenger flights cannot use a cargo gate."};
  const wingspan = Number(current.booking.wingspan);
  const maximumWingspan = Number(target.max_wingspan);
  if(!Number.isFinite(wingspan) || !Number.isFinite(maximumWingspan) || wingspan > maximumWingspan)
    return {ok:false, reason:"The aircraft exceeds this gate's wingspan limit."};
  if(current.booking.jetbridge_required && !target.jetbridge)
    return {ok:false, reason:"This flight requires a jetbridge."};

  let checked = false;
  for(let i = planIdx; i < TRACE.ticks.length; i++){
    const tick = TRACE.ticks[i];
    const flight = findFlightBooking(tick.plan, flightId);
    if(!flight) continue;
    checked = true;
    const intervals = flight.booking.intervals || [];
    if(!intervals.length) return {ok:false, reason:"The flight's occupancy interval is missing."};
    for(const interval of intervals){
      const closed = ((tick.outages || {})[targetGateId] || []).some(outage =>
        intervalsOverlap(interval, [outage[0], clampEnd(outage[1])])
      );
      if(closed) return {ok:false, reason:"The gate has an outage during this flight's occupancy."};
      const occupied = ((tick.plan || {})[targetGateId] || []).some(booking =>
        booking.flight_id !== flightId && (booking.intervals || []).some(other => intervalsOverlap(interval, other))
      );
      if(occupied) return {ok:false, reason:"Another flight occupies this gate during the requested interval."};
    }
  }
  return checked ? {ok:true, sourceGateId:current.gateId} : {ok:false, reason:"No scheduled occupancy was found."};
}

function beginManualDrag(flightId, plane, event){
  if(!manualOverrideMode){ event.preventDefault(); return; }
  setPlaying(false);
  setDecPlay(false);
  draggedFlightId = flightId;
  manualDragTargets = new Map();
  let validTargets = 0;
  for(const gate of TRACE.gates){
    const pad = document.querySelector(`.sim-gate[data-gid="${gate.id}"]`);
    const validation = manualMoveValidity(flightId, gate.id);
    const isCurrent = validation.sourceGateId === gate.id
      || findFlightBooking(TRACE.ticks[planIdx].plan, flightId)?.gateId === gate.id;
    if(validation.ok){ manualDragTargets.set(gate.id, validation); validTargets++; }
    if(pad){
      pad.classList.toggle("manual-current", isCurrent);
      pad.classList.toggle("manual-available", validation.ok);
      pad.classList.toggle("manual-blocked", !validation.ok && !isCurrent);
      pad.title = isCurrent ? `${gate.id} · current gate`
        : validation.ok ? `${gate.id} · available — drop to reassign`
        : `${gate.id} · unavailable — ${validation.reason}`;
    }
  }
  if(!validTargets){
    event.preventDefault();
    setManualFeedback(`No compatible, available gates for ${shortId(flightId)} at this time.`, false);
    finishManualDrag();
    return;
  }
  plane.classList.add("manual-dragging");
  if(event.dataTransfer){
    event.dataTransfer.setData("text/plain", flightId);
    event.dataTransfer.effectAllowed = "move";
  }
  setManualFeedback(`Dragging ${shortId(flightId)}. Green gates are compatible and available.`, true);
}

function finishManualDrag(){
  draggedFlightId = null;
  manualDragTargets.clear();
  document.querySelectorAll(".sim-gate").forEach(pad => {
    pad.classList.remove("manual-current", "manual-available", "manual-blocked", "manual-drop-target");
    pad.title = pad.dataset.baseTitle || pad.title;
  });
  document.querySelectorAll(".sim-plane.manual-dragging").forEach(plane => plane.classList.remove("manual-dragging"));
}

function setManualFeedback(message, success){
  const feedback = document.getElementById("manualOverrideFeedback");
  if(!feedback) return;
  feedback.textContent = message || "";
  feedback.style.color = success ? "#8ee99d" : "#ffb4ad";
}

function markManualScoreInvalid(){
  for(const badge of document.querySelectorAll("[data-score-result]")){
    badge.className = "badge manual";
    badge.textContent = "MANUAL · score invalid";
  }
  const compareA = document.getElementById("compareCompletionA");
  if(compareA){ compareA.className = "badge manual"; compareA.textContent = "MANUAL · score invalid"; }
}

function updateManualOverrideUI(){
  const button = document.getElementById("manualOverrideButton");
  if(!button) return;
  button.disabled = !TRACE;
  button.classList.toggle("active", manualOverrideMode);
  button.textContent = manualOverrideMode ? "EXIT MANUAL OVERRIDE"
    : manualPlanInvalidated ? "EDIT MANUAL PLAN" : "MANUAL OVERRIDE";
  const controlsLocked = !TRACE || manualOverrideMode || manualPlanInvalidated;
  for(const id of ["play", "playMore", "playBoards", "playCompare", "playDec", "playDecMore", "playDecBoards", "playDecCompare"]){
    const control = document.getElementById(id);
    if(control) control.disabled = controlsLocked;
  }
  const notice = document.getElementById("manualOverrideNotice");
  if(notice){
    notice.hidden = !manualOverrideMode && !manualPlanInvalidated;
    document.getElementById("manualOverrideHeading").textContent = manualOverrideMode
      ? "Manual override active." : "Manual plan retained.";
    document.getElementById("manualOverrideInstructions").textContent = manualOverrideMode
      ? "Playback is stopped. Scrub to an active airplane, then drag it onto a green available gate. Changes are local to this dashboard."
      : "The manual gate changes remain in this preview. Automated playback and evaluator scoring are invalid until you run a solution again.";
  }
}

function toggleManualOverride(){
  if(!TRACE){ showError("Run a solution before entering manual override."); return; }
  showError(null);
  if(manualOverrideMode){
    manualOverrideMode = false;
    finishManualDrag();
    setManualFeedback(manualPlanInvalidated
      ? "Manual gate changes are retained; run a solution again to restore automated playback."
      : "Manual editing ended. The original solution is unchanged.", true);
  } else {
    cancelActiveRunRequest();
    setPlaying(false);
    setDecPlay(false);
    manualOverrideMode = true;
    document.getElementById("visualTabButton").click();
    setManualFeedback("Simulation stopped. Drag an active airplane to a green gate.", true);
  }
  updateManualOverrideUI();
  renderSimulation(TRACE.ticks[planIdx], clock);
}

function dropManualFlight(event, targetGateId){
  if(!manualOverrideMode) return;
  const flightId = draggedFlightId || (event.dataTransfer && event.dataTransfer.getData("text/plain"));
  if(!flightId) return;
  event.preventDefault();
  const validation = manualMoveValidity(flightId, targetGateId);
  finishManualDrag();
  if(!validation.ok){ setManualFeedback(validation.reason, false); return; }
  const currentTick = TRACE.ticks[planIdx];
  const sourceGateId = validation.sourceGateId;
  if(sourceGateId === targetGateId) return;

  for(let i = planIdx; i < TRACE.ticks.length; i++){
    const tick = TRACE.ticks[i];
    const assignment = findFlightBooking(tick.plan, flightId);
    if(!assignment || assignment.gateId === targetGateId) continue;
    assignment.bookings.splice(assignment.index, 1);
    tick.plan[targetGateId] ||= [];
    tick.plan[targetGateId].push(assignment.booking);
  }
  currentTick.decisions ||= {assignments:[], reassignments:[], gate_explanations:[]};
  for(const key of ["assignments", "reassignments"])
    currentTick.decisions[key] = (currentTick.decisions[key] || []).filter(([fid]) => fid !== flightId);
  currentTick.decisions.reassignments.push([flightId, targetGateId]);
  currentTick.log ||= [];
  currentTick.log.push(`[REASSIGN] MANUAL OVERRIDE: ${shortId(flightId)} moved from ${sourceGateId} to ${targetGateId}`);
  currentTick.events ||= [];
  currentTick.events.push({
    kind:"SOLUTION", subject:`${shortId(flightId)} moved from ${sourceGateId} to ${targetGateId}`,
    reason:"Manual operator override; evaluator scoring is no longer valid.",
  });
  manualPlanInvalidated = true;
  cancelActiveRunRequest();
  markManualScoreInvalid();
  COMPARE_TICK_A = -1; COMPARE_TICK_B = -1;
  updateManualOverrideUI();
  renderPlan();
  setManualFeedback(`${shortId(flightId)} reassigned from ${sourceGateId} to ${targetGateId}. Automated playback stopped; score invalidated.`, true);
}

function buildSimulation(){
  const map = document.getElementById("simMap");
  if(!map) return;
  map.innerHTML = "";
  simGatePositions = {};

  const footprint = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  footprint.setAttribute("viewBox", "0 0 1500 780");
  footprint.setAttribute("preserveAspectRatio", "xMidYMid meet");
  footprint.classList.add("airport-footprint");
  footprint.setAttribute("aria-label", "Schematic Pearson airport terminal and apron layout");
  footprint.innerHTML = `
    <path class="apron" d="M10 330 H205 V430 H10 Z M1330 330 H1490 V430 H1330 Z"/>
    <path class="taxi" d="M205 380 H1330"/>
    <path class="runway-center" d="M22 380 H193 M1342 380 H1478"/>

    <!-- Terminal 1: three individual gate piers, perpendicular to the building. -->
    <path class="pier" d="M235 100 H285 V280 H235 Z"/>
    <path class="pier" d="M465 100 H515 V280 H465 Z"/>
    <path class="pier" d="M355 460 H405 V660 H355 Z"/>
    <path class="building" d="M235 280 Q215 280 205 305 V430 Q215 460 245 460 H510 Q545 460 555 430 V310 Q545 280 510 280 Z"/>
    <!-- Terminal 2: enlarged international terminal with one pier per gate. -->
    <g transform="translate(27.5 0)">
    <path class="pier" d="M625 75 H675 V255 H625 Z"/>
    <path class="pier" d="M925 75 H975 V255 H925 Z"/>
    <path class="pier" d="M625 515 H675 V695 H625 Z"/>
    <path class="pier" d="M925 515 H975 V695 H925 Z"/>
    <path class="building" d="M600 255 Q570 265 570 300 V470 Q580 515 615 515 H1010 Q1045 510 1055 475 V300 Q1045 255 1010 255 Z"/>
    </g>
    <!-- Terminal 3 is compact and has only its two cargo-gate piers. -->
    <g transform="translate(5 0)">
    <path class="pier" d="M1145 170 H1195 V320 H1145 Z"/>
    <path class="pier" d="M1240 440 H1290 V590 H1240 Z"/>
    <path class="building" d="M1135 320 Q1110 330 1110 355 V410 Q1115 440 1145 440 H1300 Q1320 435 1320 410 V355 Q1315 320 1290 320 Z"/>
    </g>

    <text class="runway-label" x="107" y="318">ARRIVALS</text>
    <text class="runway-label" x="1392" y="318">DEPARTURES</text>
    <text class="terminal-label" x="380" y="365">TERMINAL 1</text>
    <text class="terminal-label" x="380" y="390">DOMESTIC</text>
    <text class="terminal-label" x="840" y="365">TERMINAL 2</text>
    <text class="terminal-label" x="840" y="390">INTERNATIONAL</text>
    <text class="terminal-label" x="1220" y="365">TERMINAL 3</text>
    <text class="terminal-label" x="1220" y="390">CARGO</text>`;
  map.appendChild(footprint);

  // This exercise has synthetic gate IDs rather than real Pearson gate
  // coordinates. Give every gate a distinct wing and a separated apron slot.
  const gatePositions = {
    // Gate markers sit on their drawn terminal piers, perpendicular to the
    // terminal buildings, and are grouped by the terminal serving each type.
    D1: {x:17.3,y:24.4}, D2: {x:32.7,y:24.4}, D3: {x:25.3,y:70.5},
    I1: {x:45.2,y:21.2}, I2: {x:65.2,y:21.2}, I3: {x:45.2,y:77.6}, I4: {x:65.2,y:77.6},
    C1: {x:78.3,y:31.4}, C2: {x:84.7,y:66},
  };
  const fallbackByType = {0: [{x:9,y:79},{x:13,y:85}], 1: [{x:25,y:18}], 2: [{x:76,y:50}]};
  const counters = {0: 0, 1: 0, 2: 0};
  for(const gate of TRACE.gates){
    const type = gate.type;
    const fallback = fallbackByType[type] || [{x:50,y:50}];
    const point = gatePositions[gate.id] || fallback[counters[type]++ % fallback.length];
    const {x, y} = point;
    const pad = document.createElement("div");
    pad.className = "sim-gate";
    pad.dataset.gid = gate.id;
    const gateLabel = document.createElement("span");
    gateLabel.className = "sim-gate-label";
    gateLabel.textContent = gate.id;
    pad.appendChild(gateLabel);
    pad.style.left = `${x}%`;
    pad.style.top = `${y}%`;
      pad.title = `${gate.id} · ${TYPE_NAME[type] || "Gate"} · ${gate.max_wingspan}m max · jetbridge ${gate.jetbridge ? "yes" : "no"}`;
    pad.dataset.baseTitle = pad.title;
    pad.addEventListener("dragover", event => {
      if(!manualOverrideMode || !manualDragTargets.get(gate.id)?.ok) return;
      event.preventDefault();
      pad.classList.add("manual-drop-target");
      if(event.dataTransfer) event.dataTransfer.dropEffect = "move";
    });
    pad.addEventListener("dragleave", () => pad.classList.remove("manual-drop-target"));
    pad.addEventListener("drop", event => dropManualFlight(event, gate.id));
    map.appendChild(pad);
    simGatePositions[gate.id] = {x, y};
  }

  const planeLayer = document.createElement("div");
  planeLayer.id = "simPlaneLayer";
  planeLayer.style.cssText = "position:absolute;inset:0;pointer-events:none";
  map.appendChild(planeLayer);
}

function renderSimulation(tick, t){
  const layer = document.getElementById("simPlaneLayer");
  if(!layer || !tick) return;
  document.getElementById("simTime").textContent = `· ${fmt(t)}`;
  const planesById = new Map([...layer.children].map(plane => [plane.dataset.fid, plane]));

  for(const gate of TRACE.gates){
    const pad = document.querySelector(`.sim-gate[data-gid="${gate.id}"]`);
    if(!pad) continue;
    const occupant = (tick.plan[gate.id] || []).find(booking =>
      (booking.intervals || []).some(interval => t >= interval[0] && t < interval[1])
    );
    const down = (tick.outages[gate.id] || []).some(window => t >= window[0] && t < clampEnd(window[1]));
    pad.classList.toggle("occupied", !!occupant);
    pad.classList.toggle("down", down);
  }

  const visible = new Set();
  for(const [gateId, bookings] of Object.entries(tick.plan || {})){
    const destination = simGatePositions[gateId];
    if(!destination) continue;
    for(const booking of bookings){
      const intervals = booking.intervals || [];
      const activeInterval = intervals.find(interval => t >= interval[0] && t < interval[1]);
      const lastDeparted = activeInterval ? null : intervals
        .filter(interval => interval[1] <= t)
        .sort((a,b) => b[1] - a[1])[0];
      const departureElapsed = lastDeparted ? t - lastDeparted[1] : -1;
      const departing = departureElapsed >= 0 && departureElapsed < DEPARTURE_TOTAL_MINUTES;
      if(!activeInterval && !departing) continue;
      const fid = booking.flight_id;
      visible.add(fid);
      let plane = planesById.get(fid);
      if(!plane){
        plane = document.createElement("div");
        plane.className = "sim-plane";
        plane.dataset.fid = fid;
        const label = document.createElement("div");
        label.className = "sim-plane-label";
        label.textContent = shortId(fid);
        const aircraft = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        aircraft.classList.add("sim-plane-svg");
        aircraft.setAttribute("viewBox", "0 0 120 56");
        aircraft.setAttribute("role", "img");
        aircraft.setAttribute("aria-label", "airplane");
        const wing = document.createElementNS("http://www.w3.org/2000/svg", "path");
        wing.classList.add("sim-plane-shape");
        wing.setAttribute("d", "M55 19 L25 29 Q20 30 22 34 L55 30 L65 30 L98 34 Q100 30 95 29 L65 19 Z");
        const fuselage = document.createElementNS("http://www.w3.org/2000/svg", "path");
        fuselage.classList.add("sim-plane-shape");
        fuselage.setAttribute("d", "M60 2 C55 10 56 18 56 25 L52 42 L45 49 L46 52 L60 48 L74 52 L75 49 L68 42 L64 25 C64 18 65 10 60 2 Z");
        const highlight = document.createElementNS("http://www.w3.org/2000/svg", "path");
        highlight.classList.add("sim-plane-highlight");
        highlight.setAttribute("d", "M59 8 C58 16 59 25 59 42 L60 46");
        aircraft.append(wing, fuselage, highlight);
        const priority = document.createElement("span");
        priority.className = "sim-plane-priority";
        priority.textContent = "EMERGENCY";
        priority.hidden = true;
        const detail = document.createElement("div");
        detail.className = "sim-plane-detail";
        plane.append(label, aircraft, priority, detail);
        layer.appendChild(plane);
      }
      const wingspan = Number(booking.wingspan) || 36;
      const aircraft = (booking.aircraft_types || []).join("/") || "Aircraft";
      const color = colorFor(fid);
      const span = Math.max(55, Math.min(125, 48 + wingspan * 1.05));
      let x, y, heading = 0;
      if(activeInterval){
        const progress = Math.min(1, Math.max(0, t - activeInterval[0]) / MOVE_MINUTES);
        x = 4 + (destination.x - 4) * progress;
        y = 48 + (destination.y - 48) * progress;
      } else {
        heading = 90;
        if(departureElapsed < MOVE_MINUTES){
          const progress = Math.max(0, departureElapsed) / MOVE_MINUTES;
          x = destination.x + (DEPARTURE_RUNWAY_ENTRY.x - destination.x) * progress;
          y = destination.y + (DEPARTURE_RUNWAY_ENTRY.y - destination.y) * progress;
        } else if(departureElapsed < MOVE_MINUTES + DEPARTURE_ROLL_MINUTES){
          const progress = (departureElapsed - MOVE_MINUTES) / DEPARTURE_ROLL_MINUTES;
          x = DEPARTURE_RUNWAY_ENTRY.x + (DEPARTURE_ROLL_END.x - DEPARTURE_RUNWAY_ENTRY.x) * progress;
          y = DEPARTURE_RUNWAY_ENTRY.y;
        } else {
          const progress = (departureElapsed - MOVE_MINUTES - DEPARTURE_ROLL_MINUTES) / DEPARTURE_CLIMB_MINUTES;
          x = DEPARTURE_ROLL_END.x + (DEPARTURE_EXIT.x - DEPARTURE_ROLL_END.x) * progress;
          y = DEPARTURE_ROLL_END.y + (DEPARTURE_EXIT.y - DEPARTURE_ROLL_END.y) * progress;
          heading = 90 - 20 * progress;
        }
      }

      plane.style.left = `${x}%`;
      plane.style.top = `${y}%`;
      plane.classList.toggle("bridge", !!booking.jetbridge_required);
      plane.dataset.phase = departing ? "departing" : "arriving";
      const draggable = manualOverrideMode && !!activeInterval;
      plane.draggable = draggable;
      plane.classList.toggle("manual-draggable", draggable);
      plane.ondragstart = draggable ? event => beginManualDrag(fid, plane, event) : null;
      plane.ondragend = () => finishManualDrag();
      plane.querySelectorAll(".sim-plane-shape").forEach(shape => shape.style.fill = color);
      const aircraftSvg = plane.querySelector(".sim-plane-svg");
      aircraftSvg.style.width = `${span}px`;
      aircraftSvg.style.transform = `rotate(${heading}deg)`;
      plane.querySelector(".sim-plane-priority").hidden = !booking.diversion;
      plane.querySelector(".sim-plane-detail").textContent =
        `${aircraft} · ${wingspan}m · Bridge: ${booking.jetbridge_required ? "Y" : "N"} · Gate: ${gateId}`;
      plane.title = `${shortId(fid)} · ${aircraft} · ${wingspan}m · Bridge: ${booking.jetbridge_required ? "Y" : "N"} · Gate: ${gateId}${departing ? " · departing YYZ" : ""}`;
    }
  }
  for(const plane of [...layer.children]){
    if(!visible.has(plane.dataset.fid)) plane.remove();
  }
}

// ---- render the plan for the selected decision tick ------------------------
function renderPlan(){
  const tick = TRACE.ticks[planIdx];

  // Snapshot every bar's screen position before we wipe tracks (for FLIP animation)
  const oldPos = {};
  document.querySelectorAll('.bar[data-fid]').forEach(bar => {
    if(!(bar.dataset.fid in oldPos)){
      const r = bar.getBoundingClientRect();
      oldPos[bar.dataset.fid] = {left: r.left, top: r.top};
    }
  });
  const movedSet = new Set((tick.decisions.reassignments||[]).map(([f]) => f));

  const phase = (planIdx === 0) ? "initial plan @ " : "plan @ ";
  document.getElementById("tickLabel").textContent = phase + tick.time_label;
  document.getElementById("tickLabelMore").textContent = phase + tick.time_label;
  document.getElementById("tickLabelBoards").textContent = phase + tick.time_label;
  document.getElementById("tickLabelCompare").textContent = phase + tick.time_label;
  document.getElementById("tickLabel2").textContent = tick.time_label + (planIdx === 0 ? " (initial plan)" : "");
  for(const tr of document.querySelectorAll("[data-track]")) tr.innerHTML = "";

  for(const gid in tick.plan){
    const tr = document.querySelector(`[data-track="${gid}"]`); if(!tr) continue;
    for(const b of tick.plan[gid]){
      for(const iv of b.intervals){
        const bar = document.createElement("div"); bar.className = "bar";
        if(b.diversion) bar.classList.add("emergency");
        bar.style.left = (frac(iv[0])*100) + "%";
        bar.style.width = Math.max(0.4, (iv[1]-iv[0])/SPAN*100) + "%";
        bar.style.background = colorFor(b.flight_id);
        bar.dataset.s = iv[0]; bar.dataset.e = iv[1]; bar.dataset.fid = b.flight_id;
        bar.textContent = (b.diversion ? "⚕ " : "") + shortId(b.flight_id);
        bar.title = `${shortId(b.flight_id)} @ ${gid}  [${fmt(iv[0])} - ${fmt(iv[1])}]`;
        tr.appendChild(bar);
      }
    }
  }
  for(const gid in tick.outages){
    const tr = document.querySelector(`[data-track="${gid}"]`); if(!tr) continue;
    for(const w of tick.outages[gid]){
      const end = clampEnd(w[1]);
      const o = document.createElement("div"); o.className = "outage";
      o.style.left = (frac(w[0])*100) + "%";
      o.style.width = Math.max(0.4, (end-w[0])/SPAN*100) + "%";
      o.dataset.s = w[0]; o.dataset.e = end; o.textContent = "OUT";
      o.title = `outage [${fmt(w[0])} - ${fmt(w[1])}]`;
      tr.appendChild(o);
    }
  }

  const prevPlan = planIdx > 0 ? TRACE.ticks[planIdx-1].plan : {};
  const prevGate = {};
  for(const g in prevPlan) prevPlan[g].forEach(b => prevGate[b.flight_id] = g);
  (tick.decisions.reassignments||[]).forEach(([f, g]) => {
    document.querySelectorAll(`[data-track="${g}"] .bar`).forEach(bar => {
      if(bar.dataset.fid === f) bar.classList.add("moved");
    });
    const og = prevGate[f];
    const pb = og && (prevPlan[og]||[]).find(b => b.flight_id === f);
    if(pb){
      const tr = document.querySelector(`[data-track="${og}"]`);
      pb.intervals.forEach(iv => {
        const gh = document.createElement("div"); gh.className = "bar ghost";
        gh.style.left = (frac(iv[0])*100) + "%";
        gh.style.width = Math.max(0.4, (iv[1]-iv[0])/SPAN*100) + "%";
        gh.textContent = shortId(f);
        gh.title = `${shortId(f)} was at ${og} before ${tick.time_label}`;
        tr.appendChild(gh);
      });
    }
  });

  renderBanner(tick);
  renderQueue(tick);
  renderLog(tick.log);
  renderGateExplanations(tick);
  updateClock(clock);

  // FLIP animation: slide reassigned bars from their old gate row to their new position
  if(movedSet.size){
    requestAnimationFrame(() => {
      document.querySelectorAll('.bar.moved[data-fid]').forEach(bar => {
        const fid = bar.dataset.fid;
        if(!movedSet.has(fid) || !oldPos[fid]) return;
        const r = bar.getBoundingClientRect();
        const dx = oldPos[fid].left - r.left;
        const dy = oldPos[fid].top - r.top;
        if(Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
        bar.style.transform = `translate(${dx}px, ${dy}px)`;
        bar.style.transition = 'none';
        requestAnimationFrame(() => {
          bar.style.transition = 'transform 0.55s cubic-bezier(.25,.46,.45,.94)';
          bar.style.transform = '';
        });
      });
    });
  }
  renderDisruptionLog();
}

function renderBanner(tick){
  const evs = (tick.log||[]).filter(L => /\[(OUTAGE|REASSIGN|CANCEL|DIVERT)\]/.test(L));
  const b = document.getElementById("banner");
  if(!evs.length){ b.style.display = "none"; return; }
  b.style.display = "flex";
  b.innerHTML = `<span class="muted">at ${tick.time_label}</span>` + evs.map(L => {
    let cls = "c", icon = "✕";
    if(L.includes("[OUTAGE]")) { cls = "o"; icon = "⚠"; }
    else if(L.includes("[REASSIGN]")) { cls = "r"; icon = "↺"; }
    else if(L.includes("[DIVERT]")) { cls = "o"; icon = "⚕"; }
    const txt = L.replace(/^\s*\[[A-Z]+\]\s*/, "").replace(/&/g,"&amp;").replace(/</g,"&lt;");
    return `<span class="ev ${cls}">${icon} ${txt}</span>`;
  }).join("");
}

function qrow(fid, label, gate, cls, cat){
  const c = (cat==null) ? "" : `<span class="qcat" style="color:${TYPE_COLOR[cat]}">${TYPE_SHORT[cat]}</span>`;
  return `<div class="qrow"><span class="fchip" style="background:${colorFor(fid)}">${shortId(fid)}</span>`
    + c + `<span class="qarrow ${cls}">${label}</span><span class="qgate">${gate}</span></div>`;
}

function renderQueue(tick){
  document.getElementById("tickLabel3").textContent = tick.time_label;
  const cats = {};
  (tick.queue||[]).forEach(q => cats[q.flight_id] = q.category);
  for(const g in tick.plan) tick.plan[g].forEach(b => cats[b.flight_id] = b.category);

  const placed = new Set(), rows = [];
  (tick.decisions.assignments||[]).forEach(([f,g]) => { placed.add(f); rows.push(qrow(f, "assigned →", g, "a", cats[f])); });
  (tick.decisions.reassignments||[]).forEach(([f,g]) => rows.push(qrow(f, "moved ↺", g, "r", cats[f])));
  (tick.queue||[]).forEach(q => { if(!placed.has(q.flight_id)) rows.push(qrow(q.flight_id, "waiting", ". . .", "u", q.category)); });

  const nA = (tick.decisions.assignments||[]).length, nR = (tick.decisions.reassignments||[]).length;
  document.getElementById("queue").innerHTML =
    `<div class="count">${(tick.queue||[]).length} in queue &middot; ${nA} assigned &middot; ${nR} moved</div>`
    + (rows.length ? rows.join("") : '<div class="muted">no flights to place this tick</div>');
}

function renderLog(lines){
  const el = document.getElementById("log");
  el.innerHTML = lines.map(L => {
    let cls = "";
    if(L.includes("[OUTAGE]")) cls="o"; else if(L.includes("[ASSIGN]")) cls="a";
    else if(L.includes("[REASSIGN]")) cls="r"; else if(L.includes("[CANCEL]")) cls="c";
    else if(L.includes("[UPDATE]")||L.includes("[WARN]")) cls="u";
    const safe = L.replace(/&/g,"&amp;").replace(/</g,"&lt;");
    return `<span class="${cls}">${safe}</span>`;
  }).join("\n");
}

function renderDisruptionLog(){
  const el = document.getElementById("disruptionLog");
  if(!el || !TRACE) return;
  const entries = [];
  for(let i = 0; i <= planIdx; i++){
    const tick = TRACE.ticks[i];
    const events = tick.events || [];
    if(!events.length) continue;
    const escapeHtml = value => String(value ?? "")
      .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
      .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
    const lineHtml = events.map(event => {
      const isSolution = event.kind === "SOLUTION";
      const label = isSolution ? "SOLUTION:" : "DISRUPTION:";
      return `<div class="dis-line ${isSolution ? "solution" : "disruption"}">`
        + `<span class="dis-kind">${label}</span>`
        + `<span>${escapeHtml(event.subject)}</span>`
        + `<span class="dis-reason"><strong>REASON:</strong> ${escapeHtml(event.reason)}</span>`
        + `</div>`;
    }).join("");
    entries.push(`<div class="dis-entry"><div class="dis-time">@ ${tick.time_label}</div>${lineHtml}</div>`);
  }
  el.innerHTML = entries.length
    ? entries.join("")
    : '<span class="muted" style="font-size:12px">No disruptions yet.</span>';
  el.scrollTop = el.scrollHeight;
}

function renderGateExplanations(tick){
  const el = document.getElementById("gateExplanations");
  const label = document.getElementById("gateExplanationTick");
  if(!el || !TRACE) return;
  if(label) label.textContent = tick ? `at ${tick.time_label}` : "";
  const explanations = (tick && tick.decisions && tick.decisions.gate_explanations) || [];
  const escapeHtml = value => String(value ?? "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
  el.innerHTML = explanations.length ? explanations.map(item => {
    const safe = item.candidates.filter(candidate => candidate.safe);
    const selectedRank = item.selected_rank;
    let summary;
    if(selectedRank === 1){
      summary = `Selected gate ${item.gate_id} has the lowest displayed evaluator cost among ${safe.length} safe option${safe.length === 1 ? "" : "s"}.`;
    } else if(selectedRank !== null){
      summary = `The solution selected gate ${item.gate_id}, ranked #${selectedRank} by the displayed local evaluator cost. Other safe gates may be preferred by joint-plan or stability logic.`;
    } else {
      summary = `Gate ${item.gate_id} was selected, but the evaluator analysis did not find it safe at this decision point.`;
    }
    const candidateHtml = item.candidates.map(candidate => {
      const selected = candidate.selected ? " · SELECTED" : "";
      if(!candidate.safe){
        const reasons = candidate.reasons.length
          ? candidate.reasons.join("; ")
          : "Rejected by a hard compatibility or availability rule.";
        return `<div class="gate-candidate blocked"><strong>${escapeHtml(candidate.gate_id)}${selected}</strong> · Not available: ${escapeHtml(reasons)}</div>`;
      }
      const costs = candidate.costs;
      const costText = `walk ${Number(costs.walking_distance).toFixed(2)} + premium-gate use ${Number(costs.domestic_at_international_gate).toFixed(2)} + gate move ${Number(costs.gate_move).toFixed(2)} = ${Number(candidate.score).toFixed(2)} points`;
      return `<div class="gate-candidate${candidate.selected ? " selected" : ""}"><strong>${escapeHtml(candidate.gate_id)}${selected}</strong> · <span class="gate-cost">${costText}</span></div>`;
    }).join("");
    return `<details class="gate-explanation"><summary>Gate ${escapeHtml(item.gate_id)} for flight ${escapeHtml(item.flight_id)}</summary>`
      + `<p>${escapeHtml(summary)} Safe options are ranked by the evaluator's displayed local cost; the solution may use additional joint-planning logic.</p>`
      + `<div class="gate-candidates">${candidateHtml}</div></details>`;
  }).join("") : '<span class="muted" style="font-size:12px">No gate decisions at this plan tick.</span>';
}

function renderFlightBoards(tick, t){
  const flights = tick.flights || {};
  const gateByFlight = {};
  for(const [gateId, bookings] of Object.entries(tick.plan || {})){
    for(const booking of bookings) gateByFlight[booking.flight_id] = gateId;
  }

  const updates = {};
  for(let i = 0; i <= planIdx; i++){
    for(const event of TRACE.ticks[i].events || []){
      if(!event.flight_id || !event.status) continue;
      if(event.status === "REINSTATED") { delete updates[event.flight_id]; continue; }
      if(["DELAYED", "SCHEDULE UPDATED", "GATE CHANGED", "DIVERTED", "AIRCRAFT CHANGE"].includes(event.status)){
        const statuses = updates[event.flight_id] || (updates[event.flight_id] = []);
        if(!statuses.includes(event.status)) statuses.push(event.status);
      }
    }
  }

  const escape = value => String(value ?? "—")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
  const flightStatus = (fid, flight, leg, direction) => {
    if(flight.cancelled || leg.cancelled) return {text: "CANCELLED", className: "cancelled"};
    const complete = direction === "departure"
      ? leg.departure_minute <= t : leg.arrival_minute <= t;
    const updatesForFlight = updates[fid] || [];
    if(complete){
      const completed = direction === "departure" ? "DEPARTED" : "LANDED";
      return {
        text: [completed, ...updatesForFlight].join(" · "),
        className: direction === "departure" ? "departed" : "landed",
      };
    }
    if(updatesForFlight.length){
      const className = updatesForFlight.includes("DIVERTED") ? "diverted"
        : updatesForFlight.includes("GATE CHANGED") ? "gate-changed"
        : updatesForFlight.some(status => status === "DELAYED" || status === "SCHEDULE UPDATED") ? "delayed" : "on-time";
      return {text: updatesForFlight.join(" · "), className};
    }
    return {text: "ON TIME", className: "on-time"};
  };
  const departures = [], arrivals = [];
  for(const [fid, flight] of Object.entries(flights)){
    for(const leg of flight.legs || []){
      const gate = gateByFlight[fid] || "—";
      if(leg.departure_station === "YYZ"){
        const {text: status, className: statusClass} = flightStatus(fid, flight, leg, "departure");
        departures.push({
          minute: leg.departure_minute, time: fmt(leg.departure_minute), flight: shortId(fid),
          route: leg.arrival_station, gate, status, statusClass,
        });
      }
      if(leg.arrival_station === "YYZ"){
        const {text: status, className: statusClass} = flightStatus(fid, flight, leg, "arrival");
        arrivals.push({
          minute: leg.arrival_minute, time: fmt(leg.arrival_minute), flight: shortId(fid),
          route: leg.departure_station, gate, status, statusClass,
        });
      }
    }
  }

  const renderRows = (rows, emptyMessage) => {
    rows.sort((a,b) => a.minute - b.minute || a.flight.localeCompare(b.flight));
    if(!rows.length) return `<tr><td class="board-empty" colspan="5">${emptyMessage}</td></tr>`;
    return rows.map(row => `<tr><td class="board-time">${escape(row.time)}</td>`
      + `<td class="board-flight">${escape(row.flight)}</td><td>${escape(row.route)}</td>`
      + `<td>${escape(row.gate)}</td><td class="board-status ${row.statusClass}">${escape(row.status)}</td></tr>`
    ).join("");
  };
  document.getElementById("departuresBoard").innerHTML = renderRows(departures, "No departures in this schedule.");
  document.getElementById("arrivalsBoard").innerHTML = renderRows(arrivals, "No arrivals in this schedule.");
}

function comparisonPlanIndex(trace, t){
  let index = 0;
  for(let i = 0; i < (trace.ticks || []).length; i++){
    if(trace.ticks[i].time <= t) index = i;
    else break;
  }
  return index;
}

function renderComparisonTimeline(root, trace, tick, differentFlights){
  const start = Number(trace.time_min) || 0;
  const end = (Number(trace.time_max) || start) + DEPARTURE_TOTAL_MINUTES;
  const span = Math.max(1, end - start);
  const width = Math.max(680, Math.round(span * 0.7));
  const labelWidth = 72;
  const trackWidth = width - labelWidth;
  const step = span > 900 ? 120 : span > 480 ? 60 : 30;
  const times = [];
  for(let minute = start; minute <= end; minute += step) times.push(minute);
  if(!times.length || times[times.length - 1] < end) times.push(end);
  const escape = value => String(value ?? "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
  const marks = times.map((minute, index) => {
    const pct = Math.max(0, Math.min(1, (minute - start) / span));
    const major = index % 2 === 0 ? " major" : "";
    return `<i class="compare-time-gridline${major}" style="left:${pct * 100}%"></i>`;
  }).join("");
  const labels = times.map(minute => {
    const pct = Math.max(0, Math.min(1, (minute - start) / span));
    return `<span class="compare-time-label" style="left:${pct * 100}%">${escape(fmt(minute))}</span>`;
  }).join("");
  const rows = (trace.gates || []).map(gate => {
    const bookings = (tick.plan && tick.plan[gate.id]) || [];
    const bars = bookings.flatMap(booking => (booking.intervals || []).map(interval => {
      const left = Math.max(0, Math.min(1, (interval[0] - start) / span));
      const right = Math.max(0, Math.min(1, (interval[1] - start) / span));
      if(right <= left) return "";
      const different = differentFlights.has(booking.flight_id);
      const title = `${shortId(booking.flight_id)} at ${gate.id} · ${fmt(interval[0])}–${fmt(interval[1])}`;
      return `<span class="compare-timeline-bar${different ? " compare-bar-different" : ""}"`
        + ` style="left:${left * 100}%;width:${Math.max(0.25, (right-left) * 100)}%;background:${colorFor(booking.flight_id)}"`
        + ` title="${escape(title)}">${escape(shortId(booking.flight_id))}</span>`;
    })).join("");
    return `<div class="compare-timeline-row"><div class="compare-timeline-gate">${escape(gate.id)}</div>`
      + `<div class="compare-timeline-track">${marks}${bars}</div></div>`;
  }).join("");
  const initial = Math.max(0, Math.min(1, (clock - start) / span));
  root.innerHTML = `<div class="compare-timeline-chart" data-start="${start}" data-span="${span}"`
    + ` data-track-width="${trackWidth}" style="width:${width}px">`
    + `<div class="compare-time-ruler"><div></div><div class="compare-timeline-track">${marks}${labels}</div></div>`
    + `${rows || '<div class="compare-timeline-row"><div class="compare-timeline-gate"></div><div class="compare-timeline-track">No gates in this solution.</div></div>'}`
    + `<i class="compare-playhead" style="left:${labelWidth + trackWidth * initial}px"></i></div>`;
}

function moveComparisonPlayheads(t){
  for(const rootId of ["compareTimelineA", "compareTimelineB"]){
    const chart = document.querySelector(`#${rootId} .compare-timeline-chart`);
    if(!chart) continue;
    const start = Number(chart.dataset.start), span = Number(chart.dataset.span);
    const trackWidth = Number(chart.dataset.trackWidth);
    const pct = Math.max(0, Math.min(1, (t - start) / Math.max(1, span)));
    const playhead = chart.querySelector(".compare-playhead");
    const position = 72 + trackWidth * pct;
    if(playhead) playhead.style.left = `${position}px`;
    const viewport = chart.closest(".compare-timeline-scroll");
    if(!viewport || !viewport.clientWidth) continue;
    const maxScroll = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
    const target = Math.max(0, Math.min(maxScroll, position - viewport.clientWidth * 0.7));
    if(Math.abs(viewport.scrollLeft - target) > 1) viewport.scrollLeft = target;
  }
}

function renderComparison(t){
  if(!document.getElementById("compareTimelineA")) return;
  if(!TRACE || !TRACE_COMPARE_B || !TRACE_COMPARE_B.ticks || !TRACE_COMPARE_B.ticks.length){
    document.getElementById("compareTimelineHeadingA").textContent = "SOLUTION A";
    document.getElementById("compareTimelineHeadingB").textContent = "SOLUTION B";
    document.getElementById("compareTimelineA").innerHTML = '<div class="placeholder">Run a comparison to view this timeline.</div>';
    document.getElementById("compareTimelineB").innerHTML = '<div class="placeholder">Run a comparison to view this timeline.</div>';
    document.getElementById("compareSummary").textContent = "";
    COMPARE_TICK_A = -1; COMPARE_TICK_B = -1;
    return;
  }
  const indexB = comparisonPlanIndex(TRACE_COMPARE_B, t);
  const tickA = TRACE.ticks[planIdx];
  const tickB = TRACE_COMPARE_B.ticks[indexB];
  if(COMPARE_TICK_A === planIdx && COMPARE_TICK_B === indexB){
    moveComparisonPlayheads(t);
    return;
  }
  const gatesA = {}, gatesB = {};
  for(const [gate, bookings] of Object.entries(tickA.plan || {}))
    for(const booking of bookings) gatesA[booking.flight_id] = gate;
  for(const [gate, bookings] of Object.entries(tickB.plan || {}))
    for(const booking of bookings) gatesB[booking.flight_id] = gate;

  const flights = [...new Set([...Object.keys(gatesA), ...Object.keys(gatesB)])].sort();
  const differentFlights = new Set();
  let differences = 0, matches = 0, pending = 0;
  for(const fid of flights){
    const gateA = gatesA[fid] || null;
    const gateB = gatesB[fid] || null;
    const different = gateA !== null && gateB !== null && gateA !== gateB;
    const assignedBoth = gateA !== null && gateB !== null;
    if(different){ differences++; differentFlights.add(fid); }
    else if(assignedBoth) matches++;
    else pending++;
  }
  document.getElementById("compareTimelineHeadingA").textContent = TRACE.solution || "Solution A";
  document.getElementById("compareTimelineHeadingB").textContent = TRACE_COMPARE_B.solution || "Solution B";
  document.getElementById("compareSummary").textContent =
    `${differences} different · ${matches} matching · ${pending} pending`;
  renderComparisonTimeline(document.getElementById("compareTimelineA"), TRACE, tickA, differentFlights);
  renderComparisonTimeline(document.getElementById("compareTimelineB"), TRACE_COMPARE_B, tickB, differentFlights);
  COMPARE_TICK_A = planIdx; COMPARE_TICK_B = indexB;
  moveComparisonPlayheads(t);
}

// ---- clock: move playhead, mark gates in-use / free / down -----------------
function updateClock(t){
  clock = t;
  document.getElementById("clock").textContent = fmt(t);
  document.getElementById("clockMore").textContent = fmt(t);
  document.getElementById("clockBoards").textContent = fmt(t);
  document.getElementById("clockCompare").textContent = fmt(t);
  document.getElementById("compareClock").textContent = `Assignments at ${fmt(t)}`;
  document.getElementById("clock2").textContent = fmt(t);
  document.getElementById("scrub").value = Math.round(frac(t)*1000);
  document.getElementById("scrubMore").value = Math.round(frac(t)*1000);
  document.getElementById("scrubBoards").value = Math.round(frac(t)*1000);
  document.getElementById("scrubCompare").value = Math.round(frac(t)*1000);
  document.getElementById("playhead").style.left =
    `calc(var(--labelw) + (100% - var(--labelw)) * ${frac(t)})`;

  const tick = TRACE.ticks[planIdx];
  const statusRows = [];
  for(const g of TRACE.gates){
    const row = document.querySelector(`.row[data-gid="${g.id}"]`);
    const statusEl = row.querySelector("[data-status]");
    row.classList.remove("inuse","down");
    let occupant = null, down = false;
    (tick.plan[g.id]||[]).forEach(b => b.intervals.forEach(iv => {
      if(t >= iv[0] && t < iv[1]) occupant = shortId(b.flight_id);
    }));
    (tick.outages[g.id]||[]).forEach(w => { if(t >= w[0] && t < clampEnd(w[1])) down = true; });

    row.querySelectorAll(".bar").forEach(bar => {
      const live = t >= +bar.dataset.s && t < +bar.dataset.e;
      bar.classList.toggle("live", live);
    });

    if(down){ row.classList.add("down"); statusEl.textContent = "OUTAGE"; statusEl.style.color = "#f85149"; }
    else if(occupant){ row.classList.add("inuse"); statusEl.textContent = "● " + occupant; statusEl.style.color = "#3fb950"; }
    else { statusEl.textContent = "free"; statusEl.style.color = "#6e7681"; }
    statusRows.push(`<div class="kv"><span>${g.id} <span class="muted">(${TYPE_SHORT[g.type]})</span></span>`
      + `<span style="color:${down?"#f85149":occupant?"#3fb950":"#6e7681"}">${down?"outage":occupant?occupant:"free"}</span></div>`);
  }
  document.getElementById("status").innerHTML = statusRows.join("");
  renderSimulation(tick, t);
  renderFlightBoards(tick, t);
  renderComparison(t);
}

// ---- playback --------------------------------------------------------------
function planIndexAtTime(t){
  let index = 0;
  for(let i = 0; i < TRACE.ticks.length; i++){
    if(TRACE.ticks[i].time <= t) index = i;
    else break;
  }
  return index;
}

function setClockTime(t){
  t = Math.max(TMIN, Math.min(TMAX, t));
  const nextPlan = planIndexAtTime(t);
  clock = t;
  if(nextPlan !== planIdx){
    planIdx = nextPlan;
    renderPlan();
  } else {
    updateClock(t);
  }
}

function step(ts){
  if(!playing) return;
  if(lastTs == null) lastTs = ts;
  const dt = (ts - lastTs)/1000; lastTs = ts;
  const perSec = +document.getElementById("speed").value;   // sim-minutes per real second
  let t = clock + dt*perSec;
  if(t >= TMAX){ t = TMAX; setPlaying(false); }
  setClockTime(t);
  if(playing) raf = requestAnimationFrame(step);
}
function setPlaying(on){
  if(on && (manualOverrideMode || manualPlanInvalidated)){
    setManualFeedback("Automated playback is stopped for manual gate changes. Run a solution again to resume.", false);
    return;
  }
  playing = on; lastTs = null;
  document.getElementById("play").innerHTML = on ? "&#10073;&#10073; Pause" : "&#9654; Play";
  document.getElementById("playMore").innerHTML = on ? "&#10073;&#10073; Pause" : "&#9654; Play";
  document.getElementById("playBoards").innerHTML = on ? "&#10073;&#10073; Pause" : "&#9654; Play";
  document.getElementById("playCompare").innerHTML = on ? "&#10073;&#10073; Pause" : "&#9654; Play";
  if(on){ if(clock >= TMAX) setClockTime(TMIN); raf = requestAnimationFrame(step); }
  else if(raf){ cancelAnimationFrame(raf); raf = null; }
}

function setTick(i){
  setPlaying(false);
  planIdx = Math.max(0, Math.min(TRACE.ticks.length-1, i));
  clock = TRACE.ticks[planIdx].time;
  renderPlan();
}

function setDecPlay(on){
  if(on && (manualOverrideMode || manualPlanInvalidated)){
    setManualFeedback("Automated decision playback is stopped for manual gate changes. Run a solution again to resume.", false);
    return;
  }
  const btn = document.getElementById("playDec");
  if(decTimer){ clearInterval(decTimer); decTimer = null; }
  if(on){
    if(planIdx >= TRACE.ticks.length-1) setTick(0);
    decTimer = setInterval(() => {
      if(planIdx >= TRACE.ticks.length-1) setDecPlay(false);
      else setTick(planIdx+1);
    }, 1400);
    btn.innerHTML = "&#10073;&#10073; Decisions";
  } else {
    btn.innerHTML = "&#9654; Decisions";
  }
  document.getElementById("playDecMore").innerHTML = btn.innerHTML;
  document.getElementById("playDecBoards").innerHTML = btn.innerHTML;
  document.getElementById("playDecCompare").innerHTML = btn.innerHTML;
}

for(const id of ["play", "playMore", "playBoards", "playCompare"]) document.getElementById(id).onclick = () => setPlaying(!playing);
for(const id of ["reset", "resetMore", "resetBoards", "resetCompare"]) document.getElementById(id).onclick = () => { setPlaying(false); setClockTime(TMIN); };
for(const id of ["scrub", "scrubMore", "scrubBoards", "scrubCompare"]) document.getElementById(id).oninput = (e) => {
  setPlaying(false); setClockTime(TMIN + (e.target.value/1000)*SPAN);
};
for(const id of ["speed", "speedMore", "speedBoards", "speedCompare"]) document.getElementById(id).onchange = (e) => {
  document.querySelectorAll(".playback-speed").forEach(speed => speed.value = e.target.value);
};
for(const id of ["tprev", "tprevMore", "tprevBoards", "tprevCompare"]) document.getElementById(id).onclick = () => { setDecPlay(false); setTick(planIdx-1); };
for(const id of ["tnext", "tnextMore", "tnextBoards", "tnextCompare"]) document.getElementById(id).onclick = () => { setDecPlay(false); setTick(planIdx+1); };
for(const id of ["playDec", "playDecMore", "playDecBoards", "playDecCompare"]) document.getElementById(id).onclick = () => setDecPlay(!decTimer);

for(const button of document.querySelectorAll(".tab-button")){
  button.onclick = () => {
    document.querySelectorAll(".tab-button").forEach(tab => {
      const active = tab === button;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".tab-pane").forEach(pane =>
      pane.classList.toggle("active", pane.id === button.getAttribute("aria-controls"))
    );
    if(button.id === "compareTabButton") moveComparisonPlayheads(clock);
  };
}
document.getElementById("manualOverrideButton").onclick = toggleManualOverride;

for(const select of document.querySelectorAll(".scenario-select, .solution-select")){
  select.onchange = () => document.querySelectorAll(`.${select.classList.contains("scenario-select") ? "scenario-select" : "solution-select"}`)
    .forEach(other => { if(other !== select) other.value = select.value; });
}

// ---- server mode: scenario picker + live Run --------------------------------
function showError(msg){
  const box = document.getElementById("errorBox");
  if(!msg){ box.style.display = "none"; box.textContent = ""; return; }
  box.style.display = "block"; box.textContent = msg;
}

function cancelActiveRunRequest(){
  if(!activeRunController) return;
  const controller = activeRunController;
  activeRunController = null;
  controller.abort();
  document.querySelectorAll(".run-button").forEach(button => button.disabled = false);
  document.querySelectorAll(".run-status").forEach(status => status.textContent = "");
  const compareButton = document.getElementById("compareBtn");
  if(compareButton) compareButton.disabled = false;
  const compareStatus = document.getElementById("compareStatus");
  if(compareStatus) compareStatus.textContent = "";
  for(const id of ["compareCompletionA", "compareCompletionB"]){
    const badge = document.getElementById(id);
    if(badge && badge.textContent === "RUNNING") badge.textContent = "STOPPED";
  }
}

function runNow(){
  if(manualOverrideMode){ manualOverrideMode = false; finishManualDrag(); updateManualOverrideUI(); }
  cancelActiveRunRequest();
  setPlaying(false); setDecPlay(false);
  const activePane = document.querySelector(".tab-pane.active") || document.getElementById("visualTab");
  const scenario = activePane.querySelector(".scenario-select").value;
  const solution = activePane.querySelector(".solution-select").value;
  const controller = new AbortController();
  activeRunController = controller;
  document.querySelectorAll(".run-button").forEach(btn => btn.disabled = true);
  document.querySelectorAll(".run-status").forEach(status => status.textContent = "running…");
  showError(null);
  fetch(`/api/run?scenario=${encodeURIComponent(scenario)}&solution=${encodeURIComponent(solution)}`, {signal:controller.signal})
    .then(r => r.json())
    .then(data => {
      if(data.error){ showError(data.error); document.getElementById("chartPlaceholder").style.display=""; return; }
      if(!data.trace || !data.trace.ticks || !data.trace.ticks.length){
        const reason = data.trace && data.trace.result && data.trace.result.reason;
        showError(reason || "The simulation produced no decision ticks.");
        document.getElementById("chartPlaceholder").style.display="";
        return;
      }
      const ph = document.getElementById("chartPlaceholder"); if(ph) ph.remove();
      TRACE_COMPARE_B = null;
      loadTrace(data.trace);
    })
    .catch(err => { if(err.name !== "AbortError") showError(String(err)); })
    .finally(() => {
      if(activeRunController === controller){
        activeRunController = null;
        document.querySelectorAll(".run-button").forEach(btn => btn.disabled = false);
        document.querySelectorAll(".run-status").forEach(status => status.textContent = "");
      }
    });
}

function setCompareCompletion(side, solution, data, phase){
  const label = document.getElementById(`compareSolutionLabel${side}`);
  const badge = document.getElementById(`compareCompletion${side}`);
  label.textContent = `${side === "A" ? "Solution A" : "Solution B"}: ${solution || "—"}`;
  badge.className = "badge";
  if(phase){
    badge.textContent = phase;
    return;
  }
  const completed = !data?.error && data?.trace?.result?.status === "OK" && !!data?.trace?.ticks?.length;
  badge.textContent = completed
    ? `COMPLETED · score ${scoreText(data.trace.result)}` : "FAILED";
  badge.classList.add(completed ? "ok" : "fail");
}

function runComparison(){
  if(manualOverrideMode){ manualOverrideMode = false; finishManualDrag(); updateManualOverrideUI(); }
  cancelActiveRunRequest();
  setPlaying(false); setDecPlay(false);
  const pane = document.getElementById("compareTab");
  const scenario = pane.querySelector(".scenario-select").value;
  const solutionA = pane.querySelector(".compare-solution-a").value;
  const solutionB = pane.querySelector(".compare-solution-b").value;
  const button = document.getElementById("compareBtn");
  const status = document.getElementById("compareStatus");
  if(!solutionA || !solutionB || solutionA === solutionB){
    status.textContent = "Choose two different solutions to compare.";
    return;
  }
  setCompareCompletion("A", solutionA, null, "RUNNING");
  setCompareCompletion("B", solutionB, null, "RUNNING");
  button.disabled = true; status.textContent = "running both solutions…"; showError(null);
  const controller = new AbortController();
  activeRunController = controller;
  const fetchTrace = solution => fetch(
    `/api/run?scenario=${encodeURIComponent(scenario)}&solution=${encodeURIComponent(solution)}`,
    {signal:controller.signal}
  ).then(response => response.json());
  Promise.all([fetchTrace(solutionA), fetchTrace(solutionB)])
    .then(([dataA, dataB]) => {
      setCompareCompletion("A", solutionA, dataA);
      setCompareCompletion("B", solutionB, dataB);
      const invalid = dataA.error ? dataA : dataB.error ? dataB : null;
      if(invalid){ showError(invalid.error); return; }
      if(!dataA.trace?.ticks?.length || !dataB.trace?.ticks?.length){
        showError("One or both solutions produced no decision ticks.");
        return;
      }
      TRACE_COMPARE_B = dataB.trace;
      document.querySelectorAll(".solution-select").forEach(select => select.value = solutionA);
      const placeholder = document.getElementById("chartPlaceholder");
      if(placeholder) placeholder.remove();
      loadTrace(dataA.trace);
    })
    .catch(error => {
      if(error.name !== "AbortError"){
        setCompareCompletion("A", solutionA, {error});
        setCompareCompletion("B", solutionB, {error});
        showError(String(error));
      }
    })
    .finally(() => {
      if(activeRunController === controller){
        activeRunController = null;
        button.disabled = false;
        status.textContent = "";
      }
    });
}

if(SERVER_MODE){
  document.getElementById("runbar").style.display = "flex";
  document.getElementById("runbarMore").style.display = "flex";
  document.getElementById("runbarBoards").style.display = "flex";
  document.getElementById("runbarCompare").style.display = "flex";
  document.getElementById("runBtn").onclick = runNow;
  document.getElementById("runBtnMore").onclick = runNow;
  document.getElementById("runBtnBoards").onclick = runNow;
  document.getElementById("compareBtn").onclick = runComparison;
  Promise.all([fetch("/api/scenarios").then(r => r.json()),
               fetch("/api/solutions").then(r => r.json())]).then(([scenarios, solutions]) => {
    document.querySelectorAll(".scenario-select").forEach(sel => {
      sel.innerHTML = scenarios.scenarios.map(s => `<option value="${s}">${s}</option>`).join("");
    });
    document.querySelectorAll(".solution-select").forEach(sel => {
      sel.innerHTML = solutions.solutions.map(s => `<option value="${s}">${s}</option>`).join("");
      if(solutions.solutions.includes("solution_scored")) sel.value = "solution_scored";
      else if(solutions.solutions.includes("solution")) sel.value = "solution";
    });
    document.querySelectorAll(".compare-solution").forEach(sel => {
      sel.innerHTML = solutions.solutions.map(s => `<option value="${s}">${s}</option>`).join("");
    });
    const compareA = document.querySelector(".compare-solution-a");
    const compareB = document.querySelector(".compare-solution-b");
    compareA.value = solutions.solutions.includes("solution_scored") ? "solution_scored" : solutions.solutions[0];
    compareB.value = solutions.solutions.includes("solution") && compareA.value !== "solution"
      ? "solution" : (solutions.solutions.find(solution => solution !== compareA.value) || compareA.value);
    if(scenarios.scenarios.length && solutions.solutions.length) runNow();
  }).catch(err => showError("Could not load scenario or solution list: " + err));
} else {
  document.getElementById("chartPlaceholder").remove();
  loadTrace(MODE.trace);
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
