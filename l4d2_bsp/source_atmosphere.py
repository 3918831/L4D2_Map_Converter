"""Bounded source roles with preset-owned target atmosphere values.

Only the measured C2 source layout is currently supported. This module has
no target-campaign values: optional sun, exposure and ambience come from data.
"""
import fnmatch
import struct

from .entities import parse_entities
from .inspect import sha256
from .style import _read, _unique, TONEMAPS


C2_SOUNDS = {'c2m1.hiway.spawn': 'outdoor', 'c2m1.hotel.outdoors': 'outdoor',
            'c2m1.hotel.indoors': 'indoor', 'c2m1.parkinglot': 'outdoor',
            'c2m1.ravine': 'outdoor', 'c2m1.saferoom': 'indoor'}
POST_NAMES = ('fx_settings_intro', 'fx_settings_exterior', 'fx_settings_interior')
FOG_NAMES = ('fog_master', 'foginteriorcontroller')
COLOR_NAMES = ('color_correction_main', 'color_correction_intro', 'colorcorrection_checkpoint')


def _output(pair):
    return pair.key.startswith(('On', 'Out')) or '\x1b' in pair.value


def transfer_atmosphere(data, preset, *, source_profile, kind='bsp'):
    if source_profile != 'c2m1_highway' or source_profile not in preset.supported_sources:
        raise ValueError('Unsupported source/preset capability')
    parsed, text, entities = _read(data, kind)
    _, world = _unique(entities, 'worldspawn')
    if any(world.one(k) != v for k, v in {'musicpostfix':'Fairgrounds',
            'world_mins':'-3648 -5312 -2048', 'world_maxs':'14848 10752 256'}.items()):
        raise ValueError('Unsupported C2 internal source identity')
    if world.one('skyname') not in ('sky_l4d_c2m1_hdr', preset.role_values('world')['skyname']):
        raise ValueError('Unexpected source sky; start from the original map')
    expected = [[(p.key, p.value) for p in e.pairs] for e in entities]
    changes, touched, approved, added_outputs = [], set(), set(), []

    def named(classname, name=None):
        if name:
            matches = [(i,e) for i,e in enumerate(entities) if e.one('targetname') == name]
            if len(matches) != 1 or matches[0][1].one('classname') != classname:
                raise ValueError(f'Ambiguous source role {classname}/{name}')
            return matches[0]
        return _unique(entities, classname)

    def replace(i, j, value, category='visual_field'):
        key, old = expected[i][j]
        if old == value:
            return
        expected[i][j] = key, value
        touched.add(i)
        changes.append(dict(entity_index=i, classname=entities[i].one('classname'),
            targetname=entities[i].one('targetname'), key=key, old=old, new=value, category=category))

    def fields(i, values):
        if values is None:
            return
        for key, value in values.items():
            if not isinstance(value, str) or any(c in value for c in '\r\n\0"\\\x1b'):
                raise ValueError('Invalid preset field value')
            entities[i].one(key)  # Reject duplicate fields even if unchanged.
            indexes = [j for j,p in enumerate(entities[i].pairs) if p.key == key]
            if indexes:
                replace(i, indexes[0], value)
            else:
                expected[i].append((key, value))
                touched.add(i)
                changes.append(dict(entity_index=i, classname=entities[i].one('classname'),
                    targetname=entities[i].one('targetname'), key=key, old=None, new=value, category='visual_field'))

    for cls, role in (('worldspawn','world'), ('light_environment','environment'),
                      ('light_directional','directional'), ('shadow_control','shadow'), ('sky_camera','sky')):
        fields(named(cls)[0], preset.role_values(role))
    for cls, names in (('env_fog_controller',FOG_NAMES), ('postprocess_controller',POST_NAMES),
                       ('color_correction',COLOR_NAMES)):
        if len([e for e in entities if e.one('classname') == cls]) != len(names):
            raise ValueError(f'Unexpected source atmosphere controller set: {cls}')
        for name in names:
            i, entity = named(cls, name)
            if cls == 'env_fog_controller':
                if entity.one('spawnflags') != '1':
                    raise ValueError('Unexpected C2 fog default role')
                values = preset.role_values('fog_outdoor')
            elif cls == 'postprocess_controller':
                values = preset.role_values('postprocess_exterior')
            else:
                role = dict(zip(COLOR_NAMES, ('color_main','color_intro','color_checkpoint')))[name]
                values = preset.role_values(role) or preset.role_values('color_main')
            fields(i, values)

    winds = [i for i,e in enumerate(entities) if e.one('classname') == 'env_wind']
    if len(winds) != 2:
        raise ValueError('Unexpected C2 wind controller set')
    for i in winds:
        fields(i, preset.wind_values)
    if any(e.one('classname') == 'func_precipitation' for e in entities):
        raise ValueError('Unexpected source precipitation; weather ownership needs an adapter')
    sound_mapping = preset.soundscape_mapping
    for i,e in enumerate(entities):
        if e.one('classname') == 'env_soundscape':
            sound = e.one('soundscape')
            if sound in C2_SOUNDS:
                fields(i, {'soundscape': sound_mapping[C2_SOUNDS[sound]]})
            elif sound not in sound_mapping.values():
                raise ValueError(f'Unknown source soundscape: {sound}')

    # Only exact, unconditional startup tonemap commands may change. Any
    # other writer targeting a visual controller must be reviewed explicitly.
    visual_names = set(FOG_NAMES + POST_NAMES + COLOR_NAMES + TONEMAPS)
    visual_classes = {'light_environment','light_directional','shadow_control','sky_camera',
        'env_fog_controller','postprocess_controller','color_correction','env_wind','env_sun',
        'env_soundscape','env_tonemap_controller','env_tonemap_controller_infected','env_tonemap_controller_ghost'}
    visual_names.update(e.one('targetname') for e in entities if e.one('classname') in
                        visual_classes and e.one('targetname'))
    exposure = preset.exposure_values
    desired = {'SetAutoExposureMax':exposure['maximum'], 'SetAutoExposureMin':exposure['minimum'],
               'SetTonemapRate':exposure['rate'], 'SetTonemapPercentBrightPixels':exposure['bright_pixels']}
    startup = {}
    for i,e in enumerate(entities):
        for j,p in enumerate(e.pairs):
            if not _output(p):
                continue
            separator = '\x1b' if '\x1b' in p.value else ','
            parts = p.value.split(separator)
            self_writer = parts[0] == '!self' and e.one('classname') in visual_classes
            if not self_writer and not any(fnmatch.fnmatchcase(n, parts[0]) for n in visual_names | visual_classes):
                continue
            if (separator != '\x1b' or len(parts) != 5 or e.one('classname') != 'logic_auto' or p.key != 'OnMapSpawn'
                    or parts[0] not in TONEMAPS or parts[1] not in desired or parts[3:] != ['0','-1']):
                raise ValueError(f'Unknown atmosphere writer: {i}/{p.key}')
            if desired[parts[1]] is None:
                raise ValueError('Source bright-pixel override is not specified by target; start from original map')
            if parts[1] in ('SetTonemapRate','SetTonemapPercentBrightPixels') and parts[0] != 'tonemap_global':
                raise ValueError('Unexpected non-global exposure writer')
            identity = tuple(parts[:2])
            if identity in startup:
                raise ValueError('Duplicate startup exposure writer')
            if parts[1] == 'SetAutoExposureMax' and parts[2] not in ('6', exposure['maximum']):
                raise ValueError('Unexpected source exposure maximum')
            startup[identity] = (i,j)
            approved.add((i,j))
            parts[2] = desired[parts[1]]
            replace(i,j,'\x1b'.join(parts),'visual_io_parameter')
    for target, cls in zip(TONEMAPS, ('env_tonemap_controller','env_tonemap_controller_infected','env_tonemap_controller_ghost')):
        named(cls,target)
        if (target,'SetAutoExposureMax') not in startup:
            raise ValueError('Missing original startup exposure maximum')
        owner = startup[target,'SetAutoExposureMax'][0]
        for action, value in desired.items():
            if value is None or (target != 'tonemap_global' and action not in ('SetAutoExposureMin','SetAutoExposureMax')):
                continue
            if (target,action) not in startup:
                value = '\x1b'.join((target,action,value,'0','-1'))
                expected[owner].append(('OnMapSpawn',value))
                touched.add(owner)
                added_outputs.append(dict(entity_index=owner,key='OnMapSpawn',value=value))

    sun_values = preset.role_values('sun')
    suns = [i for i,e in enumerate(entities) if e.one('classname') == 'env_sun']
    additions = []
    if sun_values is None:
        if suns:
            raise ValueError('Unexpected source sun; start from original map')
    elif suns:
        fields(named('env_sun')[0], sun_values)
    else:
        origin = named('light_environment')[1].one('origin') or '0 0 0'
        additions.append([('classname','env_sun'),('origin',origin),*sun_values.items()])

    def serialize(pairs):
        return ('{\n'+''.join(f'"{k}" "{v}"\n' for k,v in pairs)+'}').encode('latin1')
    edits = [(entities[i].start,entities[i].end,serialize(expected[i])) for i in touched]
    if additions:
        end = text.find(b'\0')
        if end < 0: end = len(text)
        edits.append((end,end,b'\n'+b'\n'.join(serialize(p) for p in additions)+b'\n'))
    updated = text
    for start,end,value in sorted(edits,reverse=True):
        updated = updated[:start]+value+updated[end:]
    after = parse_entities(updated)
    if [[(p.key,p.value) for p in e.pairs] for e in after] != expected + additions:
        raise ValueError('Unexpected entity edit outside target fields')
    for i,e in enumerate(entities):
        if i not in touched and text[e.start:e.end] != updated[after[i].start:after[i].end]:
            raise ValueError('Protected source entity bytes changed')
    protected = [(i,j,p.key,p.value) for i,e in enumerate(entities) for j,p in enumerate(e.pairs)
                 if _output(p) and (i,j) not in approved]
    if any((after[i].pairs[j].key,after[i].pairs[j].value) != (key,value) for i,j,key,value in protected):
        raise ValueError('Protected gameplay output changed')
    output = data
    if updated != text:
        if kind == 'bsp':
            pad = b'\0' * (-len(data)%4)
            offset = len(data)+len(pad)
            if offset+len(updated) > 2**31-1: raise ValueError('BSP offset overflow')
            header = bytearray(data[:1036])
            struct.pack_into('<ii',header,12,offset,len(updated))
            output = bytes(header)+data[1036:]+pad+updated
        else:
            offset,length = parsed.entity_span
            header = bytearray(data[:20])
            struct.pack_into('<i',header,12,len(updated))
            output = bytes(header)+data[20:offset]+updated+data[offset+length:]
    checked,_,_ = _read(output,kind)
    if kind == 'bsp' and any(parsed.lump_bytes(i) != checked.lump_bytes(i) for i in range(1,64)):
        raise ValueError('Non-entity lump changed')
    return output, dict(profile=source_profile, preset=preset.metadata(), atmosphere_policy='replace',
        source_sha256=sha256(data), reference_sha256=preset.sha256, output_sha256=sha256(output),
        changes=changes, added_outputs=added_outputs, added_entity_classes=['env_sun'] if additions else [],
        entity_counts=[len(entities),len(after)], gameplay_io_unchanged=True,
        protected_io_count=len(protected), protected_entity_bytes_unchanged=True, non_entity_payloads_unchanged=True,
        limitations=['Bounded source roles; fixed atmosphere, no dynamic weather import.',
                     'Full game resource and native HDR capture validation still required.'])
