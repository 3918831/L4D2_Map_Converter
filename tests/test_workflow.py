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

    def use_preset(self):
        for key in ('profile', 'reference_bsp', 'reference_lmp'):
            self.value.pop(key)
        self.value.update(source_profile='c6m1_riverbank', preset='c5m1-daylight-v1')

    def test_preset_config_needs_no_donor_files_and_inventories_preset(self):
        from l4d2_bsp.configuration import input_inventory
        from l4d2_bsp.presets import load_preset
        self.use_preset()
        for path in (self.root / 'input').glob('c5*'):
            path.unlink()
        cfg = self.load()
        self.assertEqual(cfg['profile'], 'c6-c5')
        self.assertEqual(cfg['preset'], 'c5m1-daylight-v1')
        self.assertIsNone(cfg['reference_bsp'])
        preset = load_preset()
        self.assertIn(str(preset.source_path), [x['path'] for x in input_inventory(cfg)])

    def test_preset_config_rejects_mixed_and_missing_selectors(self):
        original = dict(self.value)
        for extra in ({'preset': 'c5m1-daylight-v1'}, {'source_profile': 'c6m1_riverbank'}):
            self.value = original | extra
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, 'mix|together'):
                self.load()
        self.use_preset()
        self.value.pop('preset')
        with self.assertRaisesRegex(ValueError, 'together'):
            self.load()

    def test_preset_config_rejects_unknown_source_or_target(self):
        self.use_preset()
        self.value['preset'] = 'c7'
        with self.assertRaisesRegex(ValueError, 'preset'):
            self.load()

    def test_full_atmosphere_preset_selects_source_adapter_and_replace_policy(self):
        self.use_preset()
        self.value.update(source_profile='c2m1_highway', preset='c4m3-overcast-static-v1')
        for key in ('source_bsp','nav'):
            self.value[key] = self.value[key].replace('c6m1_riverbank','c2m1_highway')
            (self.root/self.value[key]).write_bytes(b'fixture')
        for mode in 'hls':
            self.value['mode_lmps'][mode] = f'input/c2m1_highway_{mode}_0.lmp'
            (self.root/self.value['mode_lmps'][mode]).write_bytes(b'fixture')
        cfg = self.load()
        self.assertEqual(cfg['profile'],'c2m1_highway')
        self.assertEqual(cfg['atmosphere_policy'],'replace')
        self.value['atmosphere_policy'] = 'preserve'
        with self.assertRaisesRegex(ValueError,'policy|replace'):
            self.load()

    def test_new_target_rejects_unvalidated_source_combination(self):
        self.use_preset()
        self.value['preset'] = 'c4m3-overcast-static-v1'
        with self.assertRaisesRegex(ValueError,'capability|combination|support'):
            self.load()
        self.value.update(preset='c5m1-daylight-v1', source_profile='c7m1_docks')
        with self.assertRaisesRegex(ValueError, 'source_profile'):
            self.load()

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
    def test_run_uses_its_preset_snapshot_and_rejects_identity_drift(self):
        from l4d2_bsp.presets import load_preset
        from l4d2_bsp.workflow import load_run, run_preset
        preset = load_preset()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = root / 'preset.json'
            snapshot.write_bytes(preset.source_path.read_bytes())
            report = {'schema_version': 1, 'input_inventory': [], 'tracked_outputs': [],
                      'config': {'preset': preset.id},
                      'preset': preset.metadata() | {'snapshot_path': str(snapshot)}}
            (root / 'run.json').write_text(json.dumps(report))
            self.assertEqual(run_preset(load_run(root)).capture_exposure_max, 5)
            snapshot.write_bytes(snapshot.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'snapshot changed'):
                load_run(root)
            report.pop('preset')
            (root / 'run.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, 'Missing run preset'):
                load_run(root)

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
