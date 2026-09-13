"""Generated ambience is bounded data, with a closed installed-audio dependency set."""
import copy
from types import SimpleNamespace
import unittest

from l4d2_bsp.soundscapes import preset_soundscape_assets, validate_soundscape_definitions


def dry_preset():
    return SimpleNamespace(
        soundscape_mapping={category: 'lmc_c4m3_overcast_v1.' + category
                            for category in ('outdoor', 'indoor')},
        soundscape_definition={
            'outdoor': {'dsp': '1', 'loops': []},
            'indoor': {'dsp': '1', 'loops': [{
                'wave': 'ambient/ambience/crucial_smallroomtone_amb_loop.wav',
                'volume': '0.28', 'pitch': '100'}]},
        })


class SoundscapeTests(unittest.TestCase):
    def test_rendered_map_script_contains_only_empty_outdoor_and_roomtone(self):
        from l4d2_bsp.presets import load_preset
        preset = load_preset('c4m3-overcast-static-v1')
        self.assertEqual(preset.soundscape_definition, dry_preset().soundscape_definition)
        assets = preset_soundscape_assets(preset, 'c2m1_highway')
        self.assertEqual(list(assets), ['scripts/soundscapes_c2m1_highway.txt'])
        payload = next(iter(assets.values())).decode('ascii')
        self.assertIn('"lmc_c4m3_overcast_v1.outdoor"\n{\n\t"dsp" "1"\n}', payload)
        self.assertEqual(payload.count('"playlooping"'), 1)
        self.assertIn('"volume" "0.28"', payload)
        self.assertIn('"pitch" "100"', payload)
        self.assertIn('"wave" "ambient/ambience/crucial_smallroomtone_amb_loop.wav"', payload)
        for excluded in ('rain', 'thunder', 'storm', 'fire', 'hail', 'playsoundscape', 'playrandom'):
            self.assertNotIn(excluded, payload.lower())
        self.assertEqual(validate_soundscape_definitions(
            preset.soundscape_definition, preset.soundscape_mapping),
            ('sound/ambient/ambience/crucial_smallroomtone_amb_loop.wav',))

    def test_rendering_is_deterministic_and_changes_only_filename_for_capture_alias(self):
        preset = dry_preset()
        original = copy.deepcopy(preset)
        canonical = preset_soundscape_assets(preset, 'c2m1_highway')
        alias = preset_soundscape_assets(preset, 'lmc_capture_c2m1_highway')
        self.assertEqual(next(iter(canonical.values())), next(iter(alias.values())))
        preset.soundscape_definition = dict(reversed(list(preset.soundscape_definition.items())))
        self.assertEqual(canonical, preset_soundscape_assets(preset, 'c2m1_highway'))
        self.assertEqual(preset.soundscape_definition, original.soundscape_definition)

    def test_legacy_preset_needs_no_generated_asset(self):
        from l4d2_bsp.presets import load_preset
        self.assertEqual(preset_soundscape_assets(load_preset(), 'c2m1_highway'), {})

    def test_categories_can_independently_have_dry_loops_or_be_empty(self):
        preset = dry_preset()
        preset.soundscape_mapping = {category: 'lmc_future_dry.' + category
                                     for category in ('outdoor', 'indoor')}
        preset.soundscape_definition['outdoor']['loops'] = [{
            'wave': 'ambient/ambience/birds_loop.wav', 'volume': '0.3', 'pitch': '100'}]
        preset.soundscape_definition['indoor']['loops'] = []
        payload = next(iter(preset_soundscape_assets(preset, 'future_map').values()))
        self.assertIn(b'"wave" "ambient/ambience/birds_loop.wav"', payload)
        self.assertIn(b'"lmc_future_dry.indoor"\n{\n\t"dsp" "1"\n}', payload)
        self.assertEqual(validate_soundscape_definitions(
            preset.soundscape_definition, preset.soundscape_mapping),
            ('sound/ambient/ambience/birds_loop.wav',))
        preset.soundscape_definition['outdoor']['loops'] = []
        self.assertNotIn(b'playlooping', next(iter(
            preset_soundscape_assets(preset, 'future_map').values())))
        self.assertEqual(validate_soundscape_definitions(
            preset.soundscape_definition, preset.soundscape_mapping), ())

    def test_duplicate_loop_is_rejected(self):
        preset = dry_preset()
        indoor = preset.soundscape_definition['indoor']['loops']
        indoor.append(copy.deepcopy(indoor[0]))
        with self.assertRaises(ValueError):
            preset_soundscape_assets(preset, 'c2m1_highway')

    def test_unsafe_map_names_cannot_escape_or_inject_asset_paths(self):
        for name in ('../cfg/autoexec', 'maps/c2m1', r'maps\c2m1', 'c2m1.bsp',
                     '', 'a\n', 'a"', 'a:b', None, 'x' * 65):
            with self.subTest(name=name), self.assertRaises(ValueError):
                preset_soundscape_assets(dry_preset(), name)

    def test_unsafe_definitions_and_unclosed_nested_sources_are_rejected(self):
        mutations = [
            (('indoor', 'dsp'), '1"\n}'),
            (('indoor', 'dsp'), 1),
            (('indoor', 'loops', 0, 'wave'), '../autoexec.cfg'),
            (('indoor', 'loops', 0, 'wave'), 'ambient/a.wav"\n}'),
            (('indoor', 'loops', 0, 'wave'), 'ambient//a.wav'),
            (('indoor', 'loops', 0, 'wave'), 'ambient/rainscapes/rain.wav'),
            (('indoor', 'loops', 0, 'wave'), 'ambient/fire_loop.wav'),
            (('indoor', 'loops', 0, 'volume'), 'nan'),
            (('indoor', 'loops', 0, 'volume'), '1.1'),
            (('indoor', 'loops', 0, 'pitch'), '0'),
            (('indoor', 'loops', 0, 'pitch'), '100\n'),
            (('indoor', 'loops', 0, 'position'), 'random'),
            (('indoor', 'playsoundscape'), 'storm.dynamic'),
        ]
        for path, value in mutations:
            preset = dry_preset()
            node = preset.soundscape_definition
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            with self.subTest(path=path, value=value), self.assertRaises(ValueError):
                preset_soundscape_assets(preset, 'c2m1_highway')
        for name in ('unsafe"\n}', 'urban2.respawn', 'lmc_bad/name'):
            preset = dry_preset()
            preset.soundscape_mapping['indoor'] = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                preset_soundscape_assets(preset, 'c2m1_highway')
        preset = dry_preset()
        preset.soundscape_mapping['indoor'] = preset.soundscape_mapping['outdoor']
        with self.assertRaises(ValueError):
            preset_soundscape_assets(preset, 'c2m1_highway')


if __name__ == '__main__':
    unittest.main()
