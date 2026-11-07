"""Person 1: coordinate flight ordering, disruption repair, and gate choice."""

from copy import deepcopy
from typing import Any


def decide(observation: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    """Plan safe gate assignments for one evaluator tick.

    Team module contracts used here:
      flight_order:
        compatible(flight, gate) -> bool
        count_compatible_gates(flight, gates) -> int
        order_waiting_flights(waiting_flights, compatible_counts) -> list[str]
        CARGO, DOMESTIC, INTERNATIONAL
      gate_choice:
        choose_gate(flight_id, flight, gates, gate_assignments, gate_outages,
                    exclude_flight_id=None, skip_gate_id=None) -> str | None
      disruption_repair:
        repair_changed_flights(changed_flights, gates, gate_assignments,
                               gate_outages) -> (reassignments, updated_bookings)

    The helper modules are kept separate so this function only coordinates
    their decisions and maintains a same-tick local booking plan.
    """
    # Imports are inside decide so this package can still be imported while
    # teammates are building their modules. The evaluator can call decide once
    # the agreed helper modules are present.
    from flight_order import (
        CARGO,
        DOMESTIC,
        INTERNATIONAL,
        compatible,
        count_compatible_gates,
        order_waiting_flights,
    )
    from gate_choice import choose_gate
    from disruption_repair import repair_changed_flights

    assignments: list[tuple[str, str]] = []
    reassignments: list[tuple[str, str]] = []

    gates = observation.get("gates", {})
    waiting_flights = observation.get("waiting_flights", {})
    changed_flights = observation.get("changed_flights", {})
    gate_outages = deepcopy(observation.get("gate_outages", {}))
    local_bookings = deepcopy(observation.get("gate_assignments", {}))

    # Keep an empty booking list for every known gate so the placement helper
    # can safely add bookings to gates that were empty in the observation.
    for gate_id in gates:
        local_bookings.setdefault(gate_id, [])

    # 1. Repair assigned flights affected by updates or outages before placing
    #    new flights. The helper returns a local booking plan that includes its
    #    repairs, so subsequent gate choices see those moves.
    repair_result = repair_changed_flights(
        changed_flights,
        gates,
        local_bookings,
        gate_outages,
    )
    if repair_result is not None:
        repair_actions, repaired_bookings = repair_result
        if repaired_bookings is not None:
            local_bookings = repaired_bookings
        if repair_actions:
            reassignments.extend(
                (flight_id, gate_id) for flight_id, gate_id in repair_actions
            )

    # Ensure the repaired plan still has entries for every gate.
    for gate_id in gates:
        local_bookings.setdefault(gate_id, [])

    # 2. Order waiting flights by emergency priority and constraint level.
    compatible_counts: dict[str, int] = {}
    valid_categories = {CARGO, DOMESTIC, INTERNATIONAL}
    for flight_id, flight in waiting_flights.items():
        # Unknown category codes cannot be safely placed. Give them zero legal
        # choices; the selector will never be called for them if ordering skips
        # zero-option flights.
        if flight.get("category") not in valid_categories:
            compatible_counts[flight_id] = 0
        else:
            compatible_counts[flight_id] = count_compatible_gates(flight, gates)

    ordered_ids = order_waiting_flights(waiting_flights, compatible_counts)

    # 3. Select and reserve one gate at a time. Updating local bookings after
    #    each choice prevents later flights in this tick from taking the same
    #    gate over an overlapping interval.
    for flight_id in ordered_ids:
        flight = waiting_flights.get(flight_id)
        if flight is None or compatible_counts.get(flight_id, 0) <= 0:
            continue

        gate_id = choose_gate(
            flight_id,
            flight,
            gates,
            local_bookings,
            gate_outages,
        )
        if gate_id is None or gate_id not in gates:
            continue

        # Defense-in-depth using the shared compatibility rule; do not repeat
        # or reimplement the gate-type, wingspan, or jetbridge rules here.
        if not compatible(flight, gates[gate_id]):
            continue

        assignments.append((flight_id, gate_id))
        local_bookings.setdefault(gate_id, []).append(
            {
                "flight_id": flight_id,
                "intervals": deepcopy(flight.get("intervals", [])),
            }
        )

    return {
        "assignments": assignments,
        "reassignments": reassignments,
    }
