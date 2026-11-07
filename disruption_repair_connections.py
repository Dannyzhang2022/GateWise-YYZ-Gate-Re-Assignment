"""Disruption repair with passenger-connection urgency as a secondary factor.

This is a copy of the base repair helper with connection-aware ordering. The
existing hard constraints and replacement-gate ranking remain in place;
connection urgency only decides which changed flight is considered first when
multiple repairs compete for the same safe gate.
"""

from math import inf

from gms.compat import hard_compatible, is_premium_misuse
from gms.occupancy import intervals_overlap


def _gate_is_blocked(gate_id, intervals, bookings, outages, flight_id):
    """Check occupancy and outage windows, ignoring this flight's own booking."""
    for booking in bookings.get(gate_id, []):
        if booking.get("flight_id") == flight_id:
            continue
        if any(intervals_overlap(a, b, buffer=0)
               for a in intervals for b in booking.get("intervals", [])):
            return True

    for window in outages.get(gate_id, []):
        if any(intervals_overlap(interval, window, buffer=0)
               for interval in intervals):
            return True
    return False


def _replacement_cost(flight, gate):
    """Rank safe gates by the existing end-of-run penalties and operational fit."""
    cost = max(0, gate.get("dist", 0)) * 0.01
    if is_premium_misuse(flight["category"], gate["gate_type"]):
        cost += 5
    if not flight["jetbridge_required"] and gate.get("jetbridge"):
        cost += 0.001
    cost += max(0, gate["max_wingspan"] - flight["wingspan"]) * 0.000001
    return cost


def _connection_urgency(change):
    """Higher values mean more connecting passengers have less time to connect.

    ``connection_time`` is the average connection time in minutes, and
    ``connection_quantity`` is the number of connecting passengers aboard.
    Values may be provided on the changed-flight record or its ``new`` profile.
    Missing or invalid data gives the flight neutral urgency (0).
    """
    flight = change.get("new", {})
    quantity = change.get("connection_quantity", flight.get("connection_quantity", 0))
    time = change.get("connection_time", flight.get("connection_time", inf))
    try:
        quantity = max(0.0, float(quantity))
        time = max(0.0, float(time))
    except (TypeError, ValueError):
        return 0.0

    if quantity == 0 or time == inf:
        return 0.0
    # Passengers per remaining connection minute; clamp zero to one to avoid
    # division by zero while still making an immediate connection highly urgent.
    return quantity / max(time, 1.0)


def repair_changed_flights(
    changed_flights, gates, gate_assignments, gate_outages
) -> tuple[list[tuple[str, str]], dict]:
    """Return safe repair pairs and a local copy of the resulting gate bookings.

    A flight stays at its current gate while it remains compatible and free of
    conflicting bookings and outages. Otherwise, it moves to the safe gate
    with the lowest existing penalty cost. If no safe replacement is available,
    no reassignment is returned for that flight.

    When repairs compete for a gate, flights are processed by descending
    connection urgency (connecting passengers divided by average connection
    time). This is secondary to the existing safe-gate constraints and gate
    cost ranking. The input booking map is not mutated.
    """
    bookings = {gate_id: list(items) for gate_id, items in gate_assignments.items()}
    repairs = []

    # Stable sort preserves the input order for equal urgency.
    ordered_changes = sorted(
        changed_flights.items(),
        key=lambda item: -_connection_urgency(item[1]),
    )

    for flight_id, change in ordered_changes:
        current_gate_id = change.get("gate_id")
        flight = change.get("new", {})
        intervals = flight.get("intervals", [])
        current_gate = gates.get(current_gate_id)

        if current_gate is not None:
            compatible, _ = hard_compatible(
                flight.get("category"), flight.get("wingspan", 0),
                flight.get("jetbridge_required", False), current_gate,
            )
            if compatible and not _gate_is_blocked(
                    current_gate_id, intervals, bookings, gate_outages, flight_id):
                for booking in bookings.get(current_gate_id, []):
                    if booking.get("flight_id") == flight_id:
                        booking["intervals"] = intervals
                continue

        candidates = []
        for gate_id, gate in gates.items():
            if gate_id == current_gate_id:
                continue
            compatible, _ = hard_compatible(
                flight.get("category"), flight.get("wingspan", 0),
                flight.get("jetbridge_required", False), gate,
            )
            if compatible and not _gate_is_blocked(
                    gate_id, intervals, bookings, gate_outages, flight_id):
                candidates.append((_replacement_cost(flight, gate), gate_id))

        if not candidates:
            continue

        _, replacement_gate_id = min(candidates)
        repairs.append((flight_id, replacement_gate_id))
        if current_gate_id is not None:
            bookings[current_gate_id] = [
                booking for booking in bookings.get(current_gate_id, [])
                if booking.get("flight_id") != flight_id
            ]
        bookings.setdefault(replacement_gate_id, []).append({
            "flight_id": flight_id,
            "intervals": intervals,
        })

    return repairs, bookings
