from flight_order import compatible, count_compatible_gates, order_waiting_flights
from evaluator import run_sim
from conftest import scenario_path


def test_compatibility_and_gate_count_use_solution_schema():
    flight = {"category": 1, "wingspan": 35, "jetbridge_required": True}
    gates = {
        "small": {"gate_type": 1, "max_wingspan": 30, "jetbridge": True},
        "no_bridge": {"gate_type": 1, "max_wingspan": 40, "jetbridge": False},
        "intl": {"gate_type": 2, "max_wingspan": 40, "jetbridge": True},
        "cargo": {"gate_type": 0, "max_wingspan": 40, "jetbridge": True},
    }
    assert compatible(flight, gates["intl"])
    assert not compatible(flight, gates["small"])
    assert not compatible(flight, gates["no_bridge"])
    assert count_compatible_gates(flight, gates) == 1


def test_waiting_order_prioritizes_emergency_scarcity_then_time():
    flights = {
        "later": {"intervals": [(200, 240)]},
        "emergency": {"priority": True, "intervals": [(300, 330)]},
        "scarce": {"intervals": [(100, 140)]},
        "early": {"intervals": [(50, 90)]},
    }
    counts = {"later": 1, "emergency": 4, "scarce": 1, "early": 2}
    assert order_waiting_flights(flights, counts) == ["emergency", "scarce", "later", "early"]


def test_combined_scenario_assigns_simultaneous_first_wave(static_path):
    result = run_sim(static_path, scenario_path("combined_24h"), "solution_scored",
                     verbose=False, record=True)
    assert result["status"] == "OK", result.get("reason")
    first_tick = result["trace"]["ticks"][0]
    assigned = [line for line in first_tick["log"] if "[ASSIGN]" in line]
    assert len(assigned) == 4
    assert result["unassigned"] == []
