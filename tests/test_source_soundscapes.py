import tempfile
import unittest
from pathlib import Path

from tests.test_core import make_bsp, make_lmp
from tests.test_generic_conversion import ent


class SourceSoundscapeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file('scripts/soundscapes_manifest.txt', b'"soundscapes_manifest" { "file" "scripts/soundscapes_test.txt" }')
        self.file('sound/ambient/room.wav', b'room')
        self.file('sound/ambient/birds.wav', b'birds')
        self.file('sound/ambient/ambience/rain_on_tarp.wav', b'rain')

    def file(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def plan(self, script, names=('room',), modes=None):
        from l4d2_bsp.source_soundscapes import plan_source_soundscapes
        self.file('scripts/soundscapes_test.txt', script)
        data = make_bsp(ent('worldspawn') + b''.join(ent('env_soundscape', soundscape=n) for n in names) + b'\0')
        return plan_source_soundscapes(data, modes or {}, [self.root], 'custom_map')

    def test_nested_weather_removed_but_random_birds_and_position_preserved(self):
        p = self.plan(b'''room { playsoundscape { name mixed volume .5 position0 2 }
          playlooping { wave ambient/room.wav volume .2 } }
          mixed { playrandom { time "7,12" volume ".4,.5" position random
            rndwave { wave ambient/ambience/rain_on_tarp.wav wave ambient/birds.wav } } }''')
        self.assertNotIn('ambient/ambience/rain_on_tarp.wav', p['script'])
        self.assertIn('ambient/birds.wav', p['script'])
        self.assertIn('"position0" "2"', p['script'])
        self.assertIn('"time" "7,12"', p['script'])
        self.assertEqual(len(p['removed_layers']), 1)
        self.assertEqual(p, self.plan(b'''room { playsoundscape { name mixed volume .5 position0 2 }
          playlooping { wave ambient/room.wav volume .2 } }
          mixed { playrandom { time "7,12" volume ".4,.5" position random
            rndwave { wave ambient/ambience/rain_on_tarp.wav wave ambient/birds.wav } } }'''))

    def test_pure_weather_becomes_silent_definition_not_missing_reference(self):
        p = self.plan(b'room { dsp 1 playlooping { wave ambient/ambience/rain_on_tarp.wav volume .5 } }')
        self.assertIn('"dsp" "1"', p['script'])
        self.assertNotIn('playlooping', p['script'])
        self.assertIn(p['mapping']['room'], p['script'])

    def test_source_gulls_are_preserved(self):
        self.file('sound/ambient/random_amb_sfx/rur5b_seagull01.wav', b'gull')
        p = self.plan(b'room { playrandom { rndwave { wave ambient/random_amb_sfx/rur5b_seagull01.wav } time "7,12" } }')
        self.assertIn('rur5b_seagull01.wav', p['script'])
        self.assertEqual(p['removed_layers'], [])

    def test_installed_manifest_root_spelling_is_supported(self):
        self.file('scripts/soundscapes_manifest.txt', b'soundscaples_manifest { file scripts/soundscapes_test.txt }')
        self.assertTrue(self.plan(b'room { dsp 1 }')['mapping'])

    def test_missing_cycle_and_directive_fail_closed(self):
        for script, error in [(b'room { playsoundscape { name missing } }', 'Missing soundscape'),
                              (b'room { playsoundscape { name room } }', 'cycle'),
                              (b'#base "other.txt" room { dsp 1 }', 'directive')]:
            with self.subTest(script=script), self.assertRaisesRegex(ValueError, error):
                self.plan(script)

    def test_definition_precedence_uses_last_loaded_nonempty_definition(self):
        p = self.plan(b'room { dsp 1 } room { dsp 2 } room { }')
        self.assertIn('"dsp" "2"', p['script'])
        self.assertEqual(p['definitions'][0]['selected_occurrence'], 1)

    def test_fully_removed_weather_still_has_loadable_silent_definition(self):
        p = self.plan(b'room { playlooping { wave ambient/ambience/rain_on_tarp.wav } }')
        self.assertNotIn('"dsp"', p['script'])
        self.assertNotIn('"wave"', p['script'])
        self.assertIn('"playlooping"', p['script'])

    def test_ambiguous_playback_selectors_fail_without_deleting_normal_audio(self):
        for block in (
            b'playlooping { wave ambient/ambience/rain_on_tarp.wav wave ambient/room.wav }',
            b'playrandom { rndwave { wave ambient/birds.wav } rndwave { wave ambient/room.wav } }',
            b'playrandom { wave ambient/room.wav }',
        ):
            with self.subTest(block=block), self.assertRaisesRegex(ValueError, 'playback selector'):
                self.plan(b'room { ' + block + b' }')

    def test_map_local_and_mode_only_definition_are_available(self):
        self.file('scripts/soundscapes_custom_map.txt', b'local { playlooping { wave ambient/room.wav volume .1 } }')
        mode = make_lmp(ent('worldspawn') + ent('env_soundscape_triggerable', soundscape='local') + b'\0')
        p = self.plan(b'room { dsp 1 }', modes={'l': mode})
        self.assertEqual(set(p['mapping']), {'room', 'local'})

    def test_unknown_weather_is_reported_and_not_deleted(self):
        self.file('sound/custom/maybe_rain.wav', b'unknown')
        p = self.plan(b'room { playlooping { wave custom/maybe_rain.wav volume .2 } }')
        self.assertIn('custom/maybe_rain.wav', p['script'])
        self.assertTrue(p['warnings'])

    def test_random_pool_keeps_arbitrary_scalar_keys_and_filters_values(self):
        p = self.plan(b'room { playrandom { time "6,13" rndwave { bird ambient/birds.wav rain ambient/ambience/rain_on_tarp.wav } } }')
        self.assertIn('"bird" "ambient/birds.wav"', p['script'])
        self.assertNotIn('rain_on_tarp.wav', p['script'])

    def test_source_resource_drift_is_detected(self):
        from l4d2_bsp.source_soundscapes import verify_source_soundscapes
        p = self.plan(b'room { playlooping { wave ambient/room.wav volume .2 } }')
        self.file('sound/ambient/room.wav', b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            verify_source_soundscapes(p, [self.root], {})

    def test_embedding_preserves_original_map_script_and_unrelated_pak(self):
        from tests.test_native import bsp, pak
        from l4d2_bsp.binary import BspFile
        from l4d2_bsp.native import _pak_entries
        from l4d2_bsp.source_soundscapes import plan_source_soundscapes, embed_source_soundscapes, source_soundscape_assets
        original = b'room { playlooping { wave ambient/room.wav volume .2 } }'
        self.file('scripts/soundscapes_test.txt', b'unused { dsp 1 }')
        data = bsp({0: ent('worldspawn') + ent('env_soundscape', soundscape='room') + b'\0',
                    40: pak([('scripts/soundscapes_custom_map.txt', original), ('materials/unchanged.vmt', b'original')])})
        plan = plan_source_soundscapes(data, {}, [self.root], 'custom_map')
        out, audit = embed_source_soundscapes(data, plan)
        old, new = BspFile.parse(data), BspFile.parse(out)
        contents = _pak_entries(new.lump_bytes(40))
        self.assertTrue(contents['scripts/soundscapes_custom_map.txt'].startswith(original))
        self.assertIn(plan['mapping']['room'].encode(), contents['scripts/soundscapes_custom_map.txt'])
        self.assertEqual(contents['materials/unchanged.vmt'], b'original')
        self.assertTrue(all(old.lumps[i] == new.lumps[i] and old.lump_bytes(i) == new.lump_bytes(i) for i in range(64) if i != 40))
        self.assertEqual(source_soundscape_assets(plan, 'mc01234567')['scripts/soundscapes_mc01234567.txt'], contents['scripts/soundscapes_custom_map.txt'])
        self.assertTrue(audit['unrelated_pak_payloads_unchanged'])

    def test_parser_rejects_truncation_and_retains_ordered_duplicate_blocks(self):
        from l4d2_bsp.source_soundscapes import parse_keyvalues
        self.assertEqual(len(parse_keyvalues(b'room { dsp 1 playlooping { wave a } playlooping { wave b } }')[0][1]), 3)
        for raw in (b'room { dsp', b'room { dsp "bad', b'room { dsp 1 } [WIN32]', b'room { dsp 1 } /* unclosed'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_keyvalues(raw)
