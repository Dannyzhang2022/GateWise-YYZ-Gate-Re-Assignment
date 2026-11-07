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


def order_waiting_flights(
    waiting_flights: Mapping[str, Mapping[str, Any]],
    compatible_counts: Mapping[str, int],
) -> list[str]:
    """Return IDs ordered by emergency status, gate scarcity, then start time.

    ``waiting_flights`` maps IDs to flight data; a truthy ``priority`` field
    marks an emergency. ``compatible_counts`` maps each ID to the number of
    currently usable gates. The earliest interval start breaks count ties.
    Remaining ties retain the input mapping's order.
    """
    def sort_key(flight_id: str):
        flight = waiting_flights[flight_id]
        intervals = flight.get("intervals", [])
        earliest_start = min((interval[0] for interval in intervals), default=float("inf"))
        return (
            not bool(flight.get("priority", False)),
            compatible_counts[flight_id],
            earliest_start,
        )

    return sorted(waiting_flights, key=sort_key)