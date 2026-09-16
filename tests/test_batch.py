import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_style import entity, wrap


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in ('game/left4dead2/gameinfo.txt', 'tools/bin/vrad.exe', 'tools/bin/bspzip.exe', 'tools/bin/vpk.exe'):
            self.file(name, b'fixture')
        from l4d2_bsp.presets import load_preset
        for name in load_preset().required_resources('replace'):
            self.file('game/left4dead2/' + name, b'asset')
        self.output = self.root / 'reports/check-01'

    def file(self, name, data):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def config(self, name, text=None):
        data = wrap((text if text is not None else entity('worldspawn', skyname='old')) + b'\0')
        self.file('maps/' + name + '.bsp', data)
        self.file('maps/' + name + '.nav', b'nav')
        value = dict(conversion='generic-replace-v2', preset='c5m1-daylight-v1',
                     source_bsp='../maps/' + name + '.bsp', game_dir='../game/left4dead2',
                     tools_dir='../tools/bin', output_dir='../runs/' + name)
        return self.file('configs/' + name + '.json', json.dumps(value).encode())

    def manifest(self, jobs):
        return self.file('batch.json', json.dumps(dict(schema_version=1, jobs=jobs)).encode())

    def test_real_check_matches_single_check_and_never_builds(self):
        from l4d2_bsp.batch import batch_check
        from l4d2_bsp.workflow import check
        cfg = self.config('alpha')
        manifest = self.manifest([dict(id='alpha', config='configs/alpha.json')])
        before = (self.root/'maps/alpha.bsp').read_bytes()
        _, single, _, _ = check(cfg)
        with patch('l4d2_bsp.workflow.build', side_effect=AssertionError('must not build')):
            result = batch_check(manifest, self.output)
        self.assertEqual(result['counts'], dict(passed=1, failed=0, pending=0))
        job = json.loads((self.output/'jobs/alpha.json').read_text())
        self.assertEqual(job['preflight'], json.loads(json.dumps(single, default=str)))
        self.assertEqual(job['stages']['bake'], 'not_run')
        self.assertEqual(job['human_acceptance'], 'not_run')
        self.assertTrue(job['input_inventory'])
        self.assertTrue(job['warnings'])
        self.assertFalse((self.root/'runs').exists())
        self.assertEqual(before, (self.root/'maps/alpha.bsp').read_bytes())
        self.assertIn('alpha', (self.output/'summary.md').read_text(encoding='utf-8'))

    def test_failure_does_not_block_next_job_and_cli_returns_two(self):
        from l4d2_bsp.workflow import main
        self.config('good')
        m = self.manifest([dict(id='missing', config='missing.json'), dict(id='good', config='configs/good.json')])
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(['batch-check', '--manifest', str(m), '--output', str(self.output)])
        self.assertEqual(code, 2)
        r = json.loads((self.output/'summary.json').read_text())
        self.assertEqual(r['counts'], dict(passed=1, failed=1, pending=0))
        bad = json.loads((self.output/'jobs/missing.json').read_text())
        self.assertEqual(bad['error']['category'], 'missing_file')
        self.assertEqual(bad['stages']['preflight'], 'failed')

    def test_failure_stages_are_structured_not_message_guesses(self):
        from l4d2_bsp.batch import batch_check
        self.config('broken')
        self.file('maps/broken.bsp', b'bad bsp')
        self.config('ambiguous', entity('worldspawn') + entity('env_fog_controller', targetname='fog', vscripts='x.nut'))
        self.config('resource')
        (self.root/'game/left4dead2/materials/correction/cc_c5_main.raw').unlink()
        m = self.manifest([dict(id=n, config='configs/'+n+'.json') for n in ('broken','ambiguous','resource')])
        r = batch_check(m, self.output)
        self.assertEqual(r['counts']['failed'], 3)
        errors = [json.loads((self.output/'jobs'/f'{n}.json').read_text())['error'] for n in ('broken','ambiguous','resource')]
        self.assertEqual([x['category'] for x in errors], ['input_format', 'conversion_rejected', 'resource_check'])
        self.assertTrue(all(x['message'] and x['type'] for x in errors))

    def test_manifest_rejects_invalid_ids_duplicates_and_unknown_fields_before_writes(self):
        from l4d2_bsp.batch import batch_check
        cases = [[], [dict(id='../bad',config='a')], [dict(id='x',config='a'),dict(id='x',config='b')],
                 [dict(id='x',config='a',execute=True)], [dict(id='x',config='')]]
        for jobs in cases:
            with self.subTest(jobs=jobs):
                with self.assertRaises(ValueError): batch_check(self.manifest(jobs), self.output)
                self.assertFalse(self.output.exists())
        m=self.file('batch.json', b'{"schema_version":true,"jobs":[{"id":"x","config":"a"}]}')
        with self.assertRaises(ValueError): batch_check(m,self.output)

    def test_existing_report_is_never_overwritten(self):
        from l4d2_bsp.batch import batch_check
        self.config('a');m=self.manifest([dict(id='a',config='configs/a.json')])
        batch_check(m,self.output)
        original=(self.output/'summary.json').read_bytes()
        with self.assertRaises(FileExistsError):batch_check(m,self.output)
        self.assertEqual(original,(self.output/'summary.json').read_bytes())

    def test_report_must_not_claim_future_build_directory(self):
        from l4d2_bsp.batch import batch_check
        self.config('a');m=self.manifest([dict(id='a',config='configs/a.json')])
        for output in (self.root/'runs/a',self.root/'runs/a/report',self.root/'runs'):
            with self.subTest(output=output):
                with self.assertRaisesRegex(ValueError,'overlap'):batch_check(m,output)
                self.assertFalse(output.exists())

    def test_duplicate_future_build_directories_rejected_before_writes(self):
        from l4d2_bsp.batch import batch_check
        self.config('a');p=self.config('b')
        value=json.loads(p.read_text());value['output_dir']='../runs/a';p.write_text(json.dumps(value))
        m=self.manifest([dict(id=n,config='configs/'+n+'.json') for n in ('a','b')])
        with self.assertRaisesRegex(ValueError,'output_dir'):batch_check(m,self.output)
        self.assertFalse(self.output.exists())

    def test_interrupt_keeps_completed_results_and_pending_jobs(self):
        from l4d2_bsp.batch import batch_check
        from l4d2_bsp.workflow import check
        for n in ('a','b','c'):self.config(n)
        m=self.manifest([dict(id=n,config='configs/'+n+'.json') for n in ('a','b','c')])
        def interrupted(path, **kw):
            if Path(path).stem=='b':raise KeyboardInterrupt()
            return check(path,**kw)
        with patch('l4d2_bsp.workflow.check', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):batch_check(m,self.output)
        r=json.loads((self.output/'summary.json').read_text())
        self.assertEqual(r['status'],'interrupted')
        self.assertEqual([j['status'] for j in r['jobs']],['passed','interrupted','pending'])
        self.assertTrue((self.output/'jobs/a.json').exists())

    def test_unknown_internal_error_is_not_misclassified_as_unsupported_map(self):
        from l4d2_bsp.batch import batch_check
        self.config('a');m=self.manifest([dict(id='a',config='configs/a.json')])
        with patch('l4d2_bsp.workflow.check',side_effect=RuntimeError('unexpected failure')):
            r=batch_check(m,self.output)
        job=json.loads((self.output/'jobs/a.json').read_text())
        self.assertEqual(job['error']['category'],'internal_error')
        self.assertIn('RuntimeError',job['error']['traceback'])
        self.assertEqual(r['counts']['failed'],1)

    def test_report_directory_cannot_be_inside_game_or_tools_installation(self):
        from l4d2_bsp.batch import batch_check
        self.config('a');m=self.manifest([dict(id='a',config='configs/a.json')])
        for output in (self.root/'game/left4dead2/reports',self.root/'tools/reports'):
            with self.subTest(output=output):
                with self.assertRaisesRegex(ValueError,'installation'):batch_check(m,output)
                self.assertFalse(output.exists())

    def test_invalid_config_output_path_does_not_block_valid_job(self):
        from l4d2_bsp.batch import batch_check
        p=self.config('bad');self.config('good')
        v=json.loads(p.read_text());v['output_dir']='bad\\u0001path'.replace('\\u0001','\x01');p.write_text(json.dumps(v))
        m=self.manifest([dict(id=n,config='configs/'+n+'.json') for n in ('bad','good')])
        r=batch_check(m,self.output)
        self.assertEqual(r['counts'],dict(passed=1,failed=1,pending=0))

    def test_config_change_after_check_is_reported_as_input_identity_failure(self):
        from l4d2_bsp.batch import batch_check
        from l4d2_bsp.workflow import check
        self.config('a');m=self.manifest([dict(id='a',config='configs/a.json')])
        def changing(path,**kw):
            result=check(path,**kw)
            p=Path(path);p.write_text(p.read_text()+' ')
            return result
        with patch('l4d2_bsp.workflow.check',side_effect=changing):
            batch_check(m,self.output)
        job=json.loads((self.output/'jobs/a.json').read_text())
        self.assertEqual(job['error']['category'],'input_identity')

    def test_failed_format_retains_declared_source_and_preset_identity(self):
        from l4d2_bsp.batch import batch_check
        self.config('a');self.file('maps/a.bsp',b'broken')
        m=self.manifest([dict(id='a',config='configs/a.json')]);batch_check(m,self.output)
        job=json.loads((self.output/'jobs/a.json').read_text())
        self.assertIn(str(self.root/'maps/a.bsp'),[x['path'] for x in job['input_inventory_before']])
        self.assertTrue(any(x['name']=='source_bsp' and x.get('sha256') for x in job['declared_inputs']))
        self.assertTrue(any(x['name']=='preset' and x.get('sha256') for x in job['declared_inputs']))

    def test_legacy_donor_change_rejected_and_existing_audit_retained(self):
        from l4d2_bsp.batch import batch_check
        from l4d2_bsp.workflow import check
        from tests.test_style import style_text
        for donor_name in ('c5m1_waterfront.bsp','c5m1_waterfront_l_0.lmp'):
            with self.subTest(donor=donor_name):
                source=self.file('maps/c2m1_highway.bsp',wrap(style_text()))
                donor=self.file('maps/c5m1_waterfront.bsp',wrap(style_text(True)))
                donor_lmp=self.file('maps/c5m1_waterfront_l_0.lmp',wrap(style_text(True),'lmp'))
                nav=self.file('maps/c2m1_highway.nav',b'nav')
                modes={k:str(self.file('maps/c2m1_highway_'+k+'_0.lmp',wrap(style_text(),'lmp'))) for k in 'hls'}
                cfg=dict(profile='c2-c5',source_bsp=str(source),reference_bsp=str(donor),reference_lmp=str(donor_lmp),nav=str(nav),mode_lmps=modes,game_dir='../game/left4dead2',tools_dir='../tools/bin',output_dir='../runs/legacy')
                self.file('configs/legacy.json',json.dumps(cfg).encode())
                m=self.manifest([dict(id='legacy',config='configs/legacy.json')])
                def changing(path,**kw):
                    result=check(path,**kw)
                    p=self.root/'maps'/donor_name;p.write_bytes(p.read_bytes()+b'changed')
                    return result
                output=self.output/donor_name
                with patch('l4d2_bsp.workflow.check',side_effect=changing):r=batch_check(m,output)
                self.assertEqual(r['counts']['failed'],1)
                job=json.loads((output/'jobs/legacy.json').read_text())
                self.assertEqual(job['error']['category'],'input_identity')
                self.assertIn('preflight',job)
                self.assertIn('input_inventory_before',job)
                self.assertIn('input_inventory',job)
