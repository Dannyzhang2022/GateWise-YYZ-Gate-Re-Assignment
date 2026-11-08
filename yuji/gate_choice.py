"""Safe, score-aware gate selection for the airport gate assignment task.

The evaluator's hard rules decide whether a gate is legal and available.
Only after those rules pass do we compare soft costs: walking distance,
domestic use of an international gate, and moving an already assigned flight.

This module intentionally has no required project dependencies so it can be
imported and tested on its own. If the evaluator's ``gms.config`` package is
present, its weights are used; otherwise the evaluator defaults are used.
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Iterable


CARGO = 0
DOMESTIC = 1
INTERNATIONAL = 2

_CATEGORY_NAMES = {
    "cargo": CARGO,
    "domestic": DOMESTIC,
    "international": INTERNATIONAL,
}
_DEFAULT_WEIGHTS = {
    "reassignment": 10,
    "reassignment_after_arrival": 50,
    "domestic_at_intl_gate": 5,
    "walking_distance_per_m": 0.01,
}

try:  # Use the evaluator's source of truth when running inside its package.
    from gms.config import WEIGHTS as _EVALUATOR_WEIGHTS
except (ImportError, ModuleNotFoundError):
    _EVALUATOR_WEIGHTS = _DEFAULT_WEIGHTS


def _category(value: Any) -> int | None:
    if isinstance(value, str):
        return _CATEGORY_NAMES.get(value.strip().lower())
    if isinstance(value, bool):
        return None
    if value in (CARGO, DOMESTIC, INTERNATIONAL):
        return int(value)
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def get_delay_minutes(flight: dict[str, Any]) -> float | None:
    """Return optional nonnegative delay metadata, or None when unavailable."""
    if not isinstance(flight, dict):
        return None
    value = _number(flight.get("delay_minutes"))
    return value if value is not None and value >= 0 else None


def _compatible(flight: dict[str, Any], gate: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check only the evaluator's non-time-based hard rules."""
    reasons: list[str] = []
    category = _category(flight.get("category"))
    gate_type = _category(gate.get("gate_type"))
    wingspan = _number(flight.get("wingspan"))
    max_wingspan = _number(gate.get("max_wingspan"))

    if category is None:
        reasons.append("unknown flight category")
    if gate_type is None:
        reasons.append("unknown gate type")
    if wingspan is None:
        reasons.append("missing or invalid wingspan")
    if max_wingspan is None:
        reasons.append("missing or invalid gate wingspan limit")
    if not isinstance(flight.get("jetbridge_required"), bool):
        reasons.append("missing or invalid jetbridge requirement")

    if category is not None and gate_type is not None:
        if category == CARGO and gate_type != CARGO:
            reasons.append("cargo flights require cargo gates")
        elif category == INTERNATIONAL and gate_type != INTERNATIONAL:
            reasons.append("international flights require international gates")
        elif category == DOMESTIC and gate_type not in (DOMESTIC, INTERNATIONAL):
            reasons.append("domestic passenger flights cannot use cargo gates")

    if wingspan is not None and max_wingspan is not None and wingspan > max_wingspan:
        reasons.append(f"wingspan {wingspan:g} m exceeds {max_wingspan:g} m limit")
    # The evaluator's JSON may decode this flag as bool or 0/1; follow its
    # truth-value rule rather than requiring the singleton object True.
    if flight.get("jetbridge_required") and not bool(gate.get("jetbridge")):
        reasons.append("flight requires a jetbridge")
    return not reasons, reasons


def _intervals(value: Any) -> list[tuple[float, float]] | None:
    """Normalize intervals; None means the input cannot be checked safely."""
    if not isinstance(value, (list, tuple)):
        return None
    result: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        start, end = _number(item[0]), _number(item[1])
        if start is None or end is None or start >= end:
            return None
        result.append((start, end))
    return result


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    # Half-open intervals: 10:00-11:00 and 11:00-12:00 do not overlap.
    return a[0] < b[1] and b[0] < a[1]


