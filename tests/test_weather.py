"""Strong C6 atmosphere replacement: shared gameplay and BSP data stay intact."""
import unittest

from l4d2_bsp.binary import BspFile, LumpFile
from l4d2_bsp.entities import parse_entities
from l4d2_bsp.profiles import transfer_style
from tests.test_profiles import c6_text, donor_text
from tests.test_style import entity, wrap


FLASH_OUTPUTS = [
    ('OnTrigger', 'tonemap_global\x1bSetAutoExposureMin\x1b50\x1b0.01\x1b-1'),
    ('OnTrigger', 'tonemap_global\x1bSetAutoExposureMin\x1b1\x1b0.03\x1b-1'),
    ('OnTrigger', 'tonemap_global\x1bSetTonemapRate\x1b1000\x1b0\x1b-1'),
    ('OnTrigger', 'tonemap_global\x1bSetTonemapRate\x1b.25\x1b5.1\x1b-1'),
    ('OnTrigger', 'tonemap_global\x1bSetAutoExposureMax\x1b5\x1b0.03\x1b-1'),
    ('OnTrigger', 'tonemap_global\x1bSetAutoExposureMax\x1b50\x1b0.01\x1b-1'),
]


def clear_source():
    text = c6_text().replace(entity('logic_relay', targetname='relay_tonemap_flash', OnTrigger='tonemap_global\x1bSetAutoExposureMax\x1b50\x1b0.01\x1b-1'), b'')
    text = text.replace(entity('logic_relay', targetname='relay_storm_blendin', OnTrigger='fog_storm\x1bSetEndDistLerpTo\x1b1000\x1b0\x1b-1'), b'')
    flash = entity('logic_relay', targetname='relay_tonemap_flash', hammerid='1027706')
    flash = flash[:-2] + ''.join(f'"{k}" "{v}"\n' for k, v in FLASH_OUTPUTS).encode() + b'}\n'
    witch = entity('info_zombie_spawn', targetname='bridewitch', hammerid='285747', Onstartled='@director\x1bForcePanicEvent\x1b\x1b0\x1b-1')
    witch = witch[:-2] + b'"Onstartled" "relay_storm_start\x1bTrigger\x1b\x1b0\x1b-1"\n}\n'
    return text.rstrip(b'\0') + flash + witch + entity('func_precipitation', targetname='rain', hammerid='478037', model='*39', preciptype='6', renderamt='100') + b''.join(
        entity('postprocess_controller', targetname=name, localcontraststrength='-.5', grainstrength='1')
        for name in ('fx_settings_storm', 'fx_settings_interior', 'fx_settings_exterior')) + b'\0'


def clear_donor():
    return donor_text().rstrip(b'\0') + entity('postprocess_controller', targetname='fx_settings_exterior', localcontraststrength='-.2', grainstrength='.5', fadetoblackstrength='0') + b'\0'


