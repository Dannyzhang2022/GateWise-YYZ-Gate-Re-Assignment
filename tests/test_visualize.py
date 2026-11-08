import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request

from conftest import scenario_path
from evaluator import run_sim
from visualize import _TEMPLATE, generate, list_scenarios, list_solutions, serve


def test_record_mode_returns_a_trace(static_path):
    result = run_sim(static_path, scenario_path("gate_outage"), "solution_bestfit",
                     verbose=False, record=True)
    trace = result["trace"]
    assert [g["id"] for g in trace["gates"]]          # gate metadata present
    assert len(trace["ticks"]) == 2                   # one per info-time
    assert trace["time_min"] <= trace["ticks"][0]["time"]
    # last tick: AC-401 moved off the outaged D1
    last = trace["ticks"][-1]
    assert "D1" in last["outages"]
    assert "D1" not in last["plan"]
    disruptions = [event for event in last["events"] if event["kind"] == "DISRUPTION"]
    solutions = [event for event in last["events"] if event["kind"] == "SOLUTION"]
    assert any("Gate D1 became unavailable" in event["subject"]
               and event["reason"] for event in disruptions)
    assert any("moved from gate D1" in event["subject"]
               and event["reason"] for event in solutions)
    assert "DISRUPTION:" in _TEMPLATE and "SOLUTION:" in _TEMPLATE and "REASON:" in _TEMPLATE
    moved_flight = next(booking for bookings in last["plan"].values() for booking in bookings)
    assert {"wingspan", "jetbridge_required", "aircraft_types"} <= moved_flight.keys()
    assert 'id="simMap"' in _TEMPLATE and "function renderSimulation" in _TEMPLATE
    assert "function planIndexAtTime" in _TEMPLATE and "function setClockTime" in _TEMPLATE
    assert _TEMPLATE.index('id="simMap"') < _TEMPLATE.index('id="chart"')
    assert ">DEPARTURES</text>" in _TEMPLATE and 'setAttribute("viewBox", "0 0 1500 780")' in _TEMPLATE
    assert "dataset.phase" in _TEMPLATE and "MOVE_MINUTES" in _TEMPLATE
    assert "CONCOURSE" not in _TEMPLATE and "sim-legend" not in _TEMPLATE
    assert "text-anchor: middle" in _TEMPLATE and "runway-threshold" not in _TEMPLATE
    assert 'M55 19 L25 29' in _TEMPLATE and 'M60 2 C55 10' in _TEMPLATE
    assert ">TERMINAL 1</text>" in _TEMPLATE and ">DEPARTURES</text>" in _TEMPLATE
    assert ">TERMINAL 2</text>" in _TEMPLATE
    assert ">TERMINAL 3</text>" in _TEMPLATE
    assert ">INTERNATIONAL</text>" in _TEMPLATE
    assert ">CARGO</text>" in _TEMPLATE
    assert "CARGO APRON" not in _TEMPLATE
    assert "rotate(90deg)" in _TEMPLATE
    assert "sim-gate-label { transform: rotate(-90deg)" in _TEMPLATE
    assert 'gateLabel.className = "sim-gate-label"' in _TEMPLATE
    assert _TEMPLATE.count('<path class="pier"') == 9  # 3 domestic, 4 international, 2 cargo
    assert 'p[0]+" "+p[1]' in _TEMPLATE
    assert 'Bridge: ${booking.jetbridge_required ? "Y" : "N"} · Gate: ${gateId}' in _TEMPLATE
    assert "DEPARTING" not in _TEMPLATE


def test_generate_writes_self_contained_html(static_path):
    fd, path = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    try:
        out, result = generate(static_path, scenario_path("gate_outage"), "solution_bestfit", path)
        html = open(out, encoding="utf-8").read()
        # embedded mode payload must be valid JSON and self-contained (server: false)
        m = re.search(r"const MODE = (\{.*?\});", html, re.S)
        mode = json.loads(m.group(1))
        assert mode["server"] is False
        assert mode["trace"]["result"]["status"] == "OK"
        assert "loadTrace(MODE.trace)" in html and "function renderPlan" in html
    finally:
        os.remove(path)


def test_list_scenarios_matches_flight_data_dir():
    names = list_scenarios()
    assert "simple" in names and "gate_outage" in names
    assert names == sorted(names)


