import struct
import unittest

from l4d2_bsp.binary import BspFile, LumpFile
from l4d2_bsp.entities import parse_entities
from l4d2_bsp.patch import patch_visual


HEADER_SIZE = 1036
OLD = "materials/correction/cc_c2_main.raw"
NEW = "materials/correction/cc_c5_main.raw"
ENTITY = (b'// fixture\n{\n"classname" "color_correction"\n'
          b'"targetname" "color_correction_main"\n'
          b'"filename" "' + OLD.encode() + b'"\n'
          b'"OnMapSpawn" "a\x1bEnable\x1b\x1b0\x1b-1"\n'
          b'"OnMapSpawn" "b,Disable,,1,-1"\n}\n\0\0')


def make_bsp(entities=ENTITY, fourcc=b'\0' * 4):
    data = bytearray(HEADER_SIZE)
    struct.pack_into('<4si', data, 0, b'VBSP', 21)
    struct.pack_into('<iii4s', data, 8, 0, HEADER_SIZE + 7, len(entities), fourcc)
    struct.pack_into('<iii4s', data, 24, 2, HEADER_SIZE, 7, b'\0' * 4)
    struct.pack_into('<i', data, HEADER_SIZE - 4, 123)
    return bytes(data) + b'PAYLOAD' + entities + b'trailer'


def make_lmp(entities=ENTITY):
    return struct.pack('<5i', 24, 0, 0, len(entities), 123) + b'GAP!' + entities + b'trailer'


