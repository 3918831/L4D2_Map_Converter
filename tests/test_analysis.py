import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from tests.test_core import make_bsp, make_lmp


WORLD = b'{ "classname" "worldspawn" }\n'


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'new_custom_map.bsp'
        self.source.write_bytes(make_bsp(WORLD))

    def analyze(self, **options):
        from l4d2_bsp.analysis import analyze_map
        return analyze_map(self.source, preset_id='c5m1-daylight-v1', **options)

    def test_unknown_map_analyzes_without_legacy_source_selector(self):
        report = self.analyze()
        self.assertEqual(report['inputs']['map_name'], 'new_custom_map')
        self.assertEqual(report['stages']['conversion'], 'not_run')
        self.assertEqual(report['stages']['runtime_acceptance'], 'not_run')
        self.assertEqual(report['resource_check']['status'], 'not_checked')
        self.assertEqual(report['containers']['base']['atmosphere']['roles']['world'], [0])
        self.assertEqual(report['preset']['id'], 'c5m1-daylight-v1')

    def test_actual_mode_is_analyzed_independently(self):
        (self.root / 'new_custom_map_l_0.lmp').write_bytes(make_lmp(
            WORLD + b'{ "classname" "env_fog_controller" "targetname" "not_c2" }'))
        report = self.analyze()
        self.assertEqual(set(report['containers']), {'base', 'l_0'})
        self.assertEqual(report['containers']['base']['atmosphere']['roles']['fog'], [])
        self.assertEqual(report['containers']['l_0']['atmosphere']['roles']['fog'], [1])

    def test_bad_mode_keeps_base_report_and_error(self):
        (self.root / 'new_custom_map_l_0.lmp').write_bytes(b'bad header')
        report = self.analyze()
        self.assertEqual(report['stages']['analysis'], 'failed')
        self.assertIn('atmosphere', report['containers']['base'])
        self.assertTrue(any(x['code'] == 'container_error' for x in report['findings']))

    def test_compressed_entities_do_not_claim_pass(self):
        self.source.write_bytes(make_bsp(WORLD, fourcc=b'LZMA'))
        report = self.analyze()
        self.assertEqual(report['stages']['analysis'], 'failed')
        self.assertTrue(any(x['code'] == 'entity_error' for x in report['findings']))

    def test_duplicate_identity_key_reports_error_instead_of_losing_all_diagnostics(self):
        self.source.write_bytes(make_bsp(WORLD +
            b'{ "classname" "logic_relay" "targetname" "a" "targetname" "b" }'))
        report = self.analyze()
        self.assertEqual(report['stages']['analysis'], 'failed')
        self.assertIn('inspection', report['containers']['base'])
        self.assertTrue(any(x['code'] == 'entity_analysis_error' for x in report['findings']))

    def test_selected_preset_has_no_input_map_allowlist(self):
        from l4d2_bsp.analysis import analyze_map
        report = analyze_map(self.source, preset_id='c4m3-overcast-static-v1')
        self.assertEqual(report['preset']['id'], 'c4m3-overcast-static-v1')
        self.assertEqual(report['stages']['conversion'], 'not_run')

    def test_resource_roots_are_optional_but_explicit_missing_root_is_error(self):
        with self.assertRaises(OSError):
            self.analyze(resource_roots=[self.root / 'missing'])
        report = self.analyze(resource_roots=[self.root])
        self.assertEqual(report['resource_check']['status'], 'missing_candidates')
        self.assertTrue(report['resource_check']['missing'])

    def test_analysis_does_not_write_or_change_inputs(self):
        before = {p: p.read_bytes() for p in self.root.iterdir()}
        self.analyze()
        after = {p: p.read_bytes() for p in self.root.iterdir()}
        self.assertEqual(before, after)

    def test_empty_wildcard_targets_are_unresolved_in_summary(self):
        self.source.write_bytes(make_bsp(WORLD +
            b'{ "classname" "logic_auto" "OnMapSpawn" "missing_*,Enable,,0,-1" }'))
        report = self.analyze()
        self.assertIn('runtime_targets_unresolved', [x['code'] for x in report['findings']])

    def test_cli_prints_json_and_preserves_failure_diagnostics(self):
        from l4d2_bsp.workflow import main
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['analyze', '--bsp', str(self.source), '--preset', 'c5m1-daylight-v1'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['stages']['conversion'], 'not_run')
        self.source.write_bytes(b'broken')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['analyze', '--bsp', str(self.source), '--preset', 'c5m1-daylight-v1'])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())['stages']['analysis'], 'failed')


if __name__ == '__main__':
    unittest.main()
