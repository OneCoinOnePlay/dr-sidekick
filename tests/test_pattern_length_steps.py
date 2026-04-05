import unittest

from dr_sidekick.engine import PatternModel


class PatternLengthStepTests(unittest.TestCase):
    def setUp(self):
        self.model = PatternModel()
        self.model.new_pattern()

    def test_lengths_up_to_twenty_preserve_single_bar_steps(self):
        self.assertEqual(self.model.normalize_pattern_length_bars(1, 2), 1)
        self.assertEqual(self.model.normalize_pattern_length_bars(20, 16), 20)

    def test_lengths_above_twenty_snap_to_four_bar_steps(self):
        self.assertEqual(self.model.normalize_pattern_length_bars(21, 20), 24)
        self.assertEqual(self.model.normalize_pattern_length_bars(25, 24), 28)
        self.assertEqual(self.model.normalize_pattern_length_bars(27, 24), 28)

    def test_lengths_above_twenty_snap_down_when_reducing(self):
        self.assertEqual(self.model.normalize_pattern_length_bars(23, 24), 20)
        self.assertEqual(self.model.normalize_pattern_length_bars(29, 32), 28)

    def test_ninety_nine_remains_selectable(self):
        self.assertEqual(self.model.normalize_pattern_length_bars(97, 96), 99)
        self.assertEqual(self.model.normalize_pattern_length_bars(99, 96), 99)


if __name__ == "__main__":
    unittest.main()
