"""Prepare experimental compiler inputs, never certified gameplay outputs."""
import math
import struct

from .binary import BspFile
from .inspect import entity_list, io_signature, sha256
from .entities import parse_entities


ENVIRONMENT_FIELDS = {'_light': 4, '_ambient': 4, '_lightHDR': 4, '_ambientHDR': 4,
    '_lightscaleHDR': 1, '_AmbientScaleHDR': 1, 'angles': 3, 'pitch': 1, 'SunSpreadAngle': 1}


def _environment(entities):
    selected = [ent for ent in entities if ent.one('classname') == 'light_environment']
    if len(selected) != 1:
        raise ValueError('Lighting experiment requires exactly one light_environment per map')
    return selected[0]


def _numeric_value(key, value):
    try:
        encoded = value.encode('ascii')
        numbers = [float(x) for x in value.split(' ')]
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f'Invalid numeric lighting field {key}') from exc
    if len(numbers) != ENVIRONMENT_FIELDS[key] or not all(math.isfinite(n) for n in numbers):
        raise ValueError(f'Invalid component count or nonfinite value in {key}')
    if any(x in encoded for x in (b'"', b'\\', b'\0', b'\n', b'\r', b'\t')):
        raise ValueError('Control/quote bytes in lighting value')
    if key in ('_light', '_ambient', '_lightHDR', '_ambientHDR'):
        fallback = key.endswith('HDR') and numbers[:3] == [-1, -1, -1]
        if not fallback and not all(0 <= n <= 255 for n in numbers[:3]):
            raise ValueError(f'Invalid RGB in {key}')
        if numbers[3] < 0:
            raise ValueError(f'Negative intensity in {key}')
    if key in ('_lightscaleHDR', '_AmbientScaleHDR', 'SunSpreadAngle') and numbers[0] < 0:
        raise ValueError(f'Negative scale/spread in {key}')
    return encoded


def prepare_environment_input(source, reference):
    """Copy common numeric environment fields; preserve every existing lump address.

    A changed entity payload is appended and only its directory address/size is
    updated. Original data, including game-lump absolute offsets, stays in place.
    VRAD can then rewrite the container. This is NOT the equal-length visual mode.
    """
    bsp, target = BspFile.parse(source), BspFile.parse(reference)
    original_entities, target_entities = entity_list(bsp), entity_list(target)
    origin, donor = _environment(original_entities), _environment(target_entities)
    text = bsp.lump_bytes(0)
    patches, changes = [], []
    for key in ENVIRONMENT_FIELDS:
        old, new = origin.one(key), donor.one(key)
        if old is None or new is None:
            continue
        replacement = _numeric_value(key, new)
        if old == new:
            continue
        pair = next(p for p in origin.pairs if p.key == key)
        patches.append((pair.value_start, pair.value_end, replacement))
        changes.append(dict(key=key, old=old, new=new))
    for start, end, value in sorted(patches, reverse=True):
        text = text[:start]+value+text[end:]
    output = source
    if changes:
        pad = b'\0'*(-len(source) % 4)
        offset = len(source)+len(pad)
        if offset+len(text) > 2**31-1:
            raise ValueError('Compiler input exceeds signed 32-bit BSP offsets')
        header = bytearray(source[:1036])
        struct.pack_into('<ii', header, 12, offset, len(text))
        output = bytes(header)+source[1036:]+pad+text
    checked = BspFile.parse(output)
    io_ok = io_signature(original_entities) == io_signature(parse_entities(checked.lump_bytes(0)))
    non_entity_ok = all(bsp.lump_bytes(i) == checked.lump_bytes(i) for i in range(1, 64))
    if not io_ok or not non_entity_ok:
        raise ValueError('Compiler-input preservation check failed')
    return output, dict(status='compiler_input_only', source_sha256=sha256(source),
        reference_sha256=sha256(reference), output_sha256=sha256(output), changes=changes,
        io_unchanged=io_ok, non_entity_lumps_unchanged=non_entity_ok,
        note='Only light_environment fields; original light_directional and local lights retained. Not final C5-style equivalence.')
