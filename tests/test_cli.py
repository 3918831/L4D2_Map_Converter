"""CLI contracts: safe outputs and independent inspection of real bytes."""
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest


ENTITIES = (b'{\n"classname" "worldspawn"\n"skyname" "sky_l4d_c2m1_hdr"\n}\n'
            b'{\n"classname" "color_correction"\n"targetname" "color_correction_main"\n'
            b'"filename" "materials/correction/cc_c2_main.raw"\n'
            b'"OnUser1" "relay\x1bTrigger\x1b\x1b0\x1b-1"\n'
            b'"OnUser1" "other\x1bTrigger\x1b\x1b0\x1b-1"\n}\n\0')


def bsp_fixture():
    header = bytearray(1036)
    struct.pack_into('<4si', header, 0, b'VBSP', 21)
    struct.pack_into('<iii4s', header, 8, 0, 1036, len(ENTITIES), b'\0'*4)
    struct.pack_into('<iii4s', header, 24, 0, 1036+len(ENTITIES), 4, b'\0'*4)
    struct.pack_into('<i', header, 1032, 1234)
    return bytes(header) + ENTITIES + b'KEEP'


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source.bsp'
        self.source.write_bytes(bsp_fixture())

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'l4d2_bsp', *map(str, args)],
                              capture_output=True, text=True, encoding='utf-8')

    def test_inspect_reports_all_lumps_and_duplicate_outputs(self):
        result = self.run_cli('inspect', self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(len(report['lumps']), 64)
        self.assertEqual(report['entity_count'], 2)
        self.assertEqual(report['io_count'], 2)
        self.assertEqual(report['revision'], 1234)

    def test_roundtrip_preserves_entire_file(self):
        out = self.root/'copy.bsp'
        result = self.run_cli('roundtrip', self.source, '--output', out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(out.read_bytes(), self.source.read_bytes())

    def test_roundtrip_refuses_to_overwrite_source_or_existing_output(self):
        out = self.root/'exists.bsp'
        out.write_bytes(b'USER CONTENT')
        for target in (self.source, out):
            result = self.run_cli('roundtrip', self.source, '--output', target)
            self.assertNotEqual(result.returncode, 0)
        self.assertEqual(out.read_bytes(), b'USER CONTENT')
        self.assertEqual(self.source.read_bytes(), bsp_fixture())

    def patch_args(self, out, report):
        return ('patch', self.source, '--output', out, '--report', report,
                '--classname', 'color_correction', '--targetname', 'color_correction_main',
                '--key', 'filename', '--expected', 'materials/correction/cc_c2_main.raw',
                '--value', 'materials/correction/cc_c5_main.raw')

    def test_patch_reports_actual_single_byte_change_and_unchanged_io(self):
        out, log = self.root/'patched.bsp', self.root/'change.json'
        result = self.run_cli(*self.patch_args(out, log))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(out.read_bytes(), bsp_fixture().replace(b'cc_c2_main.raw', b'cc_c5_main.raw'))
        data = json.loads(log.read_text(encoding='utf-8'))
        self.assertTrue(data['verification']['io_unchanged'])
        self.assertTrue(data['verification']['outside_patch_unchanged'])
        self.assertEqual(data['comparison']['changed_byte_count'], 1)
        self.assertEqual(data['comparison']['changed_lumps'], [0])

    def test_report_collision_does_not_create_output(self):
        out = self.root/'patched.bsp'
        result = self.run_cli(*self.patch_args(out, self.source))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists())
        self.assertEqual(self.source.read_bytes(), bsp_fixture())

    def test_compare_detects_non_entity_tampering(self):
        out = self.root/'tampered.bsp'
        out.write_bytes(bsp_fixture()[:-4] + b'FAIL')
        result = self.run_cli('compare', self.source, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['changed_lumps'], [1])
        self.assertFalse(data['non_entity_lumps_unchanged'])


if __name__ == '__main__':
    unittest.main()
