import unittest

from dr_sidekick.engine import Event, PTNData


class PatternEndSentinelTests(unittest.TestCase):
    def test_write_pattern_places_end_sentinel_after_event_stream(self):
        ptndata = PTNData()
        events = [Event(tick=i * 24, pad=0x18, velocity=0x7F) for i in range(16)]

        ptndata.write_pattern(0, events, total_length_ticks=384)

        slot_offset = ptndata.get_slot_offset(0)
        data_offset = slot_offset + 0x70
        serialized = ptndata.encode_events(events, total_length_ticks=384)
        sentinel_offset = data_offset + len(serialized)

        self.assertEqual(
            ptndata.data[sentinel_offset:sentinel_offset + 6],
            bytes([0xFF, 0x80, 0x00, 0x00, 0x00, 0x00]),
        )

    def test_legacy_writer_places_loop_closure_in_rest_tuple_before_sentinel(self):
        ptndata = PTNData()
        events = [
            Event(tick=tick, pad=0x10, velocity=0x7F)
            for tick in (
                0, 24, 48, 72, 95, 120, 144, 167,
                192, 216, 239, 264, 288, 312, 335, 360,
                384, 408, 432, 455, 480, 504, 528, 551,
                576, 600, 624, 648, 672, 695, 720, 744,
            )
        ]

        serialized = ptndata.encode_events(events, total_length_ticks=768)

        self.assertEqual(serialized[-12:], bytes.fromhex("00107f000000188010000000"))


if __name__ == "__main__":
    unittest.main()
