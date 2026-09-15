from pathlib import Path
import tempfile
import unittest

from l4d2_bsp import discovery


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'arbitrary_map.bsp'
        self.source.write_bytes(b'source')

    def test_arbitrary_name_and_actual_modes_only(self):
        (self.root / 'arbitrary_map_l_0.lmp').write_bytes(b'campaign')
        (self.root / 'arbitrary_map_x_2.lmp').write_bytes(b'extra')
        (self.root / 'different_l_0.lmp').write_bytes(b'other')
        report = discovery.discover_inputs(self.source)
        self.assertEqual(report['map_name'], 'arbitrary_map')
        self.assertEqual(list(report['mode_lmps']), ['l_0', 'x_2'])
        self.assertEqual(report['nav']['status'], 'not_found_in_search_dirs')
        self.assertEqual(self.source.read_bytes(), b'source')

    def test_no_modes_is_reported_without_inventing_files(self):
        report = discovery.discover_inputs(self.source)
        self.assertEqual(report['mode_lmps'], {})
        self.assertEqual(report['mode_discovery'], 'none_found_in_search_dirs')
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_explicit_search_order_and_shadowed_candidates(self):
        update = self.root / 'update'
        update.mkdir()
        (update / 'arbitrary_map_l_0.lmp').write_bytes(b'new')
        (self.root / 'arbitrary_map_l_0.lmp').write_bytes(b'old')
        (self.root / 'arbitrary_map.nav').write_bytes(b'nav')
        report = discovery.discover_inputs(self.source, search_dirs=[update])
        item = report['mode_lmps']['l_0']
        self.assertEqual(item['selected']['path'], str((update / 'arbitrary_map_l_0.lmp').resolve()))
        self.assertEqual(len(item['candidates']), 2)
        self.assertNotEqual(item['candidates'][0]['sha256'], item['candidates'][1]['sha256'])
        self.assertEqual(report['nav']['status'], 'found')

    def test_source_is_explicit_even_if_search_directory_has_another_bsp(self):
        update = self.root / 'update'
        update.mkdir()
        (update / self.source.name).write_bytes(b'other BSP')
        report = discovery.discover_inputs(self.source, search_dirs=[update, update])
        self.assertEqual(report['source_bsp']['path'], str(self.source.resolve()))
        self.assertEqual(len(report['search_dirs']), 2)

    def test_invalid_or_unreadable_inputs_are_not_absence(self):
        with self.assertRaises(OSError):
            discovery.discover_inputs(self.root / 'missing.bsp')
        with self.assertRaises(OSError):
            discovery.discover_inputs(self.source, search_dirs=[self.root / 'missing'])
        for name in ('input.txt', 'bad name.bsp', 'bad;quit.bsp'):
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(b'input')
                with self.assertRaises(ValueError):
                    discovery.discover_inputs(path)

    def test_noncanonical_patch_is_reported_not_silently_ignored(self):
        (self.root / 'arbitrary_map_l_01.lmp').write_bytes(b'unknown')
        report = discovery.discover_inputs(self.source)
        self.assertEqual(len(report['unrecognized_companions']), 1)
        self.assertEqual(report['mode_lmps'], {})

    def test_metadata_has_content_identity(self):
        report = discovery.discover_inputs(self.source)
        self.assertEqual(report['source_bsp']['size'], 6)
        self.assertEqual(len(report['source_bsp']['sha256']), 64)


if __name__ == '__main__':
    unittest.main()
