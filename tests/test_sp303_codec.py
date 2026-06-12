import unittest

from dr_sidekick.engine import rdac, sp303_codec


def make_standard_page() -> bytes:
    return bytes(range(252)) * 2 + bytes(8)


class DecodeSp0StreamTests(unittest.TestCase):
    def test_length_beyond_stream_raises_by_default(self):
        raw = make_standard_page()
        with self.assertRaises(ValueError):
            sp303_codec.decode_sp0_stream(raw, encoded_length=len(raw))

    def test_clamp_length_decodes_available_audio(self):
        # Legacy Dr. Sidekick cards stored trailer-inclusive (512/page) lengths;
        # clamping decodes the real 504 audio bytes per page instead of failing.
        raw = make_standard_page()
        samples = sp303_codec.decode_sp0_stream(
            raw, encoded_length=len(raw), clamp_length=True
        )
        blocks = sp303_codec.PAGE_AUDIO_BYTES // sp303_codec.BLOCK_BYTES
        self.assertEqual(len(samples), blocks * sp303_codec.SAMPLES_PER_BLOCK)

    def test_exact_length_decodes(self):
        raw = make_standard_page()
        samples = sp303_codec.decode_sp0_stream(
            raw, encoded_length=sp303_codec.PAGE_AUDIO_BYTES
        )
        blocks = sp303_codec.PAGE_AUDIO_BYTES // sp303_codec.BLOCK_BYTES
        self.assertEqual(len(samples), blocks * sp303_codec.SAMPLES_PER_BLOCK)


class PageFramedAudioBytesTests(unittest.TestCase):
    def test_whole_pages(self):
        self.assertEqual(sp303_codec.page_framed_audio_bytes(512), 504)
        self.assertEqual(sp303_codec.page_framed_audio_bytes(5 * 512), 5 * 504)
        self.assertEqual(sp303_codec.page_framed_audio_bytes(0), 0)


class RdacBlockTests(unittest.TestCase):
    def test_decode_rdac_block_rejects_unsupported_unit_sizes(self):
        block = bytes(16)
        for unit_bytes in (5, 6, 7):
            with self.assertRaises(ValueError):
                rdac.decode_rdac_block(block, 0, unit_bytes=unit_bytes)

    def test_decode_rdac_block_accepts_preparsed_control(self):
        block = bytes(8)
        control = rdac.parse_control(block, unit_bytes=4)
        with_control = rdac.decode_rdac_block(block, 0, unit_bytes=4, control=control)
        without_control = rdac.decode_rdac_block(block, 0, unit_bytes=4)
        self.assertEqual(with_control, without_control)


class SharedWidthTableTests(unittest.TestCase):
    def test_unit6_widths_derive_from_shared_base_table(self):
        # The SP-303 unit-6 widths validated against hardware captures.
        expected = {
            "B": (5, 6, 5, 6, 5, 6, 5, 6),
            "C": (5, 6, 5, 7, 5, 6, 5, 5),
            "D": (4, 6, 4, 8, 4, 6, 4, 8),
            "E": (4, 6, 4, 7, 4, 6, 4, 9),
            "F": (4, 5, 4, 7, 4, 5, 4, 11),
        }
        for family, widths in expected.items():
            self.assertEqual(sp303_codec._base_widths(family), widths)

    def test_unit4_widths_derive_from_shared_base_table(self):
        self.assertEqual(rdac.PREDICTIVE_WIDTHS_UNIT4["B"], (3, 4, 3, 4, 3, 4, 3, 4))


if __name__ == "__main__":
    unittest.main()
