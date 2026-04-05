import tempfile
import unittest
from pathlib import Path

from dr_sidekick.engine.core import SmartMediaLibrary


class RestoreToCardTests(unittest.TestCase):
    def test_restore_card_removes_stale_sp0_files_from_target(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "SmartMedia-Library"
            library = SmartMediaLibrary(root)
            library.ensure_dirs()

            card_dir = library.cards_dir / "Upright Piano Stereo"
            card_dir.mkdir(parents=True, exist_ok=True)
            (card_dir / "SMP0000L.SP0").write_bytes(b"left")
            (card_dir / "SMP0000R.SP0").write_bytes(b"right")
            (card_dir / "SMPINFO0.SP0").write_bytes(b"info")

            target_dir = Path(tmpdir) / "BOSS DATA"
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "SMP0000L.SP0").write_bytes(b"old-left")
            (target_dir / "SMP0000R.SP0").write_bytes(b"old-right")
            (target_dir / "SMP0001L.SP0").write_bytes(b"stale-sample")
            (target_dir / "PTNINFO0.SP0").write_bytes(b"keep-pattern")
            (target_dir / "PTNDATA0.SP0").write_bytes(b"keep-pattern-data")

            library.restore_card("Upright Piano Stereo", target_dir)

            self.assertEqual((target_dir / "SMP0000L.SP0").read_bytes(), b"left")
            self.assertEqual((target_dir / "SMP0000R.SP0").read_bytes(), b"right")
            self.assertEqual((target_dir / "SMPINFO0.SP0").read_bytes(), b"info")
            self.assertFalse((target_dir / "SMP0001L.SP0").exists())
            self.assertEqual((target_dir / "PTNINFO0.SP0").read_bytes(), b"keep-pattern")
            self.assertEqual((target_dir / "PTNDATA0.SP0").read_bytes(), b"keep-pattern-data")


if __name__ == "__main__":
    unittest.main()