def _conflict(left: Iterable[tuple[float, float]], right: Iterable[tuple[float, float]]) -> bool:
    return any(_overlap(a, b) for a in left for b in right)


def _current_gate(flight_id: str, gate_assignments: dict[str, Any]) -> str | None:
    for gate_id, bookings in gate_assignments.items():
        if not isinstance(bookings, (list, tuple)):
            continue
        if any(isinstance(booking, dict) and booking.get("flight_id") == flight_id for booking in bookings):
            return gate_id
    return None


def rank_gate_choices(
    flight_id: str,
    flight: dict[str, Any],
    gates: dict[str, dict[str, Any]],
    gate_assignments: dict[str, Any],
    gate_outages: dict[str, Any],
    exclude_flight_id: str | None = None,
    skip_gate_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return a deterministic, explainable ranking of every gate.

    Each result has ``gate_id``, ``safe``, ``reasons``, ``costs`` and ``score``.
    Unsafe gates remain visible in the diagnostics but can never be selected.
    """
    if not isinstance(gates, dict):
        return []
    assignments = gate_assignments if isinstance(gate_assignments, dict) else {}
    outages = gate_outages if isinstance(gate_outages, dict) else {}
    requested = _intervals(flight.get("intervals"))
    # An absent or malformed occupancy window cannot be proved safe.
    interval_error = requested is None
    requested = requested or []
    ignored_flights = {flight_id}
    if exclude_flight_id is not None:
        ignored_flights.add(exclude_flight_id)
    current_gate = _current_gate(flight_id, assignments)
    category = _category(flight.get("category"))
    # Delay is useful context, but it is not a gate-specific cost in the
    # evaluator. Callers must pass occupancy intervals already updated for it.
    delay_minutes = get_delay_minutes(flight)
    results: list[dict[str, Any]] = []

    for gate_id, gate in gates.items():
        if not isinstance(gate, dict):
            gate = {}
        reasons: list[str] = []
        compatible, compatibility_reasons = _compatible(flight, gate)
        reasons.extend(compatibility_reasons)
        if interval_error:
            reasons.append("missing or invalid occupancy intervals")
        if gate_id == skip_gate_id:
            reasons.append("gate explicitly skipped")

        gate_bookings = assignments.get(gate_id, [])
        if gate_id in assignments and not isinstance(gate_bookings, (list, tuple)):
            reasons.append("gate occupancy data is invalid")
            gate_bookings = []
        for booking in gate_bookings:
            if not isinstance(booking, dict):
                reasons.append("gate occupancy record is invalid")
                break
            if booking.get("flight_id") in ignored_flights:
                continue
            booked_intervals = _intervals(booking.get("intervals"))
            # Malformed bookings are treated as blocking: choosing this gate
            # cannot be proven safe when the existing occupancy is unknown.
            if booked_intervals is None:
                reasons.append(f"occupancy for {booking.get('flight_id', 'another flight')} is unknown")
                break
            if _conflict(requested, booked_intervals):
                reasons.append(f"overlaps flight {booking.get('flight_id', 'unknown')}")
                break

        gate_outages = outages.get(gate_id, [])
        if gate_id in outages and not isinstance(gate_outages, (list, tuple)):
            reasons.append("gate outage data is invalid")
            gate_outages = []
        for outage in gate_outages:
            outage_intervals = _intervals([outage])
            if outage_intervals is None:
                reasons.append("gate outage window is invalid")
                break
            if _conflict(requested, outage_intervals):
                reasons.append("overlaps a gate outage")
                break

        safe = compatible and not reasons
        distance = _number(gate.get("dist"))
        distance_cost = max(0.0, distance or 0.0) * float(_EVALUATOR_WEIGHTS.get("walking_distance_per_m", 0.01))
        premium_cost = 0.0
        if category == DOMESTIC and _category(gate.get("gate_type")) == INTERNATIONAL:
            premium_cost = float(_EVALUATOR_WEIGHTS.get("domestic_at_intl_gate", 5))

        move_cost = 0.0
        if current_gate is not None and gate_id != current_gate:
            move_weight = "reassignment"
            current_time = _number(flight.get("current_time", flight.get("time")))
            starts = [start for start, _ in requested]
            if current_time is not None and starts and min(starts) <= current_time:
                move_weight = "reassignment_after_arrival"
            move_cost = float(_EVALUATOR_WEIGHTS.get(move_weight, _DEFAULT_WEIGHTS[move_weight]))

        costs = {
            "walking_distance": round(distance_cost, 6),
            "domestic_at_international_gate": round(premium_cost, 6),
            "gate_move": round(move_cost, 6),
        }
        results.append({
            "gate_id": gate_id,
            "safe": safe,
            "reasons": reasons,
            "costs": costs,
            "score": round(sum(costs.values()), 6),
            "delay_minutes": delay_minutes,
        })

    # Keep unsafe options visible, but after all safe options. The chosen
    # gate is therefore always the lowest-cost safe option.
    return sorted(results, key=lambda item: (not item["safe"], item["score"], str(item["gate_id"])))


def count_compatible_gates(flight, gates) -> int:
    """Count gates satisfying type, wingspan and jetbridge rules only.

    Time conflicts and outages are intentionally ignored. Invalid input
    returns zero instead of raising so flight ordering can fail gracefully.
    """
    if not isinstance(flight, dict) or not isinstance(gates, dict):
        return 0
    return sum(1 for gate in gates.values() if isinstance(gate, dict) and _compatible(flight, gate)[0])


def count_available_gates(
    flight: dict[str, Any],
    gates: dict[str, Any],
    gate_assignments: dict[str, Any] | None = None,
    gate_outages: dict[str, Any] | None = None,
    flight_id: str | None = None,
) -> int:
    """Count gates that pass both aircraft rules and current time checks."""
    if not isinstance(flight, dict) or not isinstance(gates, dict):
        return 0
    candidate_id = flight_id or flight.get("flight_id") or "__gate_choice_probe__"
    return sum(
        1
        for choice in rank_gate_choices(
            candidate_id, flight, gates, gate_assignments or {}, gate_outages or {}
        )
        if choice["safe"]
    )


def waiting_flight_order_key(
    flight: dict[str, Any],
    gates: dict[str, Any],
    gate_assignments: dict[str, Any] | None = None,
    gate_outages: dict[str, Any] | None = None,
    flight_id: str | None = None,
) -> tuple:
    """Order waiting flights by emergency, live scarcity, delay, then start.

    Flights with at least one safe gate go before those with none. Among
    assignable flights, fewer currently safe gates wins. More delayed flights
    break ties only after emergency priority and live scarcity.
    """
    if not isinstance(flight, dict):
        return (True, True, 0, 0.0, 0.0)
    intervals = _intervals(flight.get("intervals")) or []
    earliest_start = min((start for start, _ in intervals), default=0.0)
    delay = get_delay_minutes(flight) or 0.0
    available = count_available_gates(
        flight, gates, gate_assignments, gate_outages, flight_id=flight_id
    )
    scarcity = available if available else count_compatible_gates(flight, gates)
    return (
        not bool(flight.get("priority", False)),
        available == 0,
        scarcity,
        -delay,
        earliest_start,
    )


def choose_gate(
    flight_id, flight, gates, gate_assignments, gate_outages,
    exclude_flight_id=None, skip_gate_id=None
) -> str | None:
    """Return the lowest-cost safe gate ID, or None when no gate is safe."""
    if not isinstance(flight, dict):
        return None
    for choice in rank_gate_choices(
        flight_id, flight, gates, gate_assignments, gate_outages,
        exclude_flight_id=exclude_flight_id, skip_gate_id=skip_gate_id,
    ):
        if choice["safe"]:
            return choice["gate_id"]
    return None
