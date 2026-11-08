# Yuji — Gate selection

This folder contains the gate-selection part of the YYZ assignment project.

## Files

- `gate_choice.py`: safe candidate filtering, soft-cost ranking, availability counts, and waiting-flight ordering.
- `solution_gatechoice.py`: evaluator `decide(observation)` coordinator, including changed-flight repair and same-tick bookings.
- `gate_choice_visualizer.html`: interactive decision tree, cost breakdown, delay control, and occupancy timeline. Open it in a browser.
- `test_gate_choice.py`: edge-case tests.

## Run the tests

From the repository root:

```powershell
python -m unittest -v yuji.test_gate_choice
```

## Run the evaluator

From the repository root:

```powershell
python evaluator.py --scenario flight_data/simple.json --solution yuji.solution_gatechoice
```

The chooser rejects any gate that violates aircraft type, wingspan, jetbridge, booking, or outage rules. It then ranks only safe gates by walking distance, domestic use of an international gate, and reassignment cost. If no safe gate exists, a waiting flight remains unassigned.

A supplied `delay_minutes` value is context for tie-breaking and display; the evaluator's current occupancy intervals are used for safety. The standalone visualizer shifts its sample occupancy interval when you edit the delay field.
