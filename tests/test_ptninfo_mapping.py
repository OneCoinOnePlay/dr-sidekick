import unittest

from dr_sidekick.engine.core import PTNData, PTNInfo, PatternSlot


class PatternSlotMappingTests(unittest.TestCase):
    def test_pattern_slot_parses_hardware_mapping_entry(self):
        slot = PatternSlot.from_bytes(0, bytes.fromhex("b0040201"))

        self.assertEqual(slot.length_bars, 2)
        self.assertEqual(slot.pattern_index, 1)
        self.assertFalse(slot.has_pattern)

    def test_pattern_slot_serializes_mapping_entry(self):
        slot = PatternSlot(slot_index=0, length_bars=2, pattern_index=1)

        self.assertEqual(slot.to_bytes(), bytes.fromhex("b0040201"))

    def test_ptninfo_set_pattern_writes_bars_and_mapping_index(self):
        ptninfo = PTNInfo()

        ptninfo.set_pattern(0, bars=2, pattern_index=1)

        self.assertEqual(ptninfo.to_bytes()[:4], bytes.fromhex("b0040201"))


class PTNDataOccupancyTests(unittest.TestCase):
    def test_slot_has_serialized_events_detects_short_hardware_pattern(self):
        ptndata = PTNData()
        slot_offset = ptndata.get_slot_offset(1)
        data_offset = slot_offset + 0x70
        tuple_zone_end = slot_offset + 0x272

        fill = bytes.fromhex("07030600ff80")
        payload = [
            bytes.fromhex("000000000080"),
            bytes.fromhex("07030600ff11"),
            bytes.fromhex("7f000000ff80"),
        ]
        cursor = data_offset
        for chunk in payload:
            ptndata.data[cursor:cursor + 6] = chunk
            cursor += 6
        while cursor + 6 <= tuple_zone_end:
            ptndata.data[cursor:cursor + 6] = fill
            cursor += 6

        self.assertTrue(ptndata.slot_has_serialized_events(1))

    def test_slot_has_serialized_events_rejects_immediate_fill_slot(self):
        ptndata = PTNData()
        slot_offset = ptndata.get_slot_offset(9)
        data_offset = slot_offset + 0x70
        tuple_zone_end = slot_offset + 0x272

        payload = [
            bytes.fromhex("00000000ff80"),
            bytes.fromhex("07031100ff80"),
        ]
        cursor = data_offset
        for chunk in payload:
            ptndata.data[cursor:cursor + 6] = chunk
            cursor += 6
        while cursor + 6 <= tuple_zone_end:
            ptndata.data[cursor:cursor + 6] = payload[-1]
            cursor += 6

        self.assertFalse(ptndata.slot_has_serialized_events(9))


if __name__ == "__main__":
    unittest.main()
