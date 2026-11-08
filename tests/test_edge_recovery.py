"""Adversarial recovery and planner-isolation regression tests."""

from copy import deepcopy

import pytest

from solution import decide as team_decide
from person1.recovery_planner import decide as recovery_decide


def gate(span=60, dist=100):
    return {
        "gate_type": 1,
        "max_wingspan": span,
        "jetbridge": True,
        "dist": dist,
    }


def profile(span=30, intervals=((100, 200),)):
    return {
        "intervals": [list(interval) for interval in intervals],
        "category": 1,
        "wingspan": span,
        "jetbridge_required": True,
        "priority": False,
    }


def observation(gates, now=0):
    return {
        "time": now,
        "gates": gates,
        "gate_outages": {},
        "waiting_flights": {},
        "assigned_flights": {},
        "changed_flights": {},
        "gate_assignments": {gate_id: [] for gate_id in gates},
        "ac_info": {},
    }


def assign(obs, flight_id, gate_id, flight):
    obs["assigned_flights"][flight_id] = {**deepcopy(flight), "gate_id": gate_id}
    obs["gate_assignments"][gate_id].append({
        "flight_id": flight_id,
        "intervals": deepcopy(flight["intervals"]),
    })


def change(obs, flight_id, flight):
    current = obs["assigned_flights"][flight_id]
    gate_id = current["gate_id"]
    old = deepcopy(current)
    old.pop("gate_id")
    obs["assigned_flights"][flight_id] = {**deepcopy(flight), "gate_id": gate_id}
    obs["changed_flights"][flight_id] = {
        "flight_id": flight_id,
        "gate_id": gate_id,
        "reason": "equipment",
        "old": old,
        "new": deepcopy(flight),
    }


def test_arrived_blocker_uses_next_best_sequenceable_repair():
    obs = observation(
        {"D1": gate(35), "D2": gate(60), "D3": gate(35, 200)}, now=150
    )
    assign(obs, "A", "D1", profile())
    assign(obs, "B", "D2", profile())
    change(obs, "A", profile(50))

    actions = team_decide(obs)

    # The cheaper A<->B swap is cyclic. B must use D3 so A can then use D2.
    assert actions["reassignments"] == [("B", "D3"), ("A", "D2")]


def test_three_flight_dependency_chain_is_sequenced():
    obs = observation({
        "D1": gate(35), "D2": gate(45), "D3": gate(65), "D4": gate(35, 200),
    })
    assign(obs, "A", "D1", profile())
    assign(obs, "B", "D2", profile(40))
    assign(obs, "C", "D3", profile())
    change(obs, "A", profile(40))
    change(obs, "B", profile(55))

    actions = team_decide(obs)

    assert actions["reassignments"] == [
        ("C", "D4"), ("B", "D3"), ("A", "D2"),
    ]


def test_malformed_interval_is_not_assigned_by_either_planner():
    obs = observation({"D1": gate()})
    obs["waiting_flights"]["BAD"] = profile(intervals=((200, 100),))
    empty = {"assignments": [], "reassignments": []}

    assert team_decide(obs) == empty
    assert recovery_decide(obs) == empty


def simple_waiting_observation():
    obs = observation({"D1": gate()})
    obs["waiting_flights"]["A"] = profile()
    return obs


def test_joint_optimizer_crash_does_not_block_recovery_planner(monkeypatch):
    import person1.plan_optimizer as optimizer

    def fail(**_kwargs):
        raise RuntimeError("synthetic optimizer failure")

    monkeypatch.setattr(optimizer, "plan_joint_assignments", fail)
    actions = team_decide(simple_waiting_observation())

    assert actions["assignments"] == [("A", "D1")]


def test_recovery_planner_crash_does_not_block_joint_optimizer(monkeypatch):
    import person1.recovery_planner as recovery_planner

    def fail(_observation):
        raise RuntimeError("synthetic recovery planner failure")

    monkeypatch.setattr(recovery_planner, "decide", fail)
    actions = team_decide(simple_waiting_observation())

    assert actions["assignments"] == [("A", "D1")]


def test_unrecoverable_changed_flight_raises_explicit_error():
    obs = observation({"D1": gate(35)})
    assign(obs, "A", "D1", profile())
    change(obs, "A", profile(50))

    with pytest.raises(RuntimeError, match="No safe recovery plan.*A"):
        team_decide(obs)


def test_unchanged_changed_booking_uses_new_intervals_for_waiters():
    obs = observation({"D1": gate(), "D2": gate(60, 200)})
    assign(obs, "A", "D1", profile(intervals=((100, 140),)))
    change(obs, "A", profile(intervals=((100, 200),)))
    obs["waiting_flights"]["B"] = profile(intervals=((150, 170),))

    actions = recovery_decide(obs)

    assert actions["reassignments"] == []
    assert actions["assignments"] == [("B", "D2")]
