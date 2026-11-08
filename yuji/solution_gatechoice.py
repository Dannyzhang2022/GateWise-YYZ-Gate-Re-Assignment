"""decide() built on gate_choice: repair changed flights, then assign waiting flights."""
from .gate_choice import choose_gate, waiting_flight_order_key
from gms.compat import hard_compatible
from gms.occupancy import sets_conflict, intervals_overlap


def _still_ok(fid, cur, new, gates, local, outages):
    ok, _ = hard_compatible(new["category"], new["wingspan"], new["jetbridge_required"], gates[cur])
    if not ok:
        return False
    for b in local.get(cur, []):
        if b["flight_id"] != fid and sets_conflict(new["intervals"], b["intervals"]):
            return False
    return not any(intervals_overlap(iv, w) for iv in new["intervals"] for w in outages.get(cur, []))


def decide(observation):
    gates = observation["gates"]
    outages = observation.get("gate_outages", {})
    local = {g: list(b) for g, b in observation["gate_assignments"].items()}
    assignments, reassignments = [], []

    # 1. Repair changed flights; flights that are still fine stay put (no move cost).
    for fid, info in observation["changed_flights"].items():
        cur, new = info["gate_id"], info["new"]
        if _still_ok(fid, cur, new, gates, local, outages):
            continue
        choice_flight = dict(new)
        choice_flight["current_time"] = observation.get("time")
        gid = choose_gate(fid, choice_flight, gates, local, outages, exclude_flight_id=fid, skip_gate_id=cur)
        if gid is not None:
            reassignments.append((fid, gid))
            local[cur] = [b for b in local.get(cur, []) if b["flight_id"] != fid]
            local.setdefault(gid, []).append({"flight_id": fid, "intervals": new["intervals"]})

    # 2. Waiting flights: emergencies first, then live gate scarcity, delay, start.
    waiting = dict(observation["waiting_flights"])
    while waiting:
        # Recount available gates after each placement: a previous choice may
        # have removed one of the remaining flights' only safe options.
        fid, f = min(
            waiting.items(),
            key=lambda item: waiting_flight_order_key(
                item[1], gates, local, outages, flight_id=item[0]
            ),
        )
        del waiting[fid]
        choice_flight = dict(f)
        choice_flight.setdefault("current_time", observation.get("time"))
        gid = choose_gate(fid, choice_flight, gates, local, outages)
        if gid is not None:
            assignments.append((fid, gid))
            local.setdefault(gid, []).append({"flight_id": fid, "intervals": f["intervals"]})
    return {"assignments": assignments, "reassignments": reassignments}
