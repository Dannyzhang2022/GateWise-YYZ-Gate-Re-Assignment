"""Backward-compatible imports for the Noah flight-order component."""

from noah.flight_order import (
    CARGO,
    DOMESTIC,
    INTERNATIONAL,
    compatible,
    count_compatible_gates,
    count_usable_gates,
    order_waiting_flights,
)

__all__ = [
    "CARGO",
    "DOMESTIC",
    "INTERNATIONAL",
    "compatible",
    "count_compatible_gates",
    "count_usable_gates",
    "order_waiting_flights",
]
