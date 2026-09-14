import tempfile
import unittest
from pathlib import Path


class NativeMountTests(unittest.TestCase):
    def test_writes_minimal_ordered_absolute_resource_mounts_exclusively(self):
        from l4d2_bsp.native_mounts import write_resource_root_gameinfo
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / 'update', root / 'left4dead2_dlc3'
            first.mkdir()
            second.mkdir()
            compile_dir = root / 'bake'
            compile_dir.mkdir()

            result = write_resource_root_gameinfo(compile_dir, [first, second])

            self.assertEqual(result, compile_dir / 'gameinfo.txt')
            text = result.read_text(encoding='ascii')
            self.assertIn('"SteamAppId" "550"', text)
            self.assertIn('"ToolsAppId" "563"', text)
            first_text, second_text = first.resolve().as_posix(), second.resolve().as_posix()
            self.assertLess(text.index(first_text), text.index(second_text))
            self.assertNotIn('\\', text)
            self.assertIn('"game" "LMC Compile"', text)
            self.assertEqual(text.count('"Game"'), 2)
            original = result.read_bytes()
            with self.assertRaises(FileExistsError):
                write_resource_root_gameinfo(compile_dir, [first])
            self.assertEqual(result.read_bytes(), original)

    def test_rejects_empty_missing_nonascii_quote_and_control_paths(self):
        from l4d2_bsp.native_mounts import write_resource_root_gameinfo
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            compile_dir = root / 'bake'
            compile_dir.mkdir()
            good = root / 'good'
            good.mkdir()
            cases = [
                ([], 'nonempty'),
                ([root / 'missing'], 'directory'),
                ([root / '引擎'], 'ASCII'),
                ([Path(str(good) + '"bad')], 'quote'),
                ([Path(str(good) + '\nbad')], 'control'),
            ]
            for roots, message in cases:
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    write_resource_root_gameinfo(compile_dir, roots)
                self.assertFalse((compile_dir / 'gameinfo.txt').exists())


if __name__ == '__main__':
    unittest.main()
