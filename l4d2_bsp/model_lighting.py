"""Bounded L4D2 model evidence for rebuilding stale static-prop light caches.

MDL v49 / VVD v4 / DX90 VTX v7 only. This checks the fields needed to
derive VHV strip-group order; it is not a general model or runtime loader.
Valve public references: studio.h, optimize.h, vrad/vradstaticprops.cpp.
"""
import hashlib
from pathlib import Path
import struct

from .binary import BspFile
from .resources import read_vpk_entry, vpk_index


def _span(data, start, count, width):
    if start < 0 or count < 0 or start > len(data) or count > (len(data)-start)//width:
        raise ValueError('Model data range is invalid or truncated')
    return range(start, start+count*width, width)


def _unpack(fmt, data, start=0):
    _span(data, start, 1, struct.calcsize(fmt))
    return struct.unpack_from(fmt, data, start)


def _children(data, parent, count, relative, width, minimum):
    if count and relative < minimum:
        raise ValueError('Invalid model relative pointer')
    return _span(data, parent+relative, count, width)


def model_layout(mdl, vvd, vtx):
    """Return checksum and ordered (LOD, vertex-count) VHV groups from resources."""
    magic, version, checksum = _unpack('<4sII', mdl)
    if magic != b'IDST' or version != 49 or len(mdl) < 408 or _unpack('<i', mdl, 76)[0] != len(mdl):
        raise ValueError('Unsupported or truncated L4D2 MDL v49')
    magic, version, vvd_sum, lods = _unpack('<4sIIi', vvd)
    if magic != b'IDSV' or version != 4 or not 1 <= lods <= 8:
        raise ValueError('Unsupported VVD format or LOD count')
    if vvd_sum != checksum or _unpack('<I', vtx, 16)[0] != checksum:
        raise ValueError('MDL/VVD/VTX checksum mismatch')
    if _unpack('<i', vtx)[0] != 7 or _unpack('<i', vtx, 20)[0] != lods:
        raise ValueError('Unsupported DX90 VTX v7 or inconsistent LOD count')
    counts = _unpack('<8i', vvd, 16)
    if counts[0] <= 0 or any(n < 0 for n in counts) or any(a < b for a, b in zip(counts, counts[1:])):
        raise ValueError('Invalid VVD vertex counts')
    fixups, fix_start, verts, tangents = _unpack('<4i', vvd, 48)
    if verts < 64 or tangents < verts + counts[0]*48:
        raise ValueError('Invalid VVD vertex or tangent offset')
    _span(vvd, verts, counts[0], 48)
    _span(vvd, tangents, counts[0], 16)
    if fixups and fix_start + fixups*12 > verts:
        raise ValueError('VVD fixup table overlaps vertex data')
    reconstructed = [0]*lods
    for f in _children(vvd, 0, fixups, fix_start, 12, 64):
        lod, first, count = _unpack('<3i', vvd, f)
        if not 0 <= lod < lods or first < 0 or count < 0 or first+count > counts[0]:
            raise ValueError('Invalid VVD fixup range')
        for root_lod in range(lod+1):
            reconstructed[root_lod] += count
    if fixups and reconstructed != list(counts[:lods]):
        raise ValueError('VVD fixup output does not match declared LOD vertex counts')
    body_count, body_start = _unpack('<2i', mdl, 232)
    vb_count, vb_start = _unpack('<2i', vtx, 28)
    if body_count != vb_count or body_count <= 0:
        raise ValueError('MDL/VTX body count mismatch')
    bodies = _children(mdl, 0, body_count, body_start, 16, 408)
    vbodies = _children(vtx, 0, vb_count, vb_start, 8, 36)
    groups = []
    for body, vbody in zip(bodies, vbodies):
        _, models, _, model_start = _unpack('<4i', mdl, body)
        vm_count, vm_start = _unpack('<2i', vtx, vbody)
        if models != vm_count or models <= 0:
            raise ValueError('MDL/VTX model count mismatch')
        for model, vmodel in zip(_children(mdl, body, models, model_start, 148, 16),
                                 _children(vtx, vbody, vm_count, vm_start, 8, 8)):
            meshes, mesh_start, vertices, vertex_start = _unpack('<4i', mdl, model+72)
            if vertices <= 0 or vertex_start < 0 or vertex_start % 48 or vertex_start//48+vertices > counts[0]:
                raise ValueError('Invalid MDL model vertex range')
            mesh_rows = list(_children(mdl, model, meshes, mesh_start, 116, 148))
            lod_count, lod_start = _unpack('<2i', vtx, vmodel)
            if lod_count != lods:
                raise ValueError('VTX model LOD count mismatch')
            for lod, vlod in enumerate(_children(vtx, vmodel, lod_count, lod_start, 12, 8)):
                mesh_count, vm_start = _unpack('<2i', vtx, vlod)
                if mesh_count != meshes:
                    raise ValueError('MDL/VTX mesh count mismatch')
                for mesh, vmesh in zip(mesh_rows, _children(vtx, vlod, mesh_count, vm_start, 9, 12)):
                    mesh_vertices, mesh_first = _unpack('<2i', mdl, mesh+8)
                    if mesh_vertices < 0 or mesh_first < 0 or mesh_first+mesh_vertices > vertices:
                        raise ValueError('Invalid MDL mesh vertex range')
                    group_count, group_start = _unpack('<2i', vtx, vmesh)
                    for group in _children(vtx, vmesh, group_count, group_start, 25, 9):
                        num, first, ni, indices, ns, strips = _unpack('<6i', vtx, group)
                        for vertex in _children(vtx, group, num, first, 9, 25):
                            if _unpack('<H', vtx, vertex+4)[0] >= mesh_vertices:
                                raise ValueError('VTX vertex exceeds MDL mesh vertex range')
                        for index in _children(vtx, group, ni, indices, 2, 25):
                            if _unpack('<H', vtx, index)[0] >= num:
                                raise ValueError('VTX index exceeds strip-group vertex range')
                        for strip in _children(vtx, group, ns, strips, 27, 25):
                            n, i, nv, v, bones, flags, changes, changes_start = _unpack('<4ihB2i', vtx, strip)
                            if min(n, i, nv, v, bones, changes) < 0 or i+n > ni or v+nv > num:
                                raise ValueError('Invalid VTX strip range')
                            _children(vtx, strip, changes, changes_start, 8, 27)
                        groups.append((lod, num))
    if not groups or not sum(n for _, n in groups):
        raise ValueError('Empty model lighting layout')
    return checksum, groups


