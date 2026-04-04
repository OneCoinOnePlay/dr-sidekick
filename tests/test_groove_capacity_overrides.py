import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dr_sidekick.engine import GrooveLibrary, PatternModel


EXPECTED_SP303_FALLBACKS = {
    ("CR-78", "CR-78 16T"): 8,
    ("DMX", "DMX 16T Swing 50"): 8,
    ("DMX", "DMX 16T Swing 62"): 8,
    ("DMX", "DMX 16T Swing 75"): 8,
    ("DMX", "DMX 16T Swing 87"): 8,
    ("LM-1", "LM-1 16T"): 8,
    ("LinnDrum", "LinnDrum 16T"): 8,
    ("LinnDrum", "LinnDrum 8 + 32T"): 8,
    ("LinnDrum", "LinnDrum Swing B 16 + 32T"): 8,
    ("LinnDrum", "LinnDrum Swing B 8 + 32T"): 8,
    ("LinnDrum", "LinnDrum Swing C 16 + 32T"): 8,
    ("LinnDrum", "LinnDrum Swing C 8 + 32T"): 8,
    ("LinnDrum", "LinnDrum Swing D 16 + 32T"): 8,
    ("LinnDrum", "LinnDrum Swing D 8 + 32T"): 8,
    ("LinnDrum", "LinnDrum Swing E 16 + 32T"): 8,
    ("LinnDrum", "LinnDrum Swing E 8 + 32T"): 8,
    ("LinnDrum", "LinnDrum Swing F 8 + 32T"): 8,
    ("MPC1000", "MPC1000 16T"): 8,
    ("MPC3000", "MPC3000 16T"): 8,
    ("MPC4000", "MPC4000 16T"): 8,
    ("MPC60", "MPC60 16T"): 8,
    ("R-8", "R8 16T"): 8,
    ("SP-1200", "SP 1200 16T"): 8,
    ("TR-505", "TR-505 16T"): 8,
    ("TR-606", "TR 606 16T"): 8,
    ("TR-626", "TR 626 16T"): 8,
    ("TR-707", "TR-707 16T"): 8,
    ("TR-727", "TR-727 16T"): 8,
    ("TR-808", "TR-808 16T"): 8,
    ("TR-909", "TR-909 16T"): 8,
}


class GrooveCapacityOverrideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = GrooveLibrary(Path("packs"))

    def test_problem_grooves_define_sp303_fallback_beats(self):
        for machine, groove_name in EXPECTED_SP303_FALLBACKS:
            groove = next(
                groove
                for groove in self.library.get_grooves(machine)
                if groove.name == groove_name
            )
            self.assertEqual(groove.fallback_beats_for_device("sp303"), 8)
            self.assertEqual(groove.effective_beats_for_device("sp303"), 8)

    def test_sp303_stamp_uses_fallback_length_without_capacity_warning(self):
        for machine, groove_name in EXPECTED_SP303_FALLBACKS:
            groove = next(
                groove
                for groove in self.library.get_grooves(machine)
                if groove.name == groove_name
            )
            model = PatternModel(device_key="sp303")
            model.new_pattern()
            model.set_current_slot_length_bars(4)

            added = model.stamp_pattern(groove, 0x10)
            capacity = model.get_capacity_status()

            self.assertGreater(added, 0, groove_name)
            self.assertEqual(model.get_pattern_length_bars(), 4, groove_name)
            self.assertLess(max(event.tick for event in model.events), 2 * 4 * 96, groove_name)
            self.assertLessEqual(capacity["bytes_used"], capacity["byte_capacity"], groove_name)
            self.assertFalse(capacity["over_capacity"], groove_name)
            self.assertIsNone(model.last_stamp_warning, groove_name)

    def test_stamp_fallback_does_not_change_slot_loop_length_on_save_reload(self):
        groove = next(
            groove
            for groove in self.library.get_grooves("LM-1")
            if groove.name == "LM-1 16T"
        )
        model = PatternModel(device_key="sp303")
        model.new_pattern()
        model.set_current_slot_length_bars(4)

        model.stamp_pattern(groove, 0x10)

        self.assertEqual(model.get_pattern_length_bars(), 4)

        with TemporaryDirectory() as tmpdir:
            ptninfo_path = Path(tmpdir) / "PTNINFO0.SP0"
            ptndata_path = Path(tmpdir) / "PTNDATA0.SP0"
            model.save_pattern(ptninfo_path, ptndata_path)

            reloaded = PatternModel(device_key="sp303")
            reloaded.load_pattern(ptninfo_path, ptndata_path)

        self.assertEqual(reloaded.get_pattern_length_bars(), 4)
        self.assertEqual(max(event.tick for event in reloaded.events), 767)


if __name__ == "__main__":
    unittest.main()
