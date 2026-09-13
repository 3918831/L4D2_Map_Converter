"""Pure, bounded handoff and audit for native L4D2 HDR cubemap capture.

No game files are written and no native commands are executed here. The supported
VTF variant is the measured C2 native capture: VTF 7.4, RGBA16161616F, 16/32 square,
one frame, seven stored faces (six cube faces and the legacy sphere fallback).
Other variants require a separate format audit before this boundary is extended.
"""

import hashlib
import io
import math
import re
import struct
import zipfile

from .binary import BspFile, FormatError


_TOKEN = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
_CUBE = re.compile(r'(?:cubemapdefault|c-?\d+_-?\d+_-?\d+)(?:\.hdr)?\.vtf\Z')
_HDR_SAMPLE = re.compile(r'c-?\d+_-?\d+_-?\d+\.hdr\.vtf\Z')


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _names(map_name: str, alias: str) -> tuple[str, str]:
    for kind, value in [('map_name', map_name), ('alias', alias)]:
        if not isinstance(value, str) or not _TOKEN.fullmatch(value):
            raise ValueError(f'{kind} must be a lowercase map token: letter then letters/digits/underscores, at most 64 characters')
    if map_name == alias:
        raise ValueError('Capture alias must differ from the original map name')
    return f'materials/maps/{map_name}/', f'materials/maps/{alias}/'


def _pak(bsp: BspFile) -> dict[str, bytes]:
    if bsp.lumps[40].fourcc != b'\0' * 4:
        raise FormatError('Compressed pak lump is unsupported')
    try:
        with zipfile.ZipFile(io.BytesIO(bsp.lump_bytes(40))) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise FormatError('Duplicate pak entries are ambiguous')
            if any(info.orig_filename != info.filename for info in archive.infolist()):
                raise FormatError('Noncanonical pak path was normalized by ZIP reader')
            for name in names:
                parts = name.split('/')
                if (name != name.lower() or '\\' in name or ':' in name or
                        any(part in ('', '.', '..') for part in parts) or
                        any(ord(char) < 32 or ord(char) > 126 for char in name)):
                    raise FormatError(f'Unsafe or noncanonical pak path: {name!r}')
            return {name: archive.read(name) for name in names}
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise FormatError(f'Invalid or unsupported BSP pak archive: {error}') from error


def _samples(bsp: BspFile) -> list[str]:
    data = bsp.lump_bytes(42)
    if bsp.lumps[42].fourcc != b'\0' * 4 or not data or len(data) % 16:
        raise FormatError('L42 cubemap samples must contain uncompressed 16-byte records')
    tails = [f'c{x}_{y}_{z}.hdr.vtf' for x, y, z, _ in struct.iter_unpack('<3iI', data)]
    if len(tails) != len(set(tails)):
        raise FormatError('Duplicate L42 sample coordinates are unsupported')
    return sorted(tails)


def _audit_hdr(data: bytes, *, name: str) -> dict:
    def reject(reason):
        raise FormatError(f'Unsupported VTF {name}: {reason}; expected audited 7.4 RGBA16F 16/32-square seven-face layout')

    if len(data) < 96 or data[:4] != b'VTF\0' or struct.unpack_from('<II', data, 4) != (7, 4):
        reject('version or truncated header')
    header, = struct.unpack_from('<I', data, 12)
    width, height, flags, frames, first = struct.unpack_from('<HHIHH', data, 16)
    fmt, = struct.unpack_from('<I', data, 52)
    mips = data[56]
    depth, = struct.unpack_from('<H', data, 63)
    count, = struct.unpack_from('<I', data, 68)
    if not (fmt == 24 and flags & 0x4000 and frames == 1 and first == 0 and depth == 1):
        reject('format, cube flag, frame or depth')
    if width != height or width not in (16, 32) or mips != width.bit_length():
        reject('dimensions or mip count')
    if count != 2 or header != 96:
        reject('resource table size')
    if struct.unpack_from('<IBB', data, 57) != (0xffffffff, 0, 0):
        reject('thumbnail image is unsupported by this resource layout')
    image_tag, image_offset, crc_tag, _ = struct.unpack_from('<IIII', data, 80)
    if (image_tag, image_offset, crc_tag) != (0x30, 96, 0x02435243):
        reject('resource table must contain high-resolution image then inline CRC')
    sizes = [max(1, width >> i) ** 2 * 8 for i in reversed(range(mips))]
    if len(data) != header + 7 * sum(sizes):
        reject('seven-face mip payload length')
    nonfinite_alpha = 0
    for pixel in struct.iter_unpack('<4e', data[header:]):
        if not all(math.isfinite(value) for value in pixel[:3]):
            raise FormatError(f'Nonfinite RGB in VTF {name}; refusing captured reflections')
        nonfinite_alpha += not math.isfinite(pixel[3])
    return {'version': [7, 4], 'width': width, 'height': height,
            'format': 'RGBA16161616F', 'mips': mips, 'frames': frames,
            'stored_faces': 7, 'cube_faces': 6, 'sphere_fallback_faces': 1,
            'finite_rgb_half_floats': True, 'nonfinite_alpha_count': nonfinite_alpha,
            'flags': flags}


