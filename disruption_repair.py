"""Repair gate assignments invalidated by flight or airport updates.

The coordinator can call :func:`repair_changed_flights` while processing an
evaluator observation. The helper keeps valid assignments in place and emits
reassignment pairs only when the current gate is no longer safe.
"""

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
    """Rank safe gates by end-of-run penalties, then operational fit."""
    cost = max(0, gate.get("dist", 0)) * 0.01
    if is_premium_misuse(flight["category"], gate["gate_type"]):
        cost += 5
    # Tie-breakers conserve scarce equipment and leave room for larger aircraft.
    if not flight["jetbridge_required"] and gate.get("jetbridge"):
        cost += 0.001
    cost += max(0, gate["max_wingspan"] - flight["wingspan"]) * 0.000001
    return cost


def repair_changed_flights(
    changed_flights, gates, gate_assignments, gate_outages
) -> tuple[list[tuple[str, str]], dict]:
    """Return safe repairs and the resulting local gate occupancy map.

    A changed flight stays at its current gate if it remains physically and
    operationally compatible, has no overlapping booking, and has no outage
    during its updated presence intervals. Otherwise, the least-penalty safe
    gate is selected. If none is available, the flight is omitted so the caller
    can leave it for its normal exception/coordinator handling.

    The input gate assignment map is not modified. A local occupancy copy is updated as
    repairs are selected so two changed flights cannot be sent to the same gate
    for overlapping intervals in the same call.
    """
    bookings = {
        gate_id: list(items)
        for gate_id, items in gate_assignments.items()
    }
    repairs = []

    for flight_id, change in changed_flights.items():
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
        # The caller applies these reassignments; update this local plan so later
        # changed flights in this same call cannot conflict with the reservation.
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
