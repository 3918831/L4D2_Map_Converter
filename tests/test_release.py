import tempfile
import unittest
from pathlib import Path
import zipfile


class ReleaseTests(unittest.TestCase):
    def test_release_contains_only_allowed_source_and_never_game_artifacts(self):
        from scripts.make_release import build_release
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'
            root.mkdir()
            for name in ('README.md', 'NOTICE.md', 'CHANGELOG.md', 'pyproject.toml', '.gitignore',
                         'docs/guide.zh-CN.md', 'docs/architecture.md', 'docs/validation.md', 'docs/release.md',
                         'l4d2_bsp/__init__.py', 'tests/test_example.py', 'examples/c6-c5.example.json'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            (root / 'artifacts').mkdir()
            (root / 'artifacts/private.log').write_text('PRIVATE')
            (root / 'config.local.json').write_text('PRIVATE')
            output = Path(temp) / 'release.zip'
            build_release(root, output)
            with zipfile.ZipFile(output) as packed:
                self.assertIn('FILES.sha256', packed.namelist())
                self.assertIn('l4d2_bsp/__init__.py', packed.namelist())
                self.assertNotIn('config.local.json', packed.namelist())
                self.assertFalse(any(b'PRIVATE' in packed.read(n) for n in packed.namelist()))
            with self.assertRaises(FileExistsError):
                build_release(root, output)
