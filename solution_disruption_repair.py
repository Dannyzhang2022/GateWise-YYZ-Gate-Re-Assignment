"""Evaluator adapter that uses the standalone disruption repair helper."""

import solution_firstfit
from disruption_repair import repair_changed_flights


def decide(observation):
    """Repair changed assignments, then assign waiting flights first-fit."""
    reassignments, local_bookings = repair_changed_flights(
        observation.get("changed_flights", {}),
        observation["gates"],
        observation["gate_assignments"],
        observation.get("gate_outages", {}),
    )
    remaining = dict(observation)
    remaining["gate_assignments"] = local_bookings
    remaining["changed_flights"] = {}
    actions = solution_firstfit.decide(remaining)
    actions["reassignments"] = reassignments + actions.get("reassignments", [])
    return actions
