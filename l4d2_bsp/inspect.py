"""Read-only inventories and comparisons; no reserialization of BSP payloads."""
from collections import Counter
import hashlib
import io
import struct
import zipfile

from .binary import BSP_HEADER_SIZE, LMP_HEADER_SIZE, BspFile, LumpFile
from .entities import parse_entities


LUMP_NAMES = (
    'ENTITIES PLANES TEXDATA VERTEXES VISIBILITY NODES TEXINFO FACES LIGHTING '
    'OCCLUSION LEAFS FACEIDS EDGES SURFEDGES MODELS WORLDLIGHTS LEAFFACES '
    'LEAFBRUSHES BRUSHES BRUSHSIDES AREAS AREAPORTALS PROPCOLLISION PROPHULLS '
    'PROPHULLVERTS PROPTRIS DISPINFO ORIGINALFACES PHYSDISP PHYSCOLLIDE '
    'VERTNORMALS VERTNORMALINDICES DISP_LIGHTMAP_ALPHAS DISP_VERTS '
    'DISP_LIGHTMAP_SAMPLE_POSITIONS GAME_LUMP LEAFWATERDATA PRIMITIVES '
    'PRIMVERTS PRIMINDICES PAKFILE CLIPPORTALVERTS CUBEMAPS '
    'TEXDATA_STRING_DATA TEXDATA_STRING_TABLE OVERLAYS LEAFMINDISTTOWATER '
    'FACE_MACRO_TEXTURE_INFO DISP_TRIS PROP_BLOB WATEROVERLAYS '
    'LEAF_AMBIENT_INDEX_HDR LEAF_AMBIENT_INDEX LIGHTING_HDR WORLDLIGHTS_HDR '
    'LEAF_AMBIENT_LIGHTING_HDR LEAF_AMBIENT_LIGHTING XZIPPAKFILE FACES_HDR '
    'MAP_FLAGS OVERLAY_FADES OVERLAY_SYSTEM_LEVELS PHYSLEVEL DISP_MULTIBLEND'
).split()

