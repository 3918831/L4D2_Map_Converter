"""Preset integration must retain source geometry and unrelated events."""
import unittest

from l4d2_bsp.presets import load_preset
from l4d2_bsp.profiles import transfer_style
from tests.test_profiles import c6_text
from tests.test_style import style_text, wrap


class PresetTransferTests(unittest.TestCase):
    def test_preset_without_donor_geometry_supports_base_and_mode_and_is_idempotent(self):
        preset = load_preset()
        for profile, text in (('c2-c5', style_text()), ('c6-c5', c6_text())):
            for kind in ('bsp', 'lmp'):
                with self.subTest(profile=profile, kind=kind):
                    source = wrap(text, kind)
                    output, audit = transfer_style(source, preset, profile=profile,
                        kind=kind, reference_kind=kind, atmosphere_policy='preserve')
                    self.assertTrue(audit['non_entity_payloads_unchanged'])
                    self.assertTrue(audit['gameplay_io_unchanged'])
                    self.assertEqual(audit['reference_sha256'], preset.sha256)
                    self.assertEqual(audit['preset']['id'], preset.id)
                    repeated, _ = transfer_style(output, preset, profile=profile,
                        kind=kind, reference_kind=kind, atmosphere_policy='preserve')
                    self.assertEqual(output, repeated)
