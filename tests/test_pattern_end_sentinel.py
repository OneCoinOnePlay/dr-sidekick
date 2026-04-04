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


if __name__ == "__main__":
    unittest.main()
