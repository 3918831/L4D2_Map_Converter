"""Explicit map profiles; C6 remains statically supported pending user testing.

The byte API validates internal map/controller expectations. Its caller must
also validate input filenames against ``profile_spec``; BSP entity data does
not provide an authoritative root map filename. C2 delegates unchanged to the
previously accepted strict converter. C6 replaces its atmosphere by default;
the explicit preserve policy retains the original storm system.
"""
import struct

from .entities import parse_entities
from .inspect import io_signature, sha256
from .lighting import ENVIRONMENT_FIELDS
from .style import FOG_FIELDS, SUN_FIELDS, TONEMAPS, _read, _unique, _validate, transfer_c5_style


def profile_spec(profile):
    """Return filename roots and support status; never infer a profile by name."""
    if profile not in ('c2-c5', 'c6-c5'):
        raise ValueError(f'Unsupported style profile: {profile}')
    return dict(profile=profile,
                source_map='c2m1_highway' if profile == 'c2-c5' else 'c6m1_riverbank',
                reference_map='c5m1_waterfront',
                runtime_validation='accepted_reference' if profile == 'c2-c5' else 'pending_user_test')


def inspect_profile_support(data, reference, *, profile, kind='bsp', reference_kind='bsp', atmosphere_policy=None):
    """Dry-run the complete transfer in memory and report unsupported inputs."""
    try:
        spec = profile_spec(profile)
        _, report = transfer_style(data, reference, profile=profile, kind=kind, reference_kind=reference_kind, atmosphere_policy=atmosphere_policy)
    except ValueError as exc:
        return dict(supported=False, profile=profile, reason=str(exc))
    return dict(report, supported=True, requested_profile=profile, source_map=spec['source_map'],
                reference_map=spec['reference_map'], runtime_validation=spec['runtime_validation'])


def _named(entities, classname, name):
    matches = [(i, e) for i, e in enumerate(entities) if e.one('targetname') == name]
    if len(matches) != 1 or matches[0][1].one('classname') != classname:
        raise ValueError(f'Expected unique {classname}/{name}; ambiguous or wrong target class')
    return matches[0]


def _identity(entities, *, reference=False):
    _, world = _unique(entities, 'worldspawn')
    expected = ({'musicpostfix': 'BigEasy', 'world_mins': '-5248 -4672 -992', 'world_maxs': '3168 3232 1664'}
                if reference else
                {'musicpostfix': 'DeadLight', 'world_mins': '-6110 -2338 -320', 'world_maxs': '9600 6688 3096'})
    if any(world.one(key) != value for key, value in expected.items()):
        raise ValueError('Unsupported internal map identity for C5 reference' if reference else
                         'Unsupported internal map identity for C6 source')
    skies = ('sky_l4d_c5_1_hdr',) if reference else ('sky_l4d_c6m1_hdr', 'sky_l4d_c5_1_hdr')
    if world.one('skyname') not in skies:
        raise ValueError('Unexpected source/reference sky for C6 profile')


def _startup(entities, target, action, *, optional=False):
    matches = [(i, j, e, p) for i, e in enumerate(entities) for j, p in enumerate(e.pairs)
               if p.key.startswith('On') and p.value.split('\x1b')[:2] == [target, action]
               and (p.key == 'OnMapSpawn' or action == 'SetTonemapPercentBrightPixels')]
    if optional and not matches:
        return None
    if len(matches) != 1:
        raise ValueError(f'Expected unique startup exposure output {target}/{action}')
    i, j, entity, pair = matches[0]
    parts = pair.value.split('\x1b')
    if entity.one('classname') != 'logic_auto' or pair.key != 'OnMapSpawn' or len(parts) != 5 or parts[3:] != ['0', '-1']:
        raise ValueError('Unexpected startup exposure owner, event, timing or format')
    return i, j, parts


