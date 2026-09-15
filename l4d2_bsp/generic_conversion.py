"""Versioned strong-preset planning and audited entity-only execution."""
import re
import struct

from .atmosphere_analysis import analyze_entities, entity_value, is_output_pair
from .entities import parse_entities
from .inspect import sha256
from .style import _read


RULE = 'generic-replace-v1'
INIT = '__lmc_style_init_v1'
CLASS_ROLES = {
    'worldspawn': 'world', 'light_environment': 'environment',
    'light_directional': 'directional', 'shadow_control': 'shadow',
    'sky_camera': 'sky', 'env_sun': 'sun', 'env_fog_controller': 'fog_outdoor',
    'postprocess_controller': 'postprocess_exterior', 'color_correction': 'color_main',
    'color_correction_volume': 'color_main',
}
EXPOSURES = ('env_tonemap_controller', 'env_tonemap_controller_infected', 'env_tonemap_controller_ghost')
VISUAL = set(CLASS_ROLES) - {'worldspawn'} | set(EXPOSURES) | {
    'env_wind', 'func_precipitation', 'func_precipitation_blocker',
    'env_soundscape', 'env_soundscape_triggerable',
}
# Inputs interpreted as appearance/lifecycle changes only. Script dispatch,
# FireUser*, AddOutput and hierarchy changes are deliberately not inferred.
VISUAL_INPUTS = {
    'enable', 'disable', 'toggle', 'turnon', 'turnoff', 'kill',
    'setcolor', 'setcolorsecondary', 'setstartdist', 'setenddist',
    'setmaxdensity', 'sethdrcolorscale', 'setfarz', 'setlerptime', 'startfogtransition',
    'setautoexposuremin', 'setautoexposuremax', 'settonemaprate',
    'settonemappercentbrightpixels', 'settonemappercenttarget',
    'settonemapminavglum', 'settonemapscale', 'blendtonemapscale',
    'usedefaultautoexposure', 'setbloomscale', 'setbloomexponent', 'setbloomsaturation',
    'setlocalcontraststrength', 'setlocalcontrastedgestrength',
    'setvignettestart', 'setvignetteend', 'setvignetteblurstrength', 'setfadetoblackstrength',
    'setfadeinduration', 'setfadeoutduration', 'setshadowcolor', 'setshadowdirection',
    'setshadowdistance', 'setdensity', 'setlightcolor', 'setpattern', 'fadetopattern',
}


def _safe_value(value):
    if not isinstance(value, str) or any(c in value for c in ('"', '\\', '\0', '\n', '\r')):
        raise ValueError('Unsafe generated entity value')
    return value


def _flags(entity):
    return int(entity_value(entity, 'spawnflags') or '0')