class ClearWeatherTests(unittest.TestCase):
    def transfer(self, text=None, kind='bsp', **kwargs):
        return transfer_style(wrap(text or clear_source(), kind), wrap(clear_donor()), profile='c6-c5', kind=kind, **kwargs)

    def test_default_removes_weather_and_only_weather_branch_of_witch(self):
        original = wrap(clear_source())
        out, report = self.transfer()
        before, after = BspFile.parse(original), BspFile.parse(out)
        ents = parse_entities(after.lump_bytes(0))
        self.assertFalse(any(e.one('classname') == 'func_precipitation' for e in ents))
        self.assertFalse(any(e.one('targetname') == 'relay_tonemap_flash' for e in ents))
        witch = next(e for e in ents if e.one('targetname') == 'bridewitch')
        self.assertEqual(witch.values('Onstartled'), ['@director\x1bForcePanicEvent\x1b\x1b0\x1b-1'])
        self.assertEqual(before.lumps[1:], after.lumps[1:])
        self.assertEqual(before.lump_bytes(1), after.lump_bytes(1))
        self.assertTrue(report['gameplay_io_unchanged'])
        self.assertEqual(report['atmosphere_policy'], 'replace')

    def test_local_fog_postprocessing_checkpoint_use_target_outdoor_style(self):
        out, _ = self.transfer()
        ents = parse_entities(BspFile.parse(out).lump_bytes(0))
        named = {e.one('targetname'): e for e in ents if e.one('targetname')}
        for name in ('fog_storm', 'fog_master', 'foginteriorcontroller'):
            self.assertEqual(named[name].one('fogcolor'), '130 117 107')
            self.assertEqual(named[name].one('fogstart'), '256')
        for name in ('fx_settings_storm', 'fx_settings_interior', 'fx_settings_exterior'):
            self.assertEqual(named[name].one('localcontraststrength'), '-.2')
            self.assertEqual(named[name].one('grainstrength'), '.5')
        self.assertEqual(named['colorcorrection_checkpoint'].one('filename'), 'materials/correction/cc_c5_main.raw')
        self.assertEqual(next(e for e in ents if e.one('classname') == 'fog_volume').one('model'), '*8')

    def test_replacement_is_byte_idempotent_in_bsp_and_lmp(self):
        for kind in ('bsp', 'lmp'):
            with self.subTest(kind=kind):
                first, _ = self.transfer(kind=kind)
                second, report = transfer_style(first, wrap(clear_donor()), profile='c6-c5', kind=kind)
                self.assertEqual(first, second)
                self.assertEqual(report['changes'], [])
                if kind == 'lmp':
                    self.assertEqual(LumpFile.parse(first).revision, 1234)
                    self.assertEqual(first[20:24], b'GAP!')
                    self.assertTrue(first.endswith(b'TAIL'))

    def test_extra_gameplay_output_inside_weather_entity_is_not_silently_deleted(self):
        original = clear_source()
        changed = original.replace(b'"hammerid" "1027706"', b'"hammerid" "1027706"\n"OnTrigger" "door\x1bOpen\x1b\x1b0\x1b-1"')
        with self.assertRaisesRegex(ValueError, 'weather|Weather'):
            self.transfer(changed)

    def test_non_on_output_and_template_reference_cannot_hide_in_removed_entity(self):
        for pair in (b'"OutAnger" "door\x1bOpen\x1b\x1b0\x1b-1"', b'"Template01" "door"'):
            with self.subTest(pair=pair), self.assertRaisesRegex(ValueError, 'weather|Weather'):
                self.transfer(clear_source().replace(b'"hammerid" "1027706"', b'"hammerid" "1027706"\n' + pair))

    def test_unknown_rain_or_ambiguous_weather_identity_is_rejected(self):
        for addition in (entity('func_precipitation', targetname='new_rain', model='*17'), entity('logic_relay', targetname='relay_tonemap_flash')):
            with self.subTest(addition=addition), self.assertRaisesRegex(ValueError, 'weather|Weather'):
                self.transfer(clear_source().rstrip(b'\0') + addition + b'\0')

    def test_unknown_incoming_weather_or_visual_writer_is_rejected(self):
        for target in ('rain', 'relay_storm_start', 'fog_storm', 'fx_settings_interior'):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'weather|Weather|atmosphere'):
                self.transfer(clear_source().rstrip(b'\0') + entity('logic_relay', targetname='unknown', OnTrigger=target+'\x1bEnable\x1b\x1b0\x1b-1') + b'\0')

    def test_local_light_and_generic_gameplay_entities_remain_byte_identical(self):
        extras = entity('light', targetname='street_lamp', _light='255 100 50 100', origin='1 9 3') + entity('prop_dynamic', targetname='car', model='models/car.mdl', OnBreak='director\x1bForcePanicEvent\x1b\x1b0\x1b-1')
        out, _ = self.transfer(clear_source().rstrip(b'\0') + extras + b'\0')
        self.assertIn(extras, BspFile.parse(out).lump_bytes(0))

    def test_preserve_is_explicit_and_invalid_policy_fails(self):
        out, _ = self.transfer(atmosphere_policy='preserve')
        self.assertIn(b'func_precipitation', BspFile.parse(out).lump_bytes(0))
        with self.assertRaisesRegex(ValueError, 'atmosphere_policy'):
            self.transfer(atmosphere_policy='unknown')

    def test_rainy_soundscapes_are_replaced_without_moving_regions(self):
        source = clear_source().rstrip(b'\0') + entity('env_soundscape', targetname='amb', soundscape='c6m1_outdoor_01', radius='400', origin='1 2 3') + b'\0'
        out, _ = self.transfer(source)
        ambient = next(e for e in parse_entities(BspFile.parse(out).lump_bytes(0)) if e.one('targetname') == 'amb')
        self.assertEqual(ambient.one('soundscape'), 'c5m1.waterfront')
        self.assertEqual(ambient.one('origin'), '1 2 3')
        self.assertEqual(ambient.one('radius'), '400')

    def test_duplicate_visual_field_is_rejected_instead_of_leaving_old_override(self):
        source = clear_source().replace(b'"grainstrength" "1"', b'"grainstrength" "1"\n"grainstrength" "2"', 1)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.transfer(source)
