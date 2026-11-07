"""Cost-aware gate assignment with a small exact search per decision wave.

The evaluator's standing penalties are the objective for new placements:
walking distance and domestic use of an international gate. Existing plans are
kept stable unless a flight's updated requirements or an outage invalidates its
gate, since voluntary moves incur an additional evaluator penalty.
"""

from flight_order import compatible, count_compatible_gates, order_waiting_flights
from gms.config import DOMESTIC, INTERNATIONAL, WEIGHTS


def _overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def _blocked(gid, intervals, local, outages, exclude=None):
    for booking in local.get(gid, []):
        if booking["flight_id"] == exclude:
            continue
        if any(_overlap(a, b) for a in intervals for b in booking["intervals"]):
            return True
    return any(_overlap(a, window)
               for window in outages.get(gid, []) for a in intervals)


def _usable_gates(flight, gates, local, outages, exclude=None, skip=None):
    return {
        gid: gate for gid, gate in gates.items()
        if gid != skip
        and compatible(flight, gate)
        and not _blocked(gid, flight["intervals"], local, outages, exclude)
    }


def _gate_cost(flight, gate):
    """Return the evaluator's exact standing cost for one assignment."""
    cost = max(0, gate.get("dist", 0)) * WEIGHTS["walking_distance_per_m"]
    if flight["category"] == DOMESTIC and gate["gate_type"] == INTERNATIONAL:
        cost += WEIGHTS["domestic_at_intl_gate"]
    return cost


def _planning_cost(flight, gid, gate, outages, now):
    """Add a risk proxy when a previously outaged gate would take a future flight."""
    cost = _gate_cost(flight, gate)
    starts = [start for start, _ in flight.get("intervals", [])]
    if starts and min(starts) > now:
        had_outage = any(start < now for start, _ in outages.get(gid, []))
        if had_outage:
            # A repeat outage can force a costly move after the aircraft arrives.
            cost += WEIGHTS["reassignment_after_arrival"]
    return cost


def _scarcity_tiebreak(flight, gid, waiting, gates, local, outages):
    """Prefer gates that leave overlapping waiters with more usable options."""
    impact = 0.0
    gate = gates[gid]
    for other in waiting.values():
        if not compatible(other, gate):
            continue
        if not any(_overlap(a, b) for a in flight["intervals"] for b in other["intervals"]):
            continue
        options = _usable_gates(other, gates, local, outages)
        count = count_compatible_gates(other, options)
        if count:
            impact += 1 / count
    return impact


def _exact_wave_plan(waiting, gates, local, outages, now):
    """Find the lowest-penalty plan for a small set of currently waiting flights."""
    best_key = None
    best_plan = {}

    def search(remaining, score, scarcity, plan):
        nonlocal best_key, best_plan
        if not remaining:
            key = (score, scarcity, tuple(sorted(plan.items())))
            if best_key is None or key < best_key:
                best_key, best_plan = key, dict(plan)
            return

        counts = {
            fid: count_compatible_gates(
                flight, _usable_gates(flight, gates, local, outages)
            )
            for fid, flight in remaining.items()
        }
        fid = order_waiting_flights(remaining, counts)[0]
        flight = remaining[fid]
        rest = {other_id: other for other_id, other in remaining.items() if other_id != fid}
        options = _usable_gates(flight, gates, local, outages)

        for gid, gate in sorted(options.items()):
            impact = _scarcity_tiebreak(flight, gid, rest, gates, local, outages)
            local.setdefault(gid, []).append(
                {"flight_id": fid, "intervals": flight["intervals"]}
            )
            plan[fid] = gid
            search(rest, score + _planning_cost(flight, gid, gate, outages, now),
                   scarcity + impact, plan)
            del plan[fid]
            local[gid].pop()

        # Leaving a flight without a gate has the evaluator's final unassigned
        # penalty. This also makes emergencies win scarce capacity when needed.
        penalty = (WEIGHTS["unassigned_emergency"] if flight.get("priority", False)
                   else WEIGHTS["unassigned_flight"])
        search(rest, score + penalty, scarcity, plan)

    search(dict(waiting), 0.0, 0.0, {})
    return best_plan


def _greedy_wave_plan(waiting, gates, local, outages, now):
    """Bound search work for unusually large decision waves."""
    remaining = dict(waiting)
    plan = {}
    while remaining:
        counts = {
            fid: count_compatible_gates(
                flight, _usable_gates(flight, gates, local, outages)
            )
            for fid, flight in remaining.items()
        }
        fid = order_waiting_flights(remaining, counts)[0]
        flight = remaining.pop(fid)
        options = _usable_gates(flight, gates, local, outages)
        if not options:
            continue
        gid = min(options, key=lambda candidate: (
            _planning_cost(flight, candidate, options[candidate], outages, now),
            _scarcity_tiebreak(flight, candidate, remaining, gates, local, outages),
            candidate,
        ))
        plan[fid] = gid
        local.setdefault(gid, []).append(
            {"flight_id": fid, "intervals": flight["intervals"]}
        )
    return plan