def plan_conversion(data, preset, *, kind='bsp'):
    """Plan from original bytes. Target values come from the validated preset.

    Runtime script semantics are not inferred. Static reachability is never
    used as permission to delete upstream logic or particle/event sound entities.
    """
    _, _, entities = _read(data, kind)
    graph = analyze_entities(entities)
    if graph['issues']:
        raise ValueError(f'Entity analysis requires resolution: {graph["issues"]}')
    if any((entity_value(e, 'targetname') or '').lower() == INIT for e in entities):
        raise ValueError('Reserved conversion marker exists; start from original BSP/LMP')
    names = {entity_value(e, 'targetname').lower() for e in entities if entity_value(e, 'targetname')}
    by_class = {}
    for i, entity in enumerate(entities):
        by_class.setdefault(entity_value(entity, 'classname'), []).append(i)
    operations, removals, additions = {}, set(), []
    anchor = next((entity_value(e, 'origin') for e in entities
                   if entity_value(e, 'classname') == 'light_environment' and entity_value(e, 'origin')), '0 0 0')
    warnings = []

    def change(i, values):
        if not values:
            return
        op = operations.setdefault(i, dict(entity_index=i, fields={}, remove_outputs=[]))
        for key, value in values.items():
            _safe_value(value)
            matches = [p for p in entities[i].pairs if p.key.lower() == key.lower()]
            if len(matches) > 1:
                raise ValueError(f'Ambiguous visual field {i}/{key}')
            if not matches or matches[0].value != value:
                op['fields'][key] = value

    def add(cls, values):
        fields = dict(classname=cls, origin=anchor, **values)
        for value in fields.values():
            _safe_value(value)
        additions.append(list(fields.items()))

    def master(cls):
        indexes = by_class.get(cls, [])
        return next((i for i in indexes if _flags(entities[i]) & 1), indexes[0] if indexes else None)

    for i, entity in enumerate(entities):
        cls = entity_value(entity, 'classname')
        if cls in VISUAL and (entity_value(entity, 'vscripts') or entity_value(entity, 'thinkfunction')):
            raise ValueError(f'Visual controller script requires review: {i}/{cls}')
        if cls in CLASS_ROLES:
            values = preset.role_values(CLASS_ROLES[cls])
            if cls == 'env_sun' and values is None:
                removals.add(i)
                continue
            change(i, values)
            if cls == 'env_fog_controller':
                change(i, dict(fogenable='1', fogblend=(values or {}).get('fogblend', '0'),
                               heightFogDensity=(values or {}).get('heightFogDensity', '0')))
            if cls == 'sky_camera':
                change(i, {'fogenable': '1'})
            if cls in ('env_fog_controller', 'postprocess_controller', 'color_correction'):
                flags = (_flags(entity) & ~1) | (1 if i == master(cls) else 0)
                change(i, {'spawnflags': str(flags)})
            if cls == 'color_correction':
                change(i, dict(StartDisabled='0', exclusive='0', minfalloff='-1',
                               maxfalloff='-1', maxweight='1'))
            if cls == 'color_correction_volume':
                # Brush remains intact; replace its effect with the global point controller.
                change(i, dict(StartDisabled='1', maxweight='0'))
        elif cls in ('func_precipitation', 'func_precipitation_blocker'):
            removals.add(i)
        elif cls == 'env_wind':
            if preset.wind_values is None:
                removals.add(i)
            else:
                change(i, preset.wind_values)
        elif cls in ('env_soundscape', 'env_soundscape_triggerable'):
            change(i, {'soundscape': preset.soundscape_mapping['outdoor']})

    # Point controllers may be created; never invent a sky_camera/brush region.
    for cls, role in CLASS_ROLES.items():
        values = preset.role_values(role)
        if by_class.get(cls) or cls in ('worldspawn', 'sky_camera', 'color_correction_volume') or values is None:
            continue
        values = dict(values)
        if cls in ('env_fog_controller', 'postprocess_controller', 'color_correction'):
            values['spawnflags'] = '3' if cls == 'color_correction' else '1'
        if cls == 'env_fog_controller':
            values['fogenable'] = '1'
        if cls == 'color_correction':
            values.update(StartDisabled='0', minfalloff='-1', maxfalloff='-1', maxweight='1')
        add(cls, values)
    if not by_class.get('env_wind') and preset.wind_values is not None:
        add('env_wind', preset.wind_values)

    # Every exposure controller receives the same preset. Select a unique
    # normal-player controller for the capture guard, independent of its name.
    capture_name, exposure_names = None, []
    for cls in EXPOSURES:
        indexes = by_class.get(cls, [])
        default = '__lmc_tonemap_v1' + cls.removeprefix('env_tonemap_controller')
        if not indexes:
            if default in names:
                raise ValueError(f'Reserved exposure name collision: {default}')
            add(cls, dict(targetname=default, spawnflags='1'))
            names.add(default)
            exposure_names.append((cls, default))
            if cls == EXPOSURES[0]:
                capture_name = default
        for number, i in enumerate(indexes):
            name = entity_value(entities[i], 'targetname')
            if not name:
                name = default if number == 0 else default + '_' + str(number)
                if name in names:
                    raise ValueError(f'Reserved exposure name collision: {name}')
                names.add(name.lower())
                change(i, {'targetname': name})
            elif len([e for e in entities if (entity_value(e, 'targetname') or '').lower() == name.lower()]) != 1:
                raise ValueError(f'Ambiguous exposure target name: {name}')
            if not re.fullmatch(r'[A-Za-z0-9_]{1,64}', name):
                raise ValueError(f'Exposure name unsupported by capture script: {name}')
            change(i, {'spawnflags': str((_flags(entities[i]) & ~1) | (1 if i == master(cls) else 0))})
            exposure_names.append((cls, name))
            if cls == EXPOSURES[0] and i == master(cls):
                capture_name = name

    # Remove only edges whose *direct* target candidates are all visual.
    # A wildcard/name shared with gameplay must not lose the other recipients.
    lifecycle_targets = set()
    for output in graph['outputs']:
        i, targets = output['entity_index'], output['candidate_targets']
        if i in removals:
            raise ValueError(f'Weather/sun entity has outputs requiring review: {i}')
        visual_targets = [j for j in targets if entity_value(entities[j], 'classname') in VISUAL]
        if visual_targets:
            if len(visual_targets) != len(targets):
                raise ValueError(f'Mixed visual/nonvisual output target: {i}/{output["pair_index"]}')
            if output['input'].lower() not in VISUAL_INPUTS:
                raise ValueError(f'Unknown input to visual entity requires review: {i}/{output["input"]}')
            if any(o['entity_index'] in visual_targets for o in graph['outputs']):
                raise ValueError(f'Visual target has outputs requiring review: {i}/{output["target"]}')
            if output['input'].lower() == 'kill':
                lifecycle_targets.update(visual_targets)
            operations.setdefault(i, dict(entity_index=i, fields={}, remove_outputs=[]))['remove_outputs'].append(
                dict(pair_index=output['pair_index'], key=output['output'], value=output['raw'],
                     reason='direct_visual_writer'))
    # Deleting an entity used as a template or parenting target can affect
    # nonvisual behavior even without an IO edge. Refuse that ambiguity.
    affected = removals | lifecycle_targets
    if any(entity_value(entities[i], 'globalname') for i in affected):
        raise ValueError('Global lifecycle state on visual entity requires review')
    removed_names = {(entity_value(entities[i], 'targetname') or '').lower() for i in affected} - {''}
    for i, entity in enumerate(entities):
        for pair in entity.pairs:
            if (pair.key.lower().startswith('template') or pair.key.lower() in ('parentname', 'target')):
                target = pair.value.split(',', 1)[0].lower()
                if target in removed_names or ('*' in target and any(n.startswith(target.split('*', 1)[0]) for n in removed_names)):
                    raise ValueError(f'Reference to removed visual entity requires review: {i}/{pair.key}')

    startup = [('classname', 'logic_auto'), ('targetname', INIT), ('spawnflags', '1')]
    exposure = preset.exposure_values
    for cls, name in exposure_names:
        actions = [('SetAutoExposureMin', 'minimum'), ('SetAutoExposureMax', 'maximum')]
        if cls == EXPOSURES[0]:
            actions += [('SetTonemapRate', 'rate'), ('SetTonemapPercentBrightPixels', 'bright_pixels')]
        for action, field in actions:
            if exposure[field] is not None:
                startup.append(('OnMapSpawn', '\x1b'.join((name, action, exposure[field], '0', '-1'))))
    additions.append(startup)
    if graph['script_entrypoints']:
        warnings.append('Source scripts retained; external/dynamic atmosphere writers are not statically proven absent.')
    if by_class.get('info_particle_system') or by_class.get('ambient_generic'):
        warnings.append('Particle systems and event sounds retained; weather ownership is not inferred from names.')
    warnings.extend(['All soundscape regions use the preset outdoor definition; spatial placement is retained.',
                     'Existing local lights/materials and sky_camera transforms are preserved.',
                     'Only explicit preset fields plus documented activation/master defaults are normalized.'])
    return dict(schema_version=1, conversion=RULE, kind=kind, source_sha256=sha256(data),
                preset=preset.metadata(), capture_tonemap=capture_name,
                operations=[op for i, op in sorted(operations.items()) if i not in removals and
                            (op['fields'] or op['remove_outputs'])],
                removed_entities=sorted(removals),
                added_entities=[[[key, value] for key, value in pairs] for pairs in additions],
                coverage_warnings=warnings)


