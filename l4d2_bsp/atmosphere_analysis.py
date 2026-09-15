"""Read-only role and potential control graph; never infer deletion authority."""
from collections import defaultdict, deque
import math
import re


ROLE_CLASSES = {
    'world': ('worldspawn',),
    'environment': ('light_environment',),
    'directional': ('light_directional',),
    'shadow': ('shadow_control',),
    'sky': ('sky_camera',),
    'sun': ('env_sun',),
    'fog': ('env_fog_controller',),
    'fog_volumes': ('fog_volume',),
    'postprocess': ('postprocess_controller',),
    'color': ('color_correction', 'color_correction_volume'),
    'exposure': ('env_tonemap_controller', 'env_tonemap_controller_infected',
                 'env_tonemap_controller_ghost'),
    'wind': ('env_wind',),
    'precipitation': ('func_precipitation', 'func_precipitation_blocker'),
    'soundscapes': ('env_soundscape', 'env_soundscape_proxy', 'env_soundscape_triggerable'),
    'local_lights': ('light', 'light_spot', 'light_dynamic'),
    'event_sounds': ('ambient_generic',),
    'particles': ('info_particle_system',),
}
# Event sounds and particles may implement weather, but class alone proves nothing.
ATMOSPHERE_ROLES = set(ROLE_CLASSES) - {'world', 'local_lights', 'event_sounds', 'particles'}
REFERENCE_KEYS = {'fogname', 'postprocessname', 'colorcorrectionname',
                  'tonemapname', 'mainsoundscapename'}


def _output(value):
    if '\x1b' in value:
        fields = value.split('\x1b')
    else:
        head = value.split(',', 2)
        fields = head[:2] + head[2].rsplit(',', 2) if len(head) == 3 else head
    if len(fields) != 5 or not fields[0] or not fields[1]:
        raise ValueError('Expected target,input,parameter,delay,repeat')
    if not math.isfinite(float(fields[3])) or not re.fullmatch(r'-?[0-9]+', fields[4]):
        raise ValueError('Invalid delay or repeat')
    return dict(zip(('target', 'input', 'parameter', 'delay', 'repeat'), fields))


def analyze_entities(entities):
    """Return conservative *candidate* reachability, retaining every output.

    Names, class fallback and star patterns are only static candidates. Input
    dispatch, timing, scripts and runtime entity creation are not simulated.
    """
    roles = {role: [] for role in ROLE_CLASSES}
    names, classes = defaultdict(list), defaultdict(list)
    records, scripts, references, issues = [], [], [], []
    for i, ent in enumerate(entities):
        cls, name = ent.one('classname'), ent.one('targetname')
        classes[cls].append(i)
        if name:
            names[name].append(i)
        records.append(dict(entity_index=i, classname=cls, targetname=name,
                            hammerid=ent.one('hammerid')))
        for role, allowed in ROLE_CLASSES.items():
            if cls in allowed:
                roles[role].append(i)
        if not cls:
            issues.append(dict(code='missing_classname', entity_index=i))
        for j, pair in enumerate(ent.pairs):
            if pair.key.lower() in ('vscripts', 'thinkfunction') and pair.value:
                scripts.append(dict(entity_index=i, pair_index=j, key=pair.key, value=pair.value))
    if len(roles['world']) != 1:
        issues.append(dict(code='world_count', count=len(roles['world'])))

    def candidates(target, owner):
        if target == '!self':
            return 'self', [owner]
        if target.startswith('!'):
            return 'dynamic_context', []
        if '*' in target:
            pattern = re.compile(re.escape(target).replace(r'\*', '.*') + r'\Z')
            found = {i for table in (names, classes) for key, indexes in table.items()
                     if key and pattern.fullmatch(key) for i in indexes}
            return ('wildcard_candidates' if found else 'unresolved'), sorted(found)
        # Union is intentional: do not guess engine name/class dispatch priority.
        found = sorted(set(names.get(target, []) + classes.get(target, [])))
        return ('name_or_class_candidates' if found else 'unresolved'), found

    outputs, reverse = [], defaultdict(set)
    for i, ent in enumerate(entities):
        for j, pair in enumerate(ent.pairs):
            if pair.key.lower() in REFERENCE_KEYS and pair.value:
                resolution, targets = candidates(pair.value, i)
                references.append(dict(entity_index=i, pair_index=j, key=pair.key,
                                       target=pair.value, resolution=resolution,
                                       candidate_targets=targets))
            if not (pair.key.startswith(('On', 'Out')) or '\x1b' in pair.value):
                continue
            record = dict(entity_index=i, pair_index=j, output=pair.key, raw=pair.value)
            try:
                record.update(_output(pair.value))
            except ValueError as exc:
                record.update(resolution='malformed', candidate_targets=[], error=str(exc))
                issues.append(dict(code='output_format', entity_index=i, pair_index=j, error=str(exc)))
            else:
                resolution, targets = candidates(record['target'], i)
                record.update(resolution=resolution, candidate_targets=targets)
                for target in targets:
                    reverse[target].add(i)
                if record['input'].lower() in ('runscriptcode', 'runscriptfile', 'callscriptfunction',
                                                'beginscript', 'endscript'):
                    scripts.append(dict(entity_index=i, pair_index=j, key=pair.key,
                                        value=pair.value, input=record['input']))
            outputs.append(record)

    reachable = {i for role in ATMOSPHERE_ROLES for i in roles[role]}
    queue = deque(sorted(reachable))
    while queue:
        for parent in sorted(reverse[queue.popleft()]):
            if parent not in reachable:
                reachable.add(parent)
                queue.append(parent)
    by_owner = defaultdict(list)
    for output in outputs:
        output['may_reach_atmosphere'] = any(i in reachable for i in output['candidate_targets'])
        by_owner[output['entity_index']].append(output['may_reach_atmosphere'])
    return dict(roles=roles, entities=records, outputs=outputs, issues=issues,
                duplicate_targetnames={k: v for k, v in sorted(names.items()) if len(v) > 1},
                controller_references=references, script_entrypoints=scripts,
                atmosphere_related_sources=sorted(i for i, values in by_owner.items() if any(values)),
                mixed_output_sources=sorted(i for i, values in by_owner.items() if any(values) and not all(values)),
                automatic_removal_authorized=False,
                limitation='Potential static reachability only; unrelated outputs are not proven gameplay, '
                           'and related entities are not proven removable. Scripts, templates, timing, '
                           'dynamic targets and engine dispatch require further analysis.')