class CoreTests(unittest.TestCase):
    BspFile = BspFile
    LumpFile = LumpFile
    parse_entities = staticmethod(parse_entities)
    patch_visual = staticmethod(patch_visual)

    def patch(self, data=None, **kwargs):
        options = dict(kind='bsp', classname='color_correction',
                       targetname='color_correction_main', key='filename',
                       expected=OLD, value=NEW)
        options.update(kwargs)
        return self.patch_visual(make_bsp() if data is None else data, **options)

    def test_bsp_reads_l4d2_directory_order_and_preserves_bytes(self):
        data = make_bsp()
        parsed = self.BspFile.parse(data)
        self.assertEqual(parsed.data, data)
        self.assertEqual((parsed.version, parsed.revision), (21, 123))
        self.assertEqual(parsed.entity_span, (1043, len(ENTITY)))
        self.assertEqual(len(parsed.lumps), 64)
        self.assertEqual(parsed.lumps[1].version, 2)
        self.assertEqual(parsed.lumps[1].fourcc, b'\0' * 4)
        self.assertEqual(parsed.lump_bytes(1), b'PAYLOAD')
        self.assertEqual(parsed.lump_bytes(2), b'')
        with self.assertRaises((ValueError, IndexError)):
            parsed.lump_bytes(-1)

    def test_bsp_rejects_malformed_header_version_ranges_and_overlap(self):
        malformed = [b'', make_bsp()[:1035], b'XXXX' + make_bsp()[4:]]
        for position, value in [(4, 20), (12, -1), (16, -1), (12, 2),
                                (12, 999999), (16, 999999), (28, 1043)]:
            data = bytearray(make_bsp())
            struct.pack_into('<i', data, position, value)
            malformed.append(bytes(data))
        for data in malformed:
            with self.subTest(data=data[:24]), self.assertRaises(ValueError):
                self.BspFile.parse(data)

    def test_lmp_preserves_header_gaps_and_tail(self):
        data = make_lmp()
        parsed = self.LumpFile.parse(data)
        self.assertEqual(parsed.data, data)
        self.assertEqual(parsed.entity_span, (24, len(ENTITY)))
        self.assertEqual((parsed.lump_id, parsed.version, parsed.revision), (0, 0, 123))

    def test_lmp_rejects_other_lumps_and_invalid_ranges(self):
        for position, value in [(0, 19), (0, 999999), (4, 1), (12, -1), (12, 999999)]:
            data = bytearray(make_lmp())
            struct.pack_into('<i', data, position, value)
            with self.subTest(position=position, value=value), self.assertRaises(ValueError):
                self.LumpFile.parse(bytes(data))
        with self.assertRaises(ValueError):
            self.LumpFile.parse(b'\0' * 19)

    def test_entity_offsets_duplicate_outputs_and_latin1_are_lossless(self):
        data = ENTITY.replace(b'b,Disable', b'\xff,Disable')
        entity, = self.parse_entities(data)
        self.assertEqual(entity.one('classname'), 'color_correction')
        self.assertIsNone(entity.one('missing'))
        self.assertEqual(entity.values('OnMapSpawn'), ['a\x1bEnable\x1b\x1b0\x1b-1', '\xff,Disable,,1,-1'])
        with self.assertRaises(ValueError):
            entity.one('OnMapSpawn')
        for pair in entity.pairs:
            self.assertEqual(data[pair.value_start:pair.value_end], pair.value.encode('latin1'))

    def test_empty_and_multiple_entity_blocks_comments_and_padding(self):
        entities = self.parse_entities(b'// comment\n{}\r\n{"x" "//value"}\n\0 \r\n\0')
        self.assertEqual(len(entities), 2)
        self.assertEqual(entities[1].one('x'), '//value')
        self.assertEqual(self.parse_entities(b'\0\0'), [])

    def test_entities_reject_malformed_token_stream(self):
        for data in [b'{', b'}', b'{"x"}', b'{"x" "unfinished}',
                     b'{ x "y" }', b'{"x" "y"', b'{}garbage',
                     b'{}\0{"x" "y"}', b'{"x" "a\0b"}',
                     b'{"x" "a\\"b"}', b'{{}}']:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.parse_entities(data)

    def test_patch_only_touches_exact_value_span_and_keeps_io(self):
        original = make_bsp()
        changed, report = self.patch(original)
        start = original.index(OLD.encode())
        self.assertEqual((report['start'], report['end']), (start, start + len(OLD)))
        self.assertEqual(changed, original[:start] + NEW.encode() + original[start + len(OLD):])
        self.assertEqual(len(changed), len(original))
        self.assertEqual(report['entity_index'], 0)
        self.assertEqual(report['old'], OLD)
        self.assertEqual(report['new'], NEW)
        self.assertEqual(report['classname'], 'color_correction')
        self.assertEqual(report['targetname'], 'color_correction_main')
        self.assertEqual(report['key'], 'filename')

    def test_patch_lmp_uses_absolute_offset(self):
        original = make_lmp()
        changed, report = self.patch(original, kind='lmp')
        self.assertEqual(report['start'], original.index(OLD.encode()))
        self.assertEqual(changed[:24], original[:24])
        self.assertEqual(changed, original.replace(OLD.encode(), NEW.encode()))

    def test_patch_requires_one_entity_one_key_and_expected_old_value(self):
        blocks = ENTITY.rstrip(b'\0')
        cases = [dict(targetname='missing'), dict(expected='wrong'),
                 dict(data=make_bsp(blocks + ENTITY)),
                 dict(data=make_bsp(ENTITY.replace(b'"filename"', b'"filename" "duplicate"\n"filename"'))),
                 dict(data=make_bsp(ENTITY.replace(b'"classname"', b'"classname" "color_correction"\n"classname"'))),
                 dict(data=make_bsp(ENTITY.replace(b'"filename"', b'"otherkey"')))]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.patch(**kwargs)

    def test_patch_rejects_length_changes_and_injection(self):
        for value in [NEW + 'x', '', '"' + NEW[1:], '\\' + NEW[1:],
                      '\0' + NEW[1:], '\n' + NEW[1:], '\x1b' + NEW[1:],
                      '\x7f' + NEW[1:], '\u4e2d' + NEW[1:],
                      '../' + NEW[3:], 'C:/' + NEW[3:]]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.patch(value=value)

    def test_patch_rejects_non_whitelist_kind_and_compressed_entities(self):
        for kwargs in [dict(key='OnMapSpawn'), dict(classname='logic_auto'),
                       dict(kind='unknown'), dict(data=make_bsp(fourcc=b'\x01\0\0\0')),
                       dict(data=make_bsp(b'LZMA' + ENTITY))]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.patch(**kwargs)

    def test_patch_rejects_unknown_entity_schema_version(self):
        for kind, original, version_offset in [('bsp', make_bsp(), 8), ('lmp', make_lmp(), 8)]:
            data = bytearray(original)
            struct.pack_into('<i', data, version_offset, 1)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.patch(bytes(data), kind=kind)

    def test_rgb_and_sky_mutations_support_class_only_selection(self):
        for classname in ['env_fog_controller', 'sky_camera']:
            for key in ['fogcolor', 'fogcolor2']:
                data = make_bsp(f'{{"classname" "{classname}" "{key}" "100 100 100"}}'.encode())
                changed, _ = self.patch(data, classname=classname, targetname=None,
                                        key=key, expected='100 100 100', value='090 120 150')
                self.assertIn(b'090 120 150', changed)
                with self.assertRaises(ValueError):
                    self.patch(data, classname=classname, targetname=None,
                               key=key, expected='100 100 100', value='999 100 100')
        data = make_bsp(b'{"classname" "worldspawn" "skyname" "sky_l4d_c2m1_hdr"}')
        changed, _ = self.patch(data, classname='worldspawn', targetname=None,
                                key='skyname', expected='sky_l4d_c2m1_hdr', value='sky_l4d_c5m1_hdr')
        self.assertIn(b'"sky_l4d_c5m1_hdr"', changed)


if __name__ == '__main__':
    unittest.main()
