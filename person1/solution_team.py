"""Person 1: coordinate joint gate planning and disruption recovery."""

from copy import deepcopy
import logging
from math import isfinite
from typing import Any

from gms.compat import hard_compatible
from gms.config import WEIGHTS
from gms.occupancy import sets_conflict


logger = logging.getLogger(__name__)


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


def _valid_intervals(value):
    """Return whether value contains only finite, increasing time windows."""
    if not isinstance(value, (list, tuple)) or not value:
        return False
    for interval in value:
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            return False
        start, end = interval
        if (
            isinstance(start, bool) or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not isfinite(start) or not isfinite(end)
            or start >= end
        ):
            return False
    return True


def _actions_are_safe(actions, observation):
    """Replay candidate actions against the observation without mutating it."""
    if not isinstance(actions, dict) or not isinstance(observation, dict):
        return False
    try:
        for key in ("assignments", "reassignments"):
            sequence = actions.get(key, [])
            if not isinstance(sequence, (list, tuple)):
                return False
            if any(not isinstance(pair, (list, tuple)) or len(pair) != 2
                   for pair in sequence):
                return False

        gates = observation.get("gates", {})
        outages = observation.get("gate_outages", {})
        work = deepcopy(observation.get("gate_assignments", {}))
        waiting = observation.get("waiting_flights", {})
        assigned = observation.get("assigned_flights", {})
        changed = observation.get("changed_flights", {})
        profiles = {**waiting, **assigned}
        for flight_id, change in changed.items():
            profiles[flight_id] = {
                **profiles.get(flight_id, {}), **change.get("new", {})
            }
        current = {
            flight_id: flight.get("gate_id")
            for flight_id, flight in assigned.items()
        }

        def is_safe(flight_id, gate_id):
            flight = profiles.get(flight_id)
            if not flight or gate_id not in gates:
                return False
            intervals = flight.get("intervals")
            if not _valid_intervals(intervals):
                return False
            compatible, _ = hard_compatible(
                flight.get("category"), flight.get("wingspan", 0),
                flight.get("jetbridge_required", False), gates[gate_id],
            )
            if not compatible or sets_conflict(
                intervals, outages.get(gate_id, [])
            ):
                return False
            for booking in work.get(gate_id, []):
                if booking.get("flight_id") == flight_id:
                    continue
                booked = booking.get("intervals")
                if not _valid_intervals(booked) or sets_conflict(intervals, booked):
                    return False
            return True

        for flight_id, gate_id in actions.get("reassignments", []):
            if flight_id not in current or not is_safe(flight_id, gate_id):
                return False
            old_gate = current[flight_id]
            work[old_gate] = [
                booking for booking in work.get(old_gate, [])
                if booking.get("flight_id") != flight_id
            ]
            work.setdefault(gate_id, []).append({
                "flight_id": flight_id,
                "intervals": deepcopy(profiles[flight_id]["intervals"]),
            })
            current[flight_id] = gate_id

        for flight_id, gate_id in actions.get("assignments", []):
            if (flight_id not in waiting or flight_id in current
                    or not is_safe(flight_id, gate_id)):
                return False
            work.setdefault(gate_id, []).append({
                "flight_id": flight_id,
                "intervals": deepcopy(profiles[flight_id]["intervals"]),
            })
            current[flight_id] = gate_id

        # Match the evaluator's post-decision changed-flight validation and
        # booking updates. An unchanged changed-flight booking may still hold
        # its old intervals until this point.
        for flight_id in changed:
            if flight_id not in current or not is_safe(
                flight_id, current[flight_id]
            ):
                return False
            gate_id = current[flight_id]
            updated = False
            for booking in work.get(gate_id, []):
                if booking.get("flight_id") == flight_id:
                    booking["intervals"] = deepcopy(
                        profiles[flight_id]["intervals"]
                    )
                    updated = True
            if not updated:
                return False
        return True
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return False


def decide(observation: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    """Return the lower-cost safe plan, with isolated planner recovery."""
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

    team_actions = None
    try:
        from person1.plan_optimizer import plan_joint_assignments
        from person4.disruption_repair import assess_changed_flights

        candidate_ids = set(assigned) | set(waiting)
        disruption_status = assess_changed_flights(
            changed, gates, bookings, outages,
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
    except Exception:  # Isolate the optional optimizer from the recovery path.
        logger.exception("Joint optimizer failed; trying best-fit recovery")

    bestfit_actions = None
    try:
        from solution_bestfit import decide as bestfit_decide

        bestfit_actions = bestfit_decide(observation)
    except Exception:  # Preserve the other candidate if one planner crashes.
        logger.exception("Best-fit planner failed; trying joint optimizer recovery")

    team_safe = team_actions is not None and _actions_are_safe(
        team_actions, observation
    )
    bestfit_safe = bestfit_actions is not None and _actions_are_safe(
        bestfit_actions, observation
    )
    if not team_safe and team_actions is not None:
        logger.debug("Rejected unsafe joint-optimizer candidate")
    if not bestfit_safe and bestfit_actions is not None:
        logger.debug("Rejected unsafe best-fit candidate")

    if team_safe and bestfit_safe:
        # Preserve dependent moves: pruning one can invalidate the whole plan.
        if _heuristic_cost(bestfit_actions, observation) < _heuristic_cost(
            team_actions, observation
        ):
            return bestfit_actions
        return team_actions
    if team_safe:
        return team_actions
    if bestfit_safe:
        return bestfit_actions

    if changed:
        raise RuntimeError(
            "No safe recovery plan for changed flights: "
            + ", ".join(sorted(changed))
        )
    logger.warning("No safe candidate; leaving waiting flights unassigned")
    return {"assignments": [], "reassignments": []}