def apply_plan(data, plan, preset):
    """Reject arbitrary or stale edits by reproducing the authorized rule plan."""
    if not isinstance(plan, dict) or plan != plan_conversion(data, preset, kind=plan.get('kind', 'bsp')):
        raise ValueError('Plan differs from the current input, preset or conversion rules')
    parsed, text, entities = _read(data, plan['kind'])
    expected = [[(p.key, p.value) for p in e.pairs] for e in entities]
    removed, touched, allowed = set(plan['removed_entities']), set(), set()
    for op in plan['operations']:
        i = op['entity_index']
        fields = {k.lower(): (k, v) for k, v in op['fields'].items()}
        skip = {x['pair_index'] for x in op['remove_outputs']}
        allowed.update((i, j) for j in skip)
        updated = []
        for j, (key, value) in enumerate(expected[i]):
            if j in skip:
                continue
            replacement = fields.pop(key.lower(), None)
            updated.append((key, replacement[1] if replacement else value))
        updated.extend(fields.values())
        expected[i] = updated
        touched.add(i)

    def serialize(pairs):
        return ('{\n' + ''.join(f'"{k}" "{v}"\n' for k, v in pairs) + '}').encode('latin1')

    edits = [(e.start, e.end, b'' if i in removed else serialize(expected[i]))
             for i, e in enumerate(entities) if i in removed or i in touched]
    end = text.find(b'\0')
    if end < 0:
        end = len(text)
    additions = [[tuple(p) for p in pairs] for pairs in plan['added_entities']]
    edits.append((end, end, b'\n' + b'\n'.join(serialize(pairs) for pairs in additions) + b'\n'))
    updated = text
    for start, end, replacement in sorted(edits, reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    after = parse_entities(updated)
    retained = [i for i in range(len(entities)) if i not in removed]
    if [[(p.key, p.value) for p in e.pairs] for e in after] != [expected[i] for i in retained] + additions:
        raise ValueError('Unexpected entity changes outside plan')
    protected_count = 0
    for index, i in enumerate(retained):
        original, final = entities[i], after[index]
        if i not in touched and text[original.start:original.end] != updated[final.start:final.end]:
            raise ValueError('Protected entity bytes changed')
        outputs = [(p.key, p.value) for j, p in enumerate(original.pairs)
                   if is_output_pair(original, p) and (i, j) not in allowed]
        actual = [(p.key, p.value) for p in final.pairs if is_output_pair(final, p)]
        if outputs != actual:
            raise ValueError('Protected outputs changed')
        protected_count += len(outputs)
    if plan['kind'] == 'bsp':
        pad = b'\0' * (-len(data) % 4)
        offset = len(data) + len(pad)
        if offset + len(updated) > 2**31 - 1:
            raise ValueError('BSP offset overflow')
        header = bytearray(data[:1036])
        struct.pack_into('<ii', header, 12, offset, len(updated))
        output = bytes(header) + data[1036:] + pad + updated
        checked, _, _ = _read(output, 'bsp')
        if any(parsed.lumps[i] != checked.lumps[i] or parsed.lump_bytes(i) != checked.lump_bytes(i)
               for i in range(1, 64)):
            raise ValueError('Non-entity lump changed')
    else:
        offset, length = parsed.entity_span
        header = bytearray(data[:20])
        struct.pack_into('<i', header, 12, len(updated))
        output = bytes(header) + data[20:offset] + updated + data[offset + length:]
        _read(output, 'lmp')
    return output, dict(plan=plan, conversion=RULE, capture_tonemap=plan['capture_tonemap'],
                        source_sha256=sha256(data), output_sha256=sha256(output),
                        non_entity_payloads_unchanged=True, protected_entity_bytes_unchanged=True,
                        protected_io_unchanged=True, protected_io_count=protected_count,
                        entity_counts=[len(entities), len(after)], coverage_warnings=plan['coverage_warnings'])


def transfer_generic(data, preset, *, kind='bsp'):
    return apply_plan(data, plan_conversion(data, preset, kind=kind), preset)
