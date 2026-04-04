import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dr_sidekick.engine import PatternModel, INTERNAL_PPQN


class PTNInfoBarSyncTests(unittest.TestCase):
    def test_save_slot_raises_bar_count_to_cover_event_content(self):
        model = PatternModel()
        model.new_pattern()
        model.add_event((4 * 4 * INTERNAL_PPQN) - 24, 0x10)

        # Simulate stale PTNINFO saying 2 bars even though content spans 4.
        model.ptninfo_raw[0:4] = bytes.fromhex("b0040201")

        with TemporaryDirectory() as tmpdir:
            ptninfo_path = Path(tmpdir) / "PTNINFO0.SP0"
            ptndata_path = Path(tmpdir) / "PTNDATA0.SP0"
            model.save_pattern(ptninfo_path, ptndata_path)

            raw = ptninfo_path.read_bytes()

        self.assertEqual(raw[0:4], bytes.fromhex("b0040401"))


if __name__ == "__main__":
    unittest.main()
