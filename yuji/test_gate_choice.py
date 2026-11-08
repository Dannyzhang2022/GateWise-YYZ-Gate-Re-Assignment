"""Focused edge-case tests for gate_choice's hard rules and soft ranking."""

import unittest

from .gate_choice import (
    choose_gate,
    count_available_gates,
    count_compatible_gates,
    get_delay_minutes,
    rank_gate_choices,
    waiting_flight_order_key,
)


def gate(gate_type, max_wingspan=50, jetbridge=True, dist=100):
    return {
        "gate_type": gate_type,
        "max_wingspan": max_wingspan,
        "jetbridge": jetbridge,
        "dist": dist,
    }


def flight(category=1, wingspan=30, jetbridge_required=True, intervals=None, **extra):
    result = {
        "category": category,
        "wingspan": wingspan,
        "jetbridge_required": jetbridge_required,
        "intervals": [[100, 140]] if intervals is None else intervals,
    }
    result.update(extra)
    return result


class GateChoiceTests(unittest.TestCase):
    def test_asymmetric_gate_types(self):
        gates = {"D": gate(1), "I": gate(2), "C": gate(0)}
        self.assertEqual(count_compatible_gates(flight(category=1), gates), 2)
        self.assertEqual(count_compatible_gates(flight(category=2), gates), 1)
        self.assertEqual(count_compatible_gates(flight(category=0), gates), 1)

    def test_wingspan_and_jetbridge_are_hard_rules(self):
        gates = {
            "too_narrow": gate(1, max_wingspan=29),
            "no_bridge": gate(1, jetbridge=False),
            "legal": gate(1, max_wingspan=30, jetbridge=True),
        }
        self.assertEqual(count_compatible_gates(flight(wingspan=30), gates), 1)
        self.assertEqual(choose_gate("F1", flight(wingspan=30), gates, {}, {}), "legal")

    def test_json_integer_jetbridge_flags_match_evaluator_truthiness(self):
        gates = {"bridge": gate(1, jetbridge=1), "no_bridge": gate(1, jetbridge=0)}
        self.assertEqual(count_compatible_gates(flight(), gates), 1)
        self.assertEqual(choose_gate("F1", flight(), gates, {}, {}), "bridge")

    def test_count_ignores_time_conflicts_and_outages(self):
        gates = {"D1": gate(1)}
        assignments = {"D1": [{"flight_id": "OTHER", "intervals": [[100, 140]]}]}
        outages = {"D1": [[100, 140]]}
        self.assertEqual(count_compatible_gates(flight(), gates), 1)
        self.assertIsNone(choose_gate("F1", flight(), gates, assignments, outages))

    def test_available_gate_count_includes_bookings_and_outages(self):
        gates = {"D1": gate(1), "D2": gate(1)}
        assignments = {"D1": [{"flight_id": "A", "intervals": [[100, 140]]}]}
        outages = {"D2": [[100, 140]]}
        self.assertEqual(count_compatible_gates(flight(), gates), 2)
        self.assertEqual(count_available_gates(flight(), gates, assignments, outages), 0)

    def test_half_open_adjacent_bookings_do_not_conflict(self):
        gates = {"D1": gate(1)}
        assignments = {"D1": [{"flight_id": "OTHER", "intervals": [[60, 100]]}]}
        self.assertEqual(choose_gate("F1", flight(), gates, assignments, {}), "D1")

    def test_any_interval_overlap_blocks(self):
        gates = {"D1": gate(1), "D2": gate(1, dist=500)}
        assignments = {"D1": [{"flight_id": "OTHER", "intervals": [[130, 150]]}]}
        multi = flight(intervals=[[50, 70], [100, 120], [125, 140]])
        self.assertEqual(choose_gate("F1", multi, gates, assignments, {}), "D2")

    def test_excluding_self_allows_repair_and_skip_forces_move(self):
        gates = {"D1": gate(1, dist=50), "D2": gate(1, dist=500)}
        assignments = {"D1": [{"flight_id": "F1", "intervals": [[100, 140]]}]}
        self.assertEqual(choose_gate("F1", flight(), gates, assignments, {}), "D1")
        self.assertEqual(
            choose_gate("F1", flight(), gates, assignments, {}, exclude_flight_id="F1", skip_gate_id="D1"),
            "D2",
        )

    def test_domestic_premium_penalty_can_change_choice(self):
        gates = {
            "D_far": gate(1, dist=900),  # walking cost 9
            "I_near": gate(2, dist=100),  # walking cost 1 + premium cost 5
            "I_close": gate(2, dist=50),  # walking cost .5 + premium cost 5
        }
        self.assertEqual(choose_gate("F1", flight(category=1), gates, {}, {}), "I_close")
        gates["D_far"]["dist"] = 300
        self.assertEqual(choose_gate("F1", flight(category=1), gates, {}, {}), "D_far")

    def test_existing_assignment_is_preferred_when_safe(self):
        gates = {"D1": gate(1, dist=500), "D2": gate(1, dist=100)}
        assignments = {"D1": [{"flight_id": "F1", "intervals": [[100, 140]]}]}
        self.assertEqual(choose_gate("F1", flight(), gates, assignments, {}), "D1")
        ranked = rank_gate_choices("F1", flight(), gates, assignments, {})
        self.assertEqual(ranked[0]["gate_id"], "D1")
        self.assertEqual(ranked[0]["costs"]["gate_move"], 0)

    def test_after_arrival_move_has_higher_cost(self):
        gates = {"D1": gate(1), "D2": gate(1, dist=0)}
        assignments = {"D1": [{"flight_id": "F1", "intervals": [[100, 140]]}]}
        before = rank_gate_choices("F1", flight(current_time=90), gates, assignments, {})
        after = rank_gate_choices("F1", flight(current_time=110), gates, assignments, {})
        move_before = next(item for item in before if item["gate_id"] == "D2")
        move_after = next(item for item in after if item["gate_id"] == "D2")
        self.assertEqual(move_before["costs"]["gate_move"], 10)
        self.assertEqual(move_after["costs"]["gate_move"], 50)

    def test_delay_is_reported_but_does_not_falsely_change_occupancy(self):
        gates = {"D1": gate(1), "D2": gate(1, dist=200)}
        on_time = flight()
        delayed = flight(delay_minutes=35)
        self.assertEqual(choose_gate("F1", on_time, gates, {}, {}), choose_gate("F1", delayed, gates, {}, {}))
        self.assertEqual(rank_gate_choices("F1", delayed, gates, {}, {})[0]["delay_minutes"], 35)
        self.assertIsNone(rank_gate_choices("F1", flight(delay_minutes=-5), gates, {}, {})[0]["delay_minutes"])

    def test_delay_metadata_is_optional_and_validated(self):
        self.assertEqual(get_delay_minutes({"delay_minutes": 12.5}), 12.5)
        self.assertEqual(get_delay_minutes({"delay_minutes": 0}), 0)
        self.assertIsNone(get_delay_minutes({"delay_minutes": "unknown"}))
        self.assertIsNone(get_delay_minutes({"delay_minutes": -1}))
        self.assertIsNone(get_delay_minutes(None))

    def test_delay_breaks_ties_after_emergency_and_gate_scarcity(self):
        one_domestic_gate = {"D1": gate(1)}
        late = flight(delay_minutes=40)
        later = flight(delay_minutes=95)
        self.assertLess(
            waiting_flight_order_key(later, one_domestic_gate),
            waiting_flight_order_key(late, one_domestic_gate),
        )

        emergency = flight(delay_minutes=0, priority=True)
        regular = flight(delay_minutes=500, priority=False)
        self.assertLess(
            waiting_flight_order_key(emergency, one_domestic_gate),
            waiting_flight_order_key(regular, one_domestic_gate),
        )

        gates = {"D1": gate(1), "I1": gate(2)}
        constrained = flight(category=2, delay_minutes=1)
        flexible = flight(category=1, delay_minutes=120)
        self.assertLess(
            waiting_flight_order_key(constrained, gates),
            waiting_flight_order_key(flexible, gates),
        )

    def test_zero_available_flight_waits_behind_assignable_flight(self):
        gates = {"I1": gate(2), "I2": gate(2), "C1": gate(0)}
        assignments = {
            "I2": [{"flight_id": "BLOCK1", "intervals": [[100, 140]]}],
            "C1": [{"flight_id": "BLOCK2", "intervals": [[100, 140]]}],
        }
        international = flight(category=2, delay_minutes=0)
        cargo = flight(category=0, delay_minutes=100)
        international_key = waiting_flight_order_key(international, gates, assignments, flight_id="INT")
        cargo_key = waiting_flight_order_key(cargo, gates, assignments, flight_id="CARGO")
        self.assertLess(international_key, cargo_key)

    def test_outage_and_booking_each_block(self):
        gates = {"D1": gate(1), "D2": gate(1, dist=200)}
        assignments = {"D1": [{"flight_id": "X", "intervals": [[130, 150]]}]}
        outages = {"D2": [[100, 140]]}
        self.assertIsNone(choose_gate("F1", flight(), gates, assignments, outages))

    def test_malformed_intervals_fail_closed_without_crashing(self):
        gates = {"D1": gate(1)}
        self.assertIsNone(choose_gate("F1", flight(intervals=[[140, 100]]), gates, {}, {}))
        self.assertIsNone(choose_gate("F1", {"category": 1}, gates, {}, {}))

    def test_unknown_existing_booking_blocks_gate(self):
        gates = {"D1": gate(1), "D2": gate(1, dist=200)}
        assignments = {"D1": [{"flight_id": "X", "intervals": "not intervals"}]}
        self.assertEqual(choose_gate("F1", flight(), gates, assignments, {}), "D2")

    def test_malformed_outage_data_fails_closed(self):
        gates = {"D1": gate(1), "D2": gate(1, dist=200)}
        self.assertEqual(choose_gate("F1", flight(), gates, {}, {"D1": "unknown"}), "D2")

    def test_missing_required_flight_fields_is_not_compatible(self):
        self.assertEqual(count_compatible_gates({"category": 1, "wingspan": 30}, {"D1": gate(1)}), 0)

    def test_ties_are_deterministic(self):
        gates = {"D2": gate(1, dist=100), "D1": gate(1, dist=100)}
        self.assertEqual(choose_gate("F1", flight(), gates, {}, {}), "D1")

    def test_no_gates_returns_none(self):
        self.assertEqual(count_compatible_gates(flight(), []), 0)
        self.assertIsNone(choose_gate("F1", flight(), {}, {}, {}))


if __name__ == "__main__":
    unittest.main()
