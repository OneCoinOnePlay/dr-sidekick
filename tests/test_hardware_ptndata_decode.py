import unittest

from dr_sidekick.engine.core import Event, PTNData


class HardwarePTNDataDecodeTests(unittest.TestCase):
    HARDWARE_FILL = bytes([0x00, 0x00, 0x00, 0x00, 0x10, 0x80])

    def _write_hardware_stream(self, stream: bytes) -> PTNData:
        ptndata = PTNData()
        slot_offset = ptndata.get_slot_offset(0)
        data_offset = slot_offset + 0x70
        tuple_zone_end = slot_offset + 0x272
        ptndata.data[data_offset:tuple_zone_end] = bytes(tuple_zone_end - data_offset)
        ptndata.data[data_offset:data_offset + len(stream)] = stream
        return ptndata

    def test_07031100_zero_delta_repeats_previous_note_step(self):
        ptndata = PTNData()
        slot_offset = ptndata.get_slot_offset(0)
        data_offset = slot_offset + 0x70
        tuple_zone_end = slot_offset + 0x272
        ptndata.data[data_offset:tuple_zone_end] = bytes(tuple_zone_end - data_offset)

        stream = (
            bytes([0x00, 0x00, 0x00, 0x00, 0xFF, 0x80])
            + bytes([0x07, 0x03, 0x11, 0x00])
            + bytes([24, 0x10, 0x7F, 0x00, 0x00, 0x00])
            + bytes([0, 0x10, 0x7F, 0x00, 0x00, 0x00])
            + bytes([24, 0x11, 0x7F, 0x00, 0x00, 0x00])
            + bytes([0xFF, 0x80, 0x07, 0x03, 0x11, 0x00])
        )
        ptndata.data[data_offset:data_offset + len(stream)] = stream

        events = ptndata.decode_events(0)

        self.assertEqual(
            [(event.tick, event.pad, event.velocity) for event in events],
            [
                (0, 0x10, 0x7F),
                (24, 0x10, 0x7F),
                (48, 0x11, 0x7F),
            ],
        )

    def test_encode_events_closes_loop_with_rest_after_final_note(self):
        ptndata = PTNData()

        serialized = ptndata.encode_events(
            [Event(tick=0, pad=0x10, velocity=0x7F)],
            total_length_ticks=96,
        )

        self.assertEqual(
            serialized,
            bytes([
                0x00, 0x00, 0x00, 0x00, 0x00, 0x80,
                0x04, 0x03, 0x16, 0x00,
                0x00, 0x10, 0x7F, 0x00, 0x00, 0x00,
                96, 0x80, 0x10, 0x00, 0x00, 0x00,
            ]),
        )

    def test_write_pattern_places_fill_tuple_immediately_after_loop_rest(self):
        ptndata = PTNData()
        ptndata.write_pattern(
            0,
            [Event(tick=0, pad=0x10, velocity=0x7F)],
            total_length_ticks=96,
        )

        slot_offset = ptndata.get_slot_offset(0)
        data_offset = slot_offset + 0x70
        stream_end = data_offset + 22

        self.assertEqual(
            bytes(ptndata.data[stream_end:stream_end + 6]),
            bytes([0xFF, 0x80, 0x00, 0x00, 0x10, 0x00]),
        )

    def test_hardware_decoder_promotes_same_pad_a_to_b_to_derived_span(self):
        ptndata = self._write_hardware_stream(
            bytes([0xFF, 0x03, 0x00, 0x00, 24, 0x10])
            + bytes([0x7F, 0x00, 0x1B, 0x00, 48, 0x10])
            + (self.HARDWARE_FILL * 8)
        )

        events, debug = ptndata.decode_events_with_debug(0)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].tick, 0)
        self.assertEqual(events[0].pad, 0x10)
        self.assertEqual(events[0].duration_ticks, 24)
        self.assertEqual(events[0].render_style, "span")
        self.assertEqual(events[0].source_tuple_indices, (0, 1))
        self.assertEqual([tuple_info.family for tuple_info in debug[:2]], ["A", "B"])

    def test_hardware_decoder_promotes_same_pad_a_to_c_to_derived_span(self):
        ptndata = self._write_hardware_stream(
            bytes([0xFF, 0x03, 0x00, 0x00, 24, 0x10])
            + bytes([0x7F, 0x00, 0x2E, 0x00, 72, 0x10])
            + (self.HARDWARE_FILL * 8)
        )

        events, debug = ptndata.decode_events_with_debug(0)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].duration_ticks, 24)
        self.assertEqual(events[0].render_style, "span")
        self.assertEqual([tuple_info.family for tuple_info in debug[:2]], ["A", "C"])

    def test_hardware_decoder_keeps_a_to_d_as_unresolved_occupied_steps(self):
        ptndata = self._write_hardware_stream(
            bytes([0xFF, 0x03, 0x00, 0x00, 24, 0x10])
            + bytes([0x7F, 0x00, 0x20, 0x00, 72, 0x10])
            + (self.HARDWARE_FILL * 8)
        )

        events, debug = ptndata.decode_events_with_debug(0)

        self.assertEqual(
            [(event.tick, event.render_style, event.duration_ticks) for event in events],
            [(0, "step", 0), (24, "step", 0)],
        )
        self.assertEqual([tuple_info.family for tuple_info in debug[:2]], ["A", "D"])


if __name__ == "__main__":
    unittest.main()
