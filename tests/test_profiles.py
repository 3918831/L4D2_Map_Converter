import importlib.util
import unittest

from l4d2_bsp.binary import BspFile, LumpFile
from l4d2_bsp.entities import parse_entities
from tests.test_style import entity, style_text, wrap


def c6_text():
    text = style_text().replace(b'sky_l4d_c2m1_hdr', b'sky_l4d_c6m1_hdr')
    text = text.replace(b'"skyname"', b'"musicpostfix" "DeadLight"\n"world_mins" "-6110 -2338 -320"\n"world_maxs" "9600 6688 3096"\n"skyname"', 1)
    text = text.replace(b'SetAutoExposureMax\x1b6', b'SetAutoExposureMax\x1b8')
    text = text.replace(b'"spawnflags" "1"', b'"spawnflags" "0"')
    intro = entity('color_correction', targetname='color_correction_intro', filename='materials/correction/cc_c2_main.raw')
    text = text.replace(intro, b'')
    return text.rstrip(b'\0') + b''.join([
        entity('env_fog_controller', targetname='fog_storm', spawnflags='1', fogcolor='26 23 21', fogcolor2='255 255 255', fogstart='0', fogend='2500', fogmaxdensity='1', foglerptime='5', farz='2500'),
        entity('fog_volume', model='*8', FogName='foginteriorcontroller'),
        entity('logic_relay', targetname='relay_tonemap_flash', OnTrigger='tonemap_global\x1bSetAutoExposureMax\x1b50\x1b0.01\x1b-1'),
        entity('logic_relay', targetname='relay_storm_blendin', OnTrigger='fog_storm\x1bSetEndDistLerpTo\x1b1000\x1b0\x1b-1'),
        entity('color_correction', targetname='colorcorrection_checkpoint', filename='materials/correction/cc_checkpoint.raw'),
    ]) + b'\0'


