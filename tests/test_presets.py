"""Packaged visual presets load without game files and reject unsafe data."""
import copy
import hashlib
import importlib.util
import json
import unittest


class PresetTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('l4d2_bsp.presets'),
                             'Versioned preset implementation is missing')
        from l4d2_bsp.presets import load_preset, parse_preset
        self.load, self.parse = load_preset, parse_preset

    def document(self):
        return json.loads(self.load().source_path.read_bytes())

    def test_packaged_view_retains_exact_visual_values_without_geometry(self):
        preset = self.load('c5m1-daylight-v1')
        entities = preset.entities('bsp')
        by_class = {e.one('classname'): e for e in entities}
        by_name = {e.one('targetname'): e for e in entities if e.one('targetname')}
        self.assertEqual(by_class['worldspawn'].one('skyname'), 'sky_l4d_c5_1_hdr')
        self.assertEqual(by_class['light_environment'].one('_light'), '209 154 109 1000')
        self.assertEqual(by_class['sky_camera'].one('HDRColorScale'), '1.0')
        self.assertEqual(by_name['fx_settings_exterior'].one('localcontraststrength'), '-.2')
        self.assertEqual(by_name['fog_master'].one('spawnflags'), '1')
        self.assertEqual(by_name['foginteriorcontroller'].one('spawnflags'), '0')
        self.assertEqual(entities, preset.entities('lmp'))
        for entity in entities:
            for key in ('origin', 'world_mins', 'world_maxs', 'model', 'vscripts', 'hammerid'):
                self.assertIsNone(entity.one(key))
        self.assertEqual(preset.sha256, hashlib.sha256(preset.source_path.read_bytes()).hexdigest())
        self.assertEqual(preset.metadata()['id'], 'c5m1-daylight-v1')
        self.assertEqual(preset.metadata()['sha256'], preset.sha256)

    def test_exposure_view_has_only_bounded_original_startup_outputs(self):
        preset = self.load()
        outputs = [p.value.split('\x1b') for e in preset.entities() for p in e.pairs
                   if p.key.startswith('On')]
        expected = [
            ['tonemap_global_infected', 'SetAutoExposureMin', '1', '0', '-1'],
            ['tonemap_global_infected', 'SetAutoExposureMax', '5', '0', '-1'],
            ['tonemap_global_ghost', 'SetAutoExposureMin', '1', '0', '-1'],
            ['tonemap_global_ghost', 'SetAutoExposureMax', '5', '0', '-1'],
            ['tonemap_global', 'SetTonemapPercentBrightPixels', '5', '0', '-1'],
            ['tonemap_global', 'SetTonemapRate', '.25', '0', '-1'],
            ['tonemap_global', 'SetAutoExposureMin', '1', '0', '-1'],
            ['tonemap_global', 'SetAutoExposureMax', '5', '0', '-1'],
        ]
        self.assertEqual(outputs, expected)
        self.assertEqual(preset.capture_exposure_max, 5.0)

    def test_resources_follow_atmosphere_policy_and_views_are_independent(self):
        preset = self.load()
        preserve = preset.required_resources('preserve')
        replace = preset.required_resources('replace')
        self.assertEqual(len(preserve), 8)
        self.assertIn('materials/skybox/sky_l4d_c5_1_hdrup.vmt', preserve)
        self.assertNotIn('scripts/soundscapes_campaign5.txt', preserve)
        self.assertIn('scripts/soundscapes_campaign5.txt', replace)
        self.assertIn('sound/ambient/random_amb_sounds/rand_gulls_06.wav', replace)
        self.assertEqual(len(replace), 24)
        self.assertEqual(preset.soundscape_mapping,
                         {'outdoor': 'c5m1.waterfront', 'indoor': 'c5m1.smallstore'})
        replace.clear()
        preset.soundscape_mapping.clear()
        preset.entities().clear()
        self.assertEqual(len(preset.required_resources('replace')), 24)
        self.assertEqual(preset.soundscape_mapping['outdoor'], 'c5m1.waterfront')
        self.assertTrue(preset.entities())

    def test_unknown_identifiers_kinds_and_policies_are_rejected(self):
        for identifier in ('../c5m1-daylight-v1', 'c7-daylight-v1', '', None):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.load(identifier)
        with self.assertRaises(ValueError):
            self.load().entities('zip')
        with self.assertRaises(ValueError):
            self.load().required_resources('storm')

    def test_malformed_schema_cannot_import_gameplay_or_invalid_visual_values(self):
        original = self.document()
        mutations = [
            (('schema_version',), 2), (('schema_version',), True),
            (('id',), 'c7-daylight-v1'), (('entities',), []),
            (('roles', 'world', 'origin'), '0 0 0'),
            (('roles', 'world', 'OnMapSpawn'), 'target\x1bKill\x1b\x1b0\x1b-1'),
            (('roles', 'world', 'vscripts'), 'evil.nut'),
            (('roles', 'sun', 'classname'), 'point_servercommand'),
            (('roles', 'environment', 'angles'), '0 150'),
            (('roles', 'environment', '_light'), '256 0 0 5'),
            (('roles', 'shadow', 'color'), 'nan 0 0'),
            (('roles', 'fog_outdoor', 'fogmaxdensity'), '1.1'),
            (('roles', 'postprocess_exterior', 'grainstrength'), float('inf')),
            (('roles', 'postprocess_exterior', 'grainstrength'), '1\n'),
            (('roles', 'sun', 'material'), '../bad'),
            (('roles', 'color_main', 'filename'), 'materials/../autoexec.cfg'),
            (('exposure', 'maximum'), 'nan'),
            (('exposure', 'maximum'), '0'),
            (('exposure', 'maximum'), 5),
            (('exposure', 'output'), 'RunScriptCode'),
            (('soundscapes', 'outdoor'), 'unsafe\x1bname'),
            (('resources', 'common'), ['C:/game/secret.raw']),
            (('resources', 'clear'), ['scripts/vscripts/evil.nut']),
        ]
        for path, value in mutations:
            data = copy.deepcopy(original)
            node = data
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            with self.subTest(path=path, value=value), self.assertRaises(ValueError):
                self.parse(json.dumps(data).encode())
        for path in [('roles', 'world'), ('roles', 'world', 'skyname'), ('resources',)]:
            data = copy.deepcopy(original)
            node = data
            for key in path[:-1]:
                node = node[key]
            del node[path[-1]]
            with self.subTest(missing=path), self.assertRaises(ValueError):
                self.parse(json.dumps(data).encode())

    def test_duplicate_keys_and_invalid_json_are_rejected(self):
        raw = self.load().source_path.read_bytes()
        duplicate = raw.replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')
        for data in (duplicate, b'[]', b'null', b'{', b'\xff'):
            with self.subTest(data=data[:30]), self.assertRaises(ValueError):
                self.parse(data)

    def test_missing_or_extra_clear_dependency_is_rejected_before_preflight(self):
        original = self.document()
        for change in ('missing', 'extra', 'duplicate'):
            data = copy.deepcopy(original)
            paths = data['resources']['clear']
            if change == 'missing':
                paths.pop()
            elif change == 'extra':
                paths.append('scripts/soundscapes_unrelated.txt')
            else:
                paths.append(paths[0])
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.parse(json.dumps(data).encode())


if __name__ == '__main__':
    unittest.main()