def _context(bsp_data: bytes, map_name: str, alias: str):
    original_prefix, alias_prefix = _names(map_name, alias)
    bsp = BspFile.parse(bsp_data)
    pak = _pak(bsp)
    if any(name.startswith(alias_prefix) for name in pak):
        raise FormatError('Baseline already contains capture alias namespace; use a fresh alias')
    tails = _samples(bsp)
    actual = {name[len(original_prefix):] for name in pak
              if name.startswith(original_prefix) and _HDR_SAMPLE.fullmatch(name[len(original_prefix):])}
    if actual != set(tails):
        raise FormatError('Original HDR cubemap resources do not exactly match L42 sample coordinates')
    for tail in tails:
        _audit_hdr(pak[original_prefix + tail], name=original_prefix + tail)
    return bsp, pak, tails, original_prefix, alias_prefix


def prepare_capture(bsp_data: bytes, *, map_name: str, alias: str) -> tuple[dict[str, bytes], dict]:
    """Return byte-identical loose aliases for original map cubemap VTF resources."""
    _, pak, tails, original_prefix, alias_prefix = _context(bsp_data, map_name, alias)
    files, records = {}, []
    for name, data in sorted(pak.items()):
        if not name.startswith(original_prefix):
            continue
        tail = name[len(original_prefix):]
        if not _CUBE.fullmatch(tail):
            continue
        if len(data) < 24 or data[:4] != b'VTF\0' or not struct.unpack_from('<I', data, 20)[0] & 0x4000:
            raise FormatError(f'Original cubemap resource lacks VTF cube header: {name}')
        destination = alias_prefix + tail
        files[destination] = data
        records.append({'name': destination, 'source_pak_name': name,
                        'size': len(data), 'sha256': _sha(data)})
    return files, {'source_bsp_sha256': _sha(bsp_data), 'map_name': map_name,
                   'alias': alias, 'hdr_sample_count': len(tails), 'alias_count': len(files),
                   'capture_executed': False, 'bsp_modified': False,
                   'contents_byte_identical_to_original_pak': True, 'files': records}


def capture_replacements(baseline: bytes, captured: bytes, *, map_name: str,
                         alias: str) -> tuple[dict[str, bytes], dict]:
    """Audit native capture provenance and return sampled HDR replacement bytes.

    Directory metadata and every non-pak byte payload must be unchanged. Original
    ZIP contents must survive byte-for-byte; native additions are exactly the HDR
    L42 samples and optionally an identical LDR default. Runtime guard logs are
    not proof of capture: this audit is mandatory, and visual acceptance is manual.
    """
    source, old, tails, original_prefix, alias_prefix = _context(baseline, map_name, alias)
    result = BspFile.parse(captured)
    if (source.version, source.revision) != (result.version, result.revision):
        raise FormatError('Captured BSP version/revision differs from baseline')
    for index in range(64):
        if index == 40:
            if (source.lumps[index].version, source.lumps[index].fourcc) != (result.lumps[index].version, result.lumps[index].fourcc):
                raise FormatError('Captured pak lump metadata changed')
            continue
        if source.lumps[index] != result.lumps[index] or source.lump_bytes(index) != result.lump_bytes(index):
            raise FormatError(f'Captured non-pak lump {index} payload or directory changed; capture must use this exact baseline')
    new = _pak(result)
    removed = set(old) - set(new)
    if removed:
        raise FormatError(f'Original pak resources removed: {sorted(removed)}')
    for name, data in old.items():
        if new[name] != data:
            raise FormatError(f'Original pak resource changed: {name}')
    additions = set(new) - set(old)
    expected = {alias_prefix + tail for tail in tails}
    default = alias_prefix + 'cubemapdefault.vtf'
    if additions != expected and additions != expected | {default}:
        raise FormatError(f'Capture additions must exactly match L42 HDR samples plus optional default; missing={sorted(expected - additions)}, unexpected={sorted(additions - expected - {default})}')
    if default in additions and new[default] != old.get(original_prefix + 'cubemapdefault.vtf'):
        raise FormatError('Generated cubemap default differs from the original LDR default')
    replacements, details = {}, []
    for tail in tails:
        name = original_prefix + tail
        previous, current = old[name], new[alias_prefix + tail]
        before_info = _audit_hdr(previous, name=name)
        current_info = _audit_hdr(current, name=alias_prefix + tail)
        for field in ('version', 'width', 'height', 'format', 'mips', 'frames', 'stored_faces', 'flags'):
            if before_info[field] != current_info[field]:
                raise FormatError(f'Captured VTF layout differs from original for {name}: {field}')
        replacements[name] = current
        details.append({'name': name, 'before_sha256': _sha(previous),
                        'after_sha256': _sha(current), 'vtf': current_info})
    return replacements, {
        'baseline_sha256': _sha(baseline), 'capture_sha256': _sha(captured),
        'map_name': map_name, 'alias': alias, 'hdr_sample_count': len(tails),
        'nonpak_lumps_and_directory_unchanged': True,
        'all_original_pak_payloads_unchanged': True,
        'all_sample_coordinate_records_unchanged': True,
        'pak_added': sorted(additions), 'pak_removed': [],
        'modified_resources': details,
        'unchanged_hdr_sample_count': sum(item['before_sha256'] == item['after_sha256'] for item in details),
        'default_generated_identical_to_existing_ldr_default': default in additions,
        'ldr_and_default_resources_retained': True,
        'nonfinite_alpha_files': [item['name'] for item in details if item['vtf']['nonfinite_alpha_count']],
        'limitations': ['Only sampled HDR resources are replaced; LDR and default resources remain original.',
                       'Native nonfinite alpha is reported and retained unchanged; all stored RGB values are finite.',
                       'VTF audit supports only the measured 7.4 RGBA16F 16/32-square seven-face variant.',
                       'Binary provenance and finite RGB do not establish visual correctness; original-name runtime validation remains manual.']}


