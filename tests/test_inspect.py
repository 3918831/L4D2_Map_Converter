"""Inspection must separate unverified structures from verified payload data."""

import hashlib
import struct
import unittest

from l4d2_bsp.binary import BspFile, LumpFile
from l4d2_bsp.inspect import compare_bytes, entity_list, inspect_bytes
from tests.test_core import make_bsp, make_lmp


def game_lump_fixture(*, flags=0, offset=None, length=8):
    data = bytearray(make_bsp())
    game_start = len(data)
    directory = struct.pack('<i4sHHii', 1, b'prps', flags, 10,
                            game_start + 20 if offset is None else offset, length)
    struct.pack_into('<iii4s', data, 8 + 35 * 16, 0, game_start, 28, b'\0' * 4)
    return bytes(data) + directory + b'PROPdata' + b'OUTSIDE!'


class InspectionTests(unittest.TestCase):
    def test_lmp_header_and_padding_changes_do_not_claim_entity_payload_change(self):
        original = make_lmp()
        header_changed = bytearray(original)
        struct.pack_into('<i', header_changed, 16, 124)
        cases = [
            (bytes(header_changed), [], False),
            (original[:-7] + b'changed', [], True),
            (original[:20] + b'EDIT' + original[24:], [], True),
            (original.replace(b'cc_c2_main.raw', b'cc_c5_main.raw'), [0], True),
        ]
        for changed, expected_lumps, header_unchanged in cases:
            with self.subTest(changed=changed[:24]):
                report = compare_bytes(original, changed, kind='lmp')
                self.assertEqual(report['changed_lumps'], expected_lumps)
                self.assertEqual(report['header_unchanged'], header_unchanged)
                self.assertFalse(report['identical'])

    def test_game_lump_hash_only_covers_valid_uncompressed_subpayload(self):
        item, = inspect_bytes(game_lump_fixture())['game_lumps']
        self.assertEqual(item['id'], 'sprp')
        self.assertEqual(item['sha256'], hashlib.sha256(b'PROPdata').hexdigest())
        empty, = inspect_bytes(game_lump_fixture(offset=0, length=0))['game_lumps']
        self.assertEqual(empty['sha256'], hashlib.sha256(b'').hexdigest())

    def test_game_lump_rejects_header_directory_outside_and_negative_ranges(self):
        game_start = len(make_bsp())
        for flags, offset, length in [
            (0, 0, 8), (0, 1036, 8), (0, game_start, 8),
            (0, game_start + 19, 8), (0, game_start + 26, 8),
            (0, game_start + 28, 8), (0, -1, 0),
            (1, -1, 8), (1, game_start + 20, -1),
            (1, 0, 8), (1, game_start + 28, 8),
            (1, game_start + 100, 8),
        ]:
            with self.subTest(flags=flags, offset=offset, length=length):
                report = inspect_bytes(game_lump_fixture(flags=flags, offset=offset, length=length))
                self.assertIn('game_lump_error', report)
                self.assertNotIn('game_lumps', report)

    def test_compressed_game_sublump_keeps_declared_size_without_decoded_hash(self):
        item, = inspect_bytes(game_lump_fixture(flags=1, length=999999))['game_lumps']
        self.assertIn('unsupported', item)
        self.assertNotIn('sha256', item)
        self.assertEqual(item['length'], 999999)

    def test_entity_reader_rejects_unsupported_versions_in_bsp_and_lmp(self):
        for kind, original, parser in [('bsp', make_bsp(), BspFile), ('lmp', make_lmp(), LumpFile)]:
            changed = bytearray(original)
            struct.pack_into('<i', changed, 8, 99)
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    entity_list(parser.parse(bytes(changed)))

    def test_unsupported_entity_version_has_no_verified_io_inventory_or_comparison(self):
        for kind, original in [('bsp', make_bsp()), ('lmp', make_lmp())]:
            changed = bytearray(original)
            struct.pack_into('<i', changed, 8, 99)
            with self.subTest(kind=kind):
                report = inspect_bytes(bytes(changed), kind=kind)
                self.assertIn('entity_error', report.keys())
                self.assertNotIn('io_count', report)
                self.assertNotIn('io', report)
                comparison = compare_bytes(original, bytes(changed), kind=kind)
                self.assertIn('entity_comparison_error', comparison)
                self.assertIsNone(comparison['io_unchanged'])


if __name__ == '__main__':
    unittest.main()
