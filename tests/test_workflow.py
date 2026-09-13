import json
from pathlib import Path
import tempfile
import unittest


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.json'
        for name in ('game/left4dead2/gameinfo.txt', 'tools/bin/vrad.exe', 'tools/bin/bspzip.exe', 'tools/bin/vpk.exe',
                     'input/c6m1_riverbank.bsp', 'input/c5m1_waterfront.bsp', 'input/c5m1_waterfront_l_0.lmp',
                     'input/c6m1_riverbank.nav', *('input/c6m1_riverbank_' + x + '_0.lmp' for x in 'hls')):
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'fixture')
        self.value = dict(profile='c6-c5', source_bsp='input/c6m1_riverbank.bsp', reference_bsp='input/c5m1_waterfront.bsp',
                          reference_lmp='input/c5m1_waterfront_l_0.lmp',
                          mode_lmps={x: 'input/c6m1_riverbank_' + x + '_0.lmp' for x in 'hls'},
                          nav='input/c6m1_riverbank.nav', game_dir='game/left4dead2', tools_dir='tools/bin', output_dir='runs/test')

    def load(self):
        from l4d2_bsp.configuration import load_config
        self.config.write_text(json.dumps(self.value), encoding='utf-8')
        return load_config(self.config)

    def test_relative_paths_use_config_directory(self):
        cfg = self.load()
        self.assertEqual(cfg['source_bsp'], (self.root / 'input/c6m1_riverbank.bsp').resolve())
        self.assertEqual(cfg['threads'], 4)
        self.assertFalse((self.root / 'runs').exists())

    def test_c6_defaults_to_replace_and_can_explicitly_preserve_atmosphere(self):
        self.assertEqual(self.load()['atmosphere_policy'], 'replace')
        self.value['atmosphere_policy'] = 'preserve'
        self.assertEqual(self.load()['atmosphere_policy'], 'preserve')

    def test_invalid_atmosphere_policy_is_rejected(self):
        for value in (None, True, 'rain', 'REPLACE', {}):
            self.value['atmosphere_policy'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'atmosphere_policy'):
                self.load()

    def test_rejects_writing_anywhere_inside_game_or_tools_install(self):
        for path in ('game/new-run', 'tools/../game/left4dead2/new-run', 'tools/new-run'):
            with self.subTest(path=path):
                self.value['output_dir'] = path
                with self.assertRaisesRegex(ValueError, 'installation'):
                    self.load()

    def test_rejects_output_containing_inputs(self):
        self.value['output_dir'] = 'input'
        with self.assertRaisesRegex(ValueError, 'input'):
            self.load()

    def test_rejects_non_ascii_output_before_expensive_work(self):
        self.value['output_dir'] = 'runs/地图'
        with self.assertRaisesRegex(ValueError, 'ASCII'):
            self.load()

    def test_rejects_wrong_map_mode_pairing(self):
        self.value['mode_lmps']['h'] = self.value['mode_lmps']['l']
        with self.assertRaisesRegex(ValueError, 'mode'):
            self.load()

    def test_rejects_unknown_keys_and_invalid_thread_type(self):
        self.value['threds'] = 4
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            self.load()
        del self.value['threds']
        self.value['threads'] = True
        with self.assertRaisesRegex(ValueError, 'threads'):
            self.load()

    def test_rejects_incomplete_install_and_missing_modes(self):
        (self.root / 'tools/bin/vrad.exe').unlink()
        with self.assertRaisesRegex(ValueError, 'vrad'):
            self.load()

    def test_manifest_rejects_changed_input(self):
        from l4d2_bsp.configuration import input_inventory, verify_inventory
        cfg = self.load()
        inventory = input_inventory(cfg)
        verify_inventory(inventory)
        cfg['nav'].write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            verify_inventory(inventory)


if __name__ == '__main__':
    unittest.main()