def audit_capture_log(text: str, *, alias: str, marker: str) -> dict:
    """Audit one run-bound guarded request, not native capture completion.

    A native reload can leave a zero-player session whose later checks refuse.
    Those refusals do not undo an earlier request. The captured BSP still needs
    capture_replacements; progress messages and logs cannot establish its bytes.
    Multiple requests are ambiguous because the log cannot bind BSP pixels to
    one request. Keep separate capture attempts instead of guessing which won.
    """
    for kind, value in [('alias', alias), ('marker', marker)]:
        if not isinstance(value, str) or not _TOKEN.fullmatch(value):
            raise ValueError(f'{kind} must be a lowercase token')
    prefix = marker.upper()
    map_matches = guarded = False
    request_line = None
    refusals = []
    reloaded = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith('---- Host_NewGame') or line.startswith('Host_NewGame on map '):
            map_matches = guarded = False
            reloaded = reloaded or request_line is not None
        if line == f'{prefix}_LOAD_REQUESTED':
            map_matches = guarded = False
        if line.startswith(f'{prefix}_MAP='):
            map_matches = line == f'{prefix}_MAP={alias}'
            guarded = False
        if f'{prefix}_REFUSED:' in line:
            refusals.append({'line': number, 'message': line})
            guarded = False
        if line == f'{prefix}_GUARD_PASSED_CAPTURE_NOT_VERIFIED':
            guarded = map_matches
        if line == f'{prefix}_CAPTURE_REQUESTED':
            if request_line is not None:
                raise ValueError('Capture log has multiple requests; BSP provenance is ambiguous. Use a fresh capture run')
            if not guarded:
                raise ValueError('Capture request lacks this run/map guard sequence; supply the generated control log')
            request_line = number
            guarded = False
    if request_line is None:
        raise ValueError('Capture log lacks this run/map/request marker; a refused check is not a capture request')
    return {'marker': marker, 'alias': alias, 'request_line': request_line,
            'refusals_before_request': [item for item in refusals if item['line'] < request_line],
            'refusals_after_request': [item for item in refusals if item['line'] > request_line],
            'native_reload_after_request': reloaded, 'capture_verified': False,
            'limitations': ['Guard markers establish only the requested starting state.',
                           'A post-request refusal does not prove the initial capture failed.',
                           'Strict BSP/resource audit and manual visual acceptance remain required.']}


