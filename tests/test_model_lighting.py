"""Model-version migrations require real resource and vertex-layout evidence."""
import json
import struct
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

from l4d2_bsp.native import audit_bake
from tests.test_native import bsp, pak


MODEL = 'models/props/test.mdl'


def model_files(checksum=456):
    # One body/model, one material mesh, split into two VTX strip groups.
    mdl = bytearray(692)
    struct.pack_into('<4sII', mdl, 0, b'IDST', 49, checksum)
    struct.pack_into('<i', mdl, 76, len(mdl))
    struct.pack_into('<2i', mdl, 232, 1, 408)
    struct.pack_into('<4i', mdl, 408, 0, 1, 1, 16)
    struct.pack_into('<5i', mdl, 424+72, 1, 148, 3, 0, 0)
    struct.pack_into('<4i', mdl, 572, 0, -148, 3, 0)
    vvd = bytearray(64+3*48+3*16)
    struct.pack_into('<4sIIi', vvd, 0, b'IDSV', 4, checksum, 1)
    struct.pack_into('<i', vvd, 16, 3)
    struct.pack_into('<4i', vvd, 48, 0, 64, 64, 208)
    vtx = bytearray(242)
    struct.pack_into('<iiHHiiiiii', vtx, 0, 7, 32, 3, 3, 3, checksum, 1, 36, 1, 44)
    struct.pack_into('<2i', vtx, 44, 1, 8)
    struct.pack_into('<2i', vtx, 52, 1, 8)
    struct.pack_into('<2if', vtx, 60, 1, 12, 0)
    struct.pack_into('<2iB', vtx, 72, 2, 9, 0)
    # Each strip group contains one triangle; duplicate indices are valid here.
    struct.pack_into('<6iB', vtx, 81, 2, 50, 3, 77, 1, 89, 0)
    struct.pack_into('<6iB', vtx, 106, 1, 43, 3, 58, 1, 91, 0)
    struct.pack_into('<H', vtx, 135, 0)
    struct.pack_into('<H', vtx, 144, 1)
    struct.pack_into('<H', vtx, 153, 2)
    struct.pack_into('<3H', vtx, 158, 0, 1, 0)
    struct.pack_into('<3H', vtx, 164, 0, 0, 0)
    struct.pack_into('<4ihB2i', vtx, 170, 3, 0, 2, 0, 0, 1, 0, 0)
    struct.pack_into('<4ihB2i', vtx, 197, 3, 0, 1, 0, 0, 1, 0, 0)
    return {MODEL: bytes(mdl), MODEL[:-4]+'.vvd': bytes(vvd), MODEL[:-4]+'.dx90.vtx': bytes(vtx)}


def lighting(checksum, groups):
    data = bytearray(1024)
    struct.pack_into('<6I', data, 0, 2, checksum, 4, 4, sum(groups), len(groups))
    cursor = 512
    for i, count in enumerate(groups):
        struct.pack_into('<3I', data, 40+28*i, 0, count, cursor)
        data[cursor:cursor+count*4] = b'\1\2\3\4'*count
        cursor += count*4
    return bytes(data)


def scene(checksum=123, groups=(3,), index=0, leaf_flags=0):
    prop = bytearray(72)
    struct.pack_into('<H', prop, 24, index)
    sprp = struct.pack('<i', 1)+MODEL.encode().ljust(128, b'\0')+struct.pack('<2i', 0, 1)+prop
    leaf = bytearray(32)
    struct.pack_into('<H', leaf, 6, leaf_flags)
    return bsp({10: bytes(leaf), 40: pak([(zipfile.ZipInfo('sp_hdr_0.vhv'), lighting(checksum, groups))])},
               [('sprp', 0, 9, sprp)], versions={10: 1})


class ModelLightingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = model_files()
        self.install()

    def install(self):
        for name, data in self.files.items():
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def evidence(self):
        from l4d2_bsp.model_lighting import ModelLightingEvidence
        return ModelLightingEvidence(scene(), [self.root])

    def test_valid_current_model_allows_changed_checksum_and_mesh_partition(self):
        # Removing evidence handling must reproduce the original C6 rejection.
        try:
            proof = self.evidence()
            report = audit_bake(scene(), scene(456, (2, 1)), model_evidence=proof)
        except (ImportError, TypeError, ValueError) as exc:
            self.fail(f'Validated model-version migration was rejected: {exc}')
        self.assertEqual(report['model_lighting_migrations'][0]['model'], MODEL)
        self.assertEqual(report['model_lighting_migrations'][0]['new_checksum'], 456)
        self.assertTrue(report['sprp_payload_unchanged'])

    def test_without_evidence_checksum_change_still_fails(self):
        with self.assertRaisesRegex(ValueError, 'VHV topology'):
            audit_bake(scene(), scene(456, (2, 1)))

    def test_checksum_only_migration_with_same_layout_is_valid(self):
        from l4d2_bsp.model_lighting import ModelLightingEvidence
        original = scene(123, (2, 1))
        proof = ModelLightingEvidence(original, [self.root])
        result = audit_bake(original, scene(456, (2, 1)), model_evidence=proof)
        self.assertEqual(len(result['model_lighting_migrations']), 1)

    def test_evidence_from_another_bsp_cannot_authorize_changes(self):
        proof = self.evidence()
        with self.assertRaisesRegex(ValueError, 'another bake input'):
            audit_bake(scene(122), scene(456, (2, 1)), model_evidence=proof)

    def test_unchanged_checksum_cannot_authorize_topology_changes(self):
        self.files = model_files(123)
        self.install()
        with self.assertRaisesRegex(ValueError, 'Unproven'):
            audit_bake(scene(), scene(123, (2, 1)), model_evidence=self.evidence())

    def test_invalid_prop_model_index_fails(self):
        from l4d2_bsp.model_lighting import ModelLightingEvidence
        with self.assertRaisesRegex(ValueError, 'model index'):
            ModelLightingEvidence(scene(index=1), [self.root])

    def test_higher_priority_resource_added_after_snapshot_is_detected(self):
        from l4d2_bsp.model_lighting import ModelLightingEvidence
        high = self.root/'update'
        high.mkdir()
        proof = ModelLightingEvidence(scene(), [high, self.root])
        destination = high/MODEL
        destination.parent.mkdir(parents=True)
        destination.write_bytes(self.files[MODEL])
        with self.assertRaisesRegex(ValueError, 'changed'):
            proof.verify_current()

    def test_wrong_partition_even_with_correct_checksum_fails(self):
        proof = self.evidence()
        with self.assertRaisesRegex(ValueError, 'layout'):
            audit_bake(scene(), scene(456, (1, 2)), model_evidence=proof)

    def test_wrong_checksum_and_lod_fail(self):
        proof = self.evidence()
        with self.assertRaises(ValueError):
            audit_bake(scene(), scene(457, (2, 1)), model_evidence=proof)
        bad = bytearray(lighting(456, (2, 1)))
        struct.pack_into('<I', bad, 40, 1)
        from l4d2_bsp.binary import BspFile
        # Keep the same prop mapping; replace just the synthetic PAK payload.
        original = scene()
        from l4d2_bsp.native import _game_entries
        game = [('sprp', *_game_entries(BspFile.parse(original))['sprp'])]
        with self.assertRaises(ValueError):
            audit_bake(original, bsp({40: pak([('sp_hdr_0.vhv', bad)])}, game), model_evidence=proof)

    def test_incoherent_model_family_fails_before_bake(self):
        data = bytearray(self.files[MODEL[:-4]+'.vvd'])
        struct.pack_into('<I', data, 8, 999)
        self.files[MODEL[:-4]+'.vvd'] = bytes(data)
        self.install()
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.evidence()

    def test_out_of_range_vtx_vertex_is_rejected(self):
        data = bytearray(self.files[MODEL[:-4]+'.dx90.vtx'])
        struct.pack_into('<H', data, 153, 3)
        self.files[MODEL[:-4]+'.dx90.vtx'] = bytes(data)
        self.install()
        with self.assertRaisesRegex(ValueError, 'vertex'):
            self.evidence()

    def test_vvd_fixups_must_reconstruct_exact_lod_vertex_count(self):
        from l4d2_bsp.model_lighting import model_layout
        for fixups in ([(0, 0, 3), (0, 0, 3)], [(0, 0, 2)]):
            with self.subTest(fixups=fixups):
                original = self.files[MODEL[:-4]+'.vvd']
                table = b''.join(struct.pack('<3i', *row) for row in fixups)
                value = bytearray(original[:64]+table+original[64:])
                struct.pack_into('<4i', value, 48, len(fixups), 64, 64+len(table), 208+len(table))
                with self.assertRaisesRegex(ValueError, 'fixup'):
                    model_layout(self.files[MODEL], value, self.files[MODEL[:-4]+'.dx90.vtx'])

    def test_valid_vvd_fixups_are_accepted(self):
        from l4d2_bsp.model_lighting import model_layout
        original = self.files[MODEL[:-4]+'.vvd']
        value = bytearray(original[:64]+struct.pack('<3i', 0, 0, 3)+original[64:])
        struct.pack_into('<4i', value, 48, 1, 64, 76, 220)
        self.assertEqual(model_layout(self.files[MODEL], value, self.files[MODEL[:-4]+'.dx90.vtx']),
                         (456, [(0, 2), (0, 1)]))

    def test_mutated_resource_invalidates_snapshot(self):
        proof = self.evidence()
        (self.root/MODEL).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            proof.verify_current()

    def test_matching_checksum_still_tracks_vertex_and_topology_resources(self):
        from l4d2_bsp.model_lighting import ModelLightingEvidence
        for suffix in ('.vvd', '.dx90.vtx'):
            with self.subTest(resource=suffix):
                self.install()
                proof = ModelLightingEvidence(scene(456, (2, 1)), [self.root])
                companion = self.root/(MODEL[:-4]+suffix)
                value = bytearray(companion.read_bytes())
                value[-1] ^= 1  # Preserve the family checksum while changing its data.
                companion.write_bytes(value)
                with self.assertRaisesRegex(ValueError, 'changed'):
                    proof.verify_current()

    def test_truncated_vtx_and_invalid_relative_pointer_fail(self):
        for value in (self.files[MODEL[:-4]+'.dx90.vtx'][:75],
                      self.files[MODEL[:-4]+'.dx90.vtx'][:48]+struct.pack('<i', -20)+self.files[MODEL[:-4]+'.dx90.vtx'][52:]):
            with self.subTest(size=len(value)):
                (self.root/(MODEL[:-4]+'.dx90.vtx')).write_bytes(value)
                with self.assertRaises(ValueError):
                    self.evidence()

    def test_build_uses_model_proof_and_records_resource_identity(self):
        from l4d2_bsp.workflow import build
        from l4d2_bsp.presets import load_preset
        preset = load_preset()
        output = self.root/'run'
        nav = self.root/'test.nav'
        nav.write_bytes(b'nav')
        cfg = {'output_dir': output, 'profile': 'c6-c5', 'atmosphere_policy': 'replace', 'resource_roots': [self.root],
               'native_mounts': 'resource_roots',
               'game_dir': self.root, 'tools_dir': self.root, 'nav': nav, 'exclude': None,
               'threads': 1, 'timeout_seconds': 10, 'preset': preset.id, 'preset_file': preset.source_path}
        def bake(tool, args, **kwargs):
            game_dir = Path(args[args.index('-game') + 1])
            self.assertEqual(game_dir, output / 'bake')
            generated = game_dir / 'gameinfo.txt'
            self.assertTrue(generated.is_file())
            during = json.loads((output / 'run.json').read_text())
            self.assertTrue(any(Path(item['path']).samefile(generated) for item in during['tracked_outputs']))
            Path(args[-1]).write_bytes(scene(456, (2, 1), leaf_flags=0x0200))
            kwargs['log'].write_text('Finished fixture bake\n')
        def package(payloads, *, stage, stem, **kwargs):
            from l4d2_bsp.configuration import file_hash
            directory = stage/stem
            for name, data in payloads.items():
                path = directory/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            archive = directory.with_suffix('.vpk')
            archive.write_bytes(pak(list(payloads.items())))
            return {'vpk': str(archive), 'sha256': file_hash(archive),
                    'native_extract_verified': True, 'files': [{'name': n} for n in payloads]}
        with patch('l4d2_bsp.workflow.check', return_value=(cfg, {'map_name': 'c6m1_riverbank', 'preset': preset.metadata()}, scene(), {})), \
             patch('l4d2_bsp.workflow.input_inventory', return_value=[]), \
             patch('l4d2_bsp.native.native', bake), patch('l4d2_bsp.native.package_files', package):
            try:
                result = build(self.root/'config.json')
            except ValueError as exc:
                self.fail(f'Workflow did not supply model evidence: {exc}')
        self.assertEqual(result['status'], 'offline_ready')
        self.assertTrue(result['bake_audit']['leaf_sky_flags_changed'])
        self.assertFalse(result['bake_audit']['protected_lumps_unchanged'])
        self.assertEqual(len(result['model_resource_inventory']), 3)
        self.assertEqual((output/'preset.json').read_bytes(), preset.source_path.read_bytes())
        self.assertEqual(result['preset']['sha256'], preset.sha256)
        self.assertIn(str((output/'preset.json').resolve()), [item['path'] for item in result['tracked_outputs']])
        from l4d2_bsp.workflow import load_run
        load_run(output)
        (self.root/MODEL).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            load_run(output)


if __name__ == '__main__':
    unittest.main()
