import tempfile
import unittest
from pathlib import Path
import zipfile


class ReleaseTests(unittest.TestCase):
    def make_source(self, root):
        root.mkdir()
        for name in ('README.md', 'NOTICE.md', 'CHANGELOG.md', 'pyproject.toml', '.gitignore',
                     'docs/guide.zh-CN.md', 'docs/architecture.md', 'docs/validation.md', 'docs/release.md',
                     'docs/presets.md', 'docs/c4m3-guide.zh-CN.md', 'docs/c4m3-preparation.md',
                     'l4d2_bsp/preset_data/c5m1-daylight-v1.json',
                     'l4d2_bsp/preset_data/c4m3-overcast-static-v1.json',
                     'l4d2_bsp/__init__.py', 'tests/test_example.py', 'examples/c6-c5.example.json'):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')

    def test_release_contains_only_allowed_source_and_never_game_artifacts(self):
        from scripts.make_release import build_release
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'
            self.make_source(root)
            (root / 'artifacts').mkdir()
            (root / 'artifacts/private.log').write_text('PRIVATE')
            (root / 'config.local.json').write_text('PRIVATE')
            (root / 'l4d2_bsp/preset_data/personal.json').write_text('PRIVATE')
            (root / 'l4d2_bsp/private.json').write_text('PRIVATE')
            output = Path(temp) / 'release.zip'
            build_release(root, output)
            with zipfile.ZipFile(output) as packed:
                self.assertIn('FILES.sha256', packed.namelist())
                self.assertIn('l4d2_bsp/__init__.py', packed.namelist())
                self.assertIn('docs/presets.md', packed.namelist())
                self.assertEqual(packed.read('l4d2_bsp/preset_data/c5m1-daylight-v1.json'), b'fixture')
                self.assertEqual(packed.read('l4d2_bsp/preset_data/c4m3-overcast-static-v1.json'), b'fixture')
                self.assertIn('docs/c4m3-guide.zh-CN.md', packed.namelist())
                self.assertNotIn('config.local.json', packed.namelist())
                self.assertFalse(any(b'PRIVATE' in packed.read(n) for n in packed.namelist()))
            with self.assertRaises(FileExistsError):
                build_release(root, output)

    def test_release_requires_preset_and_its_public_documentation(self):
        from scripts.make_release import build_release
        for missing in ('docs/presets.md', 'docs/c4m3-guide.zh-CN.md',
                        'l4d2_bsp/preset_data/c5m1-daylight-v1.json',
                        'l4d2_bsp/preset_data/c4m3-overcast-static-v1.json'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / 'source'
                self.make_source(root)
                (root / missing).unlink()
                output = Path(temp) / 'release.zip'
                with self.assertRaises(FileNotFoundError):
                    build_release(root, output)
                self.assertFalse(output.exists())

    def test_python_package_registers_both_builtin_presets_explicitly(self):
        import tomllib
        project = Path(__file__).resolve().parents[1] / 'pyproject.toml'
        settings = tomllib.loads(project.read_text(encoding='utf-8'))
        self.assertEqual(settings['tool']['setuptools']['package-data']['l4d2_bsp'], [
            'preset_data/c5m1-daylight-v1.json',
            'preset_data/c4m3-overcast-static-v1.json',
        ])

    def test_release_rejects_preset_symlink(self):
        from scripts.make_release import build_release
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'
            self.make_source(root)
            preset = root / 'l4d2_bsp/preset_data/c5m1-daylight-v1.json'
            preset.unlink()
            outside = Path(temp) / 'personal.json'
            outside.write_text('PRIVATE')
            try:
                preset.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f'Symlinks unavailable: {exc}')
            output = Path(temp) / 'release.zip'
            with self.assertRaisesRegex(ValueError, 'symlink or path escape'):
                build_release(root, output)
            self.assertFalse(output.exists())
