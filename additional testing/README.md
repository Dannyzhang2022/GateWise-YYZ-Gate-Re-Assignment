# Additional scenario testing

This folder contains reproducible variations of `flight_data/combined_24h.json`.
The generator keeps its message sequence and airport disruptions, gives each
flight a new ID, and shifts all of that flight's scheduled leg times by a
seeded random amount between -30 and +30 minutes. Create and update messages
for a flight receive the same shift so they remain consistent.

Regenerate the scenarios from the repository root with:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" "additional testing\generate_scenarios.py"
```

Run one scenario through the team solution with:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" evaluator.py --scenario "additional testing/combined_24h_random_01.json" --solution solution
```

## Results

The team solution was run through the evaluator against the baseline and all
three generated scenarios. Lower scores are better.

| Scenario | Result | Score | Assigned | Unassigned | Hard failures |
| --- | --- | ---: | ---: | ---: | ---: |
| `flight_data/combined_24h.json` | OK | 51.3 | 29 | 0 | 0 |
| `combined_24h_random_01.json` | OK | 51.3 | 29 | 0 | 0 |
| `combined_24h_random_02.json` | OK | 51.3 | 29 | 0 | 0 |
| `combined_24h_random_03.json` | OK | 51.8 | 29 | 0 | 0 |

Most of each score is walking distance; every run also has two domestic flights
at international gates. None of these runs incurs a reassignment penalty. These
are useful schedule-jitter checks, but they do not add new disruption types or
airport resources beyond the combined scenario's existing messages.
