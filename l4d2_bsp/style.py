"""Bounded C2-to-C5 visual transfer, including explicitly audited visual outputs.

This is a map-specific profile, not an arbitrary entity editor. No geometry,
trigger, local-light or gameplay-output import is supported.
"""
import math
import struct

from .binary import BspFile, LumpFile
from .entities import parse_entities
from .inspect import io_signature, sha256
from .lighting import ENVIRONMENT_FIELDS, _numeric_value
from .patch import _validate_value


FOG_FIELDS = ('fogcolor', 'fogcolor2', 'fogstart', 'fogend', 'fogmaxdensity',
              'foglerptime', 'HDRColorScale', 'farz')
SUN_FIELDS = ('use_angles', 'angles', 'pitch', 'size', 'rendercolor', 'overlaysize',
              'overlaymaterial', 'overlaycolor', 'material', 'HDRColorScale')
TONEMAPS = ('tonemap_global', 'tonemap_global_infected', 'tonemap_global_ghost')
EXPOSURE_INPUTS = ('SetAutoExposureMax', 'SetTonemapPercentBrightPixels')


def _read(data, kind):
    if kind not in ('bsp', 'lmp'):
        raise ValueError('Expected bsp or lmp')
    parsed = BspFile.parse(data) if kind == 'bsp' else LumpFile.parse(data)
    version = parsed.lumps[0].version if kind == 'bsp' else parsed.version
    if version != 0 or (kind == 'bsp' and parsed.lumps[0].fourcc != b'\0'*4):
        raise ValueError('Unsupported or compressed entity lump')
    offset, length = parsed.entity_span
    text = data[offset:offset+length]
    if text.startswith(b'LZMA'):
        raise ValueError('Compressed entities are unsupported')
    return parsed, text, parse_entities(text)


def _unique(entities, classname, name=None):
    found = [(i,e) for i,e in enumerate(entities) if e.one('classname') == classname
             and (name is None or e.one('targetname') == name)]
    if len(found) != 1:
        raise ValueError(f'Expected unique {classname}/{name}, found {len(found)}')
    return found[0]


def _validate(key, value):
    if key in ('skyname', 'filename', 'fogcolor', 'fogcolor2'):
        return _validate_value(key, value)
    if key in ENVIRONMENT_FIELDS:
        return _numeric_value(key, value)
    if key in ('material', 'overlaymaterial'):
        if value != 'sprites/light_glow02_add_noz':
            raise ValueError('Unsupported sun material; resource validation required')
        return value.encode('ascii')
    raw = value.encode('ascii')
    if any(x in raw for x in (b'"', b'\\', b'\0', b'\n', b'\r', b'\t')):
        raise ValueError('Invalid numeric visual value')
    values = [float(x) for x in value.split()]
    count = 3 if key in ('color', 'rendercolor', 'overlaycolor', 'origin') else 1
    if len(values) != count or not all(math.isfinite(x) for x in values):
        raise ValueError('Invalid numeric components')
    if key in ('color', 'rendercolor', 'overlaycolor') and not all(0 <= x <= 255 for x in values):
        raise ValueError('Invalid color')
    if key == 'fogmaxdensity' and not 0 <= values[0] <= 1:
        raise ValueError('Invalid fog density')
    return raw


def _visual_output(entity, pair):
    parts = pair.value.split('\x1b')
    return (entity.one('classname') == 'logic_auto' and pair.key == 'OnMapSpawn'
            and len(parts) == 5 and parts[0] in TONEMAPS
            and parts[1] in EXPOSURE_INPUTS and parts[3:] == ['0', '-1'])


def _reference(reference, kind):
    """Resolve validated visual data or the backwards-compatible donor bytes."""
    from .presets import StylePreset
    if isinstance(reference, StylePreset):
        return reference.entities(kind), reference.sha256, reference.metadata()
    _, _, entities = _read(reference, kind)
    return entities, sha256(reference), None


