import copy
import json
import unittest

from l4d2_bsp.binary import BspFile
from l4d2_bsp.inspect import container, entity_list
from l4d2_bsp.presets import load_preset
from tests.test_core import make_bsp, make_lmp


def ent(cls, **fields):
    return ('{\n"classname" "' + cls + '"\n' +
            ''.join(f'"{k}" "{v}"\n' for k, v in fields.items()) + '}\n').encode()


def source():
    return (ent('worldspawn', skyname='original', musicpostfix='Anything') +
            ent('light_environment', origin='10 20 30', _light='1 2 3 10') +
            ent('sky_camera', origin='40 50 60', scale='16') +
            ent('env_fog_controller', targetname='basement', fogcolor='1 2 3') +
            ent('env_fog_controller', targetname='other_fog', fogcolor='9 8 7') +
            ent('env_tonemap_controller', targetname='custom_exposure') +
            ent('func_precipitation', targetname='rain', model='*2') +
            ent('logic_relay', targetname='shared', OnTrigger='rain,Enable,,0,-1',
                OnUser1='gameplay,Trigger,,0,-1') +
            ent('logic_relay', targetname='gameplay') +
            ent('prop_dynamic', model='models/truck.mdl', origin='1 2 3') +
            ent('light', _light='255 40 20 100') + b'\0')


