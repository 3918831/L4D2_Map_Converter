"""Synthetic reflection provenance and bounded native VTF regression tests."""

import importlib.util
import io
import math
import struct
import unittest
import zipfile


OLD = 'materials/maps/source/'
NEW = 'materials/maps/capture_run/'
TAIL = 'c1_-2_3.hdr.vtf'


def vtf(*, width=16, rgb=1.0, alpha=1.0):
    header = bytearray(96)
    struct.pack_into('<4sIII', header, 0, b'VTF\0', 7, 4, 96)
    struct.pack_into('<HHIHH', header, 16, width, width, 0x4000, 1, 0)
    struct.pack_into('<I', header, 52, 24)
    header[56] = width.bit_length()
    struct.pack_into('<I', header, 57, 0xffffffff)
    struct.pack_into('<H', header, 63, 1)
    struct.pack_into('<I', header, 68, 2)
    struct.pack_into('<IIII', header, 80, 0x30, 96, 0x02435243, 123)
    pixels = 7 * sum((width >> i) ** 2 for i in range(width.bit_length()))
    return bytes(header) + struct.pack('<4e', rgb, rgb, rgb, alpha) * pixels


def pak_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def bsp(files, *, samples=None, entity=b'{\n"classname" "worldspawn"\n}\n\0'):
    if samples is None:
        samples = struct.pack('<3iI', 1, -2, 3, 5)
    output = bytearray(1036)
    struct.pack_into('<4si', output, 0, b'VBSP', 21)
    # Put pak last so adding captures cannot change non-pak directory entries.
    for index, data in [(0, entity), (42, samples), (40, pak_bytes(files))]:
        output.extend(b'\0' * (-len(output) % 4))
        struct.pack_into('<iii4s', output, 8 + index * 16, 0, len(output), len(data), b'\0' * 4)
        output.extend(data)
    return bytes(output)


def originals():
    return {OLD + TAIL: vtf(), OLD + 'c1_-2_3.vtf': vtf(),
            OLD + 'cubemapdefault.vtf': vtf(), OLD + 'cubemapdefault.hdr.vtf': vtf(),
            'materials/plain.vmt': b'untouched'}


class ReflectionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('l4d2_bsp.reflections'),
                             'reflection handoff module must be implemented')
        from l4d2_bsp import reflections
        self.api = reflections
        self.old = originals()
        self.baseline = bsp(self.old)
        self.added = {NEW + TAIL: vtf(rgb=2)}

    def capture(self, files=None, **kwargs):
        return self.api.capture_replacements(self.baseline,
            bsp(self.old | (self.added if files is None else files), **kwargs),
            map_name='source', alias='capture_run')

    def test_prepare_copies_original_cube_bytes_including_both_defaults(self):
        files, report = self.api.prepare_capture(self.baseline, map_name='source', alias='capture_run')
        self.assertEqual(files, {NEW + name[len(OLD):]: value for name, value in self.old.items() if name.startswith(OLD)})
        self.assertEqual(report['hdr_sample_count'], 1)
        self.assertFalse(report['capture_executed'])

    def test_import_returns_only_original_namespace_hdr_replacements(self):
        files, report = self.capture()
        self.assertEqual(files, {OLD + TAIL: self.added[NEW + TAIL]})
        self.assertEqual(report['hdr_sample_count'], 1)
        self.assertEqual(report['nonfinite_alpha_files'], [])

    def test_optional_default_is_verified_but_never_transplanted(self):
        files, report = self.capture(self.added | {NEW + 'cubemapdefault.vtf': self.old[OLD + 'cubemapdefault.vtf']})
        self.assertEqual(set(files), {OLD + TAIL})
        self.assertTrue(report['default_generated_identical_to_existing_ldr_default'])
        with self.assertRaisesRegex(ValueError, 'default'):
            self.capture(self.added | {NEW + 'cubemapdefault.vtf': vtf(rgb=4)})

    def test_nonfinite_alpha_is_reported_without_mutating_native_bytes(self):
        raw = vtf(rgb=3, alpha=math.nan)
        files, report = self.capture({NEW + TAIL: raw})
        self.assertEqual(files[OLD + TAIL], raw)
        self.assertEqual(report['nonfinite_alpha_files'], [OLD + TAIL])
        self.assertEqual(report['modified_resources'][0]['vtf']['stored_faces'], 7)
        self.assertGreater(report['modified_resources'][0]['vtf']['nonfinite_alpha_count'], 0)

    def test_rejects_nonfinite_rgb_in_any_stored_face_or_mip(self):
        raw = bytearray(vtf())
        struct.pack_into('<e', raw, 96 + 6 * 8, math.inf)
        with self.assertRaisesRegex(ValueError, 'RGB'):
            self.capture({NEW + TAIL: bytes(raw)})

    def test_rejects_nonpak_payload_or_directory_drift(self):
        with self.assertRaisesRegex(ValueError, 'non-pak|lump'):
            self.capture(entity=b'{\n"classname" "other"\n}\n\0')
        raw = bytearray(bsp(self.old | self.added))
        struct.pack_into('<i', raw, 8 + 42 * 16, 1)
        with self.assertRaisesRegex(ValueError, 'non-pak|lump'):
            self.api.capture_replacements(self.baseline, raw, map_name='source', alias='capture_run')

    def test_rejects_original_resource_change_or_deletion(self):
        for files in [self.old | {'materials/plain.vmt': b'changed'},
                      {n: b for n, b in self.old.items() if n != OLD + 'c1_-2_3.vtf'}]:
            with self.subTest(files=list(files)):
                with self.assertRaisesRegex(ValueError, '(?i)original|removed|deleted'):
                    self.api.capture_replacements(self.baseline, bsp(files | self.added), map_name='source', alias='capture_run')

    def test_rejects_extra_missing_or_wrong_coordinate_capture(self):
        for additions in [{}, {NEW + 'c1_-2_4.hdr.vtf': vtf()},
                          self.added | {NEW + 'c1_-2_3.vtf': vtf()}]:
            with self.subTest(names=list(additions)):
                with self.assertRaisesRegex(ValueError, 'addition|sample|coordinate'):
                    self.capture(additions)

    def test_rejects_alias_collision_and_path_or_console_injection(self):
        for alias in ['source', '../escape', 'capture;quit', 'a/b', 'UPPER', '']:
            with self.subTest(alias=alias):
                with self.assertRaises(ValueError):
                    self.api.prepare_capture(self.baseline, map_name='source', alias=alias)
        self.old[NEW + TAIL] = vtf()
        with self.assertRaisesRegex(ValueError, 'alias|namespace'):
            self.api.prepare_capture(bsp(self.old), map_name='source', alias='capture_run')

    def test_rejects_duplicate_or_malformed_lump42_samples(self):
        record = struct.pack('<3iI', 1, -2, 3, 5)
        for samples in [b'', record[:-1], record + record]:
            with self.subTest(length=len(samples)):
                with self.assertRaisesRegex(ValueError, 'sample|L42|cubemap'):
                    self.api.prepare_capture(bsp(self.old, samples=samples), map_name='source', alias='capture_run')

    def test_rejects_unsupported_vtf_layout_and_dimension_changes(self):
        variants = [vtf(width=32), vtf(width=8), vtf()[:-1]]
        for offset, fmt, value in [(8, '<I', 5), (52, '<I', 0), (24, '<H', 2), (26, '<H', 65535),
                                    (20, '<I', 0), (68, '<I', 1), (80, '<I', 0x31)]:
            raw = bytearray(vtf())
            struct.pack_into(fmt, raw, offset, value)
            variants.append(bytes(raw))
        for raw in variants:
            with self.subTest(size=len(raw)):
                with self.assertRaisesRegex(ValueError, 'VTF|dimension|format|layout'):
                    self.capture({NEW + TAIL: raw})

    def test_rejects_duplicate_pak_entries_and_unsafe_names(self):
        for name in ['../escape.vtf', 'Materials/plain.vmt']:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, 'pak|path|canonical'):
                    self.api.prepare_capture(bsp(self.old | {name: b'x'}), map_name='source', alias='capture_run')
        # Windows ZipInfo construction normalizes backslashes; mutate both the
        # local and central directory records to exercise an actual hostile ZIP.
        raw = bsp(self.old | {'materials/escape.vtf': b'x'})
        raw = raw.replace(b'materials/escape.vtf', b'materials\\escape.vtf')
        with self.assertRaisesRegex(ValueError, 'pak|path|canonical'):
            self.api.prepare_capture(raw, map_name='source', alias='capture_run')
        raw = bsp(self.old | {'materials/other.vmt': b'x'})
        raw = raw.replace(b'materials/other.vmt', b'materials/plain.vmt')
        with self.assertRaisesRegex(ValueError, 'Duplicate pak'):
            self.api.prepare_capture(raw, map_name='source', alias='capture_run')

    def test_rejects_thumbnail_header_claim_without_corresponding_resource(self):
        raw = bytearray(vtf())
        struct.pack_into('<IBB', raw, 57, 13, 4, 4)
        with self.assertRaisesRegex(ValueError, 'VTF'):
            self.capture({NEW + TAIL: bytes(raw)})

    def test_revision_change_cannot_be_accepted_as_same_capture_context(self):
        raw = bytearray(bsp(self.old | self.added))
        struct.pack_into('<i', raw, 1032, 2)
        with self.assertRaisesRegex(ValueError, 'revision'):
            self.api.capture_replacements(self.baseline, raw, map_name='source', alias='capture_run')

    def test_controls_paths_are_unique_to_run_and_invalid_inputs_are_rejected(self):
        files = self.api.capture_controls(map_name='source', alias='capture_run', marker='run123')
        self.assertEqual(set(files), {'cfg/run123_load.cfg', 'cfg/run123_check.cfg',
            'cfg/run123_capture.cfg', 'cfg/run123_finish.cfg',
            'scripts/vscripts/run123_check.nut', 'scripts/vscripts/run123_capture.nut'})
        self.assertTrue(all(isinstance(value, bytes) and value for value in files.values()))
        for params in [{'marker': 'x;quit'}, {'exposure_max': math.inf}, {'exposure_max': -1}, {'alias': 'source'}]:
            with self.subTest(params=params):
                options = {'map_name': 'source', 'alias': 'capture_run', 'marker': 'run123'} | params
                with self.assertRaises(ValueError):
                    self.api.capture_controls(**options)


if __name__ == '__main__':
    unittest.main()