def _repair_changed_flights(observation, gates, local, outages):
    """Jointly repair changed flights and any incumbents blocking their options."""
    changed = observation.get("changed_flights", {})
    assigned = observation.get("assigned_flights", {})
    if not changed:
        return [], local

    profiles = {
        fid: (changed[fid]["new"] if fid in changed else info)
        for fid, info in assigned.items()
    }
    component = set(changed)

    # Include assigned flights that occupy a gate a component flight could use.
    # This lets the repair move a flexible incumbent to make room for a changed
    # large aircraft instead of declaring the changed flight unrepairable.
    grew = True
    while grew:
        grew = False
        for fid in tuple(component):
            flight = profiles[fid]
            for gid, gate in gates.items():
                if not compatible(flight, gate):
                    continue
                if any(_overlap(iv, outage)
                       for iv in flight["intervals"]
                       for outage in outages.get(gid, [])):
                    continue
                for booking in local.get(gid, []):
                    other_id = booking["flight_id"]
                    if other_id in component or other_id not in profiles:
                        continue
                    if any(_overlap(a, b) for a in flight["intervals"]
                           for b in booking["intervals"]):
                        component.add(other_id)
                        grew = True

    # Keep the combinatorial repair local. Large components fall back to the
    # regular repair loop below rather than causing an unbounded search.
    if len(component) > 10:
        return None, local

    fixed = {
        gid: [b for b in bookings if b["flight_id"] not in component]
        for gid, bookings in local.items()
    }
    best_key = None
    best_plan = None
    now = observation.get("time", 0)

    def search(remaining, work, plan, cost):
        nonlocal best_key, best_plan
        if not remaining:
            key = (cost, tuple(sorted(plan.items())))
            if best_key is None or key < best_key:
                best_key, best_plan = key, dict(plan)
            return

        counts = {
            fid: count_compatible_gates(
                profiles[fid], _usable_gates(profiles[fid], gates, work, outages)
            )
            for fid in remaining
        }
        fid = order_waiting_flights(
            {key: profiles[key] for key in remaining}, counts
        )[0]
        flight = profiles[fid]
        rest = set(remaining)
        rest.remove(fid)
        options = _usable_gates(flight, gates, work, outages)
        current_gid = assigned[fid]["gate_id"]
        start = min((iv[0] for iv in flight["intervals"]), default=now)

        for gid, gate in sorted(options.items()):
            move_cost = 0
            if gid != current_gid:
                if start <= now:
                    move_cost = WEIGHTS["reassignment_after_arrival"]
                elif fid not in changed:
                    move_cost = WEIGHTS["reassignment"]
            work.setdefault(gid, []).append(
                {"flight_id": fid, "intervals": flight["intervals"]}
            )
            plan[fid] = gid
            search(rest, work, plan, cost + move_cost + _gate_cost(flight, gate))
            del plan[fid]
            work[gid].pop()

    search(set(component), {gid: list(bookings) for gid, bookings in fixed.items()}, {}, 0.0)
    if best_plan is None:
        return None, local

    # The evaluator applies reassignments one at a time. Emit moves only when
    # their destination has been cleared by earlier moves in this same plan.
    work = {gid: list(bookings) for gid, bookings in local.items()}
    pending = {
        fid: gid for fid, gid in best_plan.items()
        if gid != assigned[fid]["gate_id"]
    }
    reassignments = []
    while pending:
        ready = None
        for fid, gid in pending.items():
            if not _blocked(gid, profiles[fid]["intervals"], work, outages, exclude=fid):
                ready = fid
                break
        if ready is None:
            return None, local  # a cyclic swap needs a temporary gate
        fid = ready
        old_gid, new_gid = assigned[fid]["gate_id"], pending.pop(fid)
        reassignments.append((fid, new_gid))
        work[old_gid] = [b for b in work.get(old_gid, []) if b["flight_id"] != fid]
        work.setdefault(new_gid, []).append(
            {"flight_id": fid, "intervals": profiles[fid]["intervals"]}
        )

    local.clear()
    local.update(work)
    return reassignments, local


def decide(observation):
    gates = observation["gates"]
    outages = observation.get("gate_outages", {})
    local = {gid: list(bookings)
             for gid, bookings in observation["gate_assignments"].items()}
    reassignments = []

    # Repair changed flights jointly with flexible incumbents that block their
    # alternatives, while charging the evaluator's actual move penalties.
    repaired, repaired_local = _repair_changed_flights(
        observation, gates, local, outages
    )
    if repaired is not None:
        reassignments.extend(repaired)
        local = repaired_local
    else:
        # Large or cyclic repair components use the simpler forced-move pass.
        for fid, info in observation.get("changed_flights", {}).items():
            current_gid, flight = info["gate_id"], info["new"]
            if (compatible(flight, gates[current_gid])
                    and not _blocked(current_gid, flight["intervals"], local, outages, fid)):
                continue
            options = _usable_gates(
                flight, gates, local, outages, exclude=fid, skip=current_gid
            )
            if not options:
                continue
            new_gid = min(options, key=lambda gid: (_gate_cost(flight, options[gid]), gid))
            reassignments.append((fid, new_gid))
            local[current_gid] = [b for b in local.get(current_gid, []) if b["flight_id"] != fid]
            local.setdefault(new_gid, []).append(
                {"flight_id": fid, "intervals": flight["intervals"]}
            )

    waiting = observation.get("waiting_flights", {})
    now = observation.get("time", 0)
    if len(waiting) <= 8:
        plan = _exact_wave_plan(waiting, gates, local, outages, now)
    else:
        plan = _greedy_wave_plan(waiting, gates, local, outages, now)

    assignments = list(plan.items())
    if len(waiting) <= 8:
        for fid, gid in assignments:
            local.setdefault(gid, []).append(
                {"flight_id": fid, "intervals": waiting[fid]["intervals"]}
            )

    return {"assignments": assignments, "reassignments": reassignments}
