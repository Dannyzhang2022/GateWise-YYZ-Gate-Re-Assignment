# Part 1: coordinated gate planning

The evaluator calls `person1.solution_team.decide(observation)` at each event
time. The evaluator-facing return format remains:

```python
{
    "assignments": [(flight_id, gate_id)],
    "reassignments": [(flight_id, gate_id)],
}
```

Part 1 now collects the current assigned and waiting flights and sends them to
`plan_optimizer.py` for a joint plan. The optimizer starts with a scarcity-first
plan, then tries safe one-flight moves and two-flight exchanges to lower the
current evaluator cost. It uses no external solver dependency.

The other parts retain their main roles:

- Part 2 orders flights using emergency priority and usable-gate scarcity.
- Part 3 supplies safe gate options and score costs.
- Part 4 assesses changed flights and identifies assignments that need repair.

Repairs and first-time assignments are planned together. The optimizer checks
overlaps and outages before accepting a gate, and orders reassignments so each
move's destination is clear when the evaluator applies it. If no safe gate is
available for a waiting flight, it is left unassigned.

The coordinator compares this candidate with the stable best-fit fallback.
Best-fit uses exact wave search for up to four waiting flights and the
scarcity-aware greedy planner for larger waves, avoiding factorial runtimes.
Reassignments from either planner are kept intact: removing a locally optional
move can break a dependent move or leave a changed flight in an invalid gate.

Planner exceptions are isolated so one failure cannot block the other planner.
Before selection, each candidate is replayed against a copied observation to
validate intervals, compatibility, outages, occupancy, move order, and changed
flight postconditions. Best-fit skips cyclic repair candidates and retains the
cheapest sequenceable alternative. If neither planner can safely recover a
changed flight, the coordinator raises an explicit recovery error.
