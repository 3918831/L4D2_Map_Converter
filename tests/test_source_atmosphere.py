import unittest
from types import SimpleNamespace

from l4d2_bsp.binary import BspFile
from l4d2_bsp.entities import parse_entities
from tests.test_style import style_text, entity, wrap


def source_text():
    text = style_text().replace(b'"skyname"', b'"musicpostfix" "Fairgrounds"\n"world_mins" "-3648 -5312 -2048"\n"world_maxs" "14848 10752 256"\n"skyname"', 1)
    text = text.rstrip(b'\0')
    for name in ('fx_settings_intro', 'fx_settings_exterior', 'fx_settings_interior'):
        text += entity('postprocess_controller', targetname=name, grainstrength='1')
    text += entity('color_correction', targetname='colorcorrection_checkpoint', filename='materials/correction/cc_checkpoint.raw')
    for _ in range(2):
        text += entity('env_wind', origin='0 0 32', minwind='20', maxwind='50')
    for sound in ('c2m1.hiway.spawn', 'c2m1.hotel.indoors'):
        text += entity('env_soundscape', soundscape=sound, origin='1 2 3', radius='128')
    return text + b'\0'


def preset():
    roles = {'world': {'skyname': 'sky_fixed_hdr'}, 'environment': {'_light': '200 225 230 10'},
             'directional': {'_light': '79 108 104 30'}, 'shadow': {'color': '0 0 0'},
             'fog_outdoor': {'fogcolor': '20 25 25', 'fogend': '1500', 'fogenable': '1'},
             'sky': {'fogcolor': '20 25 25'}, 'sun': None,
             'color_main': {'filename': 'materials/correction/cc_fixed.raw'},
             'color_intro': {'filename': 'materials/correction/cc_fixed.raw'},
             'color_checkpoint': {'filename': 'materials/correction/cc_fixed.raw'},
             'postprocess_exterior': {'localcontraststrength': '-.25', 'grainstrength': '1'}}
    return SimpleNamespace(id='fixture-static', schema_version=2, supported_sources=('c2m1_highway',),
        role_values=lambda name: roles.get(name), exposure_values={'maximum':'10','minimum':'1','rate':'.25','bright_pixels':None},
        wind_values={'minwind':'15','maxwind':'30'}, soundscape_mapping={'outdoor':'fixture.outdoor','indoor':'fixture.indoor'},
        sha256='fixture-digest', metadata=lambda: {'id':'fixture-static','schema_version':2,'sha256':'fixture-digest'})


class SourceAtmosphereTests(unittest.TestCase):
    def apply(self, text=None, kind='bsp'):
        from l4d2_bsp.source_atmosphere import transfer_atmosphere
        return transfer_atmosphere(wrap(text or source_text(), kind), preset(), source_profile='c2m1_highway', kind=kind)

    def test_full_override_without_sun_keeps_gameplay_geometry_and_is_idempotent(self):
        from l4d2_bsp.source_atmosphere import transfer_atmosphere
        for kind in ('bsp', 'lmp'):
            out, report = self.apply(kind=kind)
            again, _ = transfer_atmosphere(out, preset(), source_profile='c2m1_highway', kind=kind)
            self.assertEqual(out, again)
            self.assertTrue(report['gameplay_io_unchanged'])
            self.assertTrue(report['non_entity_payloads_unchanged'])
            self.assertNotIn(b'"classname" "env_sun"', out)
            self.assertNotIn(b'SetTonemapPercentBrightPixels', out)
            self.assertIn(b'SetAutoExposureMax\x1b10', out)
            self.assertIn(b'"localcontraststrength" "-.25"', out)
            self.assertIn(b'"soundscape" "fixture.indoor"', out)
            self.assertIn(b'"OnStartTouch" "director\x1bBeginScript\x1boriginal\x1b0\x1b-1"', out)
            if kind == 'bsp': self.assertEqual(BspFile.parse(out).lump_bytes(1), b'KEEP')

    def test_rejects_unknown_visual_writer_even_if_wildcard(self):
        extra = entity('logic_relay', OnTrigger='fog_*\x1bSetColor\x1b1 2 3\x1b0\x1b-1')
        with self.assertRaisesRegex(ValueError, 'writer'):
            self.apply(source_text().rstrip(b'\0') + extra + b'\0')

    def test_rejects_shadow_and_class_or_comma_writers(self):
        text = source_text().replace(b'"classname" "shadow_control"',
                                    b'"classname" "shadow_control"\n"targetname" "shadow_main"')
        for value in ('shadow_main\x1bSetShadowColor\x1b255 0 0\x1b0\x1b-1',
                      'shadow_main,SetShadowColor,255 0 0,0,-1',
                      'env_fog_controller\x1bSetColor\x1b1 2 3\x1b0\x1b-1'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError,'writer'):
                self.apply(text.rstrip(b'\0')+entity('logic_relay',OnTrigger=value)+b'\0')

    def test_rejects_wrong_source_identity_and_previous_other_style(self):
        for a,b in ((b'Fairgrounds', b'OtherMap'), (b'sky_l4d_c2m1_hdr', b'sky_l4d_c5_1_hdr')):
            with self.subTest(a=a), self.assertRaisesRegex(ValueError, 'identity|sky'):
                self.apply(source_text().replace(a,b))

    def test_rejects_unowned_soundscape_or_extra_wind(self):
        for extra in (entity('env_soundscape', soundscape='unknown'), entity('env_wind')):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, 'soundscape|wind'):
                self.apply(source_text().rstrip(b'\0') + extra + b'\0')

    def test_unspecified_bright_pixels_rejects_stale_override(self):
        extra = entity('logic_auto', OnMapSpawn='tonemap_global\x1bSetTonemapPercentBrightPixels\x1b5\x1b0\x1b-1')
        with self.assertRaisesRegex(ValueError, 'bright'):
            self.apply(source_text().rstrip(b'\0') + extra + b'\0')
