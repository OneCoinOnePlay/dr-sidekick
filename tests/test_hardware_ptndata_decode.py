import unittest

from dr_sidekick.engine.core import PTNData


class HardwarePTNDataDecodeTests(unittest.TestCase):
    def _ptndata_with_slot(self, slot_index: int, tuples: list[str]) -> PTNData:
        ptndata = PTNData()
        slot_offset = ptndata.get_slot_offset(slot_index)
        data_offset = slot_offset + 0x70
        tuple_zone_end = slot_offset + 0x272
        fill = bytes.fromhex(tuples[-1])

        cursor = data_offset
        for chunk in tuples:
            payload = bytes.fromhex(chunk)
            ptndata.data[cursor:cursor + 6] = payload
            cursor += 6
        while cursor + 6 <= tuple_zone_end:
            ptndata.data[cursor:cursor + 6] = fill
            cursor += 6
        return ptndata

    def test_decodes_hardware_authored_one_bar_quarter_notes(self):
        ptndata = self._ptndata_with_slot(
            0,
            [
                "000000000080",
                "070306006010",
                "7f0000006010",
                "7f0000003f10",
                "7f0000002180",
                "07030600de10",
                "7f000000ff80",
                "07030600ff80",
            ],
        )

        events = ptndata.decode_events(0)

        self.assertEqual([(e.tick, e.pad) for e in events], [(0, 0x10), (96, 0x10), (192, 0x10), (288, 0x10)])

    def test_decodes_hardware_authored_one_bar_single_hit(self):
        ptndata = self._ptndata_with_slot(
            1,
            [
                "000000000080",
                "07030600ff11",
                "7f000000ff80",
                "07030600ff80",
            ],
        )

        events = ptndata.decode_events(1)

        self.assertEqual([(e.tick, e.pad) for e in events], [(0, 0x11)])

    def test_decodes_hardware_authored_multi_bar_loop_counts(self):
        fixtures = [
            (
                10,
                0x1A,
                3,
                [
                    "000000000080",
                    "00000000ff1a",
                    "7f0000007e80",
                    "00000000811a",
                    "7f000100ff80",
                    "000000000080",
                    "00000000ff1a",
                    "7f000000ff80",
                    "00000000ff80",
                ],
            ),
            (
                12,
                0x1A,
                4,
                [
                    "000000000180",
                    "00000000fe1a",
                    "7f0000007680",
                    "00000000891a",
                    "7f030000fb80",
                    "00000000041a",
                    "7f000000ff80",
                    "000000007980",
                    "00000000861a",
                    "7f000000ff80",
                    "00000000ff80",
                ],
            ),
            (
                14,
                0x1F,
                5,
                [
                    "000000000080",
                    "00000000ff1f",
                    "7f0001007e80",
                    "00000000811f",
                    "7f000100f880",
                    "00000000071f",
                    "7f000000ff80",
                    "000000008180",
                    "000000007e1f",
                    "7f000000ff80",
                    "000000000380",
                    "00000000fc1f",
                    "7f000000ff80",
                    "00000000ff80",
                ],
            ),
        ]

        for slot_index, expected_pad, expected_count, tuples in fixtures:
            ptndata = self._ptndata_with_slot(slot_index, tuples)

            events = ptndata.decode_events(slot_index)

            self.assertEqual(len(events), expected_count)
            self.assertTrue(all(event.pad == expected_pad for event in events))
            self.assertEqual(sorted(event.tick for event in events), [event.tick for event in events])

    def test_decodes_slot9_hardware_loop_without_dropping_boundary_hit(self):
        ptndata = self._ptndata_with_slot(
            9,
            [
                "000000000080",
                "00000000ff19",
                "7f0300008680",
                "000000007919",
                "7f000000ff80",
                "000000000080",
                "00000000ff19",
                "7f000000ff80",
                "00000000ff80",
            ],
        )

        events = ptndata.decode_events(9)

        self.assertGreaterEqual(len(events), 2)
        self.assertTrue(all(event.pad == 0x19 for event in events))

    def test_decodes_legacy_app_authored_pattern(self):
        ptndata = PTNData()
        slot_offset = ptndata.get_slot_offset(0)
        data_offset = slot_offset + 0x70
        payload = bytes.fromhex(
            "00000000088004031600"
            "60107f000000"
            "60107f000000"
            "60107f000000"
            "60107f000000"
            "ff8000000000"
        )
        ptndata.data[data_offset:data_offset + len(payload)] = payload

        events = ptndata.decode_events(0)

        self.assertEqual([(e.tick, e.pad) for e in events], [(0, 0x10), (96, 0x10), (192, 0x10), (288, 0x10)])


if __name__ == "__main__":
    unittest.main()
