"""Bounded MDL v49 material-directory copies; geometry stays byte-identical.

Valve studio.h describes texture names relative to mstudiotexture_t and CD
directory offsets relative to studiohdr_t. Offsets here are the L4D2 x86 v49
layout, separately checked against installed resources, not the SDK's version.
"""
import re
import struct

from .model_lighting import _span, _unpack


def _string(data, offset):
    if not 0 <= offset < len(data):
        raise ValueError('Invalid MDL string offset')
    end = data.find(b'\0', offset)
    if end < 0 or end-offset > 1024:
        raise ValueError('Unterminated or oversized MDL string')
    return bytes(data[offset:end]).decode('ascii')


def material_table(data):
    if (len(data) < 408 or _unpack('<4sI', data) != (b'IDST', 49)
            or _unpack('<i', data, 76)[0] != len(data)):
        raise ValueError('Material policy requires valid L4D2 MDL v49')
    count, start, dirs, directory = _unpack('<4i', data, 204)
    if count > 4096 or dirs > 128 or (count and start < 408) or (dirs and directory < 408):
        raise ValueError('Unsupported MDL material table')
    names = [_string(data, i+_unpack('<i', data, i)[0]) for i in _span(data,start,count,64)]
    paths = [_string(data, _unpack('<i',data,i)[0]) for i in _span(data,directory,dirs,4)]
    for name in names:
        if not re.fullmatch(r'[A-Za-z0-9_./\\-]+', name) or '..' in name or name.startswith(('/', '\\')):
            raise ValueError('Unsafe MDL material name')
    for path in paths:
        if path and (not re.fullmatch(r'[A-Za-z0-9_./\\-]+',path) or '..' in path or path.startswith(('/', '\\'))):
            raise ValueError('Unsafe MDL material directory')
    return names, paths


def clone_model(data, name, directory):
    material_table(data)
    if (not re.fullmatch(r'models/lmc/[a-z0-9_/]+\.mdl', name)
            or not re.fullmatch(r'lmc/[a-z0-9_/]+/', directory)):
        raise ValueError('Model copy requires a private lmc namespace')
    internal = name.removeprefix('models/').encode('ascii')
    if len(internal) > 63:
        raise ValueError('Private internal model name exceeds 63 bytes')
    # Static props only; embedded/static sequence data remains untouched.
    if not _unpack('<I',data,152)[0] & 16 or _unpack('<i',data,336)[0] or _unpack('<i',data,352)[0]:
        raise ValueError('Nonstatic model or external animations/includes require review')
    out=bytearray(data)
    table=len(out);out.extend(struct.pack('<i',table+4));out.extend(directory.encode('ascii')+b'\0')
    out[12:76]=internal.ljust(64,b'\0')
    struct.pack_into('<i',out,76,len(out))
    struct.pack_into('<2i',out,212,1,table)
    # Only name, length and directory locator may change in the original bytes.
    proof=bytearray(out[:len(data)])
    for start,end in ((12,80),(212,220)):
        proof[start:end]=data[start:end]
    if bytes(proof)!=data or material_table(out)[0]!=material_table(data)[0]:
        raise ValueError('Model copy changed data outside allowed fields')
    return bytes(out)


def check_vtx_materials(data):
    if _unpack('<i',data)[0]!=7:
        raise ValueError('Material policy requires DX90 VTX v7')
    count,start=_unpack('<2i',data,20)
    if not 1 <= count <= 8 or start < 36:
        raise ValueError('Invalid VTX material replacement table')
    for i in _span(data,start,count,8):
        if _unpack('<i',data,i)[0]!=0:
            raise ValueError('LOD material replacement requires review')
