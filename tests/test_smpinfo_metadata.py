import struct
import tempfile
import unittest
from pathlib import Path

from dr_sidekick.engine import sp303_codec
from dr_sidekick.engine.core import SMPINFO, SP303CardPrep


def make_standard_sp0(pages: int = 1) -> bytes:
    """A synthetic page-framed STANDARD/LONG file: 504 audio bytes + 8-byte
    zero trailer per 512-byte page."""
    page = bytes(range(252)) * 2 + bytes(8)
    assert len(page) == sp303_codec.PAGE_BYTES
    return page * pages


class AssignArchivedSp0Tests(unittest.TestCase):
    def test_standard_assignment_stores_audio_byte_length(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sp0 = Path(tmpdir) / "SMP0000L.SP0"
            sp0.write_bytes(make_standard_sp0(pages=2))

            prep = SP303CardPrep()
            prep.assign_archived_sp0(0, sp0)
            source = prep.sources[0]

            self.assertEqual(source.sample_length, 2 * sp303_codec.PAGE_AUDIO_BYTES)
            self.assertEqual(source.sample_rate, sp303_codec.STANDARD_SAMPLE_RATE)

    def test_lofi_assignment_stores_raw_length(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sp0 = Path(tmpdir) / "SMP0000L.SP0"
            sp0.write_bytes(bytes(40))  # raw 8-byte blocks, not page-framed

            prep = SP303CardPrep()
            prep.assign_archived_sp0(0, sp0)
            source = prep.sources[0]

            self.assertEqual(source.sample_length, 40)
            self.assertEqual(source.sample_rate, sp303_codec.LOFI_SAMPLE_RATE)

    def test_explicit_record_metadata_overrides_sniffing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sp0 = Path(tmpdir) / "SMP0000L.SP0"
            sp0.write_bytes(make_standard_sp0(pages=1))

            prep = SP303CardPrep()
            prep.assign_archived_sp0(
                0, sp0, sample_rate=sp303_codec.LONG_SAMPLE_RATE, sample_length=400
            )
            source = prep.sources[0]

            self.assertEqual(source.sample_length, 400)
            self.assertEqual(source.sample_rate, sp303_codec.LONG_SAMPLE_RATE)


class PrepareCardSmpinfoTests(unittest.TestCase):
    def test_written_card_round_trips_through_decoder(self):
        """A Dr. Sidekick-written card must reload and decode without errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sp0 = Path(tmpdir) / "source" / "SMP0000L.SP0"
            sp0.parent.mkdir()
            sp0.write_bytes(make_standard_sp0(pages=3))
            output_dir = Path(tmpdir) / "card"

            prep = SP303CardPrep()
            prep.assign_archived_sp0(0, sp0)
            prep.prepare_card(output_dir)

            smpinfo = SMPINFO.from_file(output_dir / "SMPINFO0.SP0")
            record = smpinfo.slots[0]
            self.assertEqual(record.sample_length_bytes, 3 * sp303_codec.PAGE_AUDIO_BYTES)
            self.assertEqual(record.sample_rate, sp303_codec.STANDARD_SAMPLE_RATE)

            samples = sp303_codec.decode_sp0_file(
                output_dir / "SMP0000L.SP0",
                encoded_length=record.sample_length_bytes,
                lo_fi=record.sample_rate == sp303_codec.LOFI_SAMPLE_RATE,
            )
            blocks = (3 * sp303_codec.PAGE_AUDIO_BYTES) // sp303_codec.BLOCK_BYTES
            self.assertEqual(len(samples), blocks * sp303_codec.SAMPLES_PER_BLOCK)

    def test_lofi_rate_propagates_to_written_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sp0 = Path(tmpdir) / "source" / "SMP0000L.SP0"
            sp0.parent.mkdir()
            sp0.write_bytes(bytes(64))
            output_dir = Path(tmpdir) / "card"

            prep = SP303CardPrep()
            prep.assign_archived_sp0(0, sp0, sample_rate=sp303_codec.LOFI_SAMPLE_RATE)
            prep.prepare_card(output_dir)

            raw = (output_dir / "SMPINFO0.SP0").read_bytes()
            rate_field = struct.unpack(">H", raw[32:34])[0]
            self.assertEqual(rate_field, 1102)

            smpinfo = SMPINFO.from_bytes(raw)
            self.assertEqual(smpinfo.slots[0].sample_rate, sp303_codec.LOFI_SAMPLE_RATE)
            self.assertEqual(smpinfo.slots[0].sample_length_bytes, 64)


class RateFieldTests(unittest.TestCase):
    def test_rate_field_round_trip(self):
        for rate, field in ((44100, 4410), (22050, 2205), (11025, 1102)):
            self.assertEqual(sp303_codec.rate_field_from_sample_rate(rate), field)
            self.assertEqual(sp303_codec.sample_rate_from_field(field), rate)


if __name__ == "__main__":
    unittest.main()
