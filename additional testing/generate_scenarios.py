"""Generate reproducible randomized variants of the combined 24-hour scenario."""

from __future__ import annotations

import json
import random
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "flight_data" / "combined_24h.json"
OUTPUT = Path(__file__).resolve().parent
SEEDS = (2601, 2602, 2603)


def shift_time(value: str, minutes: int) -> str:
    hour, minute = (int(part) for part in value.split(":"))
    total = (hour * 60 + minute + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def variant(seed: int) -> dict:
    rng = random.Random(seed)
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    shifts: dict[tuple, int] = {}
    number_offset = seed * 10

    for messages in source.values():
        for message in messages:
            flight = message.get("flight")
            if not isinstance(flight, dict):
                continue
            key = (
                flight.get("carrier"),
                flight.get("flightNumber"),
                flight.get("flightSuffix"),
                flight.get("originatingDate"),
            )
            shifts.setdefault(key, rng.randint(-30, 30))

    for messages in source.values():
        for message in messages:
            flight = message.get("flight")
            if not isinstance(flight, dict):
                continue
            key = (
                flight.get("carrier"),
                flight.get("flightNumber"),
                flight.get("flightSuffix"),
                flight.get("originatingDate"),
            )
            old_number = str(flight.get("flightNumber", "0"))
            try:
                flight["flightNumber"] = str(int(old_number) + number_offset)
            except ValueError:
                flight["flightNumber"] = f"{number_offset}{old_number}"
            delta = shifts[key]
            for leg in message.get("legs", []):
                for field in ("scheduledDepartureTime", "scheduledArrivalTime"):
                    if field in leg:
                        leg[field] = shift_time(leg[field], delta)

    return source


def main() -> None:
    for index, seed in enumerate(SEEDS, start=1):
        path = OUTPUT / f"combined_24h_random_{index:02d}.json"
        path.write_text(json.dumps(variant(seed), indent=2) + "\n", encoding="utf-8")
        print(path.name)


if __name__ == "__main__":
    main()
