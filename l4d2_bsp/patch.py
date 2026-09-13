"""Visual substitutions restricted to one existing, equal-length value span."""

import re

from .binary import BspFile, LumpFile
from .entities import parse_entities


VISUAL_KEYS = {
    'color_correction': frozenset({'filename'}),
    'env_fog_controller': frozenset({'fogcolor', 'fogcolor2'}),
    'sky_camera': frozenset({'fogcolor', 'fogcolor2'}),
    'worldspawn': frozenset({'skyname'}),
}


class PatchError(ValueError):
    """Patch preconditions are not satisfied."""


def _validate_value(key: str, value: str) -> bytes:
    try:
        encoded = value.encode('ascii')
    except UnicodeEncodeError as exc:
        raise PatchError('Replacement visual values must be ASCII') from exc
    if not encoded or any(byte < 32 or byte == 127 for byte in encoded) or any(char in value for char in '"\\'):
        raise PatchError('Replacement contains empty, control, quote, or backslash content')
    if key in ('fogcolor', 'fogcolor2'):
        if not re.fullmatch(r'[0-9]{1,3} [0-9]{1,3} [0-9]{1,3}', value) or any(int(part) > 255 for part in value.split()):
            raise PatchError('Fog color must contain three decimal RGB components from 0 through 255')
    elif key == 'skyname':
        if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
            raise PatchError('Sky name must be a simple resource basename')
    elif key == 'filename':
        if not re.fullmatch(r'materials/correction/[A-Za-z0-9_/-]+\.raw', value) or any(part in ('', '.', '..') for part in value.split('/')):
            raise PatchError('Color correction must be a relative materials/correction/*.raw resource path')
    return encoded


def patch_visual(data: bytes, *, kind: str, classname: str, targetname: str | None,
                 key: str, expected: str, value: str) -> tuple[bytes, dict]:
    if key not in VISUAL_KEYS.get(classname, ()):
        raise PatchError(f'Visual field is not whitelisted: {classname}.{key}')
    replacement = _validate_value(key, value)
    if kind == 'bsp':
        parsed = BspFile.parse(data)
        entity_version = parsed.lumps[0].version
        if parsed.lumps[0].fourcc != b'\0' * 4:
            raise PatchError('Compressed entity lump patching is unsupported')
    elif kind == 'lmp':
        parsed = LumpFile.parse(data)
        entity_version = parsed.version
    else:
        raise PatchError(f'Unknown input kind: {kind!r}')
    if entity_version != 0:
        raise PatchError(f'Unsupported entity lump version {entity_version}; patching requires version 0')
    offset, length = parsed.entity_span
    entity_bytes = parsed.data[offset:offset + length]
    if entity_bytes.startswith(b'LZMA'):
        raise PatchError('Compressed entity lump patching is unsupported')
    entities = parse_entities(entity_bytes)
    selected = [(index, entity) for index, entity in enumerate(entities)
                if entity.one('classname') == classname
                and (targetname is None or entity.one('targetname') == targetname)]
    if len(selected) != 1:
        raise PatchError(f'Expected exactly one selected entity, found {len(selected)}')
    entity_index, entity = selected[0]
    pairs = [pair for pair in entity.pairs if pair.key == key]
    if len(pairs) != 1:
        raise PatchError(f'Expected exactly one {key!r} key, found {len(pairs)}')
    pair = pairs[0]
    if pair.value != expected:
        raise PatchError(f'Expected old value {expected!r}, found {pair.value!r}')
    if len(replacement) != pair.value_end - pair.value_start:
        raise PatchError('Replacement must have exactly the same byte length as the old value')
    start, end = offset + pair.value_start, offset + pair.value_end
    changed = parsed.data[:start] + replacement + parsed.data[end:]
    return changed, {'start': start, 'end': end, 'classname': classname,
                     'targetname': targetname, 'key': key, 'old': pair.value,
                     'new': value, 'entity_index': entity_index}
