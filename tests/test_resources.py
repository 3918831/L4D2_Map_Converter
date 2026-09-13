import struct
import tempfile
from pathlib import Path
import unittest

from l4d2_bsp.resources import lookup_resources, vpk_index


def vpk_fixture(names, version=1):
    tree = bytearray()
    for name in names:
        path = Path(name)
        tree += path.suffix[1:].encode() + b'\0'
        tree += path.parent.as_posix().encode() + b'\0'
        tree += path.stem.encode() + b'\0'
        tree += struct.pack('<IHHIIH', 0, 4, 0x7fff, 0, 0, 0xffff) + b'DATA'
        tree += b'\0\0'
    tree += b'\0'
    return struct.pack('<III', 0x55aa1234, version, len(tree)) + (b'\0'*16 if version == 2 else b'') + tree


class ResourceTests(unittest.TestCase):
    def test_v2_internal_payload_cannot_use_checksum_section(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            path = root/'pak01_dir.vpk'
            name = 'materials/correction/cc_c5_main.raw'
            raw = bytearray(vpk_fixture([name], version=2).replace(
                struct.pack('<IHHIIH', 0, 4, 0x7fff, 0, 0, 0xffff),
                struct.pack('<IHHIIH', 0, 4, 0x7fff, 0, 4, 0xffff)))
            struct.pack_into('<4I', raw, 12, 0, 28, 0, 0)
            path.write_bytes(raw + b'C'*28)
            report = lookup_resources([root], [name])
            self.assertFalse(report['resources'][name]['found'])
            self.assertEqual(vpk_index(path)[name]['file_data_section_size'], 0)

    def test_v2_payload_data_and_external_archive_bounds(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            path = root/'pak01_dir.vpk'
            name = 'materials/correction/cc_c5_main.raw'
            for archive, section_size, offset, expected in ((0x7fff, 4, 0, True),
                    (0x7fff, 4, 1, False), (1, 0, 0, True), (1, 0, 1, False)):
                with self.subTest(archive=archive, offset=offset):
                    raw = bytearray(vpk_fixture([name], version=2).replace(
                        struct.pack('<IHHIIH', 0, 4, 0x7fff, 0, 0, 0xffff),
                        struct.pack('<IHHIIH', 0, 4, archive, offset, 4, 0xffff)))
                    struct.pack_into('<4I', raw, 12, section_size, 28, 0, 0)
                    path.write_bytes(raw+b'D'*section_size+b'C'*28)
                    (root/'pak01_001.vpk').write_bytes(b'EXT!')
                    report = lookup_resources([root], [name])
                    self.assertEqual(report['resources'][name]['found'], expected)

    def test_reads_v1_v2_entries_and_preload(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'pak01_dir.vpk'
            for version in (1, 2):
                path.write_bytes(vpk_fixture(['materials/correction/cc_c5_main.raw'], version))
                entry = vpk_index(path)['materials/correction/cc_c5_main.raw']
                self.assertEqual(entry['preload_bytes'], 4)
                self.assertEqual(entry['archive_index'], 0x7fff)
                self.assertEqual(entry['package'], str(path.resolve()))

    def test_refuses_invalid_header_and_truncated_tree(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'pak01_dir.vpk'
            for data in (b'bad', vpk_fixture(['a/b.raw'])[:-2], vpk_fixture([], 3)):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    vpk_index(path)

    def test_opaque_unrelated_filename_does_not_hide_ascii_resource(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'pak01_dir.vpk'
            path.write_bytes(vpk_fixture(['x/nonascii.raw', 'materials/correction/cc_c5_main.raw']).replace(b'nonascii', b'nonasc\xffi'))
            self.assertIn('materials/correction/cc_c5_main.raw', vpk_index(path))

    def test_loose_resource_is_hashed_and_missing_archive_is_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            name = 'materials/correction/cc_c5_main.raw'
            loose = root/name
            loose.parent.mkdir(parents=True)
            loose.write_bytes(b'LUT BYTES')
            report = lookup_resources([root], [name])
            self.assertTrue(report['resources'][name]['found'])
            candidate = report['resources'][name]['candidates'][0]
            self.assertEqual(candidate['kind'], 'loose_file')
            self.assertEqual(candidate['size'], 9)
            self.assertEqual(len(candidate['sha256']), 64)
            loose.unlink()
            raw = vpk_fixture([name])
            raw = raw.replace(struct.pack('<IHHIIH', 0, 4, 0x7fff, 0, 0, 0xffff), struct.pack('<IHHIIH', 0, 4, 1, 0, 10, 0xffff))
            (root/'pak01_dir.vpk').write_bytes(raw)
            report = lookup_resources([root], [name])
            self.assertFalse(report['resources'][name]['found'])
