import struct
import unittest

from l4d2_bsp.binary import BspFile, LumpFile
from l4d2_bsp.entities import parse_entities


def entity(classname, **fields):
    return ('{\n"classname" "'+classname+'"\n'+''.join(
        f'"{key}" "{value}"\n' for key,value in fields.items())+'}\n').encode()


def style_text(reference=False):
    return b''.join([
        entity('worldspawn', skyname='sky_l4d_c5_1_hdr' if reference else 'sky_l4d_c2m1_hdr'),
        entity('light_environment', origin='1 2 3', _light='209 154 109 1000' if reference else '220 170 114 2'),
        entity('light_directional', _light='202 214 227 75' if reference else '106 160 193 30', angles='0 150 0' if reference else '0 100 0'),
        entity('shadow_control', color='157 159 162' if reference else '0 0 0'),
        entity('env_fog_controller', targetname='fog_master', fogcolor='130 117 107' if reference else '18 29 33', fogstart='256' if reference else '300', spawnflags='1'),
        entity('env_fog_controller', targetname='foginteriorcontroller', fogcolor='40 40 40' if reference else '18 29 33', fogstart='1' if reference else '300', spawnflags='0' if reference else '1'),
        entity('sky_camera', origin='9 9 9' if reference else '8 8 8', scale='16' if reference else '32', angles='0 180 0' if reference else '0 0 0', fogcolor='130 117 107' if reference else '18 29 33'),
        entity('color_correction', targetname='color_correction_main', filename='materials/correction/cc_c5_main.raw' if reference else 'materials/correction/cc_c2_main.raw'),
        entity('color_correction', targetname='color_correction_intro', filename='materials/correction/cc_c5_main.raw' if reference else 'materials/correction/cc_c2_main.raw'),
        entity('env_tonemap_controller', targetname='tonemap_global'),
        entity('env_tonemap_controller_infected', targetname='tonemap_global_infected'),
        entity('env_tonemap_controller_ghost', targetname='tonemap_global_ghost'),
        b'{\n"classname" "logic_auto"\n"OnMapSpawn" "tonemap_global\x1bSetAutoExposureMax\x1b'+(b'5' if reference else b'6')+b'\x1b0\x1b-1"\n"OnMapSpawn" "horde\x1bTrigger\x1b\x1b7\x1b1"\n'+(b'"OnMapSpawn" "tonemap_global\x1bSetTonemapPercentBrightPixels\x1b5\x1b0\x1b-1"\n' if reference else b'')+b'}\n',
        entity('logic_auto', OnMapSpawn='tonemap_global_infected\x1bSetAutoExposureMax\x1b'+('5' if reference else '6')+'\x1b0\x1b-1'),
        entity('logic_auto', OnMapSpawn='tonemap_global_ghost\x1bSetAutoExposureMax\x1b'+('5' if reference else '6')+'\x1b0\x1b-1'),
        entity('trigger_multiple', targetname='horde', model='*7', OnStartTouch='director\x1bBeginScript\x1boriginal\x1b0\x1b-1'),
        entity('env_sun', origin='0 0 0', angles='0 150 0', pitch='-12', material='sprites/light_glow02_add_noz', rendercolor='235 152 84', size='32') if reference else b'',
    ])+b'\0'


def wrap(text, kind='bsp'):
    if kind=='lmp':
        return struct.pack('<5i',24,0,0,len(text),1234)+b'GAP!'+text+b'TAIL'
    h=bytearray(1036)
    struct.pack_into('<4si',h,0,b'VBSP',21)
    struct.pack_into('<iii4s',h,8,0,1036,len(text),b'\0'*4)
    struct.pack_into('<iii4s',h,24,0,1036+len(text),4,b'\0'*4)
    return bytes(h)+text+b'KEEP'


