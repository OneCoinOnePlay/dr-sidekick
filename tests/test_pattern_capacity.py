import unittest

from dr_sidekick.engine import INTERNAL_PPQN, PatternModel


class PatternCapacityTests(unittest.TestCase):
    def setUp(self):
        self.model = PatternModel()
        self.model.new_pattern()

    def test_capacity_uses_serialized_byte_length(self):
        self.model.set_current_slot_length_bars(8)
        late_tick = (7 * 4 * INTERNAL_PPQN) + (2 * INTERNAL_PPQN)
        self.model.add_event(late_tick, 0x10)

        capacity = self.model.get_capacity_status()
        expected_bytes = len(
            self.model.ptndata.encode_events(
                self.model.events,
                total_length_ticks=8 * 4 * INTERNAL_PPQN,
            )
        )

        self.assertEqual(capacity["bytes_used"], expected_bytes)
        self.assertGreater(capacity["bytes_used"], 0)

    def test_longer_loop_length_increases_capacity_usage(self):
        self.model.set_current_slot_length_bars(1)
        self.model.add_event(0, 0x10)
        one_bar_bytes = self.model.get_capacity_status()["bytes_used"]

        self.model.set_current_slot_length_bars(8)
        eight_bar_bytes = self.model.get_capacity_status()["bytes_used"]

        self.assertGreater(eight_bar_bytes, one_bar_bytes)

    def test_undo_redo_restore_loop_length_capacity(self):
        self.model.set_current_slot_length_bars(1)
        self.model.add_event(0, 0x10)
        one_bar_capacity = self.model.get_capacity_status()["bytes_used"]

        self.model.set_current_slot_length_bars(8)
        eight_bar_capacity = self.model.get_capacity_status()["bytes_used"]
        self.assertGreater(eight_bar_capacity, one_bar_capacity)

        self.assertTrue(self.model.undo())
        self.assertEqual(self.model.get_pattern_length_bars(), 1)
        self.assertEqual(self.model.get_capacity_status()["bytes_used"], one_bar_capacity)

        self.assertTrue(self.model.redo())
        self.assertEqual(self.model.get_pattern_length_bars(), 8)
        self.assertEqual(self.model.get_capacity_status()["bytes_used"], eight_bar_capacity)


if __name__ == "__main__":
    unittest.main()