VISUAL_CLASSES = {
    'worldspawn', 'color_correction', 'color_correction_volume',
    'env_fog_controller', 'fog_volume', 'postprocess_controller',
    'env_tonemap_controller', 'env_tonemap_controller_infected',
    'env_tonemap_controller_ghost', 'sky_camera', 'env_sun', 'shadow_control',
    'light', 'light_spot', 'light_environment', 'light_directional', 'light_dynamic',
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def container(data, kind):
    if kind == 'bsp':
        return BspFile.parse(data)
    if kind == 'lmp':
        return LumpFile.parse(data)
    raise ValueError('kind must be bsp or lmp')


def entity_list(parsed):
    version = parsed.lumps[0].version if isinstance(parsed, BspFile) else parsed.version
    if version != 0:
        raise ValueError(f'Unsupported entity lump version {version}; expected version 0')
    if isinstance(parsed, BspFile) and parsed.lumps[0].fourcc != b'\0'*4:
        raise ValueError('Compressed entities are not supported in this first version')
    start, size = parsed.entity_span
    return parse_entities(parsed.data[start:start+size])


def io_signature(entities):
    """Keep duplicates and identity by original ordinal, never sort outputs."""
    return [(i, pair.key, pair.value) for i, ent in enumerate(entities)
            for pair in ent.pairs if pair.key.startswith('On')]


def _game_lump_inventory(parsed):
    raw = parsed.lump_bytes(35)
    if not raw:
        return []
    if parsed.lumps[35].fourcc != b'\0'*4:
        return {'unsupported': 'compressed game lump; raw payload retained'}
    if len(raw) < 4:
        raise ValueError('Truncated game lump directory')
    count, = struct.unpack_from('<i', raw)
    if count < 0 or 4+count*16 > len(raw):
        raise ValueError('Invalid game lump count')
    game_start = parsed.lumps[35].offset
    payload_start = game_start + 4 + count * 16
    game_end = game_start + len(raw)
    result = []
    for i in range(count):
        tag, flags, version, offset, length = struct.unpack_from('<4sHHii', raw, 4+16*i)
        # Offsets are absolute BSP offsets. Compressed sub-lumps can require
        # branch-specific size handling: do not pretend to decode them here.
        item = dict(id=tag[::-1].decode('ascii', 'replace'), flags=flags,
                    version=version, offset=offset, length=length)
        if length < 0 or offset < 0 or offset > len(parsed.data):
            raise ValueError('Invalid game lump payload offset or length')
        if length and (offset < max(BSP_HEADER_SIZE, payload_start) or offset >= game_end):
            raise ValueError('Game lump payload starts outside its enclosing payload region')
        if flags & 1:
            item['unsupported'] = 'compressed sub-lump, declared length may be uncompressed'
        elif length == 0:
            item['sha256'] = sha256(b'')
        elif offset + length > game_end:
            raise ValueError('Game lump payload extends outside its enclosing game lump')
        else:
            item['sha256'] = sha256(parsed.data[offset:offset+length])
        result.append(item)
    return result


def inspect_bytes(data, kind='bsp'):
    parsed = container(data, kind)
    result = dict(kind=kind, size=len(data), sha256=sha256(data),
                  version=parsed.version, revision=parsed.revision)
    if kind == 'bsp':
        result['lumps'] = [dict(index=i, name=LUMP_NAMES[i], version=lump.version,
            offset=lump.offset, length=lump.length, fourcc=lump.fourcc.hex(),
            sha256=sha256(parsed.lump_bytes(i))) for i, lump in enumerate(parsed.lumps)]
        try:
            result['game_lumps'] = _game_lump_inventory(parsed)
        except ValueError as exc:
            result['game_lump_error'] = str(exc)
        pak = parsed.lump_bytes(40)
        result['pak_entries'] = []
        if pak:
            try:
                with zipfile.ZipFile(io.BytesIO(pak)) as archive:
                    result['pak_entries'] = [dict(name=item.filename, size=item.file_size,
                        compressed_size=item.compress_size, crc32=f'{item.CRC:08x}')
                        for item in archive.infolist()]
            except (zipfile.BadZipFile, NotImplementedError) as exc:
                result['pak_error'] = str(exc)
    else:
        result['lump_id'] = parsed.lump_id
    try:
        entities = entity_list(parsed)
    except ValueError as exc:
        result['entity_error'] = str(exc)
        return result
    classes = Counter(ent.one('classname') for ent in entities)
    result.update(entity_count=len(entities), classes=dict(sorted(classes.items(), key=lambda x: str(x[0]))),
                  io_count=len(io_signature(entities)))
    result['visual_entities'] = [dict(entity_index=i,
        pairs=[dict(key=p.key, value=p.value) for p in ent.pairs])
        for i, ent in enumerate(entities) if ent.one('classname') in VISUAL_CLASSES]
    result['io'] = [dict(entity_index=i, output=key, value=value)
                    for i, key, value in io_signature(entities)]
    result['resource_references'] = sorted({p.value for ent in entities for p in ent.pairs
        if p.key.lower() in {'filename', 'vscripts', 'material', 'overlaymaterial', 'model'}
        and p.value and not p.value.startswith('*')})
    return result


def difference_ranges(before, after, limit=256):
    """Exact changed-byte count with bounded range output, including added tails."""
    count, start, ranges = 0, None, []
    common = min(len(before), len(after))
    for base in range(0, common, 4096):
        left, right = before[base: min(base+4096, common)], after[base: min(base+4096, common)]
        if left == right:
            if start is not None:
                if len(ranges) < limit:
                    ranges.append([start, base])
                start = None
            continue
        for index, (a, b) in enumerate(zip(left, right), base):
            if a != b:
                count += 1
                if start is None:
                    start = index
            elif start is not None:
                if len(ranges) < limit:
                    ranges.append([start, index])
                start = None
    if len(before) != len(after):
        count += abs(len(before)-len(after))
        if start is None:
            start = common
    if start is not None and len(ranges) < limit:
        ranges.append([start, max(len(before), len(after))])
    return dict(changed_byte_count=count, ranges=ranges, ranges_limit=limit,
                ranges_may_be_truncated=len(ranges) == limit)


def compare_bytes(before, after, kind='bsp'):
    old, new = container(before, kind), container(after, kind)
    result = dict(input_sha256=sha256(before), output_sha256=sha256(after),
                  input_size=len(before), output_size=len(after), identical=before == after,
                  same_size=len(before) == len(after))
    result.update(difference_ranges(before, after))
    if kind == 'bsp':
        result['changed_lumps'] = [i for i in range(64) if old.lump_bytes(i) != new.lump_bytes(i)]
        result['non_entity_lumps_unchanged'] = all(i == 0 for i in result['changed_lumps'])
        result['header_unchanged'] = before[:1036] == after[:1036]
    else:
        old_start, old_size = old.entity_span
        new_start, new_size = new.entity_span
        payload_unchanged = before[old_start:old_start+old_size] == after[new_start:new_start+new_size]
        result['changed_lumps'] = [] if payload_unchanged else [0]
        result['non_entity_lumps_unchanged'] = True
        result['header_unchanged'] = before[:LMP_HEADER_SIZE] == after[:LMP_HEADER_SIZE]
    try:
        a, b = entity_list(old), entity_list(new)
        result['io_unchanged'] = io_signature(a) == io_signature(b)
        result['entity_count_unchanged'] = len(a) == len(b)
        result['entity_count'] = [len(a), len(b)]
    except ValueError as exc:
        result['entity_comparison_error'] = str(exc)
        result['io_unchanged'] = None
    return result
