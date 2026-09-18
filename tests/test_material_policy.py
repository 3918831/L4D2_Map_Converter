import io
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from l4d2_bsp.binary import BspFile
from l4d2_bsp.native import _game_entries, _pak_entries
from tests.test_model_lighting import model_files, MODEL
from tests.test_native import bsp, pak


MATERIAL = 'materials/models/props_mill/boiler_01.vmt'
VMT = b'VertexLitGeneric\n{\n$basetexture "models/props_mill/boiler_01"\n$envmap env_cubemap\n$envmaptint "[.5 .5 .5]"\n}\n'


def resources():
    files = model_files()
    mdl = bytearray(files[MODEL])
    struct.pack_into('<i', mdl, 152, 16)
    texture = len(mdl); mdl.extend(bytes(64)); name = len(mdl); mdl.extend(b'boiler_01\0')
    dirs = len(mdl); mdl.extend(bytes(4)); directory = len(mdl); mdl.extend(b'models/props_mill/\0')
    struct.pack_into('<i', mdl, texture, name-texture)
    struct.pack_into('<i', mdl, dirs, directory)
    struct.pack_into('<4i', mdl, 204, 1, texture, 1, dirs)
    struct.pack_into('<i', mdl, 76, len(mdl))
    files[MODEL] = bytes(mdl)
    files[MODEL[:-4]+'.phy'] = struct.pack('<4I', 16, 0, 1, 456) + b'COLLISION'
    files[MATERIAL] = VMT
    return files


def scene():
    prop = bytearray(72); struct.pack_into('<3f',prop,0,12,34,56); prop[30]=6
    sprp = struct.pack('<i',1)+MODEL.encode().ljust(128,b'\0')+struct.pack('<2i',0,1)+prop
    return bsp({40:pak([(zipfile.ZipInfo('keep.txt'),b'original')])}, [('sprp',0,9,sprp)])


class MaterialPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.files=resources();self.install()

    def install(self):
        for name,data in self.files.items():
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)

    def apply(self,data=None,policy='catalogued-static-reflections-v1'):
        try:
            from l4d2_bsp.material_policy import apply_material_policy
        except ImportError as exc:
            self.fail(f'Optional material isolation is unavailable: {exc}')
        return apply_material_policy(data or scene(),[self.root],policy=policy)

    def test_private_model_material_and_unchanged_geometry_and_source(self):
        out,audit=self.apply();old=BspFile.parse(scene());new=BspFile.parse(out)
        for i in set(range(64))-{35,40}:self.assertEqual(old.lump_bytes(i),new.lump_bytes(i))
        a=_game_entries(old)['sprp'][2];b=_game_entries(new)['sprp'][2]
        self.assertEqual(a[:4],b[:4]);self.assertEqual(a[132:],b[132:])
        clone=b[4:132].split(b'\0')[0].decode();self.assertTrue(clone.startswith('models/lmc/'))
        entries=_pak_entries(new.lump_bytes(40));self.assertEqual(entries['keep.txt'],b'original')
        self.assertNotIn(MATERIAL,entries);self.assertNotIn(MODEL,entries)
        private=[n for n in entries if n.endswith('.vmt')];self.assertEqual(len(private),1)
        self.assertTrue(private[0].startswith('materials/lmc/'))
        self.assertIn(b'[0.25 0.25 0.25]',entries[private[0]])
        self.assertIn(b'models/props_mill/boiler_01',entries[private[0]])
        for ext in ('.vvd','.dx90.vtx','.phy'):self.assertEqual(entries[clone[:-4]+ext],self.files[MODEL[:-4]+ext])
        for name,data in self.files.items():self.assertEqual((self.root/name).read_bytes(),data)
        self.assertTrue(audit['prop_instances_unchanged']);self.assertTrue(audit['entities_unchanged'])

    def test_preserve_is_exact_noop_and_deterministic_opt_in(self):
        self.assertEqual(self.apply(policy='preserve')[0],scene())
        a,aa=self.apply();b,bb=self.apply();self.assertEqual(a,b);self.assertEqual(aa,bb)

    def test_uncatalogued_and_proxy_materials_remain_unchanged(self):
        for vmt in (b'VertexLitGeneric { $envmap env_cubemap $envmaptint "[.5 .5 .5]" Proxies { Sine { resultVar $envmaptint } } }',
                    b'Patch { include "other.vmt" }'):
            self.files[MATERIAL]=vmt;self.install();out,audit=self.apply()
            self.assertEqual(out,scene());self.assertTrue(audit['skipped'])

    def test_phong_boost_is_reduced_without_touching_fresnel(self):
        self.files[MATERIAL]=b'VertexLitGeneric { $phong 1 $phongboost ".1" $phongfresnelranges "[5 5 60]" }'
        self.install();out,_=self.apply();values=_pak_entries(BspFile.parse(out).lump_bytes(40))
        vmt=next(v for k,v in values.items() if k.endswith('.vmt'))
        self.assertIn(b'0.05',vmt);self.assertIn(b'[5 5 60]',vmt)

    def test_malformed_numeric_fields_and_duplicate_keys_do_not_get_guessed(self):
        for fields in (' $envmap env_cubemap $envmaptint "[nan 1 1]"',
                       ' $envmap env_cubemap $envmaptint "[1 1 1]" $ENVMapTint "[2 2 2]"'):
            self.files[MATERIAL]=('VertexLitGeneric {'+fields+'}').encode();self.install()
            out,audit=self.apply();self.assertEqual(out,scene());self.assertTrue(audit['skipped'])

    def test_external_animation_or_lod_material_replacements_are_not_cloned(self):
        for field in ('external','lod'):
            self.files=resources()
            if field=='external':
                mdl=bytearray(self.files[MODEL]);struct.pack_into('<i',mdl,336,1);self.files[MODEL]=bytes(mdl)
            else:
                vtx=bytearray(self.files[MODEL[:-4]+'.dx90.vtx']);struct.pack_into('<i',vtx,36,1);self.files[MODEL[:-4]+'.dx90.vtx']=bytes(vtx)
            self.install();out,audit=self.apply();self.assertEqual(out,scene());self.assertTrue(audit['skipped'])

    def test_model_clone_preserves_layout_and_rejects_out_of_bounds_tables(self):
        try:from l4d2_bsp.material_models import material_table, clone_model
        except ImportError as exc:self.fail(f'Model material isolation is unavailable: {exc}')
        from l4d2_bsp.model_lighting import model_layout
        original=self.files[MODEL];clone=clone_model(original,'models/lmc/test/m.mdl','lmc/test/')
        self.assertEqual(material_table(clone)[1],['lmc/test/'])
        self.assertEqual(model_layout(original,self.files[MODEL[:-4]+'.vvd'],self.files[MODEL[:-4]+'.dx90.vtx']),model_layout(clone,self.files[MODEL[:-4]+'.vvd'],self.files[MODEL[:-4]+'.dx90.vtx']))
        bad=bytearray(original);struct.pack_into('<i',bad,208,len(bad)+10)
        with self.assertRaises(ValueError):material_table(bad)

    def test_material_run_rejects_policy_drift_source_changes_and_wrong_snapshot(self):
        from l4d2_bsp.workflow import verify_material_run
        out,audit=self.apply();run=self.root/'run';(run/'bake').mkdir(parents=True)
        snapshot=run/'bake/input.bsp.snapshot';snapshot.write_bytes(out)
        report=dict(config=dict(material_policy='catalogued-static-reflections-v1',resource_roots=[self.root]),preflight=dict(material_policy=audit))
        verify_material_run(report,run)
        report['config']['material_policy']='preserve'
        with self.assertRaisesRegex(ValueError,'mismatch'):verify_material_run(report,run)
        report['config']['material_policy']='catalogued-static-reflections-v1'
        snapshot.write_bytes(scene())
        with self.assertRaisesRegex(ValueError,'identity'):verify_material_run(report,run)
        snapshot.write_bytes(out);(self.root/MATERIAL).write_bytes(VMT+b'// changed\n')
        with self.assertRaisesRegex(ValueError,'changed'):verify_material_run(report,run)

    def test_other_map_has_distinct_private_namespace(self):
        raw=scene();a,_=self.apply(raw);changed=bytearray(raw);struct.pack_into('<i',changed,1032,8)
        b,_=self.apply(bytes(changed))
        aa={n for n in _pak_entries(BspFile.parse(a).lump_bytes(40)) if n.startswith(('models/lmc/','materials/lmc/'))}
        bb={n for n in _pak_entries(BspFile.parse(b).lump_bytes(40)) if n.startswith(('models/lmc/','materials/lmc/'))}
        self.assertTrue(aa and bb);self.assertFalse(aa&bb)

    def test_missing_physics_does_not_emit_partial_private_resources(self):
        (self.root/(MODEL[:-4]+'.phy')).unlink()
        out,audit=self.apply();self.assertEqual(out,scene());self.assertEqual(audit['model_copies'],[])
        self.assertTrue(audit['skipped'])