class GenericConversionTests(unittest.TestCase):
    def transfer(self, text=None, kind='bsp', preset='c5m1-daylight-v1'):
        from l4d2_bsp.generic_conversion import transfer_generic
        data = (make_bsp if kind == 'bsp' else make_lmp)(text or source())
        return transfer_generic(data, load_preset(preset), kind=kind)

    def test_non_c2_names_multiple_fogs_and_gameplay_protection(self):
        out, report = self.transfer()
        es = entity_list(BspFile.parse(out))
        fogs = [e for e in es if e.one('classname') == 'env_fog_controller']
        self.assertEqual([e.one('fogcolor') for e in fogs], ['130 117 107'] * 2)
        self.assertFalse(any(e.one('classname') == 'func_precipitation' for e in es))
        self.assertIn(b'"OnUser1" "gameplay,Trigger,,0,-1"', out)
        self.assertNotIn(b'rain,Enable', BspFile.parse(out).lump_bytes(0))
        self.assertIn(b'"_light" "255 40 20 100"', out)
        self.assertTrue(report['non_entity_payloads_unchanged'])
        self.assertTrue(report['protected_io_unchanged'])
        self.assertEqual(report['capture_tonemap'], 'custom_exposure')
        self.assertEqual(BspFile.parse(out).lump_bytes(1), b'PAYLOAD')

    def test_lmp_and_target_preset_are_independent_of_source_campaign(self):
        out, report = self.transfer(kind='lmp', preset='c4m3-overcast-static-v1')
        es = entity_list(container(out, 'lmp'))
        self.assertTrue(any(e.one('skyname') == 'sky_l4d_c4m4_hdr' for e in es))
        self.assertFalse(any(e.one('classname') == 'env_sun' for e in es))
        self.assertEqual(report['plan']['preset']['id'], 'c4m3-overcast-static-v1')

    def test_plan_replay_and_tamper_rejection(self):
        from l4d2_bsp.generic_conversion import apply_plan, plan_conversion
        data, preset = make_bsp(source()), load_preset()
        plan = plan_conversion(data, preset)
        a, _ = apply_plan(data, plan, preset)
        b, _ = apply_plan(data, json.loads(json.dumps(plan)), preset)
        self.assertEqual(a, b)
        wrong = copy.deepcopy(plan)
        wrong['source_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'plan|Plan'):
            apply_plan(data, wrong, preset)
        wrong = copy.deepcopy(plan)
        wrong['operations'][0]['fields']['musicpostfix'] = 'CHANGED'
        with self.assertRaisesRegex(ValueError, 'plan|Plan'):
            apply_plan(data, wrong, preset)

    def test_mixed_target_name_refuses_removing_gameplay_branch(self):
        extra = ent('logic_relay', targetname='rain')
        with self.assertRaisesRegex(ValueError, 'Mixed|mixed|ambiguous'):
            self.transfer(source().rstrip(b'\0') + extra + b'\0')

    def test_weather_entity_with_gameplay_output_cannot_be_deleted(self):
        text = source().replace(b'"model" "*2"', b'"model" "*2"\n"OnUser1" "gameplay,Trigger,,0,-1"')
        with self.assertRaisesRegex(ValueError, 'output|Output'):
            self.transfer(text)

    def test_missing_roles_are_created_without_creating_skybox_geometry(self):
        out, report = self.transfer(ent('worldspawn', skyname='old') + b'\0')
        es = entity_list(BspFile.parse(out))
        self.assertTrue(any(e.one('classname') == 'light_environment' for e in es))
        self.assertTrue(any(e.one('classname') == 'env_tonemap_controller' for e in es))
        self.assertFalse(any(e.one('classname') == 'sky_camera' for e in es))
        self.assertEqual(report['capture_tonemap'], '__lmc_tonemap_v1')

    def test_preserves_sky_camera_transform(self):
        out, _ = self.transfer()
        sky = next(e for e in entity_list(BspFile.parse(out)) if e.one('classname') == 'sky_camera')
        self.assertEqual((sky.one('origin'), sky.one('scale')), ('40 50 60', '16'))

    def test_visual_scripts_require_review(self):
        text = source().replace(b'"targetname" "basement"', b'"targetname" "basement"\n"vscripts" "change_fog.nut"')
        with self.assertRaisesRegex(ValueError, 'script'):
            self.transfer(text)

    def test_shared_upstream_logic_is_not_removed(self):
        text = source().replace(b'"OnTrigger" "rain,Enable,,0,-1"',
                                b'"OnTrigger" "weather_relay,Trigger,,0,-1"')
        text = text.rstrip(b'\0') + ent('logic_relay', targetname='weather_relay',
                                      OnTrigger='rain,Enable,,0,-1') + b'\0'
        out, _ = self.transfer(text)
        out_text = BspFile.parse(out).lump_bytes(0)
        self.assertIn(b'weather_relay,Trigger,,0,-1', out_text)
        self.assertIn(b'"targetname" "weather_relay"', out_text)
        self.assertNotIn(b'rain,Enable', out_text)

    def test_fire_user_into_visual_entity_must_not_drop_gameplay(self):
        text = source().replace(b'"targetname" "basement"',
            b'"targetname" "basement"\n"OnUser1" "gameplay,Trigger,,0,-1"')
        text = text.rstrip(b'\0') + ent('logic_auto', OnMapSpawn='basement,FireUser1,,0,-1') + b'\0'
        with self.assertRaisesRegex(ValueError, 'output|input'):
            self.transfer(text)

    def test_unknown_input_to_visual_entity_requires_review(self):
        text = source().rstrip(b'\0') + ent('logic_relay', OnTrigger='basement,RunScriptCode,DoGameplay(),0,-1') + b'\0'
        with self.assertRaisesRegex(ValueError, 'input'):
            self.transfer(text)

    def test_case_insensitive_mixed_target_is_protected(self):
        text = source().rstrip(b'\0') + ent('logic_relay', targetname='RAIN') + b'\0'
        with self.assertRaisesRegex(ValueError, 'Mixed|mixed'):
            self.transfer(text)

    def test_existing_color_correction_zero_weight_becomes_global(self):
        text = source().rstrip(b'\0') + ent('color_correction', targetname='lut',
            filename='materials/correction/old.raw', maxweight='0', minfalloff='10', maxfalloff='100') + b'\0'
        out, _ = self.transfer(text)
        cc = next(e for e in entity_list(BspFile.parse(out)) if e.one('classname') == 'color_correction')
        self.assertEqual((cc.one('maxweight'), cc.one('minfalloff'), cc.one('maxfalloff')), ('1', '-1', '-1'))

    def test_case_insensitive_parent_attachment_reference_blocks_removal(self):
        text = source().rstrip(b'\0') + ent('prop_dynamic', parentname='RAIN,attach', model='models/a.mdl') + b'\0'
        with self.assertRaisesRegex(ValueError, 'Reference'):
            self.transfer(text)

    def test_lowercase_output_on_removed_entity_is_protected(self):
        text = source().replace(b'"model" "*2"', b'"model" "*2"\n"onuser1" "gameplay,Trigger,,0,-1"')
        with self.assertRaisesRegex(ValueError, 'output'):
            self.transfer(text)

    def test_mixed_case_script_key_is_protected(self):
        text = source().replace(b'"targetname" "basement"', b'"targetname" "basement"\n"VScripts" "logic.nut"')
        with self.assertRaisesRegex(ValueError, 'script'):
            self.transfer(text)

    def test_kill_with_gameplay_child_is_not_a_pure_visual_edge(self):
        text = source().rstrip(b'\0') + ent('prop_dynamic', parentname='basement,attach', model='models/door.mdl')
        text += ent('logic_auto', OnMapSpawn='basement,Kill,,0,-1') + b'\0'
        with self.assertRaisesRegex(ValueError, 'Reference|lifecycle'):
            self.transfer(text)

    def test_mid_wildcard_does_not_hide_nonvisual_recipient(self):
        text = source().rstrip(b'\0') + ent('logic_relay', targetname='rain_other')
        text += ent('logic_auto', OnMapSpawn='rain*unexpected,Kill,,0,-1') + b'\0'
        with self.assertRaisesRegex(ValueError, 'Mixed'):
            self.transfer(text)


if __name__ == '__main__':
    unittest.main()
