import unittest

from l4d2_bsp.binary import BspFile
from l4d2_bsp.generic_conversion import transfer_generic
from l4d2_bsp.presets import load_preset
from test_generic_conversion import ent
from tests.test_core import make_bsp


def scene(*parts):
    return make_bsp(ent('worldspawn', skyname='old') + b''.join(parts) + b'\0')


class GenericWeatherTests(unittest.TestCase):
    def convert(self, data, preset='c5m1-daylight-v1'):
        output, audit = transfer_generic(data, load_preset(preset), rule='generic-replace-v2')
        return BspFile.parse(output).lump_bytes(0), audit

    def test_fog_lerp_and_precip_alpha_preserve_shared_gameplay(self):
        data = scene(ent('env_fog_controller', targetname='arbitrary'),
                     ent('func_precipitation', targetname='drops', model='*7'),
                     ent('logic_relay', targetname='shared',
                         OnTrigger='arbitrary,SetEndDistLerpTo,500,0,-1',
                         OnUser1='drops,Alpha,200,0,-1', OnUser2='door,Open,,0,-1'),
                     ent('prop_door_rotating', targetname='door'))
        text, audit = self.convert(data)
        self.assertNotIn(b'SetEndDistLerpTo', text)
        self.assertNotIn(b'drops,Alpha', text)
        self.assertIn(b'door,Open', text)
        self.assertTrue(audit['protected_io_unchanged'])
        with self.assertRaisesRegex(ValueError, 'Unknown input'):
            transfer_generic(data, load_preset())

    def test_new_inputs_are_class_specific(self):
        with self.assertRaisesRegex(ValueError, 'input'):
            self.convert(scene(ent('env_soundscape', targetname='x'),
                               ent('logic_relay', OnTrigger='x,SetEndDistLerpTo,5,0,-1')))

    def test_templated_wind_is_normalized_without_changing_lifecycle(self):
        data = scene(ent('env_wind', targetname='air', minwind='55', maxwind='90'),
                     ent('point_template', targetname='factory', Template01='air'),
                     ent('logic_relay', OnTrigger='factory,ForceSpawn,,0,-1', OnUser1='air,Kill,,1,-1'))
        text, _ = self.convert(data)
        self.assertIn(b'"minwind" "0"', text)
        self.assertIn(b'"maxgust" "0"', text)
        self.assertIn(b'factory,ForceSpawn', text)
        self.assertIn(b'air,Kill', text)
        self.assertIn(b'"Template01" "air"', text)
        text, _ = self.convert(data, 'c4m3-overcast-static-v1')
        self.assertIn(b'"minwind" "15"', text)

    def test_weather_assets_and_ambiguous_wind_require_event_context(self):
        data = scene(ent('func_precipitation', targetname='rain'),
                     ent('ambient_generic', targetname='wind', message='Hospital.HelicopterWindLoop'),
                     ent('ambient_generic', targetname='helicopter', message='Hospital.HelicopterWindLoop'),
                     ent('ambient_generic', targetname='thunder', message='Weather.thunder_close_all_4'),
                     ent('logic_relay', OnTrigger='rain,Alpha,10,0,-1',
                         OnUser1='wind,PlaySound,,0,-1'),
                     ent('logic_relay', targetname='real_event', OnTrigger='helicopter,PlaySound,,0,-1'))
        # Different output events on one entity do not imply weather ownership.
        text, _ = self.convert(data)
        self.assertIn(b'"targetname" "wind"', text)
        self.assertIn(b'"targetname" "helicopter"', text)
        self.assertNotIn(b'"targetname" "thunder"', text)
        data = data.replace(b'"OnUser1"', b'"OnTrigger"')
        text, _ = self.convert(data)
        self.assertNotIn(b'"targetname" "wind"', text)
        self.assertIn(b'helicopter,PlaySound', text)

    def test_camera_shake_branch_preserves_unrelated_shake_and_physics(self):
        data = scene(ent('func_precipitation', targetname='rain'),
                     ent('env_shake', targetname='camera', spawnflags='1'),
                     ent('env_shake', targetname='physics', spawnflags='8'),
                     ent('logic_relay', OnTrigger='rain,Alpha,20,0,-1',
                         OnUser1='camera,StartShake,,0,-1'),
                     ent('logic_relay', targetname='gameplay', OnTrigger='camera,StartShake,,0,-1'))
        text, _ = self.convert(data)
        self.assertEqual(text.count(b'camera,StartShake'), 2)
        text, _ = self.convert(data.replace(b'"OnUser1"', b'"OnTrigger"'))
        self.assertEqual(text.count(b'camera,StartShake'), 1)
        text, _ = self.convert(data.replace(b'camera,StartShake', b'physics,StartShake'))
        self.assertIn(b'physics,StartShake', text)

    def test_mixer_context_flows_only_through_exclusive_trigger_edges(self):
        data = scene(ent('func_precipitation', targetname='rain'),
                     ent('sound_mix_layer', targetname='mixer', MixLayerName='voipLayer', Level='0'),
                     ent('logic_relay', targetname='mix', OnTrigger='mixer,Level,1,0,-1'),
                     ent('logic_relay', OnTrigger='rain,Alpha,20,0,-1', OnUser1='mix,Trigger,,0,-1'))
        text, _ = self.convert(data)
        self.assertIn(b'"targetname" "mixer"', text)
        text, _ = self.convert(data.replace(b'"OnUser1"', b'"OnTrigger"'))
        self.assertNotIn(b'"targetname" "mixer"', text)
        self.assertIn(b'"targetname" "mix"', text)
        # Another caller whose event has no weather evidence prevents ownership.
        extra = ent('logic_relay', OnTrigger='mix,Trigger,,0,-1')
        from l4d2_bsp.style import _read
        raw = _read(data.replace(b'"OnUser1"', b'"OnTrigger"'), 'bsp')[1].rstrip(b'\0')
        text, _ = self.convert(make_bsp(raw + extra + b'\0'))
        self.assertIn(b'"targetname" "mixer"', text)

    def test_console_command_cut_is_single_exact_visual_assignment(self):
        data = scene(ent('point_broadcastclientcommand', targetname='console'),
                     ent('logic_relay', OnTrigger='console,Command,r_skyboxfogfactor .9,0,-1',
                         OnUser1='console,Command,r_skyboxfogfactor 1; quit,0,-1'))
        text, _ = self.convert(data)
        self.assertNotIn(b'r_skyboxfogfactor .9', text)
        self.assertIn(b'r_skyboxfogfactor 1; quit', text)

    def test_shake_runtime_mutation_invalidates_camera_only_proof(self):
        data = scene(ent('func_precipitation', targetname='rain'),
                     ent('env_shake', targetname='quake', spawnflags='1'),
                     ent('logic_auto', OnMapSpawn='quake,AddOutput,spawnflags 8,0,-1'),
                     ent('logic_relay', OnTrigger='rain,Alpha,20,0,-1',
                         OnUser1='quake,StartShake,,0,-1'))
        with self.assertRaisesRegex(ValueError, 'mutation|mutator'):
            self.convert(data.replace(b'"OnUser1"', b'"OnTrigger"'))

    def test_shared_shake_state_and_stop_are_not_removed(self):
        for action, value in [('Amplitude', '10'), ('Frequency', '5'), ('StopShake', '')]:
            data = scene(ent('func_precipitation', targetname='rain'),
                         ent('env_shake', targetname='quake', amplitude='0'),
                         ent('logic_relay', OnTrigger='rain,Alpha,20,0,-1',
                             OnUser1=f'quake,{action},{value},0,-1'),
                         ent('logic_relay', targetname='gameplay', OnTrigger='quake,StartShake,,0,-1'))
            text, _ = self.convert(data.replace(b'"OnUser1"', b'"OnTrigger"'))
            self.assertIn(f'quake,{action}'.encode(), text)

    def test_weather_sound_parent_and_gameplay_outputs_refuse_deletion(self):
        for extra in [ent('prop_dynamic', parentname='thunder'),
                      ent('point_template', Template01='thunder')]:
            with self.assertRaisesRegex(ValueError, 'Reference'):
                self.convert(scene(ent('ambient_generic', targetname='thunder',
                                       message='Weather.thunder_close_all_4'), extra))
        with self.assertRaisesRegex(ValueError, 'output'):
            self.convert(scene(ent('ambient_generic', targetname='thunder',
                                   message='Weather.thunder_close_all_4', OnUser1='door,Open,,0,-1')))


if __name__ == '__main__':
    unittest.main()
