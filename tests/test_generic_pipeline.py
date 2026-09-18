import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_style import entity, wrap


def capture_audit(name='__lmc_tonemap_v1', **plan):
    return {'capture_tonemap': name, 'plan': {'capture_tonemap': name, **plan}}


class GenericConfigurationTests(unittest.TestCase):
    def test_v3_pipeline_removes_lightning_and_rejects_rule_downgrade(self):
        import copy
        from l4d2_bsp.workflow import check, run_capture_tonemap
        from l4d2_bsp.style import _read
        self.value['conversion'] = 'generic-replace-v3'
        text = (entity('worldspawn', skyname='old') +
                entity('info_particle_system', effect_name='storm_cloud_parent', start_active='1') + b'\0')
        self.file('input/custom-map.bsp', wrap(text))
        self.file('input/custom-map_l_0.lmp', wrap(text, 'lmp'))
        self.load()
        with patch('l4d2_bsp.resources.lookup_resources', return_value={'resources': {}}):
            cfg, report, output, modes = check(self.config)
        self.assertNotIn(b'storm_cloud_parent', _read(output, 'bsp')[1])
        self.assertNotIn(b'storm_cloud_parent', _read(modes['l'], 'lmp')[1])
        run = {'config': cfg, 'preflight': report}
        self.assertEqual(run_capture_tonemap(run), '__lmc_tonemap_v1')
        for rule in ('generic-replace-v1', 'generic-replace-v2', None):
            changed = copy.deepcopy(run)
            changed['config']['conversion'] = rule
            with self.subTest(rule=rule), self.assertRaisesRegex(ValueError, 'rule|conversion'):
                run_capture_tonemap(changed)
        report['mode_styles']['l']['plan']['conversion'] = 'generic-replace-v2'
        with self.assertRaisesRegex(ValueError, 'rule|conversion'):
            run_capture_tonemap(run)

    def test_v2_selection_is_recorded_in_base_and_mode_plans(self):
        from l4d2_bsp.workflow import check, run_capture_tonemap
        self.value['conversion'] = 'generic-replace-v2'
        self.file('input/custom-map_l_0.lmp', wrap(entity('worldspawn', skyname='old') + b'\0', 'lmp'))
        self.load()
        with patch('l4d2_bsp.resources.lookup_resources', return_value={'resources': {}}):
            cfg, report, _, _ = check(self.config)
        self.assertEqual(cfg['profile'], 'generic-replace-v2')
        for audit in [report['base_style'], *report['mode_styles'].values()]:
            self.assertEqual(audit['plan']['conversion'], 'generic-replace-v2')
        run = {'config': cfg, 'preflight': report}
        self.assertEqual(run_capture_tonemap(run), '__lmc_tonemap_v1')
        report['mode_styles']['l']['plan']['conversion'] = 'generic-replace-v1'
        with self.assertRaisesRegex(ValueError, 'rule|conversion'):
            run_capture_tonemap(run)

    def test_v2_manifest_cannot_fall_back_to_legacy_or_v1_capture(self):
        import copy
        from l4d2_bsp.configuration import input_inventory
        from l4d2_bsp.workflow import check, load_run, run_capture_tonemap
        self.value['conversion'] = 'generic-replace-v2'
        self.load()
        with patch('l4d2_bsp.resources.lookup_resources', return_value={'resources': {}}):
            cfg, preflight, _, _ = check(self.config)
        snapshot = self.file('preset.json', cfg['preset_file'].read_bytes())
        report = dict(schema_version=1, profile=cfg['profile'], config=cfg, preflight=preflight,
                      preset=preflight['preset'] | {'snapshot_path': str(snapshot)},
                      input_inventory=input_inventory(cfg), tracked_outputs=[])
        report = json.loads(json.dumps(report, default=str))
        for value in (None, 'generic-replace-v3', 'generic-replace-v1'):
            changed = copy.deepcopy(report)
            if value is None:
                del changed['config']['conversion']
            else:
                changed['config']['conversion'] = value
            (self.root / 'run.json').write_text(json.dumps(changed))
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'rule|conversion'):
                    run_capture_tonemap(changed)
                with self.assertRaisesRegex(ValueError, 'rule|conversion'):
                    load_run(self.root)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / 'config.json'
        self.value = dict(conversion='generic-replace-v1', preset='c5m1-daylight-v1',
                          source_bsp='input/custom-map.bsp', game_dir='game/left4dead2',
                          tools_dir='tools/bin', output_dir='runs/test')
        for name in ('game/left4dead2/gameinfo.txt', 'tools/bin/vrad.exe', 'tools/bin/bspzip.exe',
                     'tools/bin/vpk.exe', 'input/custom-map.nav'):
            self.file(name)
        self.file('input/custom-map.bsp', wrap(entity('worldspawn', skyname='old') + b'\0'))

    def file(self, name, data=b'fixture'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def load(self):
        from l4d2_bsp.configuration import load_config
        self.config.write_text(json.dumps(self.value))
        return load_config(self.config)

    def test_empty_and_single_mode_are_actual_inputs(self):
        cfg = self.load()
        self.assertEqual(cfg['profile'], 'generic-replace-v1')
        self.assertEqual(cfg['map_name'], 'custom-map')
        self.assertEqual(cfg['source_profile'], 'custom-map')
        self.assertEqual(cfg['mode_lmps'], {})
        self.assertIsNone(cfg['reference_bsp'])
        self.assertIsNone(cfg['reference_lmp'])
        self.assertEqual(cfg['atmosphere_policy'], 'replace')
        mode = self.file('input/custom-map_l_0.lmp')
        self.assertEqual(self.load()['mode_lmps'], {'l': mode})

    def test_default_mount_priority_never_replaces_explicit_source(self):
        from l4d2_bsp.configuration import input_inventory
        lower = self.file('game/left4dead2/maps/custom-map_l_0.lmp', b'lower')
        preferred = self.file('game/update/maps/custom-map_l_0.lmp', b'preferred')
        self.file('game/update/maps/custom-map.bsp', b'different')
        cfg = self.load()
        self.assertEqual(cfg['source_bsp'], self.root / 'input/custom-map.bsp')
        self.assertEqual(cfg['mode_lmps']['l'], preferred)
        paths = [item['path'] for item in input_inventory(cfg)]
        self.assertIn(str(lower), paths)
        self.assertIn(str(preferred), paths)

    def test_explicit_search_order_and_discovery_changes_are_tracked(self):
        from l4d2_bsp.workflow import load_run
        a = self.file('a/custom-map_l_0.lmp')
        self.file('b/custom-map_l_0.lmp')
        self.value['search_dirs'] = ['a', 'b']
        cfg = self.load()
        self.assertEqual(cfg['mode_lmps']['l'], a)
        report = dict(schema_version=1, config=cfg, input_inventory=[], tracked_outputs=[],
                      preflight={'base_style': capture_audit(), 'mode_styles': {'l': capture_audit()}})
        report['config'] = {key: value for key, value in cfg.items() if key not in ('preset', 'preset_file')}
        (self.root / 'run.json').write_text(json.dumps(report, default=str))
        load_run(self.root)
        self.file('a/custom-map_h_0.lmp')
        with self.assertRaisesRegex(ValueError, 'discovery|companions'):
            load_run(self.root)

    def test_unknown_modes_indexes_and_tokens_are_rejected(self):
        for name in ('custom-map_l_1.lmp', 'custom-map_v_0.lmp', 'custom-map_versus_0.lmp'):
            path = self.file('input/' + name)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Unsupported.*companion|unsupported.*companion'):
                self.load()
            path.unlink()

    def test_nav_required_and_explicit_companion_names_checked(self):
        (self.root / 'input/custom-map.nav').unlink()
        with self.assertRaisesRegex(ValueError, 'NAV|nav'):
            self.load()
        self.file('external/custom-map.nav')
        self.value['nav'] = 'external/custom-map.nav'
        self.assertEqual(self.load()['nav'], self.root / self.value['nav'])
        self.value['nav'] = 'external/wrong.nav'
        with self.assertRaisesRegex(ValueError, 'NAV|nav'):
            self.load()

    def test_mixed_selectors_and_unsafe_map_names_rejected(self):
        original = dict(self.value)
        for key in ('profile', 'source_profile', 'reference_bsp', 'reference_lmp', 'mode_lmps'):
            self.value = original | {key: 'unused'}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'mix|discovered'):
                self.load()
        self.value = original | {'atmosphere_policy': 'preserve'}
        with self.assertRaisesRegex(ValueError, 'replace'):
            self.load()
        self.value = original | {'source_bsp': 'input/unsafe;quit.bsp'}
        self.file(self.value['source_bsp'])
        with self.assertRaises(ValueError):
            self.load()

    def test_check_routes_generic_source_and_only_actual_modes_and_preserves_plan(self):
        import sys
        import types
        from l4d2_bsp.workflow import check
        self.file('input/custom-map_l_0.lmp', wrap(entity('worldspawn') + b'\0', 'lmp'))
        self.load()
        def transfer(data, preset, *, kind='bsp'):
            return b'converted-' + kind.encode(), capture_audit(kind=kind)
        core = types.ModuleType('l4d2_bsp.generic_conversion')
        core.transfer_generic = transfer
        with patch.dict(sys.modules, {'l4d2_bsp.generic_conversion': core}), \
             patch('l4d2_bsp.resources.lookup_resources', return_value={'resources': {}}), \
             patch('l4d2_bsp.profiles.transfer_style', side_effect=AssertionError('legacy adapter called')):
            cfg, report, output, modes = check(self.config)
        self.assertEqual(output, b'converted-bsp')
        self.assertEqual(modes, {'l': b'converted-lmp'})
        self.assertEqual(report['map_name'], 'custom-map')
        self.assertEqual(report['base_style']['plan'], capture_audit(kind='bsp')['plan'])
        self.assertEqual(report['discovery'], cfg['discovery'])

    def test_check_rejects_different_capture_names_between_base_and_modes(self):
        import sys
        import types
        from l4d2_bsp.workflow import check
        self.file('input/custom-map_l_0.lmp', wrap(entity('worldspawn') + b'\0', 'lmp'))
        self.load()
        core = types.ModuleType('l4d2_bsp.generic_conversion')
        core.transfer_generic = lambda data, preset, kind='bsp': (data, capture_audit(kind))
        with patch.dict(sys.modules, {'l4d2_bsp.generic_conversion': core}), \
             patch('l4d2_bsp.resources.lookup_resources', return_value={'resources': {}}):
            with self.assertRaisesRegex(ValueError, 'tonemap.*differ|different.*tonemap'):
                check(self.config)

    def test_real_check_converts_arbitrary_map_with_both_presets(self):
        from l4d2_bsp.binary import BspFile, LumpFile
        from l4d2_bsp.workflow import check
        self.file('input/custom-map_l_0.lmp', wrap(entity('worldspawn', skyname='old') + b'\0', 'lmp'))
        for preset in ('c5m1-daylight-v1', 'c4m3-overcast-static-v1'):
            self.value['preset'] = preset
            self.load()
            with patch('l4d2_bsp.resources.lookup_resources', return_value={'resources': {}}):
                cfg, report, output, modes = check(self.config)
            with self.subTest(preset=preset):
                self.assertEqual(report['map_name'], 'custom-map')
                self.assertEqual(set(modes), {'l'})
                self.assertEqual(BspFile.parse(output).lump_bytes(1), b'KEEP')
                self.assertNotIn(b'"skyname" "old"', BspFile.parse(output).lump_bytes(0))
                mode = LumpFile.parse(modes['l'])
                self.assertNotIn(b'"skyname" "old"', mode.data[mode.offset:mode.offset + mode.length])
                self.assertEqual(report['base_style']['capture_tonemap'], report['mode_styles']['l']['capture_tonemap'])
                self.assertTrue(report['base_style']['plan'])

    def test_build_keeps_plan_and_fails_on_native_protected_data_audit(self):
        from l4d2_bsp.workflow import build
        cfg = self.load()
        class Evidence:
            def __init__(self, *args):
                pass
            def inventory(self):
                return []
            def verify_current(self):
                pass
        def bake(tool, args, **kwargs):
            kwargs['log'].write_text('')
        with patch('l4d2_bsp.resources.lookup_resources', return_value={'resources': {}}), \
             patch('l4d2_bsp.model_lighting.ModelLightingEvidence', Evidence), \
             patch('l4d2_bsp.native.native', bake), \
             patch('l4d2_bsp.native.audit_bake', side_effect=ValueError('protected data changed')), \
             patch('l4d2_bsp.native.package_files', side_effect=AssertionError('packaged rejected bake')):
            with self.assertRaisesRegex(ValueError, 'protected data changed'):
                build(self.config)
        report = json.loads((cfg['output_dir'] / 'run.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['map_name'], 'custom-map')
        self.assertTrue(report['preflight']['base_style']['plan'])
        self.assertNotIn('offline_package', report)

    def test_load_run_rejects_missing_mode_audits_and_capture_plan_drift(self):
        import copy
        from l4d2_bsp.configuration import input_inventory
        from l4d2_bsp.workflow import check, load_run
        for mode in 'hl':
            self.file(f'input/custom-map_{mode}_0.lmp', wrap(entity('worldspawn') + b'\0', 'lmp'))
        self.load()
        with patch('l4d2_bsp.resources.lookup_resources', return_value={'resources': {}}):
            cfg, preflight, _, _ = check(self.config)
        snapshot = self.file('preset.json', cfg['preset_file'].read_bytes())
        report = dict(schema_version=1, config=cfg, preflight=preflight,
                      preset=preflight['preset'] | {'snapshot_path': str(snapshot)},
                      input_inventory=input_inventory(cfg), tracked_outputs=[])
        report = json.loads(json.dumps(report, default=str))
        manifest = self.root / 'run.json'
        manifest.write_text(json.dumps(report))
        load_run(self.root)
        for mutation in ('one_mode', 'all_modes', 'mode_styles', 'config_modes',
                         'base_summary', 'all_summaries', 'base_plan', 'mode_plan', 'missing_plan'):
            changed = copy.deepcopy(report)
            preflight = changed['preflight']
            if mutation == 'one_mode':
                del preflight['mode_styles']['h']
            elif mutation == 'all_modes':
                preflight['mode_styles'] = {}
            elif mutation == 'mode_styles':
                del preflight['mode_styles']
            elif mutation == 'config_modes':
                changed['config']['mode_lmps'] = {}
                preflight['mode_styles'] = {}
            elif mutation == 'base_summary':
                preflight['base_style']['capture_tonemap'] = 'other_safe_name'
            elif mutation == 'all_summaries':
                for audit in [preflight['base_style'], *preflight['mode_styles'].values()]:
                    audit['capture_tonemap'] = 'other_safe_name'
            elif mutation == 'base_plan':
                preflight['base_style']['plan']['capture_tonemap'] = 'other_safe_name'
            elif mutation == 'mode_plan':
                preflight['mode_styles']['l']['plan']['capture_tonemap'] = 'other_safe_name'
            else:
                del preflight['mode_styles']['l']['plan']
            manifest.write_text(json.dumps(changed))
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'mode|tonemap|plan'):
                load_run(self.root)

    def test_generic_map_length_is_rejected_before_build(self):
        self.value['source_bsp'] = 'input/' + 'a' * 65 + '.bsp'
        self.file(self.value['source_bsp'])
        self.file('input/' + 'a' * 65 + '.nav')
        with self.assertRaisesRegex(ValueError, 'map name|map_name'):
            self.load()

    def test_explicit_invalid_companion_path_does_not_fall_back_to_discovery(self):
        for key in ('nav', 'exclude'):
            self.value[key] = False
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Paths'):
                self.load()
            del self.value[key]

    def test_native_mount_and_output_safety_still_apply(self):
        for changes in ({'output_dir': 'game/run'}, {'output_dir': 'input'},
                        {'native_mounts': 'bad'}, {'threads': True}, {'search_dirs': 'input'},
                        {'search_dirs': ['missing']}, {'resource_roots': []}):
            original = dict(self.value)
            self.value.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load()
            self.value = original


class GenericCaptureTests(unittest.TestCase):
    def test_manual_and_auto_controls_use_explicit_safe_tonemap(self):
        from l4d2_bsp.reflections import capture_controls
        from l4d2_bsp.auto_capture import controls
        for name in ('__lmc_tonemap_v1', 'Authored-Tonemap_2'):
            manual = capture_controls(map_name='custom-map', alias='mc01234567', marker='run123', tonemap_name=name)
            auto = controls('0123456789abcdef', 'mc01234567', 5, 'normal.cfg', 5, tonemap_name=name)
            for files in (manual, auto):
                script = b'\n'.join(files.values())
                self.assertIn(('FindByName(null, "' + name + '")').encode(), script)
                self.assertNotIn(b'"tonemap_global"', script)
        for name in ('bad;quit', 'a"b', 'a\\b', 'a*', '', None, 'a\nb'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                capture_controls(map_name='custom', alias='mc01234567', marker='run123', tonemap_name=name)
            with self.subTest(auto=name), self.assertRaises(ValueError):
                controls('0123456789abcdef', 'mc01234567', 5, 'normal.cfg', 5, tonemap_name=name)

    def test_generic_metadata_accepts_safe_map_names(self):
        from l4d2_bsp.workflow import addon_info
        from l4d2_bsp.soundscapes import preset_soundscape_assets
        from l4d2_bsp.presets import load_preset
        self.assertIn(b'custom-map', addon_info('custom-map', 'offline'))
        self.assertIn('scripts/soundscapes_custom-map.txt', preset_soundscape_assets(load_preset('c4m3-overcast-static-v1'), 'custom-map'))
        for name in ('bad;quit', '../bad', 'bad"', '', None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                addon_info(name, 'offline')

    def test_run_capture_name_is_shared_and_missing_generic_metadata_fails_closed(self):
        from l4d2_bsp import workflow
        self.assertTrue(hasattr(workflow, 'run_capture_tonemap'), 'Run capture tonemap reader is missing')
        self.assertEqual(workflow.run_capture_tonemap({}), 'tonemap_global')
        report = {'config': {'conversion': 'generic-replace-v1', 'mode_lmps': {}, 'discovery': {'mode_lmps': {}}},
                  'preflight': {'base_style': capture_audit(), 'mode_styles': {}}}
        self.assertEqual(workflow.run_capture_tonemap(report), '__lmc_tonemap_v1')
        del report['preflight']['base_style']['capture_tonemap']
        with self.assertRaisesRegex(ValueError, 'tonemap'):
            workflow.run_capture_tonemap(report)

    def test_default_capture_payloads_keep_previous_bytes(self):
        import hashlib
        from l4d2_bsp.reflections import capture_controls
        from l4d2_bsp.auto_capture import controls
        manual = capture_controls(map_name='source', alias='mc01234567', marker='run123')
        auto = controls('0123456789abcdef', 'mc01234567', 5, 'normal.cfg', 5)
        expected = ('abcda6f07c5d9b56299f36e221b4e6825215134bddf01e929a36fce9db13414e',
                    '63e4d9fa543d6f814edd6ff5a499bf44cd1856ca27d35375384853e04eb73624')
        for files, digest in zip((manual, auto), expected):
            self.assertEqual(hashlib.sha256(b''.join(key.encode() + b'\0' + files[key] for key in sorted(files))).hexdigest(), digest)

    def test_prepare_writes_guard_bound_to_generic_plan(self):
        from l4d2_bsp.workflow import prepare
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            baseline = root / 'map.bsp'
            baseline.write_bytes(b'baseline')
            report = dict(status='offline_ready', map_name='custom-map', run_id='0123456789abcdef',
                          config={'conversion': 'generic-replace-v1', 'game_dir': str(root),
                                  'mode_lmps': {}, 'discovery': {'mode_lmps': {}}},
                          offline_map=str(baseline), offline_package={'vpk': str(root / 'offline.vpk'), 'files': []},
                          tracked_outputs=[], preflight={'base_style': capture_audit(), 'mode_styles': {}})
            with patch('l4d2_bsp.workflow.load_run', return_value=report), \
                 patch('l4d2_bsp.reflections.prepare_capture', return_value=({}, {})):
                prepare(root)
            guard = (root / 'capture/scripts/vscripts/lmc_0123456789abcdef_check.nut').read_text()
            self.assertIn('FindByName(null, "__lmc_tonemap_v1")', guard)
            self.assertIn('m_bUseCustomAutoExposureMax', guard)
