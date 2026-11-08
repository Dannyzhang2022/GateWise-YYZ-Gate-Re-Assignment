"""Person 1: coordinate joint gate planning and disruption recovery."""

from copy import deepcopy
from typing import Any

from gms.config import WEIGHTS


def _heuristic_cost(actions, observation):
    """Approximate the evaluator's soft score for a candidate action list."""
    gates = observation.get("gates", {})
    flights = {}
    flights.update(observation.get("assigned_flights", {}))
    flights.update(observation.get("waiting_flights", {}))
    changed = observation.get("changed_flights", {})
    current_time = observation.get("time")

    assignments = {}
    for flight_id, gate_id in actions.get("assignments", []):
        assignments[flight_id] = gate_id
    for flight_id, gate_id in actions.get("reassignments", []):
        assignments[flight_id] = gate_id

    total = 0.0
    for flight_id, gate_id in assignments.items():
        flight = flights.get(flight_id, {})
        gate = gates.get(gate_id)
        if gate is None:
            continue
        total += gate.get("dist", 0) * WEIGHTS["walking_distance_per_m"]
        if flight.get("category") == 1 and gate.get("gate_type") == 2:
            total += WEIGHTS["domestic_at_intl_gate"]
        current_gate = flight.get("gate_id")
        if current_gate is not None and gate_id != current_gate:
            starts = [iv[0] for iv in flight.get("intervals", []) if len(iv) == 2]
            arrived = (
                current_time is not None and starts and min(starts) <= current_time
            )
            if arrived:
                total += WEIGHTS["reassignment_after_arrival"]
            elif flight_id not in changed:
                total += WEIGHTS["reassignment"]

    # Unassigned flight penalties
    for fid, flight in flights.items():
        if fid in assignments:
            continue
        if flight.get("priority"):
            total += WEIGHTS["unassigned_emergency"]
        else:
            total += WEIGHTS["unassigned_flight"]

    return total


def decide(observation: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    """Choose the lower-cost plan between the coordinated optimizer and the
    stable best-fit fallback."""
    from person1.plan_optimizer import plan_joint_assignments
    from solution_bestfit import decide as bestfit_decide

    gates = observation.get("gates", {})
    outages = deepcopy(observation.get("gate_outages", {}))
    bookings = deepcopy(observation.get("gate_assignments", {}))
    waiting = deepcopy(observation.get("waiting_flights", {}))
    assigned = deepcopy(observation.get("assigned_flights", {}))
    changed = observation.get("changed_flights", {})

    for flight_id, change in changed.items():
        current = assigned.setdefault(flight_id, {})
        current.update(deepcopy(change.get("new", {})))
        current["gate_id"] = change.get("gate_id", current.get("gate_id"))

    candidate_ids = set(assigned) | set(waiting)
    from person4.disruption_repair import assess_changed_flights
    disruption_status = assess_changed_flights(
        changed,
        gates,
        bookings,
        outages,
        planning_flight_ids=candidate_ids,
    )

    team_actions = plan_joint_assignments(
        gates=gates,
        outages=outages,
        bookings=bookings,
        assigned_flights=assigned,
        waiting_flights=waiting,
        changed_flights=changed,
        disruption_status=disruption_status,
        current_time=observation.get("time"),
    )
    bestfit_actions = bestfit_decide(observation)

    # Both planners account for occupancy and sequence dependent moves. Do not
    # prune individual reassignments here: a locally optional move may clear a
    # destination for another flight or be required by an updated booking.
    bt = _heuristic_cost(team_actions, observation)
    bb = _heuristic_cost(bestfit_actions, observation)
    if bb < bt:
        return bestfit_actions
    return team_actions