def capture_controls(*, map_name: str, alias: str, marker: str,
                     exposure_max: float = 5) -> dict[str, bytes]:
    """Generate manual load/check/capture/finish controls with a per-run guard.

    Check and capture read actual tonemap entity NetProps. The user must set
    mat_specular 0 and wait for material reload before running the check/capture.
    Launch the full game process with -insecure before loading; sv_lan scopes the
    session to LAN and sv_cheats permits commands, neither verifies process flags.
    Capture only queues the native command once per map session; a native reload
    resets that guard. Archive before retrying. Finishing merely restores specular
    and prints state. A successful offline audit is still needed.
    """
    _names(map_name, alias)
    if not isinstance(marker, str) or not _TOKEN.fullmatch(marker):
        raise ValueError('marker must be a lowercase token of at most 64 letters/digits/underscores')
    if isinstance(exposure_max, bool) or not isinstance(exposure_max, (int, float)) or not math.isfinite(exposure_max) or exposure_max <= 0:
        raise ValueError('exposure_max must be a positive finite number')
    exposure = format(exposure_max, '.9g')
    prefix = marker.upper()
    guard = f'''// Fail closed if any required runtime API or property is unavailable.
local mapName = Director.GetMapName();
printl("{prefix}_MAP=" + mapName);
if (mapName != "{alias}")
    throw "{prefix}_REFUSED: expected exact alias {alias}";
local lan = Convars.GetFloat("sv_lan");
printl("{prefix}_LAN=" + lan);
if (lan == null || lan != 1)
    throw "{prefix}_REFUSED: use the local capture load CFG (sv_lan 1)";
if (!("GetListenServerHost" in getroottable()) || GetListenServerHost() == null)
    throw "{prefix}_REFUSED: local listen-server host must be connected; archive any earlier capture before retrying";
// LAN and cheats do not prove -insecure. Verify launcher flags and status manually.
local hdr = Convars.GetFloat("mat_hdr_level");
local specular = Convars.GetFloat("mat_specular");
printl("{prefix}_HDR=" + hdr + " SPECULAR=" + specular);
if (hdr == null || hdr != 2)
    throw "{prefix}_REFUSED: HDR level must be 2";
if (specular == null || specular != 0)
    throw "{prefix}_REFUSED: set mat_specular 0 and wait for material reload";
local exposure = Entities.FindByName(null, "tonemap_global");
if (exposure == null || exposure.GetClassname() != "env_tonemap_controller" ||
    Entities.FindByName(exposure, "tonemap_global") != null ||
    !NetProps.HasProp(exposure, "m_flCustomAutoExposureMax") ||
    !NetProps.HasProp(exposure, "m_bUseCustomAutoExposureMax"))
    throw "{prefix}_REFUSED: expected one tonemap_global controller with exposure NetProps";
local enabled = NetProps.GetPropInt(exposure, "m_bUseCustomAutoExposureMax");
local maximum = NetProps.GetPropFloat(exposure, "m_flCustomAutoExposureMax");
printl("{prefix}_EXPOSURE_ENABLED=" + enabled + " EXPOSURE_MAX=" + maximum);
if (enabled != 1 || maximum != {exposure})
    throw "{prefix}_REFUSED: unexpected actual exposure state";
printl("{prefix}_GUARD_PASSED_CAPTURE_NOT_VERIFIED");
'''
    # Inline the guard so a nested script loader cannot swallow a guard failure.
    capture = guard + f'''if ("L4D2BspCaptureRequested" in getroottable())
    throw "{prefix}_REFUSED: capture already requested in this map session";
::L4D2BspCaptureRequested <- "{marker}";
printl("{prefix}_CAPTURE_REQUESTED");
SendToConsole("buildcubemaps");
// No return/echo implies success. Archive the BSP and run finish-capture audit.
'''
    common = f'fs_warning_level 0\ncon_logfile {marker}_capture.log\n'
    files = {
        f'cfg/{marker}_load.cfg': common + f'// Restart the normal launcher with process option -insecure before this CFG.\n// sv_lan and sv_cheats are not substitutes for -insecure.\necho {prefix}_LOAD_REQUESTED\nsv_lan 1\nsv_cheats 1\nmat_hdr_level 2\nmap {alias} coop\n',
        f'cfg/{marker}_check.cfg': common + f'status\nsv_cheats 1\nscript_execute {marker}_check\n',
        f'cfg/{marker}_capture.cfg': common + f'status\nsv_cheats 1\nscript_execute {marker}_capture\n',
        f'cfg/{marker}_finish.cfg': common + f'mat_specular 1\nmat_specular\nmat_hdr_level\nstatus\necho {prefix}_USER_FINISHED_CAPTURE_NOT_VERIFIED\n// Exit the game, then use collect-capture to archive the BSP and log.\n',
        f'scripts/vscripts/{marker}_check.nut': guard,
        f'scripts/vscripts/{marker}_capture.nut': capture,
    }
    return {name: content.encode('ascii') for name, content in files.items()}
