import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch


class AutoCaptureTests(unittest.TestCase):
    def module(self):
        from l4d2_bsp import auto_capture
        return auto_capture

    def test_request_requires_matching_map_then_guard_and_is_once_only(self):
        auto = self.module()
        gate = auto.CaptureGate('mc01234567', 'lmc_0123456789abcdef')
        prefix = 'LMC_0123456789ABCDEF'
        self.assertIsNone(gate.observe('READY', prefix + '_GUARD_PASSED_CAPTURE_NOT_VERIFIED'))
        text = f'{prefix}_MAP=mc01234567\n{prefix}_GUARD_PASSED_CAPTURE_NOT_VERIFIED\n'
        self.assertEqual(gate.observe('READY', text), 'CAPTURE')
        self.assertIsNone(gate.observe('READY', text))
        self.assertIsNone(gate.observe('RELOADED', text + prefix + '_CAPTURE_REQUESTED\n'))

    def test_prior_request_and_pre_request_refusal_fail_closed(self):
        auto = self.module()
        for suffix in ('_CAPTURE_REQUESTED', '_REFUSED: bad exposure'):
            with self.subTest(suffix=suffix):
                with self.assertRaises(ValueError):
                    auto.CaptureGate('mc01234567', 'lmc_0123456789abcdef').observe(
                        'READY', 'LMC_0123456789ABCDEF' + suffix)

    def test_post_request_refusal_does_not_erase_first_request(self):
        auto = self.module()
        gate = auto.CaptureGate('mc01234567', 'lmc_0123456789abcdef')
        text = 'LMC_0123456789ABCDEF_MAP=mc01234567\nLMC_0123456789ABCDEF_GUARD_PASSED_CAPTURE_NOT_VERIFIED\n'
        self.assertEqual(gate.observe('READY', text), 'CAPTURE')
        text += 'LMC_0123456789ABCDEF_CAPTURE_REQUESTED\nLMC_0123456789ABCDEF_REFUSED: host disconnected\n'
        self.assertIsNone(gate.observe('RELOADED', text))

    def test_mailbox_rejects_foreign_content_and_duplicate_capture(self):
        auto = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            auto.mailbox(game, '0123456789abcdef', 'CAPTURE')
            path = game / 'ems/lmc_0123456789abcdef_command.txt'
            self.assertEqual(path.read_text(), 'CAPTURE:0123456789abcdef')
            with self.assertRaises(ValueError):
                auto.mailbox(game, '0123456789abcdef', 'CAPTURE')
            auto.mailbox(game, '0123456789abcdef', 'STOP')
            path.write_text('FOREIGN')
            with self.assertRaises(ValueError):
                auto.mailbox(game, '0123456789abcdef', 'STOP')
            self.assertEqual(path.read_text(), 'FOREIGN')

    def test_mailbox_cannot_escape_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.module().mailbox(Path(tmp), '../escape', 'STOP')

    def test_stop_recovers_interrupted_atomic_command_write(self):
        auto = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            (game / 'ems').mkdir()
            pending = game / 'ems/lmc_0123456789abcdef_command.tmp'
            pending.write_text('CAPTURE:0123456789abcdef')
            auto.mailbox(game, '0123456789abcdef', 'STOP')
            self.assertFalse(pending.exists())
            self.assertEqual((game / 'ems/lmc_0123456789abcdef_command.txt').read_text(), 'STOP:0123456789abcdef')

    def test_cleanup_preserves_changed_files(self):
        auto = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / 'game'
            game.mkdir()
            item = game / 'owned.txt'
            item.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                auto.remove_owned(game, [{'name': 'owned.txt', 'sha256': 'bad'}])
            self.assertEqual(item.read_bytes(), b'changed')

    def test_control_input_is_not_console_code(self):
        auto = self.module()
        with self.assertRaises(ValueError):
            auto.controls('0123456789abcdef', 'mc01234567', 10, 'x.cfg;quit', 5)

    def test_controls_chain_original_cfg_and_persist_dispatch_before_capture(self):
        files = self.module().controls('0123456789abcdef', 'mc01234567', 10, 'custom.cfg', 5)
        ready = files['cfg/lmc_0123456789abcdef_ready.cfg'].decode()
        self.assertLess(ready.index('exec custom.cfg'), ready.index('script_execute'))
        script = files['scripts/vscripts/lmc_0123456789abcdef_auto.nut'].decode()
        self.assertLess(script.index('StringToFile(job.statefile, "DISPATCHED")'),
                        script.index('SendToConsole("exec lmc_0123456789abcdef_capture")'))
        self.assertIn('lservercfgfile custom.cfg', script)
        self.assertIn('m_hViewEntity', script)
        self.assertNotIn('GetTeam()', script)

    def test_existing_attempt_cannot_prepare_or_launch_again(self):
        auto = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'auto-capture').mkdir()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                auto.run(root, root / 'unused.json')

    def test_recovery_rejects_mismatching_run_before_game_operations(self):
        auto = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'auto-capture').mkdir()
            (root / 'run.json').write_text(json.dumps({'run_id': '0123456789abcdef'}))
            (root / 'auto-capture/attempt.json').write_text(json.dumps({'run_id': 'fedcba9876543210'}))
            with self.assertRaisesRegex(ValueError, 'identity'):
                auto.recover(root)

    def test_concurrent_coordinators_cannot_own_one_run(self):
        auto = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with auto.run_lock(root):
                with self.assertRaisesRegex(ValueError, 'coordinator'):
                    with auto.run_lock(root):
                        self.fail('concurrent lock was acquired')

    def test_recovery_recovers_identity_when_coordinator_died_after_launch(self):
        auto = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'auto-capture').mkdir()
            (root / 'auto-capture/launch.json').write_text('{}')
            journal = {'run_id': '0123456789abcdef', 'game_dir': str(root),
                       'launcher': {'stop_timeout_seconds': 1}}
            identity = {'process_id': 123}
            with patch('l4d2_bsp.launch.recover_identity', return_value=identity), \
                 patch('l4d2_bsp.launch.is_running', return_value=False), \
                 patch('l4d2_bsp.launch.restore_launcher', return_value=True), \
                 patch('l4d2_bsp.handoff.require_game_closed'), \
                 patch.object(auto, '_archive_cleanup'):
                auto._stop(root, journal)
            self.assertEqual(journal.get('process'), identity)

    def test_prelaunch_failure_can_clean_without_launch_receipt(self):
        auto = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'auto-capture').mkdir()
            journal = {'run_id': '0123456789abcdef', 'game_dir': str(root)}
            with patch('l4d2_bsp.launch.recover_identity', side_effect=ValueError('missing launch journal')), \
                 patch('l4d2_bsp.launch.restore_launcher', return_value=True), \
                 patch('l4d2_bsp.handoff.require_game_closed'), \
                 patch.object(auto, '_archive_cleanup'):
                auto._stop(root, journal)

    def test_controller_accepts_normal_cfg_with_hyphen(self):
        self.assertIn('exec my-server.cfg', self.module().controls(
            '0123456789abcdef', 'mc01234567', 10, 'my-server.cfg', 5)[
                'cfg/lmc_0123456789abcdef_ready.cfg'].decode())

    def test_interrupted_archive_is_completed_without_losing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, dest = root / 'source', root / 'archive'
            source.write_bytes(b'complete evidence')
            partial = root / 'archive.partial'
            partial.write_bytes(b'complete')
            self.module()._archive_exact(source, dest)
            self.assertEqual(dest.read_bytes(), source.read_bytes())
            self.assertFalse(partial.exists())

    def test_different_runs_cannot_share_one_game_installation(self):
        auto = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            game, root = Path(tmp) / 'game', Path(tmp) / 'run'
            game.mkdir()
            root.mkdir()
            report = {'status': 'offline_ready', 'config': {'game_dir': str(game)}}
            with auto.run_lock(game, '.lmc-auto-capture.lock'), \
                 patch('l4d2_bsp.workflow.load_run', return_value=report), \
                 patch('l4d2_bsp.handoff.require_game_closed'):
                with self.assertRaisesRegex(ValueError, 'coordinator'):
                    auto.run(root, root / 'absent.json')

    def test_monitor_uses_real_bsp_audit_before_finishing_and_observes_exit(self):
        from test_reflections import originals, bsp, vtf, NEW, TAIL
        auto = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'auto-capture').mkdir()
            game = root / 'game'
            (game / 'ems').mkdir(parents=True)
            (game / 'maps').mkdir()
            baseline = root / 'baseline.bsp'
            baseline.write_bytes(bsp(originals()))
            (game / 'maps/capture_run.bsp').write_bytes(bsp(originals() | {NEW + TAIL: vtf(rgb=2)}))
            run_id = '0123456789abcdef'
            marker = 'lmc_' + run_id
            prefix = marker.upper()
            log = game / (marker + '_capture.log')
            log.write_text(f'{prefix}_MAP=capture_run\n{prefix}_GUARD_PASSED_CAPTURE_NOT_VERIFIED\n')
            state = game / 'ems' / (marker + '_state.txt')
            state.write_text('READY\x00')
            command = game / 'ems' / (marker + '_command.txt')
            clock = [0]

            def alive(identity):
                if command.exists():
                    value = command.read_text()
                    if value.startswith('FINISH'):
                        return False
                    if value.startswith('CAPTURE'):
                        state.write_text('RELOADED\x00')
                        text = log.read_text()
                        if '_CAPTURE_REQUESTED' not in text:
                            log.write_text(text + prefix + '_CAPTURE_REQUESTED\n')
                return True

            report = {'run_id': run_id, 'capture_alias': 'capture_run', 'map_name': 'source', 'offline_map': str(baseline)}
            journal = {'game_dir': str(game), 'process': {'process_id': 1}, 'launcher': {'timeout_seconds': 30}}
            with patch('l4d2_bsp.launch.is_running', side_effect=alive), \
                 patch.object(auto.time, 'monotonic', side_effect=lambda: clock[0]), \
                 patch.object(auto.time, 'sleep', side_effect=lambda _: clock.__setitem__(0, clock[0] + 1)):
                auto.monitor(root, report, journal)
            self.assertEqual(journal['live_capture_audit']['hdr_sample_count'], 1)
            self.assertEqual(log.read_text().count('_CAPTURE_REQUESTED'), 1)
            self.assertEqual(journal['events'][-1]['event'], 'GAME_EXITED')

    def test_timeout_without_ready_never_sends_capture(self):
        auto = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'auto-capture').mkdir()
            game = root / 'game'
            game.mkdir()
            baseline = root / 'baseline.bsp'
            baseline.write_bytes(b'not read as a BSP before capture')
            report = {'run_id': '0123456789abcdef', 'capture_alias': 'mc01234567',
                      'map_name': 'source', 'offline_map': str(baseline)}
            journal = {'game_dir': str(game), 'process': {'process_id': 1}, 'launcher': {'timeout_seconds': 3}}
            clock = [0]
            with patch('l4d2_bsp.launch.is_running', return_value=True), \
                 patch.object(auto.time, 'monotonic', side_effect=lambda: clock[0]), \
                 patch.object(auto.time, 'sleep', side_effect=lambda _: clock.__setitem__(0, clock[0] + 1)):
                with self.assertRaisesRegex(ValueError, 'timed out'):
                    auto.monitor(root, report, journal)
            self.assertFalse((game / 'ems/lmc_0123456789abcdef_command.txt').exists())
