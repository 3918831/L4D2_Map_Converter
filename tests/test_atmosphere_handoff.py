"""New preset assets must survive both sides of the manual capture boundary."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from l4d2_bsp.presets import load_preset
from l4d2_bsp.soundscapes import preset_soundscape_assets
from l4d2_bsp.workflow import prepare, finish


class AtmosphereHandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.preset = load_preset('c4m3-overcast-static-v1')
        self.name = 'c2m1_highway'
        self.map = self.root/'offline/maps/c2m1_highway.bsp'
        self.map.parent.mkdir(parents=True)
        self.map.write_bytes(b'baseline')
        self.script_name = f'scripts/soundscapes_{self.name}.txt'
        self.script = preset_soundscape_assets(self.preset,self.name)[self.script_name]
        script_path = self.root/'offline'/self.script_name
        script_path.parent.mkdir()
        script_path.write_bytes(self.script)
        snapshot = self.root/'preset.json'
        snapshot.write_bytes(self.preset.source_path.read_bytes())
        self.report = {'schema_version':1,'run_id':'0123456789abcdef','map_name':self.name,
            'status':'offline_ready','input_inventory':[],'tracked_outputs':[],
            'config':{'preset':self.preset.id,'game_dir':str(self.root),'tools_dir':str(self.root)},
            'preset':self.preset.metadata()|{'snapshot_path':str(snapshot)}, 'offline_map':str(self.map),
            'offline_package':{'vpk':str(self.root/'offline.vpk'),
                'files':[{'name':f'maps/{self.name}.bsp'},{'name':self.script_name}]}}

    def save(self):
        (self.root/'run.json').write_text(json.dumps(self.report))

    def test_alias_gets_soundscape_script_and_its_own_exposure_guard(self):
        self.save()
        with patch('l4d2_bsp.reflections.prepare_capture',return_value=({},{})):
            result = prepare(self.root)
        path = self.root/f'capture/scripts/soundscapes_{result["capture_alias"]}.txt'
        self.assertEqual(path.read_bytes(),self.script)
        scripts = b'\n'.join(p.read_bytes() for p in (self.root/'capture/scripts/vscripts').glob('*'))
        self.assertIn(b'maximum != 10',scripts)
        self.assertNotIn(b'maximum != 5',scripts)

    def test_final_package_retains_soundscape_and_target_identity(self):
        self.report.update(status='capture_prepared',capture_alias='mc01234567')
        self.save()
        captured,log = self.root/'captured.bsp',self.root/'capture.log'
        captured.write_bytes(b'capture')
        log.write_text('LMC_0123456789ABCDEF_MAP=mc01234567\n'
                      'LMC_0123456789ABCDEF_GUARD_PASSED_CAPTURE_NOT_VERIFIED\n'
                      'LMC_0123456789ABCDEF_CAPTURE_REQUESTED\n')
        packages = []
        def native(tool,args,**kwargs):
            Path(args[3]).write_bytes(b'final')
        def package(payloads,**kwargs):
            packages.append(payloads)
            output = self.root/'final.vpk'
            output.write_bytes(b'package')
            return {'vpk':str(output),'files':[{'name':n} for n in payloads]}
        with patch('l4d2_bsp.reflections.capture_replacements',return_value=({},{})), \
             patch('l4d2_bsp.native.native',native), patch('l4d2_bsp.workflow.audit_repacked'), \
             patch('l4d2_bsp.native.package_files',package):
            finish(self.root,captured,log)
        self.assertEqual(packages[0][self.script_name],self.script)
        self.assertIn(self.preset.id.encode(),packages[0]['addoninfo.txt'])

    def test_role_specific_preset_capture_uses_survivor_maximum(self):
        preset = load_preset('c7m1-hazy-static-v1')
        snapshot = self.root / 'preset.json'
        snapshot.write_bytes(preset.source_path.read_bytes())
        self.report['preset'] = preset.metadata() | {'snapshot_path': str(snapshot)}
        self.report['config']['preset'] = preset.id
        self.save()
        with patch('l4d2_bsp.reflections.prepare_capture', return_value=({}, {})):
            result = prepare(self.root)
        scripts = b'\n'.join(p.read_bytes() for p in (self.root/'capture/scripts/vscripts').glob('*'))
        self.assertIn(b'maximum != 9', scripts)
        self.assertNotIn(b'maximum != 3', scripts)
        sound = self.root/f'capture/scripts/soundscapes_{result["capture_alias"]}.txt'
        self.assertIn(b'lmc_c7m1_hazy_v1.outdoor', sound.read_bytes())
