import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


class LaunchConfigTests(unittest.TestCase):
    def make_install(self, root):
        install = root / 'Left 4 Dead 2'
        game = install / 'left4dead2'
        game.mkdir(parents=True)
        game_exe = install / 'left4dead2.exe'
        game_exe.write_bytes(b'game')
        launcher = install / 'L4D2 Launcher.exe'
        launcher.write_bytes(b'launcher')
        ini = install / 'ColdClientLoader.ini'
        ini.write_bytes(b'[Loader]\r\nExeCommandLine=-steam -novid\r\nOther=keep\r\n')
        return install, game, game_exe, launcher, ini

    def write_config(self, root, data):
        path = root / 'launcher.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_load_launcher_resolves_paths_and_supplies_defaults(self):
        from l4d2_bsp.launch import load_launcher
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            install, game, game_exe, launcher, ini = self.make_install(root)
            path = self.write_config(root, {
                'backend': 'coldclient', 'executable': str(launcher.relative_to(root)),
                'game_executable': str(game_exe.relative_to(root)),
                'ini_path': str(ini.relative_to(root)),
            })
            got = load_launcher(path, game)
            self.assertEqual(got['executable'], str(launcher.resolve()))
            self.assertEqual(got['game_executable'], str(game_exe.resolve()))
            self.assertEqual(got['ini_path'], str(ini.resolve()))
            self.assertEqual(got['arguments'], [])
            self.assertEqual(got['listenserver_cfg'], 'listenserver.cfg')
            self.assertEqual(got['startup_timeout_seconds'], 60)
            self.assertEqual(got['timeout_seconds'], 600)
            self.assertEqual(got['stop_timeout_seconds'], 60)
            self.assertEqual(got['stable_seconds'], 5)

    def test_load_launcher_accepts_utf8_bom_json(self):
        from l4d2_bsp.launch import load_launcher
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, game, game_exe, launcher, ini = self.make_install(root)
            path = root / 'launcher.json'
            path.write_text(json.dumps({'backend': 'coldclient', 'executable': str(launcher),
                                         'game_executable': str(game_exe), 'ini_path': str(ini)}),
                            encoding='utf-8-sig')
            self.assertEqual(load_launcher(path, game)['backend'], 'coldclient')

    def test_load_launcher_accepts_safe_cfg_subdirectory(self):
        from l4d2_bsp.launch import load_launcher
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, game, game_exe, launcher, ini = self.make_install(root)
            path = self.write_config(root, {'backend': 'coldclient', 'executable': str(launcher),
                                             'game_executable': str(game_exe), 'ini_path': str(ini),
                                             'listenserver_cfg': 'custom/listen-server.cfg'})
            self.assertEqual(load_launcher(path, game)['listenserver_cfg'], 'custom/listen-server.cfg')

    def test_load_launcher_normalizes_suffixless_cfg_name(self):
        from l4d2_bsp.launch import load_launcher
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, game, game_exe, launcher, ini = self.make_install(root)
            path = self.write_config(root, {'backend': 'coldclient', 'executable': str(launcher),
                                             'game_executable': str(game_exe), 'ini_path': str(ini),
                                             'listenserver_cfg': 'custom/listen-server'})
            self.assertEqual(load_launcher(path, game)['listenserver_cfg'], 'custom/listen-server.cfg')

    def test_load_launcher_rejects_unknown_keys_and_authoritative_commands(self):
        from l4d2_bsp.launch import load_launcher
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, game, game_exe, launcher, ini = self.make_install(root)
            base = {'backend': 'coldclient', 'executable': str(launcher),
                    'game_executable': str(game_exe), 'ini_path': str(ini)}
            invalid = [
                {**base, 'surprise': True},
                {**base, 'arguments': ['-novid', '+exec', 'other.cfg']},
                {**base, 'arguments': ['+MAP', 'c2m1_highway']},
                {**base, 'arguments': ['+lservercfgfile=other.cfg']},
                {**base, 'arguments': ['-novid +exec other.cfg']},
                {**base, 'arguments': ['-novid; +map c2m1_highway']},
                {**base, 'arguments': ['-novid\r\n+exec other.cfg']},
                {**base, 'timeout_seconds': math.inf},
                {**base, 'timeout_seconds': 3601},
                {**base, 'stable_seconds': 0},
                {**base, 'stable_seconds': 0.5},
                {**base, 'stable_seconds': 61},
            ]
            for number, data in enumerate(invalid):
                with self.subTest(number=number):
                    with self.assertRaises(ValueError):
                        load_launcher(self.write_config(root, data), game)

    def test_load_launcher_requires_exact_installed_game_executable(self):
        from l4d2_bsp.launch import load_launcher
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, game, _, launcher, ini = self.make_install(root)
            wrong = root / 'other' / 'left4dead2.exe'
            wrong.parent.mkdir()
            wrong.write_bytes(b'wrong')
            path = self.write_config(root, {'backend': 'coldclient', 'executable': str(launcher),
                                             'game_executable': str(wrong), 'ini_path': str(ini)})
            with self.assertRaisesRegex(ValueError, 'game_executable'):
                load_launcher(path, game)

    def test_executable_backend_rejects_ini_path(self):
        from l4d2_bsp.launch import load_launcher
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, game, game_exe, _, ini = self.make_install(root)
            path = self.write_config(root, {'backend': 'executable', 'executable': str(game_exe),
                                             'game_executable': str(game_exe), 'ini_path': str(ini)})
            with self.assertRaisesRegex(ValueError, 'ini_path'):
                load_launcher(path, game)

    def test_executable_backend_requires_the_verified_game_binary(self):
        from l4d2_bsp.launch import load_launcher
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, game, game_exe, launcher, _ = self.make_install(root)
            path = self.write_config(root, {'backend': 'executable', 'executable': str(launcher),
                                             'game_executable': str(game_exe)})
            with self.assertRaisesRegex(ValueError, 'executable'):
                load_launcher(path, game)


