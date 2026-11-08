"""Small dependency-free joint optimizer for the current flight schedule.

It starts with a feasible scarcity-first plan, then improves it with single
flight moves and two-flight exchanges. Hard constraints are checked before
every move; the objective uses the evaluator's soft-score weights.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from gms.config import WEIGHTS, DOMESTIC, INTERNATIONAL
from gms.compat import hard_compatible
from gms.occupancy import sets_conflict, intervals_overlap
from person2.flight_order import order_waiting_flights
from person3.gate_choice import rank_gate_choices


def _overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def _flight_cost(flight_id, gate_id, choice, flight, current_gate, changed, now, outages=None):
    costs = choice["costs"]
    total = costs["walking_distance"] + costs["domestic_at_international_gate"]

    # Risk proxy: gates that previously had an outage may be outaged again,
    # forcing a costly reassignment_after_arrival move later. Discourage
    # assigning future flights to such gates when alternatives exist.
    starts = [iv[0] for iv in flight.get("intervals", []) if len(iv) == 2]
    if starts and min(starts) > now and outages:
        had_outage = any(start < now for start, _ in outages.get(gate_id, []))
        if had_outage:
            total += WEIGHTS["reassignment_after_arrival"]

    if current_gate is not None and gate_id != current_gate:
        arrived = now is not None and starts and min(starts) <= now
        if arrived:
            if flight_id in changed:
                total += WEIGHTS["reassignment_after_arrival"]
            else:
                # Keep arrived incumbents stable in this candidate. The
                # coordinator's repair fallback can still relocate one when
                # that is necessary to accommodate a disrupted flight.
                return float("inf")
        elif flight_id not in changed:
            total += WEIGHTS["reassignment"]
    return total


def _conflicts(flight_id, gate_id, assignment, flights, ignored_ids=()):
    flight = flights[flight_id]
    for other_id, other_gate in assignment.items():
        if other_id == flight_id or other_id in ignored_ids or other_gate != gate_id:
            continue
        if sets_conflict(flight.get("intervals", []), flights[other_id].get("intervals", [])):
            return True
    return False


def _solution_cost(assignment, options, unassigned_costs):
    total = 0.0
    for flight_id, gate_id in assignment.items():
        if gate_id is None:
            total += unassigned_costs[flight_id]
        else:
            total += options[flight_id].get(gate_id, {}).get("total_cost", unassigned_costs[flight_id])
    return total


def _sequence_reassignments(assignment, flights, current_gates, bookings):
    """Order moves so each target is clear when the evaluator applies it."""
    moves = {
        flight_id: gate_id
        for flight_id, gate_id in assignment.items()
        if current_gates[flight_id] is not None
        and gate_id is not None
        and gate_id != current_gates[flight_id]
    }
    occupancy = {
        gate_id: [
            {"flight_id": item.get("flight_id"), "intervals": item.get("intervals", [])}
            for item in items
        ]
        for gate_id, items in bookings.items()
    }
    ordered = []
    remaining = dict(moves)
    while remaining:
        ready = None
        for flight_id, target_gate in remaining.items():
            intervals = flights[flight_id].get("intervals", [])
            blocked = any(
                item["flight_id"] != flight_id
                and sets_conflict(intervals, item["intervals"])
                for item in occupancy.get(target_gate, [])
            )
            if not blocked:
                ready = flight_id
                break
        if ready is None:
            return None
        old_gate, new_gate = current_gates[ready], remaining.pop(ready)
        occupancy[old_gate] = [
            item for item in occupancy.get(old_gate, [])
            if item["flight_id"] != ready
        ]
        occupancy.setdefault(new_gate, []).append({
            "flight_id": ready,
            "intervals": flights[ready].get("intervals", []),
        })
        ordered.append((ready, new_gate))
    return ordered


def plan_joint_assignments(
    *, gates, outages, bookings, assigned_flights, waiting_flights,
    changed_flights, disruption_status, current_time,
):
    """Plan existing-flight repairs and new assignments in one search."""
    flights: dict[str, dict[str, Any]] = {}
    current_gates: dict[str, str | None] = {}
    for flight_id, view in assigned_flights.items():
        flight = deepcopy(view)
        flight["current_time"] = current_time
        flights[flight_id] = flight
        current_gates[flight_id] = view.get("gate_id")
    for flight_id, view in waiting_flights.items():
        flight = deepcopy(view)
        flight["current_time"] = current_time
        flights[flight_id] = flight
        current_gates[flight_id] = None

    if not flights:
        return {"assignments": [], "reassignments": []}

    # These flights are decision variables, so their present bookings must not
    # block their own alternatives. Their mutual conflicts are enforced by the
    # joint assignment search below.
    fixed_bookings = {
        gate_id: [
            deepcopy(booking)
            for booking in gate_bookings
            if booking.get("flight_id") not in flights
        ]
        for gate_id, gate_bookings in bookings.items()
    }
    for gate_id in gates:
        fixed_bookings.setdefault(gate_id, [])

    options: dict[str, dict[str, dict[str, Any]]] = {}
    unassigned_costs: dict[str, float] = {}
    for flight_id, flight in flights.items():
        current_gate = current_gates[flight_id]
        changed_status = disruption_status.get(flight_id, {})
        usable: dict[str, dict[str, Any]] = {}
        # Rank against only fixed occupancy. Variable flights are checked
        # together below; retain this flight's booking so the chooser can read
        # its current gate while ignoring that same flight as a blocker.
        solo_bookings = {
            gid: list(items) for gid, items in fixed_bookings.items()
        }
        if current_gate is not None:
            for gid, items in bookings.items():
                own = [b for b in items if b.get("flight_id") == flight_id]
                if own:
                    solo_bookings.setdefault(gid, []).extend(own)
        for safe_choice in rank_gate_choices(
            flight_id, flight, gates, solo_bookings, outages
        ):
            if not safe_choice["safe"]:
                continue
            gate_id = safe_choice["gate_id"]
            if (
                gate_id == current_gate
                and flight_id in changed_flights
                and not changed_status.get("current_gate_safe", True)
            ):
                continue
            safe_choice = deepcopy(safe_choice)
            safe_choice["total_cost"] = _flight_cost(
                flight_id,
                gate_id,
                safe_choice,
                flight,
                current_gate,
                changed_flights,
                current_time,
                outages,
            )
            usable[gate_id] = safe_choice
        options[flight_id] = usable
        # Prefer domestic gates in this stability-first candidate. The
        # coordinator's best-fit fallback retains international alternatives
        # when joint occupancy makes them necessary for a feasible plan.
        if flight.get("category") == DOMESTIC:
            d_options = {
                gid: c
                for gid, c in usable.items()
                if gates.get(gid, {}).get("gate_type") == DOMESTIC
            }
            if d_options:
                options[flight_id] = d_options

        # Keep the current gate when it is compatible and outage-free. This
        # candidate deliberately favors stability; the coordinator compares it
        # with best-fit, which handles repairs that require moving incumbents.
        if current_gate is not None and current_gate in gates:
            gate = gates[current_gate]
            compatible, _ = hard_compatible(
                flight.get("category"), flight.get("wingspan", 0),
                flight.get("jetbridge_required", False), gate,
            )
            if compatible:
                intervals = flight.get("intervals", [])
                outaged = any(
                    _overlap(iv, w) for iv in intervals
                    for w in outages.get(current_gate, [])
                )
                if not outaged:
                    if current_gate not in usable:
                        dist = max(0, gate.get("dist", 0))
                        premium = 5.0 if (
                            flight.get("category") == DOMESTIC
                            and gate.get("gate_type") == INTERNATIONAL
                        ) else 0.0
                        usable[current_gate] = {
                            "gate_id": current_gate,
                            "safe": True,
                            "reasons": [],
                            "costs": {
                                "walking_distance": dist * 0.01,
                                "domestic_at_international_gate": premium,
                                "gate_move": 0.0,
                            },
                            "score": dist * 0.01 + premium,
                            "delay_minutes": 0,
                        }
                        usable[current_gate]["total_cost"] = usable[current_gate]["score"]
                    options[flight_id] = {current_gate: usable[current_gate]}

        if current_gate is None:
            if flight.get("priority"):
                unassigned_costs[flight_id] = WEIGHTS["unassigned_emergency"]
            else:
                unassigned_costs[flight_id] = WEIGHTS["unassigned_flight"]
        else:
            # Existing flights cannot be unassigned by the evaluator. The
            # large sentinel lets search move other flights to free a gate.
            unassigned_costs[flight_id] = 1_000_000_000.0

    # Part 2's emergency/scarcity ordering supplies a robust feasible starting
    # point. Costs among each flight's gate options are already ranked by Part 3.
    order = order_waiting_flights(
        flights,
        {flight_id: len(options[flight_id]) for flight_id in flights},
    )
    assignment: dict[str, str | None] = {}
    for flight_id in order:
        ranked = sorted(
            options[flight_id].values(),
            key=lambda item: (item["total_cost"], str(item["gate_id"])),
        )
        selected = next(
            (
                item["gate_id"] for item in ranked
                if not _conflicts(flight_id, item["gate_id"], assignment, flights)
            ),
            None,
        )
        assignment[flight_id] = selected

    # Best-improvement local search. Single-flight moves fix straightforward
    # cost choices; two-flight exchanges can free a scarce gate for a constrained
    # flight without leaving the schedule in an intermediate unsafe state.
    ids = list(order)
    rounds = min(24, max(4, len(ids)))
    for _ in range(rounds):
        best_delta = 1e-9
        best_change = None
        base_cost = _solution_cost(assignment, options, unassigned_costs)

        for flight_id in ids:
            old_gate = assignment[flight_id]
            candidates = list(options[flight_id])
            candidates.append(None)
            for gate_id in candidates:
                if gate_id == old_gate:
                    continue
                if gate_id is not None and _conflicts(flight_id, gate_id, assignment, flights):
                    continue
                trial_cost = base_cost
                trial_cost -= (
                    unassigned_costs[flight_id] if old_gate is None
                    else options[flight_id].get(old_gate, {}).get("total_cost", unassigned_costs[flight_id])
                )
                trial_cost += (
                    unassigned_costs[flight_id] if gate_id is None
                    else options[flight_id][gate_id]["total_cost"]
                )
                delta = base_cost - trial_cost
                if delta > best_delta:
                    best_delta = delta
                    best_change = (flight_id, gate_id)

        for index, left_id in enumerate(ids):
            for right_id in ids[index + 1:]:
                old_left, old_right = assignment[left_id], assignment[right_id]
                left_choices = list(options[left_id])
                right_choices = list(options[right_id])
                left_choices.append(None)
                right_choices.append(None)
                for left_gate in left_choices:
                    for right_gate in right_choices:
                        if left_gate == old_left and right_gate == old_right:
                            continue
                        assignment[left_id] = old_left
                        assignment[right_id] = old_right
                        ignored_pair = {left_id, right_id}
                        if left_gate is not None and _conflicts(
                            left_id, left_gate, assignment, flights, ignored_pair
                        ):
                            continue
                        if right_gate is not None and _conflicts(
                            right_id, right_gate, assignment, flights, ignored_pair
                        ):
                            continue
                        if (
                            left_gate is not None and right_gate is not None
                            and left_gate == right_gate
                            and sets_conflict(
                                flights[left_id].get("intervals", []),
                                flights[right_id].get("intervals", []),
                            )
                        ):
                            continue
                        old_cost = (
                            (unassigned_costs[left_id] if old_left is None else options[left_id].get(old_left, {}).get("total_cost", unassigned_costs[left_id]))
                            + (unassigned_costs[right_id] if old_right is None else options[right_id].get(old_right, {}).get("total_cost", unassigned_costs[right_id]))
                        )
                        new_cost = (
                            (unassigned_costs[left_id] if left_gate is None else options[left_id][left_gate]["total_cost"])
                            + (unassigned_costs[right_id] if right_gate is None else options[right_id][right_gate]["total_cost"])
                        )
                        delta = old_cost - new_cost
                        if delta > best_delta:
                            best_delta = delta
                            best_change = (left_id, left_gate, right_id, right_gate)

        if best_change is None:
            break
        if len(best_change) == 2:
            flight_id, gate_id = best_change
            assignment[flight_id] = gate_id
        else:
            left_id, left_gate, right_id, right_gate = best_change
            assignment[left_id] = left_gate
            assignment[right_id] = right_gate

    reassignments = _sequence_reassignments(
        assignment, flights, current_gates, bookings
    )
    if reassignments is None:
        # Avoid a cyclic exchange the evaluator cannot apply atomically. Keep
        # safe existing placements where possible, then sequence forced repairs.
        for flight_id in ids:
            current_gate = current_gates[flight_id]
            if current_gate is None or flight_id in changed_flights:
                continue
            if current_gate not in options[flight_id]:
                continue
            if not _conflicts(flight_id, current_gate, assignment, flights):
                assignment[flight_id] = current_gate
        reassignments = _sequence_reassignments(
            assignment, flights, current_gates, bookings
        ) or []

    actions = {"assignments": [], "reassignments": reassignments}
    for flight_id, gate_id in assignment.items():
        current_gate = current_gates[flight_id]
        if gate_id is None:
            continue
        if current_gate is None:
            actions["assignments"].append((flight_id, gate_id))
        # Reassignments were already ordered through currently free gates.
    return actions
