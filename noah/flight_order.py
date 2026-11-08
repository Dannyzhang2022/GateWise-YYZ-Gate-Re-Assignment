"""Standalone ordering utilities for flights awaiting gate assignment."""

from collections.abc import Mapping
from typing import Any

CARGO, DOMESTIC, INTERNATIONAL = 0, 1, 2


def compatible(flight: Mapping[str, Any], gate: Mapping[str, Any]) -> bool:
    """Return whether a gate satisfies a flight's hard requirements."""
    if flight["wingspan"] > gate["max_wingspan"]:
        return False
    if flight["jetbridge_required"] and not gate["jetbridge"]:
        return False

    category, gate_type = flight["category"], gate["gate_type"]
    if category == CARGO:
        return gate_type == CARGO
    if category == INTERNATIONAL:
        return gate_type == INTERNATIONAL
    return gate_type in (DOMESTIC, INTERNATIONAL)


def count_compatible_gates(flight: Mapping[str, Any], gates: Mapping[str, Mapping[str, Any]]) -> int:
    """Count gates satisfying the flight's hard requirements."""
    return sum(compatible(flight, gate) for gate in gates.values())


def _overlap(left: Any, right: Any) -> bool:
    """Return whether two half-open [start, end) intervals overlap."""
    return left[0] < right[1] and right[0] < left[1]


def count_usable_gates(
    flight: Mapping[str, Any],
    gates: Mapping[str, Mapping[str, Any]],
    gate_assignments: Mapping[str, list[Mapping[str, Any]]] | None = None,
    gate_outages: Mapping[str, list[tuple[float, float]]] | None = None,
    flight_id: str | None = None,
) -> int:
    """Count compatible gates free for all of this flight's intervals.

    A gate is unavailable if any flight booking or outage overlaps any of the
    candidate's intervals. ``flight_id`` lets callers ignore that flight's
    existing booking while recalculating a changed flight.
    """
    intervals = flight.get("intervals", [])
    assignments = gate_assignments or {}
    outages = gate_outages or {}
    usable = 0

    for gate_id, gate in gates.items():
        if not compatible(flight, gate):
            continue

        blocked = any(
            booking.get("flight_id") != flight_id
            and any(_overlap(candidate, occupied)
                    for candidate in intervals
                    for occupied in booking.get("intervals", []))
            for booking in assignments.get(gate_id, [])
        )
        if not blocked:
            blocked = any(
                _overlap(candidate, outage)
                for candidate in intervals
                for outage in outages.get(gate_id, [])
            )
        if not blocked:
            usable += 1

    return usable


def order_waiting_flights(
    waiting_flights: Mapping[str, Mapping[str, Any]],
    compatible_counts: Mapping[str, int],
) -> list[str]:
    """Return IDs ordered by emergency, live gate scarcity, delay, then start.

    ``waiting_flights`` maps IDs to flight data; a truthy ``priority`` field
    marks an emergency. ``compatible_counts`` maps each ID to the current
    usable-gate count (usually from :func:`count_usable_gates`). More delayed
    flights break scarcity ties when ``delay_minutes`` is supplied. The
    earliest interval start is the next tie-breaker; remaining ties retain the
    input mapping's order.
    """
    def sort_key(flight_id: str):
        flight = waiting_flights[flight_id]
        intervals = flight.get("intervals", [])
        earliest_start = min((interval[0] for interval in intervals), default=float("inf"))
        try:
            delay = float(flight.get("delay_minutes", 0) or 0)
        except (TypeError, ValueError):
            delay = 0.0
        return (
            not bool(flight.get("priority", False)),
            compatible_counts[flight_id],
            -delay,
            earliest_start,
        )

    return sorted(waiting_flights, key=sort_key)