def test_list_solutions_matches_solution_modules():
    names = list_solutions()
    assert "solution_scored" in names
    assert "solution_firstfit" in names
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# Interactive server
# ---------------------------------------------------------------------------
def _start_server(port):
    t = threading.Thread(target=serve, kwargs={"port": port, "open_browser": False}, daemon=True)
    t.start()
    time.sleep(0.3)
    return t


def _get_json(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, json.loads(r.read())


def test_server_index_and_scenarios(static_path):
    _start_server(8851)
    with urllib.request.urlopen("http://127.0.0.1:8851/", timeout=5) as r:
        html = r.read().decode("utf-8")
    assert r.status == 200 and "runbar" in html
    assert 'id="solutionSelect"' in html

    status, data = _get_json("http://127.0.0.1:8851/api/scenarios")
    assert status == 200
    assert "simple" in data["scenarios"]

    status, data = _get_json("http://127.0.0.1:8851/api/solutions")
    assert status == 200
    assert "solution_scored" in data["solutions"]


def test_server_run_executes_real_solution(static_path):
    _start_server(8852)
    status, data = _get_json(
        "http://127.0.0.1:8852/api/run?scenario=gate_outage&solution=solution_bestfit")
    assert status == 200
    assert data["trace"]["result"]["status"] == "OK"


def test_server_run_reports_missing_solution_module(static_path):
    _start_server(8853)
    status, data = _get_json(
        "http://127.0.0.1:8853/api/run?scenario=simple&solution=does_not_exist_xyz")
    assert status == 200
    assert "error" in data and "does_not_exist_xyz" in data["error"]


def test_server_run_reports_unknown_scenario(static_path):
    _start_server(8854)
    try:
        urllib.request.urlopen("http://127.0.0.1:8854/api/run?scenario=nope&solution=solution", timeout=5)
        assert False, "expected an HTTPError"
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
        body = json.loads(exc.read())
        assert "Unknown scenario" in body["error"]


def test_server_picks_up_live_edits_without_restart(static_path):
    # A student edits solution.py while the server is already running; the next
    # /api/run must reflect the new code, not a cached import. The probe module
    # lives at the project root, same as a real solution.py.
    module_path = "_live_edit_probe.py"
    try:
        with open(module_path, "w") as f:
            f.write("def decide(observation):\n    return {'assignments': [], 'reassignments': []}\n")

        _start_server(8855)
        status, d1 = _get_json(
            "http://127.0.0.1:8855/api/run?scenario=simple&solution=_live_edit_probe")
        assert d1["trace"]["result"]["assigned"] == 0

        # A correct (compatibility + conflict aware) version of the probe.
        with open(module_path, "w") as f:
            f.write(
                "def _ok(fl, g):\n"
                "    if fl['wingspan'] > g['max_wingspan']: return False\n"
                "    if fl['jetbridge_required'] and not g['jetbridge']: return False\n"
                "    return g['gate_type'] in ((1, 2) if fl['category'] == 1 else (fl['category'],))\n"
                "def _free(intervals, bookings):\n"
                "    return not any(a[0] < y[1] and y[0] < a[1] for x in bookings for a in intervals for y in x['intervals'])\n"
                "def decide(observation):\n"
                "    gates = observation['gates']\n"
                "    local = {g: list(b) for g, b in observation['gate_assignments'].items()}\n"
                "    out = []\n"
                "    for fid, fl in observation['waiting_flights'].items():\n"
                "        for gid, g in gates.items():\n"
                "            if _ok(fl, g) and _free(fl['intervals'], local.get(gid, [])):\n"
                "                out.append((fid, gid))\n"
                "                local.setdefault(gid, []).append({'flight_id': fid, 'intervals': fl['intervals']})\n"
                "                break\n"
                "    return {'assignments': out, 'reassignments': []}\n"
            )

        status, d2 = _get_json(
            "http://127.0.0.1:8855/api/run?scenario=simple&solution=_live_edit_probe")
        assert d2["trace"]["result"]["status"] == "OK"
        assert d2["trace"]["result"]["assigned"] > 0   # proves the edit was picked up live
    finally:
        if os.path.exists(module_path):
            os.remove(module_path)
        import sys
        sys.modules.pop("_live_edit_probe", None)
