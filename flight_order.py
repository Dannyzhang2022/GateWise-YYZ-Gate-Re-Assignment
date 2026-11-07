"""Backward-compatible imports for the Person 2 flight-order component."""

from person2.flight_order import (
    CARGO,
    DOMESTIC,
    INTERNATIONAL,
    compatible,
    count_compatible_gates,
    order_waiting_flights,
)

__all__ = [
    "CARGO",
    "DOMESTIC",
    "INTERNATIONAL",
    "compatible",
    "count_compatible_gates",
    "order_waiting_flights",
]