class StyleTests(unittest.TestCase):
    def run_transfer(self, source=None, reference=None, kind='bsp'):
        from l4d2_bsp.style import transfer_c5_style
        return transfer_c5_style(wrap(source or style_text(),kind),wrap(reference or style_text(True)),kind=kind)

    def test_variable_length_and_added_sun_preserve_geometry_and_gameplay(self):
        source=wrap(style_text())
        out,report=self.run_transfer()
        before,after=BspFile.parse(source),BspFile.parse(out)
        self.assertEqual(source[1036:],out[1036:len(source)])
        self.assertEqual(after.lump_bytes(1),b'KEEP')
        self.assertEqual(before.lumps[1:],after.lumps[1:])
        text=after.lump_bytes(0)
        self.assertIn(b'"horde\x1bTrigger\x1b\x1b7\x1b1"',text)
        self.assertIn(entity('trigger_multiple',targetname='horde',model='*7',OnStartTouch='director\x1bBeginScript\x1boriginal\x1b0\x1b-1'),text)
        self.assertTrue(report['protected_entity_bytes_unchanged'])
        self.assertTrue(report['gameplay_io_unchanged'])
        self.assertFalse(report['all_io_unchanged'])
        self.assertEqual(report['added_entity_classes'],['env_sun'])
        io_changes=[c for c in report['changes'] if c['category']=='visual_io_parameter']
        self.assertEqual(len(io_changes),3)
        for change in io_changes:
            self.assertEqual(change['old'].split('\x1b')[2],'6')
            self.assertEqual(change['new'].split('\x1b')[2],'5')
            self.assertEqual(change['old'].split('\x1b')[:2],change['new'].split('\x1b')[:2])
            self.assertEqual(change['old'].split('\x1b')[3:],change['new'].split('\x1b')[3:])
        self.assertEqual(len(report['added_outputs']),1)

    def test_default_outdoor_fog_and_sky_geometry_are_not_matched_by_name(self):
        out,_=self.run_transfer()
        entities=parse_entities(BspFile.parse(out).lump_bytes(0))
        fog=next(e for e in entities if e.one('targetname')=='foginteriorcontroller')
        self.assertEqual(fog.one('fogcolor'),'130 117 107')
        self.assertEqual(fog.one('spawnflags'),'1')
        sky=next(e for e in entities if e.one('classname')=='sky_camera')
        self.assertEqual((sky.one('origin'),sky.one('scale'),sky.one('angles')),('8 8 8','32','0 0 0'))

    def test_lmp_retains_revision_gap_and_tail(self):
        out,report=self.run_transfer(kind='lmp')
        parsed=LumpFile.parse(out)
        self.assertEqual((parsed.revision,parsed.offset),(1234,24))
        self.assertEqual(out[20:24],b'GAP!')
        self.assertTrue(out.endswith(b'TAIL'))
        self.assertTrue(report['non_entity_payloads_unchanged'])

    def test_reapplying_does_not_duplicate_sun_or_visual_output(self):
        from l4d2_bsp.style import transfer_c5_style
        first,_=self.run_transfer()
        second,report=transfer_c5_style(first,wrap(style_text(True)))
        self.assertEqual(first,second)
        self.assertEqual(report['added_entity_classes'],[])

    def test_duplicate_selected_entity_is_rejected(self):
        text=style_text().rstrip(b'\0')+entity('shadow_control',color='0 0 0')+b'\0'
        with self.assertRaisesRegex(ValueError,'unique'):
            self.run_transfer(source=text)

    def test_malicious_reference_value_is_rejected(self):
        with self.assertRaises(ValueError):
            self.run_transfer(reference=style_text(True).replace(b'130 117 107',b'NaN 117 107'))

    def test_exposure_target_collision_is_rejected(self):
        source=style_text().replace(b'"classname" "env_tonemap_controller"',b'"classname" "logic_relay"')
        with self.assertRaisesRegex(ValueError,'tonemap'):
            self.run_transfer(source=source)

    def test_reference_gameplay_outputs_are_never_imported(self):
        out,_=self.run_transfer(reference=style_text(True).replace(b'horde\x1bTrigger',b'evil\x1bKill'))
        self.assertNotIn(b'evil',BspFile.parse(out).lump_bytes(0))

    def test_duplicate_source_exposure_is_rejected(self):
        line=b'"OnMapSpawn" "tonemap_global\x1bSetAutoExposureMax\x1b6\x1b0\x1b-1"\n'
        text=style_text().replace(line,line+line)
        with self.assertRaisesRegex(ValueError,'exposure'):
            self.run_transfer(source=text)

    def test_missing_or_retimed_source_exposure_is_rejected(self):
        text_with_bright=style_text().rstrip(b'\0')+entity('logic_auto',OnMapSpawn='tonemap_global\x1bSetTonemapPercentBrightPixels\x1b5\x1b0\x1b-1')+b'\0'
        for text in (text_with_bright.replace(b'SetAutoExposureMax',b'SetAutoExposureMin'),
                     text_with_bright.replace(b'SetAutoExposureMax\x1b6\x1b0',b'SetAutoExposureMax\x1b6\x1b2')):
            with self.subTest(text=text),self.assertRaisesRegex(ValueError,'exposure'):
                self.run_transfer(source=text)

    def test_nondefault_interior_fog_is_not_silently_repurposed(self):
        text=style_text().replace(b'"fogstart" "300"\n"spawnflags" "1"',b'"fogstart" "300"\n"spawnflags" "0"')
        with self.assertRaisesRegex(ValueError,'fog'):
            self.run_transfer(source=text)


if __name__=='__main__':
    unittest.main()
