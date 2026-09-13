"""Fail-closed native process, lighting audit, and verified VPK packaging boundaries."""
import hashlib
import io
import os
from pathlib import Path
import re
import struct
import subprocess
import zipfile

from .binary import BspFile
from .inspect import _game_lump_inventory


LIGHTING_LUMPS = frozenset({30, 31, 35, 40, 51, 53, 54, 55, 58, 59})
_NATIVE_VHV = re.compile(r'sp_hdr_[0-9]+\.vhv\Z')
_RESERVED = re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z', re.I)


def native(tool: Path, args: list[str], *, cwd: Path, log: Path, timeout: int) -> None:
    """Run an executable directly, retaining exclusive output even on failure."""
    tool, cwd, log = Path(tool).resolve(), Path(cwd).resolve(), Path(log)
    if tool.suffix.lower() != '.exe' and os.name == 'nt':
        raise ValueError('Native tools must be .exe files; shell scripts are unsupported')
    if tool.suffix.lower() in {'.bat', '.cmd', '.ps1', '.sh'}:
        raise ValueError('Shell scripts are unsupported native tools')
    if timeout <= 0:
        raise ValueError('Native timeout must be positive')
    if not tool.is_file():
        raise FileNotFoundError(tool)
    if not cwd.is_dir():
        raise NotADirectoryError(cwd)
    with log.open('xb') as output:
        subprocess.run([str(tool), *args], cwd=cwd, stdout=output,
                       stderr=subprocess.STDOUT, shell=False, check=True,
                       timeout=timeout,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or any(ord(c) < 32 for c in name):
        raise ValueError(f'Unsafe package path: {name!r}')
    if any(c in name for c in '\\:<>"|?*'):
        raise ValueError(f'Unsafe package path: {name!r}')
    parts = name.split('/')
    if any(p in {'', '.', '..'} or p.endswith((' ', '.')) or _RESERVED.fullmatch(p)
           for p in parts):
        raise ValueError(f'Unsafe package path: {name!r}')
    return name


def _detail_geometry(raw: bytes, version: int) -> tuple[bytes, int]:
    """v4: two counted dictionaries followed by counted 52-byte records.

    Valve Source SDK gamebspfile.h DetailObjectLump_t: bytes 28:37 are
    ColorRGBExp32, uint lightstyle index and byte lightstyle count. Origins,
    angles, model/leaf indices, sway/shape/type/orientation/scale stay exact.
    https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/public/gamebspfile.h
    """
    if version != 4:
        raise ValueError(f'Unsupported dprp version {version}')
    cursor = 0
    for width in (128, 32):
        if cursor + 4 > len(raw):
            raise ValueError('Truncated dprp dictionary count')
        count, = struct.unpack_from('<i', raw, cursor)
        cursor += 4
        if count < 0 or count > (len(raw) - cursor) // width:
            raise ValueError('Invalid dprp dictionary range')
        cursor += count * width
    if cursor + 4 > len(raw):
        raise ValueError('Truncated dprp record count')
    count, = struct.unpack_from('<i', raw, cursor)
    cursor += 4
    if count < 0 or len(raw) - cursor != count * 52:
        raise ValueError('Unsupported dprp record layout; expected exact 52-byte records')
    geometry = bytearray(raw)
    for offset in range(cursor, len(raw), 52):
        geometry[offset + 28:offset + 37] = b'\0' * 9
    return bytes(geometry), count


def _game_entries(parsed: BspFile) -> dict:
    inventory = _game_lump_inventory(parsed)
    if not isinstance(inventory, list):
        raise ValueError('Compressed game lumps are unsupported')
    entries, spans = {}, []
    for item in inventory:
        name = item['id']
        if 'unsupported' in item or item['flags'] != 0:
            raise ValueError(f'Unsupported game lump flags: {name}')
        if name in entries or '\ufffd' in name:
            raise ValueError(f'Duplicate or invalid game lump id: {name}')
        start, size = item['offset'], item['length']
        raw = parsed.data[start:start+size]
        entries[name] = (item['flags'], item['version'], raw)
        if size:
            spans.append((start, start + size))
    spans.sort()
    if any(b[0] < a[1] for a, b in zip(spans, spans[1:])):
        raise ValueError('Overlapping game sub-lumps')
    return entries


def _audit_game(before: BspFile, after: BspFile) -> dict:
    old, new = _game_entries(before), _game_entries(after)
    lighting = {'dplt', 'dplh'}
    if (old.keys() - new.keys() or
            [n for n in old if n not in lighting] != [n for n in new if n not in lighting]):
        raise ValueError('Game lump directory membership or order changed')
    for entries in (old, new):
        for name in lighting & entries.keys():
            flags, version, raw = entries[name]
            if version != 0 or len(raw) < 4:
                raise ValueError(f'Unsupported detail lighting stream: {name}')
            count, = struct.unpack_from('<i', raw)
            if count < 0 or len(raw) != 4 + count * 5:
                raise ValueError(f'Invalid detail lighting record count: {name}')
    detail_count = 0
    for name, (flags, version, raw) in old.items():
        new_flags, new_version, value = new[name]
        if (flags, version) != (new_flags, new_version):
            raise ValueError(f'Game lump metadata changed: {name}')
        if name == 'dprp':
            geometry, detail_count = _detail_geometry(raw, version)
            if geometry != _detail_geometry(value, new_version)[0]:
                raise ValueError('Detail prop placement or dictionary changed')
        elif name not in {'dplt', 'dplh'} and raw != value:
            raise ValueError(f'Protected game lump changed: {name}')
    return dict(sprp_payload_unchanged=True, detail_prop_placement_unchanged=True,
                detail_prop_count=detail_count,
                changed_game_lumps=[name for name in new if old.get(name) != new[name]])


def _pak_entries(raw: bytes) -> dict[str, bytes]:
    if not raw:
        return {}
    entries, aliases = {}, set()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                name = _safe_name(info.filename)
                if name != info.orig_filename or name.casefold() in aliases:
                    raise ValueError(f'Duplicate or aliased PAK path: {name}')
                aliases.add(name.casefold())
                entries[name] = archive.read(info)  # Validates CRC and local name.
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
        raise ValueError(f'Invalid or unsupported PAK: {exc}') from exc
    return entries


def _vhv_header(data: bytes) -> bytes:
    """Return all topology/padding with only mesh vertex-color streams masked.

    Valve materialsystem/hardwareverts.h v2: 40-byte header, 28-byte meshes;
    vradstaticprops.cpp aligns the color-stream partition to 512 bytes.
    """
    if len(data) < 40:
        raise ValueError('Truncated VHV topology header')
    version, checksum, flags, vertex_size, vertices, meshes = struct.unpack_from('<6I', data)
    if version != 2 or flags != 4 or vertex_size != 4:
        raise ValueError('Unsupported native HDR VHV format')
    if not vertices or not meshes or meshes > vertices or 40 + meshes * 28 > len(data):
        raise ValueError('Invalid VHV vertex or mesh counts')
    directory_end = 40 + meshes * 28
    position = (directory_end + 511) // 512 * 512
    if any(data[24:40]) or any(data[directory_end:position]):
        raise ValueError('Unexpected native VHV reserved bytes or padding')
    topology, total = bytearray(data), 0
    for index in range(meshes):
        offset = 40 + index * 28
        lod, count, start = struct.unpack_from('<3I', data, offset)
        end = start + count * vertex_size
        if start != position or end > len(data) or any(data[offset+12:offset+28]):
            raise ValueError('Invalid VHV mesh stream range or reserved bytes')
        topology[start:end] = b'\0' * (end - start)
        total += count
        position = end
    if total != vertices or len(data) != (position + 511) // 512 * 512 or any(data[position:]):
        raise ValueError('Invalid VHV aggregate vertex count or tail padding')
    return bytes(topology)


def _audit_pak(before: bytes, after: bytes, model_evidence=None) -> dict:
    old, new = _pak_entries(before), _pak_entries(after)
    removed, added = sorted(old.keys() - new.keys()), sorted(new.keys() - old.keys())
    if removed:
        raise ValueError(f'PAK entries removed: {removed}')
    changed = sorted(name for name in old if old[name] != new[name])
    migrations = []
    for name in changed + added:
        if not _NATIVE_VHV.fullmatch(name):
            raise ValueError(f'Non-lighting PAK entry changed or added: {name}')
        header = _vhv_header(new[name])
        if name in old and (header != _vhv_header(old[name]) or len(old[name]) != len(new[name])):
            if model_evidence is None:
                raise ValueError(f'VHV topology changed: {name}')
            migrations.append(model_evidence.validate(name, old[name], new[name]))
    return dict(pak_crc_verified=True, pak_added=added, pak_removed=removed,
                changed_pak_entries=changed, pak_entry_count=len(new),
                model_lighting_migrations=migrations)


def audit_bake(before: bytes, after: bytes, *, model_evidence=None) -> dict:
    """Reject any change outside understood lighting fields; never rewrite bytes."""
    old, new = BspFile.parse(before), BspFile.parse(after)
    if model_evidence is not None and model_evidence.input_sha256 != hashlib.sha256(before).hexdigest():
        raise ValueError('Model lighting evidence belongs to another bake input')
    if (old.version, old.revision) != (new.version, new.revision):
        raise ValueError('BSP version or revision changed')
    changed = []
    for index, (a, b) in enumerate(zip(old.lumps, new.lumps)):
        if (a.version, a.fourcc) != (b.version, b.fourcc):
            raise ValueError(f'Lump metadata changed: {index}')
        left, right = old.lump_bytes(index), new.lump_bytes(index)
        if left != right:
            changed.append(index)
            if index not in LIGHTING_LUMPS:
                raise ValueError(f'Protected lump changed: {index}')
            if a.fourcc != b'\0'*4:
                raise ValueError(f'Compressed changed lump is unsupported: {index}')
    left, right = old.lump_bytes(58), new.lump_bytes(58)
    if left or right:
        if old.lumps[58].version != 1 or old.lumps[58].fourcc != b'\0'*4:
            raise ValueError('Unsupported HDR face version/compression')
        if len(left) != len(right) or len(left) % 56:
            raise ValueError('HDR face count/layout changed; expected 56-byte records')
        # Valve bspfile.h dface_t: only styles[4] and lightofs (bytes 16:24).
        # Lightmap extents are geometry-derived and remain protected.
        for offset in range(0, len(left), 56):
            if (left[offset:offset+16] != right[offset:offset+16] or
                    left[offset+24:offset+56] != right[offset+24:offset+56]):
                raise ValueError(f'HDR face geometry changed at face {offset // 56}')
    report = dict(input_sha256=hashlib.sha256(before).hexdigest(),
                  output_sha256=hashlib.sha256(after).hexdigest(), changed_lumps=changed,
                  entities_byte_identical=True, protected_lumps_unchanged=True,
                  face_topology_unchanged=True, hdr_face_count=len(left) // 56)
    report.update(_audit_game(old, new))
    report.update(_audit_pak(old.lump_bytes(40), new.lump_bytes(40), model_evidence))
    return report


def package_files(payloads: dict[str, bytes], *, stage: Path, stem: str,
                  tools_dir: Path, game_dir: Path) -> dict:
    """Pack with official VPK and verify every extracted byte in a fresh directory."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', stem):
        raise ValueError('Package stem must contain only letters, numbers, underscores and hyphens')
    _safe_name(stem)
    if not payloads:
        raise ValueError('Cannot package an empty payload')
    aliases = set()
    for name, data in payloads.items():
        _safe_name(name)
        if not isinstance(data, bytes):
            raise ValueError(f'Package payload must be bytes: {name}')
        if name.casefold() in aliases:
            raise ValueError(f'Case-aliased package path: {name}')
        aliases.add(name.casefold())
    if any('/'.join(n.split('/')[:i]).casefold() in aliases
           for n in payloads for i in range(1, len(n.split('/')))):
        raise ValueError('Package file/directory path collision')
    stage, tools_dir, game_dir = Path(stage).resolve(), Path(tools_dir).resolve(), Path(game_dir).resolve()
    if any(stage.is_relative_to(root) for root in (tools_dir, game_dir)):
        raise ValueError('Package stage must be outside tool and game installations')
    candidates = [tools_dir/'vpk.exe', tools_dir/'bin/vpk.exe']
    tool = next((p for p in candidates if p.is_file()), None)
    if tool is None:
        raise FileNotFoundError(f'Official vpk.exe not found under {tools_dir}')
    package, verify = stage/stem, stage/(stem + '-verify')
    vpk = package.with_suffix('.vpk')
    build_log, extract_log = stage/(stem + '-vpk-build.log'), stage/(stem + '-vpk-extract.log')
    for path in (package, verify, vpk, build_log, extract_log):
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
    stage.mkdir(parents=True, exist_ok=True)
    package.mkdir()
    for name, data in payloads.items():
        destination = package/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as output:
            output.write(data)
    native(tool, [str(package)], cwd=stage, log=build_log, timeout=120)
    if not vpk.is_file():
        raise ValueError('Native VPK tool did not create the expected archive')
    verify.mkdir()
    for name in payloads:
        (verify/name).parent.mkdir(parents=True, exist_ok=True)
    native(tool, ['x', str(vpk), *payloads], cwd=verify, log=extract_log, timeout=120)
    for name, data in payloads.items():
        extracted = verify/name
        if not extracted.is_file() or extracted.read_bytes() != data:
            raise ValueError(f'Native VPK extraction verification failed: {name}')
    return dict(vpk=str(vpk), sha256=hashlib.sha256(vpk.read_bytes()).hexdigest(),
                files=[dict(name=n, size=len(d), sha256=hashlib.sha256(d).hexdigest())
                       for n, d in payloads.items()], native_extract_verified=True)
