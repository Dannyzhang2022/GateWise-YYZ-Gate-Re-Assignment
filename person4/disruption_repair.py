"""Repair gate assignments invalidated by flight or airport updates.

``repair_changed_flights`` is a pure planning helper: it leaves its inputs
untouched and returns both safe reassignments and a local copy of the resulting
gate occupancy for the coordinator's subsequent assignment decisions.
"""

from gms.compat import hard_compatible, is_premium_misuse
from gms.occupancy import intervals_overlap


def _overlaps_any(intervals, other_intervals):
    return any(
        intervals_overlap(a, b, buffer=0)
        for a in intervals
        for b in other_intervals
    )


def _blocked_by_outage(gate_id, intervals, outages):
    return any(
        _overlaps_any(intervals, [window])
        for window in outages.get(gate_id, [])
    )


def _blocked_by_bookings(gate_id, intervals, bookings, ignored_flights=()):
    ignored_flights = set(ignored_flights)
    return any(
        booking.get("flight_id") not in ignored_flights
        and _overlaps_any(intervals, booking.get("intervals", []))
        for booking in bookings.get(gate_id, [])
    )


def _replacement_cost(flight, gate):
    """Rank safe gates by the applicable end-of-run penalties, then fit."""
    cost = max(0, gate.get("dist", 0)) * 0.01
    if is_premium_misuse(flight["category"], gate["gate_type"]):
        cost += 5
    if not flight["jetbridge_required"] and gate.get("jetbridge"):
        cost += 0.001
    cost += max(0, gate["max_wingspan"] - flight["wingspan"]) * 0.000001
    return cost


def _copy_bookings(gate_assignments):
    """Copy booking records and interval lists, not just the outer containers."""
    return {
        gate_id: [
            {**booking, "intervals": [tuple(iv) for iv in booking.get("intervals", [])]}
            for booking in items
        ]
        for gate_id, items in gate_assignments.items()
    }


def repair_changed_flights(
    changed_flights, gates, gate_assignments, gate_outages
) -> tuple[list[tuple[str, str]], dict]:
    """Keep changed flights at safe gates and find safe replacements otherwise.

    All changed bookings are first updated to their new intervals so conflict
    checks account for the complete incoming update. Flights that must move are
    removed from their old gates before replacements are selected. If no safe
    replacement exists, that flight is left out of ``repairs`` for the caller
    to handle.

    Returns ``(reassignment_pairs, local_bookings)``. The input booking map and
    its nested records are never modified.
    """
    bookings = _copy_bookings(gate_assignments)
    changed = []
    changed_ids = set(changed_flights)

    # Stage every new interval before checking any current gate. This avoids
    # treating another changed flight's old interval as its final occupancy.
    for flight_id, change in changed_flights.items():
        flight = change.get("new", {})
        intervals = [tuple(iv) for iv in flight.get("intervals", [])]
        current_gate_id = change.get("gate_id")
        own_bookings = [
            booking for booking in bookings.get(current_gate_id, [])
            if booking.get("flight_id") == flight_id
        ]
        for booking in own_bookings:
            booking["intervals"] = intervals
        changed.append((flight_id, change, flight, intervals, current_gate_id, bool(own_bookings)))

    must_move = set()

    # First identify changes invalidated by compatibility, outages, or flights
    # that are not part of this same update.
    for flight_id, _change, flight, intervals, gate_id, _had_booking in changed:
        gate = gates.get(gate_id)
        compatible = False
        if gate is not None:
            compatible, _ = hard_compatible(
                flight.get("category"), flight.get("wingspan", 0),
                flight.get("jetbridge_required", False), gate,
            )
        if (
            not compatible
            or _blocked_by_outage(gate_id, intervals, gate_outages)
            or _blocked_by_bookings(
                gate_id, intervals, bookings, ignored_flights=changed_ids
            )
        ):
            must_move.add(flight_id)

    # Resolve collisions among changed flights deterministically. Keep the
    # first still-valid current booking; subsequent overlapping bookings move.
    retained = []
    for flight_id, _change, _flight, intervals, gate_id, _had_booking in changed:
        if flight_id in must_move:
            continue
        if _blocked_by_bookings(
            gate_id, intervals, bookings, ignored_flights=changed_ids
        ) or any(
            prior_gate == gate_id and _overlaps_any(intervals, prior_intervals)
            for prior_gate, prior_intervals in retained
        ):
            must_move.add(flight_id)
            continue
        retained.append((gate_id, intervals))
        # If the observation omitted its booking record, make the returned
        # local occupancy complete for the coordinator.
        if not any(
            booking.get("flight_id") == flight_id
            for booking in bookings.get(gate_id, [])
        ):
            bookings.setdefault(gate_id, []).append({
                "flight_id": flight_id,
                "intervals": intervals,
            })

    # Release all known invalid assignments first. This lets a later repair use
    # gates vacated by other changed flights without risking conflicts.
    for flight_id, _change, _flight, _intervals, gate_id, _had_booking in changed:
        if flight_id in must_move:
            bookings[gate_id] = [
                booking for booking in bookings.get(gate_id, [])
                if booking.get("flight_id") != flight_id
            ]

    # Give the most constrained flights first choice of the remaining safe
    # gates; stable flight-ID ordering makes ties reproducible.
    to_repair = [item for item in changed if item[0] in must_move]
    to_repair.sort(key=lambda item: (
        sum(
            hard_compatible(
                item[2].get("category"), item[2].get("wingspan", 0),
                item[2].get("jetbridge_required", False), gate,
            )[0]
            for gate in gates.values()
        ),
        item[0],
    ))

    repairs = []
    for flight_id, _change, flight, intervals, old_gate_id, _had_booking in to_repair:
        candidates = []
        for gate_id, gate in gates.items():
            if gate_id == old_gate_id:
                continue
            compatible, _ = hard_compatible(
                flight.get("category"), flight.get("wingspan", 0),
                flight.get("jetbridge_required", False), gate,
            )
            if (
                compatible
                and not _blocked_by_outage(gate_id, intervals, gate_outages)
                and not _blocked_by_bookings(gate_id, intervals, bookings)
            ):
                candidates.append((_replacement_cost(flight, gate), gate_id))

        if not candidates:
            continue

        _, new_gate_id = min(candidates)
        repairs.append((flight_id, new_gate_id))
        bookings.setdefault(new_gate_id, []).append({
            "flight_id": flight_id,
            "intervals": intervals,
        })

    return repairs, bookings
