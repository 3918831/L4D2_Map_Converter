import unittest

from l4d2_bsp.entities import parse_entities


def entity(**fields):
    return '{\n' + ''.join(f'"{k}" "{v}"\n' for k, v in fields.items()) + '}\n'


class AtmosphereAnalysisTests(unittest.TestCase):
    def analyze(self, text):
        from l4d2_bsp.atmosphere_analysis import analyze_entities
        return analyze_entities(parse_entities(text.encode('latin1')))

    def test_roles_use_classes_not_c2_names_or_counts(self):
        report = self.analyze(entity(classname='worldspawn') +
            entity(classname='env_fog_controller', targetname='basement') +
            entity(classname='env_fog_controller', targetname='distant') +
            entity(classname='env_wind'))
        self.assertEqual(report['roles']['fog'], [1, 2])
        self.assertEqual(report['roles']['wind'], [3])
        self.assertEqual(report['roles']['exposure'], [])
        self.assertFalse(report['automatic_removal_authorized'])

    def test_missing_and_multiple_world_report_issues(self):
        self.assertIn('world_count', [x['code'] for x in self.analyze('')['issues']])
        report = self.analyze(entity(classname='worldspawn') * 2)
        self.assertIn('world_count', [x['code'] for x in report['issues']])

    def test_mixed_gameplay_weather_outputs_stay_separate(self):
        text = (entity(classname='logic_relay', targetname='shared',
                       OnTrigger='rain,Enable,,0,-1', OutAnger='gameplay,Trigger,,0,-1') +
                entity(classname='func_precipitation', targetname='rain') +
                entity(classname='logic_relay', targetname='gameplay'))
        report = self.analyze(text)
        self.assertEqual(len(report['outputs']), 2)
        self.assertTrue(report['outputs'][0]['may_reach_atmosphere'])
        self.assertFalse(report['outputs'][1]['may_reach_atmosphere'])
        self.assertEqual(report['mixed_output_sources'], [0])
        self.assertFalse(report['automatic_removal_authorized'])

    def test_duplicate_outputs_and_comma_script_parameter_are_preserved(self):
        text = ('{ "classname" "logic_auto" '
                '"OnMapSpawn" "x,RunScriptCode,Foo(1,2),0,-1" '
                '"OnMapSpawn" "x\x1bEnable\x1b\x1b1\x1b-1" }')
        report = self.analyze(text)
        self.assertEqual([x['pair_index'] for x in report['outputs']], [1, 2])
        self.assertEqual(report['outputs'][0]['parameter'], 'Foo(1,2)')
        self.assertEqual(len(report['script_entrypoints']), 1)

    def test_cycles_and_transitive_visual_candidates_terminate(self):
        text = (entity(classname='logic_relay', targetname='a', OnTrigger='b,Trigger,,0,-1') +
                entity(classname='logic_relay', targetname='b', OnTrigger='a,Trigger,,0,-1',
                       OnUser1='rain,Enable,,0,-1') +
                entity(classname='func_precipitation', targetname='rain'))
        report = self.analyze(text)
        self.assertEqual(report['atmosphere_related_sources'], [0, 1])
        self.assertTrue(all(x['may_reach_atmosphere'] for x in report['outputs']))

    def test_special_wildcard_and_type_targets_are_candidates_not_proof(self):
        text = (entity(classname='logic_relay', OnTrigger='fog_*,Enable,,0,-1',
                       OnUser1='env_fog_controller,Disable,,0,-1',
                       OnUser2='!activator,Kill,,0,-1', OnUser3='!self,Trigger,,0,-1') +
                entity(classname='env_fog_controller', targetname='fog_a') +
                entity(classname='env_fog_controller', targetname='fog_a'))
        report = self.analyze(text)
        self.assertEqual(report['outputs'][0]['candidate_targets'], [1, 2])
        self.assertEqual(report['outputs'][1]['candidate_targets'], [1, 2])
        self.assertEqual(report['outputs'][2]['resolution'], 'dynamic_context')
        self.assertEqual(report['outputs'][3]['candidate_targets'], [0])
        self.assertIn('fog_a', report['duplicate_targetnames'])

    def test_malformed_output_keeps_raw_and_diagnostic(self):
        report = self.analyze(entity(classname='logic_auto', OnMapSpawn='bad') +
                              entity(classname='worldspawn'))
        self.assertEqual(report['outputs'][0]['raw'], 'bad')
        self.assertEqual(report['outputs'][0]['resolution'], 'malformed')
        self.assertIn('output_format', [x['code'] for x in report['issues']])

    def test_local_lights_and_event_sounds_are_not_weather_endpoints(self):
        report = self.analyze(entity(classname='light', targetname='bulb') +
                              entity(classname='ambient_generic', targetname='alarm') +
                              entity(classname='logic_relay', OnTrigger='alarm,PlaySound,,0,-1'))
        self.assertEqual(report['roles']['local_lights'], [0])
        self.assertEqual(report['roles']['event_sounds'], [1])
        self.assertFalse(report['outputs'][0]['may_reach_atmosphere'])

    def test_script_properties_and_name_references_are_reported(self):
        report = self.analyze(entity(classname='logic_script', vscripts='weather.nut',
                                     thinkfunction='Tick') +
                              entity(classname='fog_volume', FogName='arbitrary_fog') +
                              entity(classname='env_fog_controller', targetname='arbitrary_fog'))
        self.assertEqual(len(report['script_entrypoints']), 2)
        self.assertEqual(report['controller_references'][0]['candidate_targets'], [2])

    def test_director_begin_script_is_not_omitted(self):
        report = self.analyze(entity(classname='info_director', targetname='director',
                                     OnGameplayStart='director,BeginScript,mission_logic,0,-1'))
        self.assertEqual(len(report['script_entrypoints']), 1)
        self.assertEqual(report['script_entrypoints'][0]['input'], 'BeginScript')


if __name__ == '__main__':
    unittest.main()
