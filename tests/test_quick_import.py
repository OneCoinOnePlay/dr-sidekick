import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from dr_sidekick.engine.core import SP303CardPrep, quick_import


class QuickImportTests(unittest.TestCase):
    def test_quick_import_skips_invalid_wavs_and_continues(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            output_dir = Path(tmpdir) / "output"
            source_dir.mkdir()
            output_dir.mkdir()

            for name in ["good_a.wav", "bad.wav", "good_b.wav"]:
                (source_dir / name).write_bytes(b"placeholder")

            def fake_prepare(self, source: Path, target: Path):
                if source.name == "bad.wav":
                    raise wave.Error("unknown format: 3")
                target.write_bytes(b"ok")
                return []

            with patch.object(SP303CardPrep, "_prepare_wav", fake_prepare):
                payload = quick_import(source_dir, output_dir)

            self.assertEqual(payload["total_found"], 3)
            self.assertEqual(payload["imported_count"], 2)
            self.assertEqual(payload["skipped_count"], 1)
            self.assertEqual(len(payload["results"]["wav_prepared"]), 2)
            self.assertEqual(len(payload["results"]["errors"]), 1)
            self.assertIn(
                "Skipped bad.wav: unsupported WAV encoding: IEEE float (format 3). Convert to PCM WAV first.",
                payload["results"]["errors"],
            )
            self.assertTrue((output_dir / "SMPL0001.WAV").exists())
            self.assertTrue((output_dir / "SMPL0002.WAV").exists())


if __name__ == "__main__":
    unittest.main()
