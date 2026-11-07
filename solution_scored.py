"""
Reference solution (a cleaner, scoring-aware example).

Same hard-constraint handling as the starter (including gate outages), but
instead of the first compatible gate it picks the *best* one by a small cost
function:
  - keep domestic flights off international gates when possible (avoid the
    premium-gate penalty),
  - prefer a tighter wingspan fit,
  - prefer a shorter walking distance,
  - don't waste a jetbridge on a flight that doesn't need one.

This is a greedy heuristic, not an optimum, but it is correct and scores well
on the sample scenarios.
"""

from flight_order import compatible, count_compatible_gates, order_waiting_flights

CARGO, DOMESTIC, INTERNATIONAL = 0, 1, 2


def _overlap(a, b) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def gate_blocked(gid, intervals, local, outages, exclude=None) -> bool:
    for booking in local.get(gid, []):
        if booking["flight_id"] == exclude:
            continue
        if any(_overlap(x, y) for x in intervals for y in booking["intervals"]):
            return True
    for window in outages.get(gid, []):
        if any(_overlap(x, window) for x in intervals):
            return True
    return False


def gate_cost(flight, gate) -> float:
    """Lower is better."""
    cost = 0.0
    if flight["category"] == DOMESTIC and gate["gate_type"] == INTERNATIONAL:
        cost += 1000   # avoid premium gates for domestic flights
    if not flight["jetbridge_required"] and gate["jetbridge"]:
        cost += 100    # don't waste a jetbridge
    cost += max(0, gate["max_wingspan"] - flight["wingspan"])   # tight fit
    cost += max(0, gate.get("dist", 0))                          # short walk
    return cost


def best_gate(flight, gates, local, outages, exclude=None, skip=None, waiting=None):
    best_gid, best = None, None
    intervals = flight["intervals"]
    for gid, gate in gates.items():
        if gid == skip or not compatible(flight, gate):
            continue
        if gate_blocked(gid, intervals, local, outages, exclude):
            continue
        # Prefer not to consume a gate that another waiting flight badly needs.
        scarcity_impact = 0.0
        for other_fid, other in (waiting or {}).items():
            if not any(_overlap(a, b) for a in intervals for b in other["intervals"]):
                continue
            if not compatible(other, gate):
                continue
            usable = {
                other_gid: other_gate
                for other_gid, other_gate in gates.items()
                if not gate_blocked(other_gid, other["intervals"], local, outages)
            }
            options = count_compatible_gates(other, usable)
            if options:
                scarcity_impact += 1 / options
        c = (scarcity_impact, gate_cost(flight, gate))
        if best is None or c < best:
            best, best_gid = c, gid
    return best_gid


def decide(observation):
    gates = observation["gates"]
    outages = observation.get("gate_outages", {})
    assignments, reassignments = [], []
    local = {gid: list(bookings) for gid, bookings in observation["gate_assignments"].items()}

    # 1. Repair changed flights (timing/equipment change or gate outage).
    for fid, info in observation["changed_flights"].items():
        cur = info["gate_id"]
        new = info["new"]
        flight = {
            "intervals": new["intervals"],
            "wingspan": new["wingspan"],
            "jetbridge_required": new["jetbridge_required"],
            "category": new["category"],
        }
        if compatible(flight, gates[cur]) and not gate_blocked(cur, flight["intervals"], local, outages, exclude=fid):
            continue
        new_gid = best_gate(flight, gates, local, outages, exclude=fid, skip=cur)
        if new_gid is not None:
            reassignments.append((fid, new_gid))
            local[cur] = [b for b in local.get(cur, []) if b["flight_id"] != fid]
            local.setdefault(new_gid, []).append({"flight_id": fid, "intervals": flight["intervals"]})

    # 2. Assign waiting flights. Recalculate usable gates after each placement:
    #    occupied gates and outages reduce the options available to later flights.
    waiting = dict(observation["waiting_flights"])
    while waiting:
        compatible_counts = {
            fid: count_compatible_gates(
                flight,
                {gid: gate for gid, gate in gates.items()
                 if not gate_blocked(gid, flight["intervals"], local, outages)},
            )
            for fid, flight in waiting.items()
        }
        fid = order_waiting_flights(waiting, compatible_counts)[0]
        flight = waiting.pop(fid)
        gid = best_gate(flight, gates, local, outages, waiting=waiting)
        if gid is not None:
            assignments.append((fid, gid))
            local.setdefault(gid, []).append({"flight_id": fid, "intervals": flight["intervals"]})

    return {"assignments": assignments, "reassignments": reassignments}
