"""Native boundaries and bake invariants, using synthetic bytes and isolated files."""
import hashlib
import io
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from l4d2_bsp import native as module


def pak(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return out.getvalue()


def bsp(lumps=None, game=None, versions=None, compressed=None, revision=7):
    values = {0: b'{\n"classname" "worldspawn"\n}\n\0', 1: b'planes'}
    values.update(lumps or {})
    header = bytearray(1036)
    struct.pack_into('<4si', header, 0, b'VBSP', 21)
    struct.pack_into('<i', header, 1032, revision)
    body = bytearray()
    if game is not None:
        values[35] = b''
    for i, data in sorted(values.items()):
        offset = 1036 + len(body)
        if i == 35 and game is not None:
            directory = bytearray(struct.pack('<i', len(game)))
            payload = bytearray()
            start = offset + 4 + 16 * len(game)
            for tag, flags, version, value in game:
                directory.extend(struct.pack('<4sHHii', tag[::-1].encode(), flags,
                                             version, start + len(payload), len(value)))
                payload.extend(value)
            data = bytes(directory + payload)
        struct.pack_into('<iii4s', header, 8 + i*16,
                         (versions or {}).get(i, 1 if i == 58 else 0),
                         offset, len(data), (compressed or {}).get(i, b'\0'*4))
        body.extend(data)
    return bytes(header + body)


def vhv(color=b'\1\2\3\4'):
    header = struct.pack('<6I', 2, 123, 4, 4, 1, 1) + b'\0'*16
    mesh = struct.pack('<7I', 0, 1, 512, 0, 0, 0, 0)
    return (header + mesh).ljust(512, b'\0') + color + b'\0'*508


def detail(record=bytes(52)):
    return struct.pack('<3i', 0, 0, 1) + record


class BakeAuditTests(unittest.TestCase):
    def test_leaf_sky_flags_are_opt_in_and_reported_without_weakening_geometry(self):
        leaves = bytes(64)
        changed = bytearray(leaves)
        struct.pack_into('<H', changed, 6, 0x0200)
        struct.pack_into('<H', changed, 38, 0x0800)
        old = bsp({10: leaves}, versions={10: 1})
        new = bsp({10: bytes(changed)}, versions={10: 1})
        with self.assertRaisesRegex(ValueError, 'Protected lump'):
            module.audit_bake(old, new)
        report = module.audit_bake(old, new, allow_leaf_sky_flags=True)
        self.assertEqual(report['changed_lumps'], [10])
        self.assertTrue(report['leaf_sky_flags_changed'])
        self.assertEqual(report['leaf_sky_flags_changed_count'], 2)
        self.assertTrue(report['leaf_geometry_unchanged'])
        self.assertFalse(report['protected_lumps_unchanged'])
        self.assertEqual(report['leaf_allowed_fields'], ['flags.SKY', 'flags.SKY2D'])

    def test_leaf_exception_rejects_area_radial_unknown_flags_and_geometry(self):
        old_leaf = bytes(32)
        cases = {
            'area': (6, 0x01),
            'radial': (7, 0x04),
            'unknown flags': (7, 0x10),
            'geometry': (0, 0x01),
            'bounds': (20, 0x01),
            'cluster': (4, 0x01),
            'water': (28, 0x01),
            'padding': (31, 0x01),
        }
        old = bsp({10: old_leaf}, versions={10: 1})
        for name, (offset, value) in cases.items():
            changed = bytearray(old_leaf)
            changed[offset] ^= value
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'leaf|Leaf'):
                module.audit_bake(old, bsp({10: bytes(changed)}, versions={10: 1}),
                                  allow_leaf_sky_flags=True)

    def test_leaf_exception_rejects_invalid_layout_version_and_compression(self):
        cases = [
            (bytes(31), bytearray(bytes(31)), {10: 1}, None),
            (bytes(32), bytearray(bytes(32)), {10: 0}, None),
            (bytes(32), bytearray(bytes(32)), {10: 1}, {10: b'LZMA'}),
        ]
        for old_leaf, changed, versions, compressed in cases:
            changed[6] ^= 2
            with self.subTest(size=len(old_leaf), version=versions[10], compressed=bool(compressed)), \
                 self.assertRaisesRegex(ValueError, 'leaf|Leaf'):
                module.audit_bake(
                    bsp({10: old_leaf}, versions=versions, compressed=compressed),
                    bsp({10: bytes(changed)}, versions=versions, compressed=compressed),
                    allow_leaf_sky_flags=True,
                )

    def test_accepts_lighting_but_retains_entities_geometry_and_pak_resources(self):
        game = [('sprp', 0, 9, b'props'), ('dprp', 0, 4, detail()),
                ('dplh', 0, 0, struct.pack('<i', 1) + b'dark\0')]
        face = bytes(range(56))
        lit = bytearray(face)
        lit[16:24] = b'\0'*8  # light styles and light data offset only
        old = bsp({53: b'dark', 58: face, 40: pak([('material.vmt', b'keep'),
                                                    ('sp_hdr_0.vhv', vhv())])}, game)
        game[-1] = ('dplh', 0, 0, struct.pack('<i', 2) + b'light\0lit\0')
        new = bsp({53: b'brighter', 58: bytes(lit), 40: pak([('material.vmt', b'keep'),
                    ('sp_hdr_0.vhv', vhv(b'\4\3\2\1')), ('sp_hdr_1.vhv', vhv())])}, game)
        report = module.audit_bake(old, new)
        self.assertTrue(report['entities_byte_identical'])
        self.assertTrue(report['protected_lumps_unchanged'])
        self.assertEqual(report['changed_lumps'], [35, 40, 53, 58])
        self.assertEqual(report['pak_added'], ['sp_hdr_1.vhv'])

    def test_rejects_entity_geometry_revision_and_lump_metadata_changes(self):
        for bad in (bsp({0: b'changed'}), bsp({1: b'changed'}), bsp(revision=8),
                    bsp(versions={1: 1}), bsp(compressed={1: b'ABCD'})):
            with self.subTest(bad=bad[-12:]), self.assertRaises(ValueError):
                module.audit_bake(bsp(), bad)

    def test_every_nonlighting_face_byte_is_protected(self):
        old = bsp({58: bytes(56)})
        for index in list(range(16)) + list(range(24, 56)):
            face = bytearray(56)
            face[index] = 1
            with self.subTest(index=index), self.assertRaises(ValueError):
                module.audit_bake(old, bsp({58: bytes(face)}))

    def test_rejects_unsupported_faces_and_compressed_game_lumps(self):
        for raw in (bsp({58: b'x'}), bsp({58: bytes(56)}, versions={58: 2}),
                    bsp(game=[('sprp', 1, 9, b'compressed')])):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                module.audit_bake(raw, raw)

    def test_preserves_all_prop_bytes_metadata_and_unknown_game_lumps(self):
        original = [('sprp', 0, 9, b'props'), ('dprp', 0, 4, detail()),
                    ('abcd', 0, 1, b'unknown')]
        old = bsp(game=original)
        for index in range(3):
            for mutation in ('payload', 'version', 'remove'):
                changed = list(original)
                tag, flags, version, value = changed[index]
                if mutation == 'remove':
                    changed.pop(index)
                else:
                    changed[index] = (tag, flags, version + (mutation == 'version'),
                                      value + b'x' if mutation == 'payload' else value)
                with self.subTest(index=index, mutation=mutation), self.assertRaises(ValueError):
                    module.audit_bake(old, bsp(game=changed))

    def test_detail_prop_allows_only_verified_lighting_fields(self):
        old = bsp(game=[('dprp', 0, 4, detail())])
        for offset in range(52):
            record = bytearray(52)
            record[offset] = 1
            new = bsp(game=[('dprp', 0, 4, detail(bytes(record)))])
            with self.subTest(offset=offset):
                if 28 <= offset < 37:
                    self.assertTrue(module.audit_bake(old, new)['detail_prop_placement_unchanged'])
                else:
                    with self.assertRaises(ValueError):
                        module.audit_bake(old, new)

    def test_detail_prop_truncated_counts_and_unknown_versions_fail_closed(self):
        for raw, version in [(b'bad', 4), (struct.pack('<i', 99999), 4),
                             (detail() + b'tail', 4), (detail(), 5)]:
            with self.subTest(version=version, size=len(raw)), self.assertRaises(ValueError):
                module.audit_bake(bsp(game=[('dprp', 0, version, raw)]),
                                  bsp(game=[('dprp', 0, version, raw)]))

    def test_detail_model_and_sprite_dictionaries_cannot_change(self):
        raw = struct.pack('<i', 1) + b'model'.ljust(128, b'\0')
        raw += struct.pack('<i', 1) + bytes(32) + struct.pack('<i', 1) + bytes(52)
        old = bsp(game=[('dprp', 0, 4, raw)])
        for offset in (4, 140):
            changed = bytearray(raw)
            changed[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                module.audit_bake(old, bsp(game=[('dprp', 0, 4, bytes(changed))]))

    def test_allows_native_detail_lighting_addition_but_not_prop_addition(self):
        old = bsp(game=[('sprp', 0, 9, b'props')])
        new = bsp(game=[('sprp', 0, 9, b'props'), ('dplh', 0, 0, struct.pack('<i', 0))])
        self.assertTrue(module.audit_bake(old, new)['detail_prop_placement_unchanged'])
        with self.assertRaises(ValueError):
            module.audit_bake(old, bsp(game=[('sprp', 0, 9, b'props'), ('dprp', 0, 4, detail())]))

    def test_rejects_malformed_detail_lighting_stream(self):
        for value in (b'bad', struct.pack('<i', -1), struct.pack('<i', 99)):
            bad = bsp(game=[('dplh', 0, 0, value)])
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.audit_bake(bad, bad)

    def test_rejects_game_sublump_overlap(self):
        raw = bytearray(bsp(game=[('sprp', 0, 9, b'props'), ('dprp', 0, 4, b'detail')]))
        offset = struct.unpack_from('<i', raw, 8 + 35*16 + 4)[0]
        first = struct.unpack_from('<i', raw, offset + 12)[0]
        struct.pack_into('<i', raw, offset + 28, first)
        with self.assertRaises(ValueError):
            module.audit_bake(bytes(raw), bytes(raw))

    def test_pak_only_accepts_exact_native_vhv_names_and_preserved_headers(self):
        initial = [('material.vmt', b'keep'), ('sp_hdr_0.vhv', vhv())]
        old = bsp({40: pak(initial)})
        changed_header = bytearray(vhv())
        changed_header[4] ^= 1
        cases = [initial[:1], [('material.vmt', b'bad'), initial[1]],
                 initial + [('sp_hdr_evil.vhv', vhv())],
                 initial + [('other/sp_hdr_1.vhv', vhv())],
                 [initial[0], ('sp_hdr_0.vhv', bytes(changed_header))],
                 initial + [('sp_hdr_1.vhv', b'truncated')],
                 initial + [('MATERIAL.vmt', b'keep')]]
        for entries in cases:
            with self.subTest(names=[n for n, _ in entries]), self.assertRaises(ValueError):
                module.audit_bake(old, bsp({40: pak(entries)}))

    def test_reads_zip_payloads_to_validate_crc(self):
        raw = pak([('material.vmt', b'PAYLOAD')]).replace(b'PAYLOAD', b'CORRUPT')
        with self.assertRaises(ValueError):
            module.audit_bake(bsp({40: raw}), bsp({40: raw}))

    def test_vhv_mesh_topology_ranges_and_padding_cannot_change(self):
        old = bsp({40: pak([('sp_hdr_0.vhv', vhv())])})
        for offset in (24, 40, 44, 48, 52, 100, 1023):
            data = bytearray(vhv())
            data[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                module.audit_bake(old, bsp({40: pak([('sp_hdr_0.vhv', bytes(data))])}))


class NativeProcessTests(unittest.TestCase):
    def test_native_logs_real_output_and_refuses_existing_log(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root/'process.log'
            module.native(Path(sys.executable), ['-c', 'print("native-output")'],
                          cwd=root, log=log, timeout=5)
            self.assertIn(b'native-output', log.read_bytes())
            with self.assertRaises(FileExistsError):
                module.native(Path(sys.executable), ['-c', 'print("replace")'],
                              cwd=root, log=log, timeout=5)
            self.assertIn(b'native-output', log.read_bytes())

    def test_failure_and_timeout_raise_and_preserve_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(subprocess.CalledProcessError):
                module.native(Path(sys.executable), ['-c', 'raise SystemExit(3)'],
                              cwd=root, log=root/'failed.log', timeout=5)
            with self.assertRaises(subprocess.TimeoutExpired):
                module.native(Path(sys.executable), ['-c', 'import time; time.sleep(5)'],
                              cwd=root, log=root/'timeout.log', timeout=1)
            self.assertTrue((root/'timeout.log').is_file())

    def test_rejects_shell_script_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root/'tool.cmd'
            script.write_text('echo nope')
            with self.assertRaises(ValueError):
                module.native(script, [], cwd=root, log=root/'log', timeout=1)
            self.assertFalse((root/'log').exists())


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage, self.tools, self.game = [self.root/x for x in ('stage', 'tools', 'game')]
        self.tools.mkdir()
        self.game.mkdir()
        (self.tools/'vpk.exe').write_bytes(b'external tool fixture')
        self.payloads = {'maps/test.bsp': b'bsp-data', 'materials/custom/a.vmt': b'material'}

    def package(self):
        return module.package_files(self.payloads, stage=self.stage, stem='baseline',
                                    tools_dir=self.tools, game_dir=self.game)

    def fake_vpk(self, tool, args, *, cwd, log, timeout):
        # Simulate the two official CLI modes including its missing-parent behavior.
        if args[0] != 'x':
            source = Path(args[0])
            data = pak([(n, (source/n).read_bytes()) for n in self.payloads])
            source.with_suffix('.vpk').write_bytes(data)
        else:
            with zipfile.ZipFile(args[1]) as archive:
                for name in args[2:]:
                    (cwd/name).write_bytes(archive.read(name))

    def test_packages_and_verifies_nested_files(self):
        with patch.object(module, 'native', self.fake_vpk):
            report = self.package()
        output = Path(report['vpk'])
        self.assertEqual(report['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual({x['name'] for x in report['files']}, set(self.payloads))
        self.assertTrue(report['native_extract_verified'])
        with self.assertRaises(FileExistsError):
            self.package()

    def test_rejects_traversal_aliases_and_install_output_before_writing(self):
        for name in ('../evil', '/evil', 'C:/evil', 'maps\\evil', 'maps/../evil',
                     'maps//evil', 'maps/evil:stream', 'maps/a.', 'NUL'):
            self.payloads = {name: b'bad'}
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.package()
            self.assertFalse(self.stage.exists())
        self.payloads = {'maps/one': b'a', 'MAPS/ONE': b'b'}
        with self.assertRaises(ValueError):
            self.package()
        self.payloads = {'maps/a.bsp': b'a'}
        self.stage = self.game/'output'
        with self.assertRaises(ValueError):
            self.package()
        self.assertFalse(self.stage.exists())

    def test_corrupted_extraction_cannot_report_success(self):
        def corrupt(*args, **kwargs):
            self.fake_vpk(*args, **kwargs)
            if args[1][0] == 'x':
                (kwargs['cwd']/'maps/test.bsp').write_bytes(b'wrong')
        with patch.object(module, 'native', corrupt), self.assertRaises(ValueError):
            self.package()


if __name__ == '__main__':
    unittest.main()
