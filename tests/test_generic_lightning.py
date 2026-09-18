"""Dry target weather removal without relying on a source map's identity."""
import unittest

from l4d2_bsp.generic_conversion import transfer_generic
from l4d2_bsp.presets import load_preset
from l4d2_bsp.style import _read
from tests.test_core import make_bsp, make_lmp
from test_generic_conversion import ent


def scene(*parts, kind='bsp'):
    return (make_bsp if kind == 'bsp' else make_lmp)(
        ent('worldspawn', skyname='unrelated') + b''.join(parts) + b'\0')


class GenericLightningTests(unittest.TestCase):
    def convert(self, data, kind='bsp', preset='c5m1-daylight-v1', rule='generic-replace-v3'):
        output, audit = transfer_generic(data, load_preset(preset), kind=kind, rule=rule)
        return _read(output, kind)[1], audit

    def test_catalogued_particles_and_only_direct_writers_removed_in_bsp_and_lmp(self):
        for kind in ('bsp', 'lmp'):
            for preset in ('c5m1-daylight-v1', 'c4m3-overcast-static-v1'):
                with self.subTest(kind=kind, preset=preset):
                    data = scene(
                        ent('info_particle_system', targetname='a', effect_name='storm_cloud_parent', start_active='1'),
                        ent('info_particle_system', targetname='b', effect_name='storm_lightning_02', start_active='0', cpoint1='position'),
                        ent('info_particle_system', targetname='c', effect_name='storm_lightning_screenglow', start_active='0'),
                        ent('info_particle_target', targetname='position', origin='99 42 18'),
                        ent('logic_timer', targetname='scheduler', OnTimer='shared,Trigger,,0,-1'),
                        ent('logic_relay', targetname='shared', OnTrigger='b,Start,,.3,-1', OnUser1='b,Stop,,1,-1',
                            OnUser2='gate,Open,,2,-1', OnUser3='c,Start,,0,-1'),
                        ent('func_door', targetname='gate'), kind=kind)
                    text, audit = self.convert(data, kind, preset)
                    self.assertNotIn(b'storm_', text)
                    self.assertNotIn(b'b,Start', text)
                    self.assertNotIn(b'b,Stop', text)
                    self.assertNotIn(b'c,Start', text)
                    self.assertIn(b'gate,Open,,2,-1', text)
                    self.assertIn(b'scheduler', text)
                    self.assertIn(b'shared,Trigger', text)
                    self.assertIn(b'99 42 18', text)
                    self.assertEqual(audit['protected_io_count'], 2)
                    self.assertTrue(audit['non_entity_payloads_unchanged'])
                    owned = audit['plan']['weather_evidence']['owned_endpoints']
                    self.assertEqual(len(owned), 3)
                    self.assertTrue(all(x['reason'] == 'catalogued_lightning_particle' for x in owned))

    def test_unknown_particles_and_weather_like_names_are_preserved(self):
        text, _ = self.convert(scene(
            ent('info_particle_system', targetname='storm_lightning', effect_name='custom_storm_lightning', start_active='1'),
            ent('info_particle_system', targetname='rain', effect_name='fire', start_active='1'),
            ent('logic_relay', OnTrigger='storm_lightning,Start,,0,-1', OnUser1='rain,Stop,,0,-1')))
        self.assertIn(b'custom_storm_lightning', text)
        self.assertIn(b'storm_lightning,Start', text)
        self.assertIn(b'rain,Stop', text)

    def test_legacy_rules_keep_particles_and_edges(self):
        data = scene(ent('info_particle_system', targetname='p', effect_name='storm_lightning_02'),
                     ent('logic_relay', OnTrigger='p,Start,,0,-1'))
        for rule in ('generic-replace-v1', 'generic-replace-v2'):
            text, _ = self.convert(data, rule=rule)
            self.assertIn(b'storm_lightning_02', text)
            self.assertIn(b'p,Start', text)

    def test_mixed_name_and_wildcard_recipients_are_rejected(self):
        for target in ('p', 'p*', 'info_particle_system'):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'Mixed'):
                self.convert(scene(ent('info_particle_system', targetname='p', effect_name='storm_lightning_02'),
                                   ent('info_particle_system', targetname='p', effect_name='fire'),
                                   ent('logic_relay', OnTrigger=f'{target},Start,,0,-1')))

    def test_particle_scripts_outputs_and_unknown_inputs_are_rejected(self):
        for fields in ({'vscripts': 'custom.nut'}, {'thinkfunction': 'Think'},
                       {'OnUser1': 'gate,Open,,0,-1'}, {'globalname': 'shared'}):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, 'script|outputs|lifecycle'):
                self.convert(scene(ent('info_particle_system', targetname='p', effect_name='storm_lightning_02', **fields)))
        for action in ('AddOutput', 'RunScriptCode', 'FireUser1', 'SetParent'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'Unknown input'):
                self.convert(scene(ent('info_particle_system', targetname='p', effect_name='storm_lightning_02'),
                                   ent('logic_relay', OnTrigger=f'p,{action},value,0,-1')))

    def test_retained_particle_control_point_template_and_parent_references_reject(self):
        for cls, key in (('info_particle_system', 'cpoint1'), ('info_particle_system', 'cpoint63'),
                         ('point_template', 'Template01'), ('prop_dynamic', 'parentname')):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Reference'):
                self.convert(scene(ent('info_particle_system', targetname='p', effect_name='storm_lightning_02'),
                                   ent(cls, **{key: 'p'})))

    def test_lightning_does_not_grant_ownership_of_other_event_branches(self):
        text, _ = self.convert(scene(
            ent('info_particle_system', targetname='p', effect_name='storm_lightning_02'),
            ent('ambient_generic', targetname='wind', message='Hospital.HelicopterWindLoop'),
            ent('logic_relay', OnTrigger='p,Start,,0,-1', OnUser1='wind,PlaySound,,0,-1'))
            .replace(b'"OnUser1"', b'"OnTrigger"'))
        self.assertIn(b'wind,PlaySound', text)
        self.assertNotIn(b'p,Start', text)

    def test_control_points_between_removed_particles_do_not_block_conversion(self):
        text, _ = self.convert(scene(
            ent('info_particle_system', targetname='p', effect_name='storm_lightning_02', cpoint1='q'),
            ent('info_particle_system', targetname='q', effect_name='storm_lightning_screenglow', cpoint1='p')))
        self.assertNotIn(b'info_particle_system', text)