def transfer_style(data, reference, *, profile, kind='bsp', reference_kind='bsp', atmosphere_policy=None):
    """Apply an explicitly selected profile, preserving all non-entity payloads.

    C6 replace copies the target outdoor fog into all source fog controllers,
    then removes explicitly owned weather and overrides local postprocessing.
    Preserve retains legacy storm timing and local fog ranges. Neither policy
    imports reference geometry or gameplay outputs.
    """
    profile_spec(profile)
    if atmosphere_policy is None:
        atmosphere_policy = 'replace' if profile == 'c6-c5' else 'preserve'
    if atmosphere_policy not in ('replace', 'preserve'):
        raise ValueError('atmosphere_policy must be replace or preserve')
    if profile == 'c2-c5':
        if atmosphere_policy != 'preserve':
            raise ValueError('atmosphere_policy=replace currently supports c6-c5 only')
        return transfer_c5_style(data, reference, kind=kind, reference_kind=reference_kind)
    parsed, text, entities = _read(data, kind)
    _, _, donor = _read(reference, reference_kind)
    _identity(entities)
    _identity(donor, reference=True)

    roles = [('fog_storm', '1', 'storm_default'), ('fog_master', '0', 'outdoor_nondefault'),
             ('foginteriorcontroller', '0', 'interior_volume')]
    fogs = [e for e in entities if e.one('classname') == 'env_fog_controller']
    if len(fogs) != len(roles):
        raise ValueError('Unexpected C6 fog controller set')
    for name, flag, _ in roles:
        _, fog = _named(entities, 'env_fog_controller', name)
        if fog.one('spawnflags') != flag:
            raise ValueError(f'Unexpected C6 fog role/default flag: {name}')
    if not any(e.one('classname') == 'fog_volume' and e.one('FogName') == 'foginteriorcontroller' for e in entities):
        raise ValueError('C6 interior fog must retain original fog_volume routing')
    for name, flag in [('fog_master', '1'), ('foginteriorcontroller', '0')]:
        _, fog = _named(donor, 'env_fog_controller', name)
        if fog.one('spawnflags') != flag:
            raise ValueError('Unexpected C5 reference fog role')

    edits, changes, added_outputs, added_classes = [], [], [], []
    missing, skipped = [], []
    touched = set()
    expected = [[(p.key, p.value) for p in e.pairs] for e in entities]

    def replace(i, pair_index, value, category='visual_field'):
        pair = entities[i].pairs[pair_index]
        if pair.value == value:
            return
        edits.append((pair.value_start, pair.value_end, value.encode('ascii')))
        expected[i][pair_index] = (pair.key, value)
        touched.add(i)
        changes.append(dict(entity_index=i, classname=entities[i].one('classname'),
                            targetname=entities[i].one('targetname'), key=pair.key,
                            old=pair.value, new=value, category=category))

    def copy_fields(classname, fields, name=None, donor_name=None):
        i, original = _named(entities, classname, name) if name else _unique(entities, classname)
        _, target = _named(donor, classname, donor_name or name) if name else _unique(donor, classname)
        for key in fields:
            old, value = original.one(key), target.one(key)
            if old is None or value is None:
                skipped.append(dict(classname=classname, targetname=name, key=key,
                                    reason='field absent from source or reference'))
                continue
            _validate(key, value)
            replace(i, next(j for j, p in enumerate(original.pairs) if p.key == key), value)

    copy_fields('worldspawn', ('skyname',))
    copy_fields('light_environment', sorted(ENVIRONMENT_FIELDS))
    copy_fields('light_directional', ('_light', '_lightHDR', '_lightscaleHDR', 'angles', 'pitch', 'SunSpreadAngle'))
    copy_fields('shadow_control', ('color', 'angles', 'distance'))
    for name, _, _ in roles:
        copy_fields('env_fog_controller', FOG_FIELDS if atmosphere_policy == 'replace' else ('fogcolor', 'fogcolor2', 'HDRColorScale'), name,
                    'foginteriorcontroller' if atmosphere_policy == 'preserve' and name == 'foginteriorcontroller' else 'fog_master')
    copy_fields('sky_camera', tuple(k for k in FOG_FIELDS if k not in ('farz', 'foglerptime')))
    copy_fields('color_correction', ('filename',), 'color_correction_main')
    intro = [e for e in entities if e.one('targetname') == 'color_correction_intro']
    if intro:
        raise ValueError('C6 audited profile has no intro color-correction controller')
    missing.append(dict(classname='color_correction', targetname='color_correction_intro',
                        action='not_added', reason='C6 has no intro controller or corresponding routing'))

    source_exposures = []
    for target, classname in zip(TONEMAPS, ('env_tonemap_controller', 'env_tonemap_controller_infected', 'env_tonemap_controller_ghost')):
        _named(entities, classname, target)
        _named(donor, classname, target)
        original = _startup(entities, target, 'SetAutoExposureMax')
        reference_output = _startup(donor, target, 'SetAutoExposureMax')
        if original[2][2] not in ('8', '5') or reference_output[2][2] != '5':
            raise ValueError('Unapproved C6/C5 startup exposure maximum')
        i, j, parts = original
        source_exposures.append((i, j))
        parts[2] = '5'
        replace(i, j, '\x1b'.join(parts), 'visual_io_parameter')

    bright = _startup(donor, 'tonemap_global', 'SetTonemapPercentBrightPixels')
    if bright[2][2] != '5':
        raise ValueError('Unapproved C5 bright-pixel exposure value')
    existing = _startup(entities, 'tonemap_global', 'SetTonemapPercentBrightPixels', optional=True)
    owner = source_exposures[0][0]
    if existing:
        if existing[0] != owner or existing[2][2] != '5':
            raise ValueError('Unapproved existing bright-pixel output value or owner')
    else:
        value = '\x1b'.join(bright[2])
        edits.append((entities[owner].end - 1, entities[owner].end - 1,
                      f'"OnMapSpawn" "{value}"\n'.encode('ascii')))
        expected[owner].append(('OnMapSpawn', value))
        touched.add(owner)
        added_outputs.append(dict(entity_index=owner, key='OnMapSpawn', value=value))

    _, sun = _unique(donor, 'env_sun')
    suns = [e for e in entities if e.one('classname') == 'env_sun']
    if suns:
        copy_fields('env_sun', SUN_FIELDS)
    else:
        _, light = _unique(entities, 'light_environment')
        origin = light.one('origin')
        if origin is None:
            raise ValueError('Missing source light origin for C6 sun placement')
        _validate('origin', origin)
        pairs = [('classname', 'env_sun'), ('origin', origin)]
        for key in SUN_FIELDS:
            value = sun.one(key)
            if value is None:
                skipped.append(dict(classname='env_sun', targetname=None, key=key,
                                    reason='field absent from reference; omitted from added sun'))
                continue
            _validate(key, value)
            pairs.append((key, value))
        end = text.find(b'\0')
        if end < 0:
            end = len(text)
        edits.append((end, end, ('\n{\n' + ''.join(f'"{k}" "{v}"\n' for k, v in pairs) + '}\n').encode('ascii')))
        expected.append(pairs)
        added_classes.append('env_sun')
        missing.append(dict(classname='env_sun', targetname=None, action='added_visual_only',
                            reason='Source has no sun; source light origin retained'))

    updated = text
    for start, end, value in sorted(edits, reverse=True):
        updated = updated[:start] + value + updated[end:]
    after = parse_entities(updated)
    if [[(p.key, p.value) for p in e.pairs] for e in after] != expected:
        raise ValueError('Unexpected entity change outside explicit C6 edits')
    for i, e in enumerate(entities):
        if i not in touched and text[e.start:e.end] != updated[after[i].start:after[i].end]:
            raise ValueError('Protected C6 entity bytes changed')
    approved = set(source_exposures)
    if existing:
        approved.add(existing[:2])
    protected = [(i, j, p.key, p.value) for i, e in enumerate(entities) for j, p in enumerate(e.pairs)
                 if p.key.startswith('On') and (i, j) not in approved]
    if any((after[i].pairs[j].key, after[i].pairs[j].value) != (key, value) for i, j, key, value in protected):
        raise ValueError('Protected gameplay/storm I/O changed')

    output = data
    if updated != text:
        if kind == 'bsp':
            pad = b'\0' * (-len(data) % 4)
            offset = len(data) + len(pad)
            if offset + len(updated) > 2**31 - 1:
                raise ValueError('BSP offset exceeds signed range')
            header = bytearray(data[:1036])
            struct.pack_into('<ii', header, 12, offset, len(updated))
            output = bytes(header) + data[1036:] + pad + updated
        else:
            offset, length = parsed.entity_span
            header = bytearray(data[:20])
            struct.pack_into('<i', header, 12, len(updated))
            output = bytes(header) + data[20:offset] + updated + data[offset + length:]
    checked, _, _ = _read(output, kind)
    if kind == 'bsp' and any(parsed.lump_bytes(i) != checked.lump_bytes(i) for i in range(1, 64)):
        raise ValueError('Non-entity lump changed')
    storm_overrides = [dict(entity_index=i, targetname=e.one('targetname'), key=p.key, value=p.value)
                       for i, e in enumerate(entities) for p in e.pairs
                       if p.key.startswith('On') and e.one('targetname') in
                       ('relay_tonemap_flash', 'relay_storm_blendin', 'relay_storm_blendout')]
    report = dict(profile='c6-to-c5-globals-v1', atmosphere_policy=atmosphere_policy, source_sha256=sha256(data),
        reference_sha256=sha256(reference), output_sha256=sha256(output), changes=changes,
        added_outputs=added_outputs, added_entity_classes=added_classes,
        entity_counts=[len(entities), len(after)], all_io_unchanged=io_signature(entities) == io_signature(after),
        gameplay_io_unchanged=True, protected_io_count=len(protected), protected_entity_bytes_unchanged=True,
        non_entity_payloads_unchanged=True, missing_optional_controllers=missing, skipped_fields=skipped,
        fog_roles=[dict(targetname=name, role=role, original_spawnflags=flag) for name, flag, role in roles],
        retained_storm_visual_outputs=storm_overrides, runtime_validation='pending_user_test',
        limitations=['Partial global C5 appearance; original C6 storm/weather and all gameplay routing retained',
                     'Storm lightning can override startup exposure; reflection capture requires stable conditions',
                     'Fog controller distances/density/timing and checkpoint correction retained',
                     'New sun and other visual edits require user runtime validation',
                     'No geometry/local-light import; no full model lighting or cubemap recapture'])
    if atmosphere_policy == 'replace':
        from .weather import replace_c6_weather
        output, weather = replace_c6_weather(output, reference, kind=kind, reference_kind=reference_kind)
        report.update(profile='c6-to-c5-clear-v2', output_sha256=sha256(output), weather=weather,
                      entity_counts=[len(entities), weather['entity_counts'][1]],
                      changes=changes + weather['changes'], retained_storm_visual_outputs=[],
                      protected_io_count=weather['protected_io_count'],
                      limitations=weather['limitations'])
        report['all_io_unchanged'] = report['all_io_unchanged'] and not weather['removed_outputs']
    return output, report
