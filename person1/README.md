# Person 1 work area: decision flow

## Your deliverable

Implement `solution_team.py` as the coordinator for the gate-assignment team solution. The evaluator loads it as `person1.solution_team` and calls `decide(observation)` at each event time.

## Function contract

`decide(observation) -> dict`

Return exactly the expected action shape:

```python
{
    "assignments": [(flight_id, gate_id)],
    "reassignments": [(flight_id, gate_id)],
}
```

Assignments are first-time gate placements. Reassignments move a flight that already has a gate. If no safe gate is available for a waiting flight, leave it out of `assignments`.

## Integration order

1. Start with local copies of `observation["gate_assignments"]` and `observation["gate_outages"]`.
2. Call `disruption_repair.repair_changed_flights(...)` first. Collect its reassignments and use the returned booking plan.
3. Ask `gate_choice.count_compatible_gates(flight, gates)` for each waiting flight.
4. Call `flight_order.order_waiting_flights(waiting_flights, compatible_counts)`.
5. In that order, call `gate_choice.choose_gate(...)` using the current local bookings. If it returns a gate, append an assignment and add that booking to the local plan before processing the next flight. If it returns `None`, skip the flight.
6. Return assignments and reassignments as lists of `(flight_id, gate_id)` pairs.

## Observation fields you will use

- `time`
- `gates`
- `gate_outages`
- `waiting_flights`
- `assigned_flights`
- `changed_flights`
- `gate_assignments`
- `ac_info`

Each flight view provides its `intervals`, `category`, `wingspan`, `jetbridge_required`, and `priority` fields. Category codes are `0` cargo, `1` domestic, and `2` international.

## Things to do

- [ ] Confirm the helper function names and exact argument/return formats with teammates before they implement them.
- [ ] Coordinate repairs before new placements, so the local plan accounts for the disruption.
- [ ] Update local bookings after every chosen repair or assignment to avoid same-tick collisions.
- [ ] Preserve the evaluator's hard constraints; never return a known unsafe gate.
- [ ] Handle missing optional fields with safe defaults where appropriate.
- [ ] Run the evaluator using `--solution person1.solution_team` once helper modules are in place.
- [ ] Work with the team to compare results with `solution_scored.py` and prepare a concise explanation of the decisions.

## Files

- `solution_team.py` — your coordinator implementation.
- Helper modules are expected in the parent `gate-sorting-algorithm` directory and will be created by the other team members.
