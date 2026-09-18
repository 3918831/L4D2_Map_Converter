"""Night preset integration on arbitrary source containers and package assets."""
import unittest

from l4d2_bsp.presets import load_preset
from l4d2_bsp.generic_conversion import transfer_generic
from l4d2_bsp.inspect import container, entity_list
from l4d2_bsp.soundscapes import preset_soundscape_assets
from tests.test_core import make_bsp, make_lmp
from tests.test_generic_conversion import source, ent

ID = 'c10m3-night-v1'


class C10PresetTests(unittest.TestCase):
    def test_night_roles_and_explicit_global_exposure_fallback(self):
        p = load_preset(ID)
        self.assertEqual(p.schema_version, 3)
        self.assertEqual(p.supported_sources, ())
        self.assertEqual(p.role_values('world')['skyname'], 'sky_day01_09_hdr')
        self.assertEqual(p.role_values('environment')['_light'], '52 101 124 10')
        self.assertEqual(p.role_values('environment')['_AmbientScaleHDR'], '0.7')
        self.assertEqual(p.role_values('directional')['_light'], '22 102 146 1')
        self.assertIsNone(p.role_values('sun'))
        self.assertIsNone(p.wind_values)
        self.assertEqual(p.capture_exposure_max, 7)
        for suffix in ('', '_infected', '_ghost'):
            values = dict(p.tonemap_inputs('env_tonemap_controller' + suffix))
            self.assertEqual(values['SetAutoExposureMax'], '7')
            self.assertEqual(values['SetAutoExposureMin'], '0.25')
            self.assertEqual(values['SetTonemapRate'], '0.5')
            self.assertEqual(values['SetBloomScale'], '1')
            self.assertNotIn('SetTonemapPercentBrightPixels', values)

    def test_generic_containers_remove_sun_calm_wind_and_keep_gameplay_local_lights(self):
        light = ent('light', targetname='local_lamp', _light='255 160 60 300')
        for kind, wrap in [('bsp', make_bsp), ('lmp', make_lmp)]:
            data = wrap(source().rstrip(b'\0') + light + ent('env_sun', targetname='old_sun') +
                        ent('env_wind', targetname='old_wind') + b'\0')
            out, report = transfer_generic(data, load_preset(ID), kind=kind, rule='generic-replace-v3')
            entities = entity_list(container(out, kind))
            self.assertFalse(any(e.one('classname') == 'env_sun' for e in entities))
            # v3 retains wind entity lifecycle while making absent target wind calm.
            from l4d2_bsp.generic_weather import WIND_CALM
            wind = next(e for e in entities if e.one('classname') == 'env_wind')
            for key, value in WIND_CALM.items():
                self.assertEqual(wind.one(key), value)
            directional = next(e for e in entities if e.one('classname') == 'light_directional')
            self.assertEqual(directional.one('_light'), '22 102 146 1')
            self.assertEqual(directional.one('pitch'), '-90')
            post = next(e for e in entities if e.one('classname') == 'postprocess_controller')
            self.assertEqual(post.one('localcontraststrength'), '-0.2')
            self.assertEqual(post.one('localcontrastedgestrength'), '-0.7')
            self.assertIn(light.rstrip(), out)
            self.assertIn(b'gameplay,Trigger', out)
            self.assertTrue(report['protected_io_unchanged'])
            self.assertTrue(report['non_entity_payloads_unchanged'])
            sky = next(e for e in entities if e.one('classname') == 'sky_camera')
            self.assertEqual(sky.one('origin'), '40 50 60')
            self.assertEqual(sky.one('scale'), '16')

    def test_removed_sun_keeps_reference_guard(self):
        data = source().rstrip(b'\0') + ent('env_sun', targetname='old_sun')
        data += ent('prop_dynamic', parentname='old_sun', model='models/truck.mdl') + b'\0'
        with self.assertRaisesRegex(ValueError, 'Reference to removed'):
            transfer_generic(make_bsp(data), load_preset(ID), rule='generic-replace-v3')

    def test_owned_ambience_resources_exclude_donor_spatial_sounds(self):
        p = load_preset(ID)
        assets = preset_soundscape_assets(p, 'another-community-map')
        self.assertEqual(set(assets), {'scripts/soundscapes_another-community-map.txt'})
        self.assertIn(b'"volume" "0.3"', next(iter(assets.values())))
        self.assertNotIn(b'position', next(iter(assets.values())))
        self.assertFalse(any('sprites/' in path for path in p.required_resources('replace')))
        for e in p.entities():
            for key in ('origin', 'hammerid', 'vscripts', 'model', 'scale'):
                self.assertIsNone(e.one(key))