class ResourceSnapshot:
    """Configured roots in order; reject ambiguous files within a root.

    This is evidence for the native result, not proof of runtime mounts.
    Embedded BSP resources take precedence and remain protected by PAK audit.
    """
    def __init__(self, roots, embedded):
        self.roots = [Path(p).resolve() for p in roots]
        self.embedded = {n.casefold(): d for n, d in embedded.items()}
        self.indices = {}
        self.used = {}

    def read(self, name):
        from .native import _safe_name
        _safe_name(name)
        if name in self.used:
            return self.used[name][0]
        candidates = []
        if name in self.embedded:
            candidates = [(self.embedded[name], 'bsp:pak/'+name)]
        else:
            for root in self.roots:
                loose = root/name
                if not loose.resolve().is_relative_to(root):
                    raise ValueError('Model resource escapes configured root')
                if loose.is_file():
                    candidates.append((loose.read_bytes(), str(loose)))
                if root not in self.indices:
                    self.indices[root] = [(p, vpk_index(p)) for p in sorted(root.glob('*_dir.vpk'))]
                for p, index in self.indices[root]:
                    if name in index:
                        candidates.append((read_vpk_entry(index[name]), str(p)+'::'+name))
                if candidates:
                    break
        if not candidates:
            raise ValueError(f'Missing model resource: {name}')
        data, origin = candidates[0]
        if any(other != data for other, _ in candidates):
            raise ValueError(f'Ambiguous model resources in one search root: {name}')
        self.used[name] = (data, {'name': name, 'origin': origin, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
        return data

    def inventory(self):
        return [self.used[n][1] for n in sorted(self.used)]

    def verify_current(self):
        verify_resource_inventory(self.roots, self.embedded, self.inventory())


def verify_resource_inventory(roots, embedded, inventory):
    current = ResourceSnapshot(roots, embedded)
    for expected in inventory:
        try:
            current.read(expected['name'])
        except (OSError, ValueError) as exc:
            raise ValueError(f"Model resource changed or missing: {expected['name']}") from exc
        if current.used[expected['name']][1] != expected:
            raise ValueError(f"Model resource changed: {expected['name']}")


def _prop_models(parsed):
    from .native import _game_entries, _safe_name
    entry = _game_entries(parsed).get('sprp')
    if entry is None or entry[:2] != (0, 9):
        raise ValueError('Model migration requires L4D2 sprp v9')
    raw = entry[2]
    count = _unpack('<i', raw)[0]
    names = []
    for offset in _span(raw, 4, count, 128):
        field = raw[offset:offset+128]
        if b'\0' not in field:
            raise ValueError('Unterminated sprp model name')
        name = field.split(b'\0', 1)[0].decode('ascii').replace('\\', '/').lower()
        _safe_name(name)
        if not name.startswith('models/') or not name.endswith('.mdl'):
            raise ValueError('Invalid sprp model resource name')
        names.append(name)
    cursor = 4+count*128
    leaves = _unpack('<i', raw, cursor)[0]
    _span(raw, cursor+4, leaves, 2)
    cursor += 4+leaves*2
    props = _unpack('<i', raw, cursor)[0]
    cursor += 4
    if props < 0 or len(raw)-cursor != props*72:
        raise ValueError('Unsupported sprp v9 record layout')
    result = []
    for offset in _span(raw, cursor, props, 72):
        index = _unpack('<H', raw, offset+24)[0]
        if index >= len(names):
            raise ValueError('Invalid sprp model index')
        result.append(names[index])
    return result


class ModelLightingEvidence:
    """Freeze current model families before VRAD; authorize only proven upgrades."""
    def __init__(self, before, roots):
        from .native import _pak_entries, _NATIVE_VHV, _vhv_header
        self.input_sha256 = hashlib.sha256(before).hexdigest()
        parsed = BspFile.parse(before)
        entries = _pak_entries(parsed.lump_bytes(40))
        self.resources = ResourceSnapshot(roots, entries)
        self.models = _prop_models(parsed)
        self.layouts = {}
        for name, data in entries.items():
            if not _NATIVE_VHV.fullmatch(name):
                continue
            _vhv_header(data)
            index = int(name[7:-4])
            if index >= len(self.models):
                raise ValueError(f'VHV refers to missing static prop: {name}')
            model = self.models[index]
            mdl = self.resources.read(model)
            if _unpack('<4sI', mdl) != (b'IDST', 49):
                raise ValueError(f'Unsupported current MDL: {model}')
            old_sum = _unpack('<I', data, 4)[0]
            checksum = _unpack('<I', mdl, 8)[0]
            # A matching MDL checksum does not freeze its separate vertex and
            # topology files. VRAD reads these even without a cache migration.
            vvd = self.resources.read(model[:-4]+'.vvd')
            vtx = self.resources.read(model[:-4]+'.dx90.vtx')
            if old_sum != checksum and model not in self.layouts:
                self.layouts[model] = model_layout(mdl, vvd, vtx)

    def validate(self, name, before, after):
        model = self.models[int(name[7:-4])]
        old_sum, new_sum = [_unpack('<I', data, 4)[0] for data in (before, after)]
        if old_sum == new_sum or model not in self.layouts:
            raise ValueError(f'Unproven VHV topology change: {name}')
        checksum, groups = self.layouts[model]
        actual = [_unpack('<2I', after, 40+28*i) for i in range(_unpack('<I', after, 20)[0])]
        if new_sum != checksum or actual != groups:
            raise ValueError(f'VHV layout/checksum does not match current model: {name} ({model})')
        return {'vhv': name, 'model': model, 'old_checksum': old_sum, 'new_checksum': new_sum,
                'old_meshes': _unpack('<I', before, 20)[0], 'new_meshes': len(groups),
                'current_model_layout_verified': True}

    def inventory(self):
        return self.resources.inventory()

    def verify_current(self):
        self.resources.verify_current()