class LaunchProcessTests(unittest.TestCase):
    make_install = LaunchConfigTests.make_install
    write_config = LaunchConfigTests.write_config

    def loaded_config(self, root):
        from l4d2_bsp.launch import load_launcher
        _, game, game_exe, launcher, ini = self.make_install(root)
        path = self.write_config(root, {'backend': 'coldclient', 'executable': str(launcher),
                                         'game_executable': str(game_exe), 'ini_path': str(ini),
                                         'arguments': ['-novid']})
        return load_launcher(path, game), game_exe, ini

    def test_launch_patches_one_ini_key_and_returns_verified_process_identity(self):
        from l4d2_bsp.launch import launch, restore_launcher
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, game_exe, ini = self.loaded_config(root)
            original = ini.read_bytes()
            attempt = root / 'attempt'
            attempt.mkdir()

            def observe(command, **kwargs):
                changed = ini.read_text(encoding='utf-8')
                self.assertIn('ExeCommandLine=-steam -novid -novid -insecure +exec lmc_abc_boot', changed)
                self.assertIn('Other=keep', changed)
                payload = json.loads(kwargs['input'])
                self.assertEqual(payload['arguments'], ['-novid'])
                self.assertEqual(payload['boot'], 'lmc_abc_boot')
                self.assertNotIn('argument_line', payload)
                identity = {'process_id': 321, 'executable_path': str(game_exe.resolve()),
                            'command_line': f'"{game_exe.resolve()}" -novid -insecure +exec lmc_abc_boot'}
                return subprocess.CompletedProcess(command, 0, json.dumps(identity), '')

            with patch('l4d2_bsp.launch.subprocess.run', side_effect=observe):
                identity = launch(config, 'lmc_abc_boot', attempt)
            self.assertEqual(identity['process_id'], 321)
            self.assertEqual(Path(identity['executable_path']), game_exe.resolve())
            self.assertEqual((attempt / 'launcher-original.ini').read_bytes(), original)
            self.assertTrue((attempt / 'launch.json').is_file())
            journal = json.loads((attempt / 'launch.json').read_text(encoding='utf-8'))
            self.assertEqual(journal['identity']['process_id'], 321)
            self.assertEqual(ini.read_bytes(), original)
            self.assertTrue(restore_launcher(attempt))
            self.assertEqual(ini.read_bytes(), original)

    def test_launch_rejects_unverified_process_and_restores_ini(self):
        from l4d2_bsp.launch import launch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, _, ini = self.loaded_config(root)
            original = ini.read_bytes()
            attempt = root / 'attempt'
            attempt.mkdir()
            observed = {'process_id': 321, 'executable_path': str(root / 'wrong.exe'),
                        'command_line': 'left4dead2.exe -novid'}
            completed = subprocess.CompletedProcess(['powershell'], 0, json.dumps(observed), '')
            with patch('l4d2_bsp.launch.subprocess.run', return_value=completed):
                with self.assertRaisesRegex(ValueError, 'observed game process'):
                    launch(config, 'lmc_abc_boot', attempt)
            self.assertEqual(ini.read_bytes(), original)

    def test_restore_refuses_to_overwrite_external_ini_edit(self):
        from l4d2_bsp.launch import launch, restore_launcher
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, game_exe, ini = self.loaded_config(root)
            attempt = root / 'attempt'
            attempt.mkdir()
            observed = {'process_id': 321, 'executable_path': str(game_exe.resolve()),
                        'command_line': f'"{game_exe.resolve()}" -novid -insecure +exec lmc_abc_boot'}
            def observe(command, **kwargs):
                ini.write_bytes(ini.read_bytes() + b'External=edit\r\n')
                return subprocess.CompletedProcess(command, 0, json.dumps(observed), '')
            with patch('l4d2_bsp.launch.subprocess.run', side_effect=observe):
                with self.assertRaisesRegex(ValueError, 'changed externally'):
                    launch(config, 'lmc_abc_boot', attempt)
            self.assertFalse(restore_launcher(attempt))
            self.assertIn(b'External=edit', ini.read_bytes())

    def test_launch_rejects_existing_exec_or_map_before_ini_mutation(self):
        from l4d2_bsp.launch import launch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, _, ini = self.loaded_config(root)
            for command in ('+exec old.cfg', '+MAP c2m1_highway'):
                with self.subTest(command=command):
                    ini.write_bytes(f'[Loader]\r\nExeCommandLine=-steam {command}\r\n'.encode('ascii'))
                    attempt = root / command.split()[0].replace('+', '')
                    attempt.mkdir()
                    with self.assertRaisesRegex(ValueError, 'authoritative'):
                        launch(config, 'lmc_abc_boot', attempt)
                    self.assertFalse((attempt / 'launch.json').exists())

    def test_is_running_requires_same_pid_path_and_boot_tokens(self):
        from l4d2_bsp.launch import is_running
        identity = {'process_id': 321, 'executable_path': r'J:\Game\left4dead2.exe',
                    'command_line': r'left4dead2.exe -insecure +exec lmc_abc_boot'}
        same = subprocess.CompletedProcess(['powershell'], 0, json.dumps(identity), '')
        gone = subprocess.CompletedProcess(['powershell'], 0, '', '')
        with patch('l4d2_bsp.launch.subprocess.run', return_value=same):
            self.assertTrue(is_running(identity))
        with patch('l4d2_bsp.launch.subprocess.run', return_value=gone):
            self.assertFalse(is_running(identity))

    def test_recover_identity_adopts_only_exact_journaled_boot_process(self):
        from l4d2_bsp.launch import recover_identity
        with tempfile.TemporaryDirectory() as temp:
            attempt = Path(temp)
            game_exe = attempt / 'left4dead2.exe'
            journal = {'schema_version': 1, 'backend': 'executable',
                       'game_executable': str(game_exe), 'boot': 'lmc_abc_boot',
                       'intended_arguments': ['-novid', '-insecure', '+exec', 'lmc_abc_boot']}
            (attempt / 'launch.json').write_text(json.dumps(journal), encoding='utf-8')
            observed = [{'process_id': 654, 'executable_path': str(game_exe),
                         'command_line': f'"{game_exe}" -novid -insecure +exec lmc_abc_boot'}]
            completed = subprocess.CompletedProcess(['powershell'], 0, json.dumps(observed), '')
            with patch('l4d2_bsp.launch.subprocess.run', return_value=completed):
                identity = recover_identity(attempt)
            self.assertEqual(identity['process_id'], 654)
            self.assertEqual(identity['boot'], 'lmc_abc_boot')

    def test_recover_identity_rejects_unowned_live_game_process(self):
        from l4d2_bsp.launch import recover_identity
        with tempfile.TemporaryDirectory() as temp:
            attempt = Path(temp)
            game_exe = attempt / 'left4dead2.exe'
            journal = {'schema_version': 1, 'backend': 'executable',
                       'game_executable': str(game_exe), 'boot': 'lmc_abc_boot',
                       'intended_arguments': ['-insecure', '+exec', 'lmc_abc_boot']}
            (attempt / 'launch.json').write_text(json.dumps(journal), encoding='utf-8')
            observed = [{'process_id': 777, 'executable_path': str(game_exe),
                         'command_line': f'"{game_exe}" -steam'}]
            completed = subprocess.CompletedProcess(['powershell'], 0, json.dumps(observed), '')
            with patch('l4d2_bsp.launch.subprocess.run', return_value=completed):
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    recover_identity(attempt)

    def test_recover_identity_rejects_ambiguous_extra_game_process(self):
        from l4d2_bsp.launch import recover_identity
        with tempfile.TemporaryDirectory() as temp:
            attempt = Path(temp)
            game_exe = attempt / 'left4dead2.exe'
            journal = {'schema_version': 1, 'backend': 'executable',
                       'game_executable': str(game_exe), 'boot': 'lmc_abc_boot',
                       'intended_arguments': ['-insecure', '+exec', 'lmc_abc_boot']}
            (attempt / 'launch.json').write_text(json.dumps(journal), encoding='utf-8')
            observed = [
                {'process_id': 654, 'executable_path': str(game_exe),
                 'command_line': f'"{game_exe}" -insecure +exec lmc_abc_boot'},
                {'process_id': 777, 'executable_path': str(game_exe),
                 'command_line': f'"{game_exe}" -steam'},
            ]
            completed = subprocess.CompletedProcess(['powershell'], 0, json.dumps(observed), '')
            with patch('l4d2_bsp.launch.subprocess.run', return_value=completed):
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    recover_identity(attempt)


if __name__ == '__main__':
    unittest.main()