def donor_text():
    return style_text(True).replace(b'"skyname"', b'"musicpostfix" "BigEasy"\n"world_mins" "-5248 -4672 -992"\n"world_maxs" "3168 3232 1664"\n"skyname"', 1)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('l4d2_bsp.profiles'), 'Explicit profile implementation is missing')
        from l4d2_bsp.profiles import transfer_style
        self.transfer = transfer_style

    def run_c6(self, source=None, reference=None, kind='bsp', reference_kind='bsp'):
        return self.transfer(wrap(source or c6_text(), kind), wrap(reference or donor_text(), reference_kind), profile='c6-c5', kind=kind, reference_kind=reference_kind, atmosphere_policy='preserve')

    def test_c6_keeps_storm_gameplay_and_fog_distances(self):
        source = wrap(c6_text())
        out, report = self.run_c6()
        before, after = BspFile.parse(source), BspFile.parse(out)
        self.assertEqual(before.lumps[1:], after.lumps[1:])
        self.assertEqual(source[1036:], out[1036:len(source)])
        ents = parse_entities(after.lump_bytes(0))
        named = {e.one('targetname'): e for e in ents if e.one('targetname')}
        self.assertEqual(named['fog_storm'].one('fogcolor'), '130 117 107')
        self.assertEqual(named['foginteriorcontroller'].one('fogcolor'), '40 40 40')
        for key, expected in [('spawnflags', '1'), ('fogstart', '0'), ('fogend', '2500'), ('fogmaxdensity', '1'), ('foglerptime', '5'), ('farz', '2500')]:
            self.assertEqual(named['fog_storm'].one(key), expected)
        for name in ['relay_tonemap_flash', 'relay_storm_blendin', 'colorcorrection_checkpoint', 'horde']:
            original = next(e for e in parse_entities(before.lump_bytes(0)) if e.one('targetname') == name)
            self.assertEqual([(p.key, p.value) for p in original.pairs], [(p.key, p.value) for p in named[name].pairs])
        self.assertTrue(report['gameplay_io_unchanged'])
        self.assertTrue(report['protected_entity_bytes_unchanged'])
        self.assertTrue(report['non_entity_payloads_unchanged'])
        self.assertEqual(report['runtime_validation'], 'pending_user_test')

    def test_c6_has_only_three_startup_parameter_changes_and_one_visual_insertion(self):
        _, report = self.run_c6()
        changes = [c for c in report['changes'] if c['category'] == 'visual_io_parameter']
        self.assertEqual(len(changes), 3)
        for change in changes:
            self.assertEqual(change['old'].split('\x1b')[2:], ['8', '0', '-1'])
            self.assertEqual(change['new'].split('\x1b')[2:], ['5', '0', '-1'])
        self.assertEqual(len(report['added_outputs']), 1)
        self.assertEqual(report['added_outputs'][0]['value'], 'tonemap_global\x1bSetTonemapPercentBrightPixels\x1b5\x1b0\x1b-1')
        self.assertEqual(report['added_entity_classes'], ['env_sun'])
        self.assertIn('color_correction_intro', [item['targetname'] for item in report['missing_optional_controllers']])

    def test_c6_preserves_sky_geometry_and_adds_sun_at_source_light(self):
        out, _ = self.run_c6()
        ents = parse_entities(BspFile.parse(out).lump_bytes(0))
        sky = next(e for e in ents if e.one('classname') == 'sky_camera')
        self.assertEqual([sky.one(k) for k in ('origin', 'scale', 'angles')], ['8 8 8', '32', '0 0 0'])
        sun = next(e for e in ents if e.one('classname') == 'env_sun')
        self.assertEqual(sun.one('origin'), '1 2 3')
        self.assertEqual(sun.one('angles'), '0 150 0')

    def test_c6_is_byte_idempotent_for_bsp_and_lmp(self):
        for kind in ('bsp', 'lmp'):
            with self.subTest(kind=kind):
                first, _ = self.run_c6(kind=kind, reference_kind='lmp')
                second, report = self.transfer(first, wrap(donor_text(), 'lmp'), profile='c6-c5', kind=kind, reference_kind='lmp', atmosphere_policy='preserve')
                self.assertEqual(first, second)
                self.assertEqual(report['changes'], [])
                self.assertEqual(report['added_outputs'], [])
                self.assertEqual(report['added_entity_classes'], [])
                if kind == 'lmp':
                    self.assertEqual((LumpFile.parse(first).offset, LumpFile.parse(first).revision), (24, 1234))
                    self.assertEqual(first[20:24], b'GAP!')
                    self.assertTrue(first.endswith(b'TAIL'))

    def test_c6_rejects_wrong_internal_map_and_reference(self):
        for source, reference in [(c6_text().replace(b'DeadLight', b'Wrong'), donor_text()), (c6_text().replace(b'-6110 -2338 -320', b'0 0 0'), donor_text()), (c6_text(), donor_text().replace(b'BigEasy', b'Wrong'))]:
            with self.subTest(source=source[:80]), self.assertRaises(ValueError):
                self.run_c6(source, reference)

    def test_c6_rejects_duplicate_or_nondefault_storm_and_misrouted_interior(self):
        for text in [c6_text().replace(b'"targetname" "fog_storm"\n"spawnflags" "1"', b'"targetname" "fog_storm"\n"spawnflags" "0"'), c6_text().rstrip(b'\0') + entity('env_fog_controller', targetname='fog_storm', spawnflags='1') + b'\0', c6_text().replace(b'"FogName" "foginteriorcontroller"', b'"FogName" "fog_master"')]:
            with self.subTest(text=text[-300:]), self.assertRaises(ValueError):
                self.run_c6(text)

    def test_c6_rejects_ambiguous_tonemap_names_and_startup_output_drift(self):
        line = b'"OnMapSpawn" "tonemap_global\x1bSetAutoExposureMax\x1b8\x1b0\x1b-1"\n'
        for text in [c6_text().replace(line, line + line), c6_text().replace(line, line.replace(b'\x1b8\x1b0', b'\x1b8\x1b2')), c6_text().replace(line, b''), c6_text().rstrip(b'\0') + entity('logic_relay', targetname='tonemap_global') + b'\0']:
            with self.subTest(text=text[-100:]), self.assertRaises(ValueError):
                self.run_c6(text)

    def test_c6_rejects_unapproved_donor_exposure_and_invalid_visual_values(self):
        for reference in [donor_text().replace(b'SetAutoExposureMax\x1b5', b'SetAutoExposureMax\x1b7'), donor_text().replace(b'130 117 107', b'NaN 117 107')]:
            with self.subTest(reference=reference[:80]), self.assertRaises(ValueError):
                self.run_c6(reference=reference)

    def test_reference_gameplay_outputs_are_never_added(self):
        out, _ = self.run_c6(reference=donor_text().replace(b'horde\x1bTrigger', b'evil\x1bKill'))
        self.assertNotIn(b'evil', BspFile.parse(out).lump_bytes(0))

    def test_added_c6_sun_reports_every_missing_donor_field(self):
        reference = donor_text().replace(b'"material" "sprites/light_glow02_add_noz"\n', b'')
        out, report = self.run_c6(reference=reference)
        sun = next(e for e in parse_entities(BspFile.parse(out).lump_bytes(0)) if e.one('classname') == 'env_sun')
        self.assertIsNone(sun.one('material'))
        omissions = [item for item in report['skipped_fields'] if item['classname'] == 'env_sun']
        self.assertEqual({item['key'] for item in omissions},
                         {'use_angles', 'overlaysize', 'overlaymaterial', 'overlaycolor', 'material', 'HDRColorScale'})
        self.assertTrue(all(item['reason'] == 'field absent from reference; omitted from added sun' for item in omissions))

    def test_c2_delegates_to_the_accepted_strict_converter(self):
        from l4d2_bsp.style import transfer_c5_style
        source, donor = wrap(style_text()), wrap(style_text(True))
        self.assertEqual(self.transfer(source, donor, profile='c2-c5'), transfer_c5_style(source, donor))

    def test_profile_support_inspection_reports_failures_without_writing(self):
        from l4d2_bsp.profiles import inspect_profile_support, profile_spec
        report = inspect_profile_support(wrap(c6_text()), wrap(donor_text()), profile='c6-c5', atmosphere_policy='preserve')
        self.assertTrue(report['supported'])
        self.assertFalse(inspect_profile_support(wrap(style_text()), wrap(donor_text()), profile='c6-c5')['supported'])
        self.assertEqual(profile_spec('c6-c5')['source_map'], 'c6m1_riverbank')
        self.assertEqual(profile_spec('c2-c5')['reference_map'], 'c5m1_waterfront')
        with self.assertRaises(ValueError):
            self.transfer(wrap(c6_text()), wrap(donor_text()), profile='arbitrary-map')


if __name__ == '__main__':
    unittest.main()
