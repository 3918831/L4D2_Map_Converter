"""Schema 2 keeps target values semantic, bounded and donor-independent."""
import copy
import hashlib
import json
import unittest

from l4d2_bsp.presets import load_preset, parse_preset


class C4PresetTests(unittest.TestCase):
    def test_fixed_state_retains_donor_values_without_storm_or_geometry(self):
        preset = load_preset('c4m3-overcast-static-v1')
        self.assertEqual(preset.schema_version, 2)
        self.assertEqual(preset.metadata()['schema_version'], 2)
        self.assertEqual(preset.supported_sources, ('c2m1_highway',))
        self.assertEqual(preset.atmosphere_policy, 'replace')
        self.assertEqual(preset.role_values('world'), {'skyname': 'sky_l4d_c4m4_hdr'})
        self.assertEqual(preset.role_values('environment')['_light'], '200 225 230 10')
        self.assertEqual(preset.role_values('environment')['_ambient'], '79 108 104 5')
        self.assertEqual(preset.role_values('directional')['_light'], '79 108 104 30')
        self.assertEqual(preset.role_values('fog_outdoor')['foglerptime'], '5')
        self.assertEqual(preset.role_values('fog_outdoor')['heightFogDensity'], '0.0')
        self.assertEqual(preset.role_values('sky')['fogend'], '1')
        self.assertEqual(preset.role_values('postprocess_exterior')['localcontraststrength'], '-.25')
        self.assertEqual(preset.role_values('postprocess_exterior')['localcontrastedgestrength'], '-.3')
        self.assertEqual(preset.role_values('postprocess_exterior')['fadetoblackstrength'], '0')
        for role in ('color_main', 'color_intro', 'color_checkpoint'):
            self.assertEqual(preset.role_values(role), {'filename': 'materials/correction/cc_c4_return.raw'})
        self.assertIsNone(preset.role_values('sun'))
        self.assertEqual(preset.wind_values, dict(windradius='-1', minwind='15', maxwind='30',
            mingust='50', maxgust='100', mingustdelay='15', maxgustdelay='30',
            gustduration='5', gustdirchange='20', angles='0 -180 0'))
        for entity in preset.entities():
            self.assertNotEqual(entity.one('classname'), 'env_sun')
            for key in ('origin', 'world_mins', 'world_maxs', 'scale', 'model', 'vscripts', 'hammerid'):
                self.assertIsNone(entity.one(key))

    def test_absent_bright_pixel_override_does_not_fabricate_output(self):
        preset = load_preset('c4m3-overcast-static-v1')
        self.assertEqual(preset.exposure_values, dict(minimum='1', maximum='10', rate='.25', bright_pixels=None))
        outputs = [p.value.split('\x1b') for e in preset.entities() for p in e.pairs if p.key == 'OnMapSpawn']
        self.assertEqual(len(outputs), 7)
        self.assertFalse(any(p[1] == 'SetTonemapPercentBrightPixels' for p in outputs))
        self.assertEqual(preset.capture_exposure_max, 10.0)

    def test_public_views_are_independent_and_c5_compatibility_is_retained(self):
        preset = load_preset('c4m3-overcast-static-v1')
        preset.role_values('world').clear()
        preset.exposure_values.clear()
        preset.wind_values.clear()
        preset.soundscape_definition['indoor']['loops'].clear()
        self.assertTrue(preset.role_values('world'))
        self.assertTrue(preset.exposure_values)
        self.assertTrue(preset.wind_values)
        self.assertTrue(preset.soundscape_definition['indoor']['loops'])
        legacy = load_preset()
        self.assertEqual(legacy.schema_version, 1)
        self.assertEqual(legacy.exposure_values['bright_pixels'], '5')
        self.assertEqual(legacy.role_values('world')['skyname'], 'sky_l4d_c5_1_hdr')
        self.assertIsNone(legacy.wind_values)
        self.assertIsNone(legacy.soundscape_definition)
        self.assertEqual(legacy.sha256, hashlib.sha256(legacy.source_path.read_bytes()).hexdigest())

    def test_resource_closure_contains_only_visuals_and_dry_loop_assets(self):
        preset = load_preset('c4m3-overcast-static-v1')
        self.assertEqual(preset.soundscape_definition['outdoor']['loops'], [])
        resources = preset.required_resources('replace')
        self.assertIn('materials/correction/cc_c4_return.raw', resources)
        self.assertEqual(len(preset.required_resources('preserve')), 7)
        self.assertTrue(preset.soundscape_definition['indoor']['loops'])
        for path in resources:
            self.assertFalse(any(word in path.lower() for word in ('rain', 'storm', 'seagull', 'fire')))
        loops = preset.soundscape_definition['indoor']['loops']
        self.assertEqual(set(resources) - set(preset.required_resources('preserve')),
                         {'sound/' + loop['wave'] for loop in loops})

    def test_schema_rejects_unsafe_unbounded_or_incomplete_data(self):
        document = json.loads(load_preset('c4m3-overcast-static-v1').source_path.read_bytes())
        mutations = [
            (('id',), 'custom-style'), (('schema_version',), True),
            (('supported_sources',), ['c6m1_riverbank']),
            (('supported_sources',), ['c2m1_highway', 'c2m1_highway']),
            (('atmosphere_policy',), 'preserve'),
            (('roles', 'world', 'origin'), '0 0 0'),
            (('roles', 'fog_outdoor', 'fogdir'), '1 0'),
            (('roles', 'fog_outdoor', 'fogenable'), '2'),
            (('roles', 'fog_outdoor', 'heightFogDensity'), '-1'),
            (('roles', 'shadow', 'distance'), '-1'),
            (('roles', 'postprocess_exterior', 'grainstrength'), 'nan'),
            (('wind', 'angles'), '0 nan 0'), (('wind', 'windradius'), '-2'),
            (('wind', 'windradius'), '-0.5'),
            (('wind', 'minwind'), '31'), (('wind', 'gustduration'), '-1'),
            (('roles', 'environment', '_light'), '200 225 230 1e100'),
            (('wind', 'targetname'), 'wind_storm'),
            (('exposure', 'bright_pixels'), '101'), (('exposure', 'maximum'), '0'),
            (('resources', 'clear'), []),
            (('resources', 'common'), ['materials/skybox/../bad.vmt']),
            (('soundscape_definition', 'outdoor', 'OnMapSpawn'), 'evil'),
        ]
        for path, value in mutations:
            altered = copy.deepcopy(document)
            node = altered
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                parse_preset(json.dumps(altered).encode())

    def test_schema_supports_optional_sun_wind_and_explicit_bright_pixels(self):
        document = json.loads(load_preset('c4m3-overcast-static-v1').source_path.read_bytes())
        document['wind'] = None
        document['exposure']['bright_pixels'] = '6'
        document['roles']['sun'] = load_preset().role_values('sun')
        document['resources']['common'].append('materials/sprites/light_glow02_add_noz.vmt')
        parsed = parse_preset(json.dumps(document).encode())
        self.assertIsNone(parsed.wind_values)
        self.assertTrue(any(e.one('classname') == 'env_sun' for e in parsed.entities()))
        self.assertIn('materials/sprites/light_glow02_add_noz.vmt', parsed.required_resources())
        outputs = [p.value.split('\x1b') for e in parsed.entities() for p in e.pairs if p.key == 'OnMapSpawn']
        self.assertIn(['tonemap_global', 'SetTonemapPercentBrightPixels', '6', '0', '-1'], outputs)


if __name__ == '__main__':
    unittest.main()
