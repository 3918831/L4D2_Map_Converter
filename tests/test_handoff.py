import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch


class HandoffTests(unittest.TestCase):
    def test_install_refuses_existing_files_without_overwriting_anything(self):
        from l4d2_bsp.handoff import install_files
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'cfg').mkdir()
            existing = root / 'cfg/test.cfg'
            existing.write_bytes(b'original')
            with self.assertRaisesRegex(ValueError, 'exists'):
                install_files(root, {'cfg/new.cfg': b'new', 'cfg/test.cfg': b'new'})
            self.assertEqual(existing.read_bytes(), b'original')
            self.assertFalse((root / 'cfg/new.cfg').exists())

    def test_install_rejects_parent_traversal_before_writing(self):
        from l4d2_bsp.handoff import install_files
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'game'
            root.mkdir()
            with self.assertRaises(ValueError):
                install_files(root, {'../escape.cfg': b'x'})
            self.assertFalse((root.parent / 'escape.cfg').exists())

    def test_receipt_hashes_new_files_and_does_not_touch_unrelated_files(self):
        from l4d2_bsp.handoff import install_files
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receipt = install_files(root, {'maps/alias.bsp': b'fixture'})
            self.assertEqual(len(receipt), 1)
            self.assertEqual(receipt[0]['name'], 'maps/alias.bsp')
            self.assertEqual((root / 'maps/alias.bsp').read_bytes(), b'fixture')

    def test_game_running_check_fails_closed(self):
        from l4d2_bsp.handoff import require_game_closed
        from types import SimpleNamespace
        with patch('l4d2_bsp.handoff.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='"left4dead2.exe","123"')):
            with self.assertRaisesRegex(ValueError, 'Exit'):
                require_game_closed()

    def test_cleanup_uses_receipt_even_when_original_inputs_are_unavailable(self):
        import json
        from l4d2_bsp.handoff import remove
        from l4d2_bsp.configuration import file_hash
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = root / 'game'
            game.mkdir()
            installed = game / 'owned.cfg'
            installed.write_bytes(b'owned')
            manifest = {'schema_version': 1, 'config': {'game_dir': str(game)}, 'capture_alias': 'mcfixture',
                        'input_inventory': [{'path': str(root / 'unavailable.dll'), 'sha256': 'missing'}],
                        'tracked_outputs': [], 'capture_installation': [{'name': 'owned.cfg', 'path': str(installed), 'sha256': file_hash(installed)}]}
            (root / 'run.json').write_text(json.dumps(manifest))
            with patch('l4d2_bsp.handoff.require_game_closed'):
                remove(root)
            self.assertFalse(installed.exists())