class RunIdentityTests(unittest.TestCase):
    def test_native_import_failure_records_error_and_allows_new_attempt(self):
        from l4d2_bsp.workflow import finish
        from unittest.mock import patch
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline, captured, log = root / 'base.bsp', root / 'capture.bsp', root / 'capture.log'
            baseline.write_bytes(b'baseline')
            captured.write_bytes(b'capture')
            log.write_text('LMC_ABC_MAP=mctest\nLMC_ABC_GUARD_PASSED_CAPTURE_NOT_VERIFIED\nLMC_ABC_CAPTURE_REQUESTED\n')
            report = {'schema_version': 1, 'input_inventory': [], 'tracked_outputs': [], 'run_id': 'abc',
                      'status': 'capture_installed', 'map_name': 'c2m1_highway', 'capture_alias': 'mctest',
                      'offline_map': str(baseline), 'config': {'tools_dir': str(root), 'game_dir': str(root)}}
            (root / 'run.json').write_text(json.dumps(report))
            with patch('l4d2_bsp.reflections.capture_replacements', return_value=({'materials/maps/c2m1_highway/c0_0_0.hdr.vtf': b'vtf'}, {})), \
                 patch('l4d2_bsp.native.native', side_effect=subprocess.CalledProcessError(1, 'bspzip.exe')):
                for attempt in (1, 2):
                    with self.assertRaises(subprocess.CalledProcessError):
                        finish(root, captured, log)
                    self.assertTrue((root / f'reflection-import/attempt-{attempt:02}/captured.bsp').is_file())
            result = json.loads((root / 'run.json').read_text())
            self.assertEqual(result['status'], 'capture_installed')
            self.assertIn('bspzip', result['last_capture_import_error'])

    def test_reflection_repack_rejects_changed_protected_header(self):
        from l4d2_bsp.workflow import audit_repacked
        from tests.test_style import wrap, style_text
        import struct
        original = wrap(style_text())
        altered = bytearray(original)
        struct.pack_into('<i', altered, 24, 999)
        with self.assertRaisesRegex(ValueError, 'protected'):
            audit_repacked(original, bytes(altered), {})

    def test_import_rejects_unbound_request_before_reading_capture_resources(self):
        from l4d2_bsp.workflow import finish
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline, captured, log = root/'base.bsp', root/'capture.bsp', root/'capture.log'
            baseline.write_bytes(b'baseline')
            captured.write_bytes(b'capture')
            log.write_text('abc mctest OTHER_CAPTURE_REQUESTED\n')
            report = {'schema_version': 1, 'input_inventory': [], 'tracked_outputs': [], 'run_id': 'abc',
                      'status': 'capture_installed', 'map_name': 'c2m1_highway', 'capture_alias': 'mctest',
                      'offline_map': str(baseline), 'config': {}}
            (root/'run.json').write_text(json.dumps(report))
            with patch('l4d2_bsp.reflections.capture_replacements', side_effect=AssertionError('unbound log accepted')):
                with self.assertRaisesRegex(ValueError, 'marker|guard'):
                    finish(root, captured, log)

    def test_addon_metadata_identifies_original_map_and_phase(self):
        from l4d2_bsp.workflow import addon_info
        content = addon_info('c6m1_riverbank', 'offline')
        self.assertIn(b'"addonSteamAppID" "550"', content)
        self.assertIn(b'c6m1_riverbank', content)
        self.assertIn(b'offline', content)

    def test_loading_run_detects_tampered_offline_map(self):
        from l4d2_bsp.workflow import load_run
        from l4d2_bsp.configuration import file_hash
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'map.bsp'
            source.write_bytes(b'original')
            report = {'schema_version': 1, 'input_inventory': [], 'tracked_outputs': [
                {'path': str(source), 'sha256': file_hash(source)}]}
            (root / 'run.json').write_text(json.dumps(report))
            load_run(root)
            source.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'changed'):
                load_run(root)

    def test_status_does_not_claim_runtime_acceptance_for_built_file(self):
        from l4d2_bsp.workflow import status_summary
        result = status_summary({'status': 'offline_ready', 'profile': 'c6-c5', 'run_id': 'abc', 'map_name': 'c6m1_riverbank'})
        self.assertFalse(result['runtime_accepted'])
