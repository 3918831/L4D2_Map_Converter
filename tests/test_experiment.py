import json
from pathlib import Path
import struct
import tempfile
import unittest

from scripts.run_experiment import run_experiment
from tests.test_cli import ENTITIES
from tests.test_resources import vpk_fixture


def map_fixture():
    entities = ENTITIES[:-1] + b'{\n"classname" "env_fog_controller"\n"targetname" "fog_master"\n"fogcolor" "18 29 33"\n}\n\0'
    header = bytearray(1036)
    struct.pack_into('<4si', header, 0, b'VBSP', 21)
    struct.pack_into('<iii4s', header, 8, 0, 1036, len(entities), b'\0'*4)
    struct.pack_into('<iii4s', header, 24, 0, 1036+len(entities), 4, b'\0'*4)
    struct.pack_into('<i', header, 1032, 49)
    return bytes(header)+entities+b'KEEP', entities


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root/'game'
        self.output = self.root/'output'
        self.bsp, self.entities = map_fixture()
        self.source = self.root/'c2m1_highway.bsp'
        self.source.write_bytes(self.bsp)
        for name in ('update', 'left4dead2_dlc3', 'left4dead2_dlc2', 'left4dead2_dlc1', 'left4dead2', 'hl2'):
            (self.game/name/'maps').mkdir(parents=True)
        (self.game/'left4dead2/gameinfo.txt').write_text('"GameInfo" { "FileSystem" { "SearchPaths" { Game update Game left4dead2_dlc3 Game left4dead2_dlc2 Game left4dead2_dlc1 Game |gameinfo_path|. Game hl2 } } }')
        self.base = self.game/'left4dead2/maps'
        (self.base/self.source.name).write_bytes(self.bsp)
        (self.base/'c2m1_highway_exclude.lst').write_bytes(b'EXCLUDE\r\n')
        (self.base/'c2m1_highway.nav').write_bytes(b'BASE NAV')
        (self.game/'update/maps/c2m1_highway.nav').write_bytes(b'UPDATE NAV')
        self.sidecars = []
        for i, mode in enumerate(('l', 'h', 's')):
            entities = self.entities.replace(b'other', ('mode'+str(i)).encode())
            data = struct.pack('<5i', 20, 0, 0, len(entities), 1234)+entities+b'OPAQUE TAIL'
            path = self.game/f'update/maps/c2m1_highway_{mode}_0.lmp'
            path.write_bytes(data)
            self.sidecars.append((path, data))
        resources = ['materials/correction/cc_c2_main.raw', 'materials/correction/cc_c5_main.raw']
        resources += [f'materials/skybox/sky_l4d_c5_1_hdr{face}.vmt' for face in ('bk', 'dn', 'ft', 'lf', 'rt', 'up')]
        self.vpk = self.game/'left4dead2/pak01_dir.vpk'
        self.vpk.write_bytes(vpk_fixture(resources))

    def test_bundle_preserves_effective_nav_and_distinct_modes(self):
        report = run_experiment(self.source, self.game, self.output)
        self.assertEqual(report['status'], 'prepared_pending_runtime_validation')
        self.assertTrue(report['source']['matches_base_game'])
        changes = {'c5-color': (b'cc_c2_main.raw', b'cc_c5_main.raw'), 'fog-warm': (b'18 29 33', b'90 75 60'), 'sky-warm': (b'sky_l4d_c2m1_hdr', b'sky_l4d_c5_1_hdr')}
        for name, (old, new) in changes.items():
            folder = self.output/name/'maps'
            self.assertEqual((folder/self.source.name).read_bytes(), self.bsp.replace(old, new))
            self.assertEqual((folder/'c2m1_highway.nav').read_bytes(), b'UPDATE NAV')
            for path, data in self.sidecars:
                self.assertEqual((folder/path.name).read_bytes(), data.replace(old, new))
            for item in report['variants'][name]['files']:
                if 'comparison' in item:
                    self.assertTrue(item['comparison']['io_unchanged'])
                    self.assertEqual(item['comparison']['changed_lumps'], [0])
                    self.assertTrue(item['outside_patch_unchanged'])
        self.assertEqual((self.output/'baseline/maps'/self.source.name).read_bytes(), self.bsp)
        self.assertEqual(self.source.read_bytes(), self.bsp)
        self.assertEqual(json.loads((self.output/'experiment.json').read_text())['source'], report['source'])

    def test_rejects_existing_root_missing_sidecar_or_base_mismatch_before_write(self):
        self.output.mkdir()
        with self.assertRaises(ValueError):
            run_experiment(self.source, self.game, self.output)
        self.output.rmdir()
        self.sidecars[0][0].unlink()
        with self.assertRaises(ValueError):
            run_experiment(self.source, self.game, self.output)
        self.assertFalse(self.output.exists())
        self.sidecars[0][0].write_bytes(self.sidecars[0][1])
        (self.base/self.source.name).write_bytes(self.bsp+b'ALTERED')
        with self.assertRaises(ValueError):
            run_experiment(self.source, self.game, self.output)
        self.assertFalse(self.output.exists())

    def test_missing_resource_blocks_only_affected_variants_explicitly(self):
        self.vpk.write_bytes(vpk_fixture(['materials/correction/cc_c2_main.raw']))
        report = run_experiment(self.source, self.game, self.output)
        self.assertEqual(report['status'], 'partially_blocked')
        self.assertEqual(report['variants']['c5-color']['status'], 'blocked')
        self.assertFalse((self.output/'c5-color').exists())
        self.assertEqual(report['variants']['sky-warm']['status'], 'blocked')
        self.assertTrue((self.output/'fog-warm/maps'/self.source.name).exists())

    def test_mismatching_sidecar_field_prevents_any_output(self):
        path, data = self.sidecars[1]
        path.write_bytes(data.replace(b'cc_c2_main.raw', b'cc_c1_main.raw'))
        with self.assertRaises(ValueError):
            run_experiment(self.source, self.game, self.output)
        self.assertFalse(self.output.exists())
