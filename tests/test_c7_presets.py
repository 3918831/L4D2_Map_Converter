"""C7 target semantics must work on arbitrary source names without donor IO."""
import copy
import json
import unittest

from l4d2_bsp.presets import load_preset, parse_preset
from l4d2_bsp.generic_conversion import transfer_generic
from l4d2_bsp.inspect import container, entity_list
from tests.test_core import make_bsp, make_lmp
from tests.test_generic_conversion import source, ent

ID = 'c7m1-hazy-static-v1'


class C7PresetTests(unittest.TestCase):
    def test_reference_roles_and_independent_team_exposures(self):
        p = load_preset(ID)
        self.assertEqual(p.schema_version, 3)
        self.assertEqual(p.supported_sources, ())  # Generic entry only, no legacy adapter promise.
        self.assertEqual(p.role_values('world')['skyname'], 'river_hdr')
        self.assertIsNone(p.role_values('directional'))
        self.assertEqual(p.role_values('sun')['material'], 'sprites/light_glow02_add_noz_docks')
        self.assertEqual(p.role_values('environment')['_light'], '185 157 115 60')
        self.assertEqual(p.capture_exposure_max, 9)
        normal = dict(p.tonemap_inputs('env_tonemap_controller'))
        self.assertEqual(normal['SetAutoExposureMin'], '.25')
        self.assertEqual(normal['SetAutoExposureMax'], '9')
        self.assertEqual(normal['SetBloomScale'], '0')
        self.assertNotIn('SetTonemapRate', normal)
        self.assertNotIn('SetTonemapPercentBrightPixels', normal)
        for suffix in ('_infected', '_ghost'):
            inputs = dict(p.tonemap_inputs('env_tonemap_controller' + suffix))
            self.assertEqual(inputs['SetAutoExposureMax'], '3')
            self.assertEqual(inputs['SetBloomScale'], '1')
            self.assertEqual(inputs['SetBloomExponent'], '2')
        for entity in p.entities():
            for key in ('origin', 'hammerid', 'vscripts', 'model', 'scale'):
                self.assertIsNone(entity.one(key))

    def test_generic_bsp_and_modes_preserve_gameplay_and_apply_role_inputs(self):
        for kind, wrap in [('bsp', make_bsp), ('lmp', make_lmp)]:
            data = wrap(source().rstrip(b'\0') + ent('light_directional', targetname='extra_sky_light') + b'\0')
            out, report = transfer_generic(data, load_preset(ID), kind=kind, rule='generic-replace-v3')
            es = entity_list(container(out, kind))
            self.assertFalse(any(e.one('classname') == 'light_directional' for e in es))
            self.assertTrue(report['protected_io_unchanged'])
            self.assertTrue(report['non_entity_payloads_unchanged'])
            sky = next(e for e in es if e.one('classname') == 'sky_camera')
            self.assertEqual(sky.one('origin'), '40 50 60')
            self.assertEqual(sky.one('scale'), '16')
            outputs = [p.value.split('\x1b') for e in es for p in e.pairs if p.key == 'OnMapSpawn']
            self.assertIn(['custom_exposure', 'SetAutoExposureMax', '9', '0', '-1'], outputs)
            self.assertIn(['__lmc_tonemap_v1_infected', 'SetAutoExposureMax', '3', '0', '-1'], outputs)
            self.assertIn(['custom_exposure', 'SetBloomScale', '0', '0', '-1'], outputs)
            self.assertIn(b'gameplay,Trigger', out)

    def test_absent_directional_obeys_existing_reference_protection(self):
        data = source().rstrip(b'\0') + ent('light_directional', targetname='parent_sun')
        data += ent('prop_dynamic', parentname='parent_sun', model='models/truck.mdl') + b'\0'
        with self.assertRaisesRegex(ValueError, 'Reference to removed'):
            transfer_generic(make_bsp(data), load_preset(ID), rule='generic-replace-v3')

    def test_schema_rejects_partial_roles_unsafe_paths_and_invalid_exposure(self):
        original = json.loads(load_preset(ID).source_path.read_bytes())
        mutations = [
            (('supported_sources',), ['c2m1_highway']),
            (('roles','sun','material'), 'sprites/../unsafe'),
            (('roles','environment'), None),
            (('exposure','survivor','maximum'), '.1'),
            (('exposure','infected','bloom_scale'), '-1'),
            (('exposure','ghost','minimum'), 'nan'),
            (('exposure','survivor','rate'), '0'),
            (('exposure','survivor','bloom_exponent'), '1e100'),
            (('exposure','ghost','run_script'), 'untrusted'),
            (('resources','clear'), []),
        ]
        for path, value in mutations:
            d = copy.deepcopy(original); node = d
            for key in path[:-1]: node = node[key]
            node[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                parse_preset(json.dumps(d).encode())

    def test_assets_are_owned_and_exclude_donor_scene_sounds(self):
        from l4d2_bsp.soundscapes import preset_soundscape_assets
        p = load_preset(ID)
        assets = preset_soundscape_assets(p, 'third-party-map')
        self.assertEqual(set(assets), {'scripts/soundscapes_third-party-map.txt'})
        self.assertIn(b'crucial_town_ambience.wav', next(iter(assets.values())))
        for path in p.required_resources('replace'):
            self.assertFalse(any(s in path.lower() for s in ('fire','thunder','foghorn','seagull','dock_close')))
        p.exposure_values.clear()
        inputs = p.tonemap_inputs('env_tonemap_controller')
        inputs.clear()
        self.assertEqual(dict(p.tonemap_inputs('env_tonemap_controller'))['SetAutoExposureMax'], '9')
        with self.assertRaises(ValueError): p.tonemap_inputs('arbitrary_class')