def transfer_c5_style(data, reference, *, kind='bsp', reference_kind='bsp'):
    """Transfer global appearance; retain source routing and skybox geometry.

    Both C2 default fog controllers receive the reference outdoor fog. Existing
    outputs retain their order, targets and timing. Only exposure values change;
    one reference exposure input and one non-solid sun entity may be added.
    """
    parsed, text, entities = _read(data, kind)
    donor, reference_hash, preset_metadata = _reference(reference, reference_kind)
    edits, changes, added_outputs, added_classes = [], [], [], []
    expected = [[(p.key,p.value) for p in e.pairs] for e in entities]
    touched = set()

    def replace(i, pair_index, value, category):
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
        i, origin = _unique(entities, classname, name)
        _, target = _unique(donor, classname, donor_name if donor_name else name)
        for key in fields:
            old, value = origin.one(key), target.one(key)
            if old is None or value is None:
                continue
            _validate(key, value)
            pi = next(j for j,p in enumerate(origin.pairs) if p.key == key)
            replace(i, pi, value, 'visual_field')

    copy_fields('worldspawn', ('skyname',))
    copy_fields('light_environment', sorted(ENVIRONMENT_FIELDS))
    copy_fields('light_directional', ('_light','_lightHDR','_lightscaleHDR','angles','pitch','SunSpreadAngle'))
    copy_fields('shadow_control', ('color','angles','distance'))
    for name in ('fog_master','foginteriorcontroller'):
        _, fog = _unique(entities, 'env_fog_controller', name)
        if fog.one('spawnflags') != '1':
            raise ValueError('C2 profile requires both original default fog controllers')
        copy_fields('env_fog_controller', FOG_FIELDS, name, 'fog_master')
    copy_fields('sky_camera', tuple(k for k in FOG_FIELDS if k not in ('farz','foglerptime')))
    for name in ('color_correction_main','color_correction_intro'):
        copy_fields('color_correction', ('filename',), name)

    # Validate targets by class: name matching alone must never edit a relay.
    _unique(entities, 'env_tonemap_controller', 'tonemap_global')
    for target in TONEMAPS:
        named = [e for e in entities if e.one('targetname') == target]
        if len(named) > 1 or any(e.one('classname') not in
            ('env_tonemap_controller','env_tonemap_controller_infected','env_tonemap_controller_ghost') for e in named):
            raise ValueError(f'Ambiguous or non-tonemap target {target}')
    # Refuse drifted inputs even when a preexisting bright-pixel output would
    # otherwise bypass the insertion owner's uniqueness check below.
    for target in TONEMAPS:
        matches = [(e,p) for e in entities for p in e.pairs
                   if p.key.startswith('On') and
                   p.value.split('\x1b')[:2] == [target,'SetAutoExposureMax']]
        if len(matches) != 1 or not _visual_output(*matches[0]):
            raise ValueError(f'Expected unique original exposure output for {target}')
        if matches[0][1].value.split('\x1b')[2] not in ('6','5'):
            raise ValueError('Unexpected source exposure maximum for C2 profile')
    for i, e in enumerate(entities):
        for pi, pair in enumerate(e.pairs):
            if not _visual_output(e, pair):
                continue
            parts = pair.value.split('\x1b')
            matches = [p for d in donor for p in d.pairs if _visual_output(d,p)
                       and p.value.split('\x1b')[:2] == parts[:2]]
            if len(matches) != 1:
                raise ValueError('Expected unique reference exposure output')
            value = matches[0].value.split('\x1b')[2]
            _validate('exposure', value)
            if not 0 < float(value) <= 100:
                raise ValueError('Exposure outside profile bounds')
            parts[2] = value
            replace(i, pi, '\x1b'.join(parts), 'visual_io_parameter')

    bright = [p for d in donor for p in d.pairs if _visual_output(d,p)
              and p.value.split('\x1b')[:2] == ['tonemap_global','SetTonemapPercentBrightPixels']]
    if len(bright) != 1:
        raise ValueError('Expected unique reference bright-pixel output')
    _validate('exposure', bright[0].value.split('\x1b')[2])
    if not 0 < float(bright[0].value.split('\x1b')[2]) <= 100:
        raise ValueError('Invalid bright-pixel target')
    existing = [(i,p) for i,e in enumerate(entities) for p in e.pairs if _visual_output(e,p)
                and p.value.split('\x1b')[:2] == ['tonemap_global','SetTonemapPercentBrightPixels']]
    if len(existing) > 1:
        raise ValueError('Expected unique source bright-pixel output')
    if not existing:
        owners = [i for i,e in enumerate(entities) if any(_visual_output(e,p) and
                  p.value.split('\x1b')[:2] == ['tonemap_global','SetAutoExposureMax'] for p in e.pairs)]
        if len(owners) != 1:
            raise ValueError('Expected unique source exposure logic_auto')
        i = owners[0]
        pair = bright[0]
        edits.append((entities[i].end-1, entities[i].end-1,
                      f'"{pair.key}" "{pair.value}"\n'.encode('ascii')))
        expected[i].append((pair.key,pair.value))
        touched.add(i)
        added_outputs.append(dict(entity_index=i,key=pair.key,value=pair.value))

    _, sun = _unique(donor, 'env_sun')
    existing_suns = [e for e in entities if e.one('classname') == 'env_sun']
    if existing_suns:
        copy_fields('env_sun', SUN_FIELDS)
    else:
        _, light = _unique(entities, 'light_environment')
        origin = light.one('origin') or '0 0 0'
        _validate('origin', origin)
        pairs = [('classname','env_sun'),('origin',origin)]
        for key in SUN_FIELDS:
            value = sun.one(key)
            if value is not None:
                _validate(key, value)
                pairs.append((key,value))
        new_entity = ('\n{\n'+''.join(f'"{k}" "{v}"\n' for k,v in pairs)+'}\n').encode('ascii')
        end = text.find(b'\0')
        if end == -1:
            end = len(text)
        edits.append((end,end,new_entity))
        added_classes.append('env_sun')
        expected.append(pairs)
    updated = text
    for start,end,value in sorted(edits, reverse=True):
        updated = updated[:start]+value+updated[end:]
    after = parse_entities(updated)
    if [[(p.key,p.value) for p in e.pairs] for e in after] != expected:
        raise ValueError('Unexpected entity change outside explicit edits')
    for i,e in enumerate(entities):
        if i not in touched and text[e.start:e.end] != updated[after[i].start:after[i].end]:
            raise ValueError('Protected entity bytes changed')
    protected_io = lambda ents: [(i,p.key,p.value) for i,e in enumerate(ents)
                                for p in e.pairs if p.key.startswith('On') and not _visual_output(e,p)]
    if protected_io(entities) != protected_io(after):
        raise ValueError('Gameplay I/O changed')
    output = data
    if updated != text:
        if kind == 'bsp':
            pad = b'\0'*(-len(data)%4)
            offset = len(data)+len(pad)
            if offset+len(updated) > 2**31-1:
                raise ValueError('BSP offset exceeds signed range')
            header = bytearray(data[:1036])
            struct.pack_into('<ii', header, 12, offset, len(updated))
            output = bytes(header)+data[1036:]+pad+updated
        else:
            offset,length = parsed.entity_span
            header = bytearray(data[:20])
            struct.pack_into('<i',header,12,len(updated))
            output = bytes(header)+data[20:offset]+updated+data[offset+length:]
    checked, _, _ = _read(output, kind)
    if kind == 'bsp' and any(parsed.lump_bytes(i) != checked.lump_bytes(i) for i in range(1,64)):
        raise ValueError('Non-entity lump changed')
    return output, dict(source_sha256=sha256(data),reference_sha256=reference_hash, preset=preset_metadata,
        output_sha256=sha256(output), changes=changes, added_outputs=added_outputs,
        added_entity_classes=added_classes, entity_counts=[len(entities),len(after)],
        all_io_unchanged=io_signature(entities)==io_signature(after),gameplay_io_unchanged=True,
        protected_io_count=len(protected_io(entities)),protected_entity_bytes_unchanged=True,
        non_entity_payloads_unchanged=True, profile='c2-to-c5-globals-v1',
        limitations=['Map-specific outdoor fog routing','New visual entity requires runtime validation',
                     'No geometry/local-light import; no full model lighting/cubemap recapture'])
