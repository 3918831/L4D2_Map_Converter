"""Native runner contracts without executing or modifying a game installation."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_vrad
from tests.test_cli import bsp_fixture
from tests.test_inspect import game_lump_fixture


class VradTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source.bsp'
        self.source.write_bytes(bsp_fixture())
        self.game, self.tools = self.root/'game', self.root/'tools'
        (self.game/'left4dead2').mkdir(parents=True)
        (self.game/'left4dead2/gameinfo.txt').write_text('fixture')
        (self.tools/'bin').mkdir(parents=True)
        (self.tools/'bin/vrad.exe').write_bytes(b'fixture executable')
        self.output = self.root/'output'

    def invoke(self, *, mutate=None, exit_code=0, timeout=False, launch_error=None, profile='fast'):
        class Compiler:
            pid = 1234
            killed = False

            def __init__(instance, command, **kwargs):
                if launch_error:
                    raise launch_error
                if mutate:
                    mutate(Path(command[-1]))

            def wait(instance, **kwargs):
                if timeout and not instance.killed:
                    raise subprocess.TimeoutExpired('fixture compiler', 1)
                return exit_code

            def kill(instance):
                instance.killed = True

        with patch.object(run_vrad.subprocess, 'Popen', Compiler), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return run_vrad.main(['--source', str(self.source), '--game-root', str(self.game),
                '--tools-root', str(self.tools), '--output', str(self.output), '--timeout', '1',
                '--profile', profile])

    def report(self):
        return json.loads((self.output/'run.json').read_text())

    def test_rejects_output_inside_either_install_before_writing(self):
        for install in (self.game, self.tools):
            with self.subTest(install=install):
                self.output = install/'subdir/../probe'
                self.assertEqual(self.invoke(), 2)
                self.assertFalse(self.output.resolve().exists())
                self.assertFalse((install/'subdir').exists())

    def test_rejects_non_bsp_source_before_writing(self):
        self.source = self.root/'run.json'
        self.source.write_bytes(bsp_fixture())
        self.assertEqual(self.invoke(), 2)
        self.assertFalse(self.output.exists())

    def test_launch_failure_is_recorded(self):
        self.assertEqual(self.invoke(launch_error=OSError('fixture launch denied')), 2)
        report = self.report()
        self.assertEqual(report['status'], 'launch_failed')
        self.assertIn('fixture launch denied', report['error'])

    def test_success_requires_output_structure_validation(self):
        cases = [b'CORRUPT', bsp_fixture().replace(b'"worldspawn"', b' worldspawn '),
                 game_lump_fixture(offset=0)]
        for i, data in enumerate(cases):
            with self.subTest(case=i):
                self.output = self.root/f'invalid{i}'
                self.assertEqual(self.invoke(mutate=lambda dest: dest.write_bytes(data)), 2)
                report = self.report()
                self.assertEqual(report['status'], 'output_validation_failed')
                self.assertTrue(report['source_unchanged'])
                self.assertIn('output_validation_error', report)

    def test_missing_output_is_recorded_as_validation_failure(self):
        self.assertEqual(self.invoke(mutate=lambda dest: dest.unlink()), 2)
        self.assertEqual(self.report()['status'], 'output_validation_failed')

    def test_changed_source_cannot_report_success(self):
        self.assertEqual(self.invoke(mutate=lambda dest: self.source.write_bytes(b'CHANGED')), 2)
        report = self.report()
        self.assertEqual(report['status'], 'source_changed')
        self.assertFalse(report['source_unchanged'])

    def test_compiler_failure_is_retained_even_when_output_is_corrupt(self):
        self.assertEqual(self.invoke(exit_code=5, mutate=lambda dest: dest.write_bytes(b'CORRUPT')), 2)
        report = self.report()
        self.assertEqual(report['status'], 'compiler_failed')
        self.assertEqual(report['exit_code'], 5)

    def test_timeout_is_recorded_as_a_distinct_failure(self):
        self.assertEqual(self.invoke(timeout=True), 2)
        report = self.report()
        self.assertEqual(report['status'], 'compiler_timeout')
        self.assertEqual(report['exit_code'], 124)
        self.assertTrue(report['timed_out'])

    def test_success_retains_pending_runtime_status_and_audits(self):
        self.assertEqual(self.invoke(), 0)
        report = self.report()
        self.assertEqual(report['status'], 'compiled_pending_runtime_validation')
        self.assertTrue(report['source_unchanged'])
        self.assertEqual(report['source_sha256'], report['output_sha256'])
        self.assertTrue(json.loads((self.output/'comparison.json').read_text())['identical'])
        self.assertEqual(json.loads((self.output/'inspect.json').read_text())['entity_count'], 2)

    def test_normal_onebounce_is_a_single_flag_control_for_fast(self):
        self.assertEqual(self.invoke(), 0)
        fast = self.report()
        self.output = self.root/'normal-output'
        self.assertEqual(self.invoke(profile='normal-onebounce'), 0)
        normal = self.report()
        self.assertEqual([arg for arg in fast['argv'][:-1] if arg != '-fast'],
                         normal['argv'][:-1])
        self.assertEqual(fast['compiler_input_sha256'], normal['compiler_input_sha256'])
        self.assertNotIn('-StaticPropLighting', normal['argv'])


if __name__ == '__main__':
    unittest.main()
