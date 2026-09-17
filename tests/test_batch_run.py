import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_batch


class BatchRunTests(unittest.TestCase):
    # Reuse the real single-map fixture without inheriting its tests.
    setUp = test_batch.BatchTests.setUp
    file = test_batch.BatchTests.file
    config = test_batch.BatchTests.config
    manifest = test_batch.BatchTests.manifest

    def setup_jobs(self, names=('a', 'b')):
        for name in names:
            self.config(name)
        self.file('game/left4dead2.exe', b'game')
        self.launcher = self.file('launcher.json', json.dumps(dict(
            backend='executable', executable='game/left4dead2.exe',
            game_executable='game/left4dead2.exe')).encode())
        self.batch = self.manifest([dict(id=n, config='configs/'+n+'.json') for n in names])
        self.actions = []

    def build(self, path):
        from l4d2_bsp.workflow import check, write_json
        from l4d2_bsp.configuration import input_inventory
        cfg, preflight, _, _ = check(path)
        self.actions.append('build:'+cfg['map_name'])
        cfg['output_dir'].mkdir(parents=True)
        report = dict(run_id='0123456789abcdef', status='offline_ready', config=cfg,
                      preflight=preflight, input_inventory=input_inventory(cfg), map_name=cfg['map_name'])
        write_json(cfg['output_dir']/'run.json', report)
        return report

    def capture(self, root, launcher):
        from l4d2_bsp.workflow import write_json
        from l4d2_bsp.configuration import file_hash
        report = self.load(root)
        self.actions.append('capture:'+report['map_name'])
        package = root/'final.vpk'; package.write_bytes(report['map_name'].encode())
        report.update(status='final_ready_pending_user_validation', capture_files_removed=True,
                      final_package=dict(vpk=str(package), sha256=file_hash(package), native_extract_verified=True))
        write_json(root/'run.json', report)
        attempt = root/'auto-capture'; attempt.mkdir()
        write_json(attempt/'attempt.json', dict(status='complete', cleaned=True, run_id=report['run_id']))
        return report

    def load(self, root):
        return json.loads((Path(root)/'run.json').read_text(encoding='utf-8'))

    def run_batch(self, *, build=None, capture=None):
        from l4d2_bsp.batch_run import batch_run
        with patch('l4d2_bsp.workflow.build', side_effect=build or self.build), \
             patch('l4d2_bsp.workflow.load_run', side_effect=self.load), \
             patch('l4d2_bsp.auto_capture.run', side_effect=capture or self.capture), \
             patch('l4d2_bsp.handoff.require_game_closed'), contextlib.redirect_stdout(io.StringIO()):
            return batch_run(self.batch, self.output, self.launcher)

    def test_serial_final_packages_are_pending_human_acceptance(self):
        self.setup_jobs()
        result=self.run_batch()
        self.assertEqual(result['counts'], dict(ready=2, failed=0, pending=0))
        self.assertEqual(self.actions, ['build:a','capture:a','build:b','capture:b'])
        for job in result['jobs']:
            self.assertEqual(job['human_acceptance'], 'not_run')
            self.assertEqual(job['stages'], dict(preflight='passed', build='passed', capture='passed'))
            self.assertTrue(job['final_package']['sha256'])
        self.assertFalse(result['runtime_accepted'])
        self.assertTrue((self.output/'acceptance.json').is_file())
        self.assertEqual(list((self.root/'game/left4dead2').glob('addons/*.vpk')), [])

    def test_build_failure_continues_to_next_map(self):
        self.setup_jobs()
        def failing(path):
            if Path(path).stem=='a': raise ValueError('native audit rejected')
            return self.build(path)
        result=self.run_batch(build=failing)
        self.assertEqual(result['counts'],dict(ready=1,failed=1,pending=0))
        self.assertEqual(result['jobs'][0]['error']['stage'],'build')
        self.assertEqual(self.actions,['build:b','capture:b'])

    def test_preflight_failure_does_not_build_that_map(self):
        self.setup_jobs();self.file('maps/a.bsp',b'bad')
        result=self.run_batch()
        self.assertEqual(result['counts'],dict(ready=1,failed=1,pending=0))
        self.assertEqual(self.actions,['build:b','capture:b'])

    def test_capture_failure_stops_queue_without_retry(self):
        self.setup_jobs()
        def failing(root, launcher): raise ValueError('exit or cleanup uncertain')
        result=self.run_batch(capture=failing)
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['counts'],dict(ready=0,failed=1,pending=1))
        self.assertEqual(self.actions,['build:a'])
        self.assertIn('recover-auto-capture',result['jobs'][0]['recovery_hint'])

    def test_missing_cleanup_receipt_is_not_ready(self):
        self.setup_jobs()
        def dirty(root,launcher):
            result=self.capture(root,launcher)
            (root/'auto-capture/attempt.json').unlink()
            return result
        result=self.run_batch(capture=dirty)
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['jobs'][0]['status'],'failed')
        self.assertEqual(result['jobs'][1]['status'],'pending')

    def test_changed_final_package_is_not_ready(self):
        self.setup_jobs()
        def corrupt(root,launcher):
            result=self.capture(root,launcher);Path(result['final_package']['vpk']).write_bytes(b'changed')
            return result
        result=self.run_batch(capture=corrupt)
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['counts']['ready'],0)

    def test_changed_preflight_input_does_not_build_next_job(self):
        self.setup_jobs()
        def mutation(root,launcher):
            result=self.capture(root,launcher)
            self.file('maps/b.bsp',b'changed')
            return result
        result=self.run_batch(capture=mutation)
        self.assertEqual(result['counts'],dict(ready=1,failed=1,pending=0))
        self.assertEqual(self.actions,['build:a','capture:a'])

    def test_existing_run_is_preserved_and_next_job_continues(self):
        self.setup_jobs();self.file('runs/a/important.txt',b'keep')
        result=self.run_batch()
        self.assertEqual(result['counts'],dict(ready=1,failed=1,pending=0))
        self.assertEqual((self.root/'runs/a/important.txt').read_bytes(),b'keep')
        self.assertEqual(self.actions,['build:b','capture:b'])

    def test_interrupt_preserves_current_stage_and_pending_jobs(self):
        self.setup_jobs()
        def interrupt(path): raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.run_batch(build=interrupt)
        result=json.loads((self.output/'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(result['status'],'interrupted')
        self.assertEqual([j['status'] for j in result['jobs']],['interrupted','pending'])

    def test_batch_report_is_never_reused(self):
        self.setup_jobs();self.run_batch()
        old=(self.output/'summary.json').read_bytes()
        with self.assertRaises(FileExistsError):self.run_batch()
        self.assertEqual((self.output/'summary.json').read_bytes(),old)

    def test_manifest_change_during_preflight_prevents_execution(self):
        self.setup_jobs()
        from l4d2_bsp.batch import batch_check
        def changed(manifest, output):
            result=batch_check(manifest,output)
            Path(manifest).write_bytes(Path(manifest).read_bytes()+b' ')
            return result
        with patch('l4d2_bsp.batch_run.batch_check',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'manifest'):self.run_batch()
        self.assertEqual(self.actions,[])

    def test_launcher_change_stops_before_next_map_build(self):
        self.setup_jobs()
        def changed(root,launcher):
            result=self.capture(root,launcher)
            Path(launcher).write_bytes(Path(launcher).read_bytes()+b' ')
            return result
        result=self.run_batch(capture=changed)
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(self.actions,['build:a','capture:a'])

    def test_unexpected_build_exception_stops_queue(self):
        self.setup_jobs()
        def broken(path):raise RuntimeError('internal bug')
        result=self.run_batch(build=broken)
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['counts'],dict(ready=0,failed=1,pending=1))
        self.assertIn('RuntimeError',result['jobs'][0]['error']['traceback'])

    def test_game_busy_prevents_build(self):
        self.setup_jobs()
        from l4d2_bsp.batch_run import batch_run
        with patch('l4d2_bsp.handoff.require_game_closed',side_effect=ValueError('game busy')), \
             patch('l4d2_bsp.workflow.build',side_effect=AssertionError('must not build')), \
             contextlib.redirect_stdout(io.StringIO()):
            result=batch_run(self.batch,self.output,self.launcher)
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['jobs'][0]['error']['stage'],'environment')
        self.assertFalse((self.root/'runs').exists())

    def test_internal_preflight_error_blocks_all_execution(self):
        self.setup_jobs()
        from l4d2_bsp.workflow import check
        def broken(path,**kwargs):
            if Path(path).stem=='a':raise RuntimeError('unexpected checker defect')
            return check(path,**kwargs)
        with patch('l4d2_bsp.workflow.check',side_effect=broken):result=self.run_batch()
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['counts'],dict(ready=0,failed=1,pending=1))
        self.assertEqual(self.actions,[])
