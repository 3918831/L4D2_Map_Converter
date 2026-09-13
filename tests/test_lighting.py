import struct
import unittest

from tests.test_cli import bsp_fixture


def with_light(rgb):
    raw = bsp_fixture()
    offset, length = struct.unpack_from('<ii', raw, 12)
    text = raw[offset:offset+length].rstrip(b'\0')
    text += (b'{\n"classname" "light_environment"\n"_light" "'+rgb+
             b'"\n"_ambient" "106 160 193 3"\n"_lightHDR" "-1 -1 -1 1"\n'
             b'"angles" "0 100 0"\n"pitch" "-15"\n}\n\0')
    header = bytearray(raw[:1036])
    struct.pack_into('<ii', header, 12, 1036, len(text))
    struct.pack_into('<ii', header, 28, 1036+len(text), 4)
    return bytes(header)+text+b'KEEP'


class LightingTests(unittest.TestCase):
    def test_longer_light_value_preserves_other_lumps_and_outputs(self):
        from l4d2_bsp.lighting import prepare_environment_input
        from l4d2_bsp.binary import BspFile
        source = with_light(b'220 170 114 2')
        reference = with_light(b'209 154 109 1000')
        prepared, report = prepare_environment_input(source, reference)
        old, new = BspFile.parse(source), BspFile.parse(prepared)
        self.assertIn(b'"_light" "209 154 109 1000"', new.lump_bytes(0))
        self.assertEqual(new.lump_bytes(1), b'KEEP')
        self.assertTrue(report['io_unchanged'])
        self.assertEqual([i for i in range(64) if old.lump_bytes(i) != new.lump_bytes(i)], [0])
        self.assertEqual(prepared[1036:len(source)], source[1036:])
        self.assertEqual(report['status'], 'compiler_input_only')

    def test_reference_without_environment_is_rejected(self):
        from l4d2_bsp.lighting import prepare_environment_input
        with self.assertRaises(ValueError):
            prepare_environment_input(with_light(b'220 170 114 2'), bsp_fixture())

    def test_non_numeric_light_input_is_rejected(self):
        from l4d2_bsp.lighting import prepare_environment_input
        with self.assertRaises(ValueError):
            prepare_environment_input(with_light(b'220 170 114 2'), with_light(b'bad values'))

    def test_baseline_reference_is_byte_identical(self):
        from l4d2_bsp.lighting import prepare_environment_input
        data = with_light(b'220 170 114 2')
        prepared, report = prepare_environment_input(data, data)
        self.assertEqual(prepared, data)
        self.assertEqual(report['changes'], [])


if __name__ == '__main__':
    unittest.main()
