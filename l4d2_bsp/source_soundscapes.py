"""Private source ambience with catalogued weather layers removed.

No global script overrides, donor ambience, map-name classification or guessed
deletions. Ordered KeyValues preserve repeated loops, random pools and positions.
"""
import hashlib
import io
import re
import struct
import zipfile

from .atmosphere_analysis import entity_value
from .binary import BspFile
from .model_lighting import ResourceSnapshot, verify_resource_inventory
from .native import _pak_entries, _safe_name
from .style import _read


POLICY = 'source-dry-v1'
CONTROLLERS = {'env_soundscape', 'env_soundscape_triggerable'}
# Exact asset identities audited in installed soundscape scripts. In particular,
# drainage water and rooftop TRAIN ambience are not weather despite substrings.
WEATHER_WAVES = {
    'ambient/ambience/conduit_rain.wav', 'ambient/ambience/rain_on_tarp.wav',
    'ambient/spacial_loops/1b_rainontarp_loop.wav',
    'ambient/weather/crucial_rumble_rain.wav', 'ambient/weather/crucial_rumble_rain_nowind.wav',
    'ambient/ambience/rainscapes/distinctrain_hard_loop.wav',
    'ambient/ambience/rainscapes/hail_hard_loop.wav',
    'ambient/ambience/rainscapes/interior_rain_med_loop.wav',
    'ambient/ambience/rainscapes/metal_rainverb_med_loop.wav',
    'ambient/ambience/rainscapes/metalrain_hard_loop.wav',
    'ambient/ambience/rainscapes/metalrain_med_loop.wav',
    *(f'ambient/ambience/rainscapes/thunder_distant{i:02}.wav' for i in range(1, 4)),
    *(f'ambient/ambience/rainscapes/rain/stereo_gust_{i:02}.wav' for i in range(1, 7)),
    *(f'ambient/ambience/rainscapes/rain/mono_gust_{i:02}.wav' for i in range(1, 5)),
    *(f'ambient/ambience/rainscapes/rain/debris_{i:02}.wav' for i in (2, 3, 4, 5, 7, 8)),
    *(f'ambient/ambience/rainscapes/{name}.wav' for name in (
        'crucial_int_rainverb_hard_loop', 'crucial_int_rainverb_med_loop',
        'crucial_surfacerain_hard_loop', 'crucial_surfacerain_light_loop',
        'crucial_surfacerain_med_loop', 'crucial_waterrain_hard_loop', 'crucial_waterrain_med_loop')),
    *(f'ambient/ambience/rainscapes/rain/{name}.wav' for name in (
        'crucial_int_rainverb_hard_loop', 'crucial_int_rainverb_med_loop',
        'crucial_surfacerain_light_loop', 'crucial_surfacerain_med_loop',
        'crucial_waterrain_light_loop', 'interior_rain_med_loop',
        'crucial_wind_rain_loop', 'heavy_wind01', 'whistle_debris_loop')),
}
_TOKEN = re.compile(r'//[^\r\n]*|/\*[\s\S]*?\*/|"[^"\r\n]*"|[{}]|[^\s{}"]+')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def parse_keyvalues(data):
    """Bounded ordered script reader; no include/conditional expansion."""
    if len(data) > 4 * 1024 * 1024:
        raise ValueError('Soundscape script exceeds supported size')
    text = data.decode('utf-8-sig') if data.startswith(b'\xef\xbb\xbf') else data.decode('latin1')
    tokens, end = [], 0
    for m in _TOKEN.finditer(text):
        if text[end:m.start()].strip():
            raise ValueError('Unsupported soundscape syntax')
        end = m.end()
        raw = m[0]
        if raw.startswith('//') or (raw.startswith('/*') and raw.endswith('*/')):
            continue
        value = raw[1:-1] if raw.startswith('"') else raw
        if '\0' in value or '\\"' in raw or raw.startswith(('/*', '#', '[')):
            raise ValueError('Unsupported soundscape directive, conditional or escape')
        tokens.append(value)
    if text[end:].strip():
        raise ValueError('Truncated soundscape token')
    cursor = 0

    def block(depth=0):
        nonlocal cursor
        if depth > 32:
            raise ValueError('Soundscape nesting limit exceeded')
        result = []
        while cursor < len(tokens):
            key = tokens[cursor]
            cursor += 1
            if key == '}':
                if depth:
                    return result
                raise ValueError('Unexpected soundscape closing brace')
            if key == '{' or cursor == len(tokens):
                raise ValueError('Truncated soundscape key/value')
            value = tokens[cursor]
            cursor += 1
            if value == '}':
                raise ValueError('Missing soundscape value')
            result.append((key, block(depth + 1) if value == '{' else value))
        if depth:
            raise ValueError('Unclosed soundscape block')
        return result

    return block()


def _serialize(pairs, depth=0):
    lines = []
    for key, value in pairs:
        if any(c in key for c in ('"', '\n', '\r', '\0')):
            raise ValueError('Unsafe soundscape key')
        if isinstance(value, list):
            lines.extend(('\t' * depth + f'"{key}"', '\t' * depth + '{',
                          _serialize(value, depth + 1), '\t' * depth + '}'))
        else:
            if any(c in value for c in ('"', '\n', '\r', '\0')):
                raise ValueError('Unsafe soundscape value')
            lines.append('\t' * depth + f'"{key}" "{value}"')
    return '\n'.join(lines)


def wave_path(value):
    # Source sound-character prefixes alter playback; leave original values in
    # the emitted script and strip them only for catalog lookup/resource checks.
    name = value.lstrip('*#@><^)}?$!(').replace('\\', '/').lower()
    _safe_name(name)
    return name


def _optional(snapshot, name):
    try:
        return snapshot.read(name)
    except ValueError as exc:
        if str(exc) != f'Missing model resource: {name}':
            raise
        return None


def plan_source_soundscapes(source, modes, roots, map_name):
    if not re.fullmatch(r'[a-z0-9_-]{1,64}', map_name):
        raise ValueError('Invalid source soundscape map name')
    names = set()
    for data, kind in [(source, 'bsp'), *((raw, 'lmp') for raw in modes.values())]:
        for e in _read(data, kind)[2]:
            if entity_value(e, 'classname') in CONTROLLERS:
                name = entity_value(e, 'soundscape')
                if name:
                    names.add(name.lower())
    snapshot = ResourceSnapshot(roots, _pak_entries(BspFile.parse(source).lump_bytes(40)))
    definitions, provenance = {}, {}
    local_path = f'scripts/soundscapes_{map_name}.txt'
    local_absent = None
    local = None

    def register(data, path):
        for name, tree in parse_keyvalues(data):
            if not isinstance(tree, list):
                raise ValueError(f'Invalid soundscape definition in {path}: {name}')
            if not tree:
                continue  # Source does not register empty top-level definitions.
            key = name.lower()
            definitions.setdefault(key, []).append(tree)
            provenance.setdefault(key, []).append(path)

    if names:
        manifest = parse_keyvalues(snapshot.read('scripts/soundscapes_manifest.txt'))
        # Valve's installed L4D2 manifest spells this root "soundscaples".
        if len(manifest) != 1 or manifest[0][0].lower() not in ('soundscapes_manifest', 'soundscaples_manifest') or not isinstance(manifest[0][1], list):
            raise ValueError('Unsupported soundscape manifest')
        paths = set()
        for key, path in manifest[0][1]:
            if key.lower() != 'file' or not isinstance(path, str):
                raise ValueError('Unsupported soundscape manifest entry')
            path = path.replace('\\', '/').lower()
            if not re.fullmatch(r'scripts/[a-z0-9_/-]+\.txt', path):
                raise ValueError('Invalid soundscape script path')
            register(snapshot.read(path), path)
            paths.add(path)
        local = _optional(snapshot, local_path)
        local_absent = local is None
        if local is not None and local_path not in paths:
            register(local, local_path)

    mapping, finished, visiting, removed, warnings = {}, {}, set(), [], []
    prefix = 'lmc_src_' + _sha(map_name.encode('ascii') + source)[:16]

    def private(name):
        return prefix + '.' + _sha(name.encode('latin1'))[:16]

    def reject_weather(value, owner):
        path = wave_path(value)
        if path in WEATHER_WAVES:
            removed.append(dict(soundscape=owner, wave=value, resource=path, reason='catalogued_weather_wave'))
            return True
        if any(word in path for word in ('rain', 'thunder', 'storm', 'hail')):
            warning = f'Uncatalogued weather-like wave retained: {owner}/{value}'
            if warning not in warnings:
                warnings.append(warning)
        # Runtime may deliberately contain unavailable optional waves. Preserve
        # them and report rather than fabricate audio or silently drop a layer.
        if _optional(snapshot, 'sound/' + path) is None:
            warning = f'Missing source wave retained: {owner}/{value}'
            if warning not in warnings:
                warnings.append(warning)
        return False

    def layer(key, pairs, owner):
        if key == 'playsoundscape':
            refs = [v for k, v in pairs if k.lower() == 'name']
            if len(refs) != 1 or not isinstance(refs[0], str):
                raise ValueError('Ambiguous nested soundscape name')
            ref = refs[0].lower()
            visit(ref)
            return [(k, private(ref) if k.lower() == 'name' else v) for k, v in pairs]
        if key not in ('playlooping', 'playrandom'):
            raise ValueError(f'Unsupported soundscape block: {owner}/{key}')
        selectors = [(k.lower(), v) for k, v in pairs if k.lower() in ('wave', 'rndwave')]
        expected = 'wave' if key == 'playlooping' else 'rndwave'
        if (len(selectors) > 1 or any(k != expected for k, v in selectors)
                or any(not isinstance(v, str if expected == 'wave' else list) for k, v in selectors)):
            raise ValueError(f'Ambiguous or unsupported playback selector: {owner}/{key}')
        result, audio = [], 0
        for k, value in pairs:
            if k.lower() == 'wave' and isinstance(value, str):
                if reject_weather(value, owner):
                    if key == 'playlooping':
                        return None
                    continue
                audio += 1
            elif k.lower() == 'rndwave' and isinstance(value, list):
                if any(not isinstance(b, str) for a, b in value):
                    raise ValueError('Unsupported soundscape random pool')
                value = [(a, b) for a, b in value if not reject_weather(b, owner)]
                audio += len(value)
            elif isinstance(value, list):
                raise ValueError(f'Unsupported nested soundscape parameter: {k}')
            result.append((k, value))
        return result if audio else None

    def visit(name):
        if name in visiting:
            raise ValueError(f'Soundscape reference cycle: {name}')
        if name in finished:
            return
        if len(visiting) >= 64 or len(finished) >= 4096:
            raise ValueError('Soundscape graph exceeds supported limit')
        candidates = definitions.get(name)
        if not candidates:
            raise ValueError(f'Missing soundscape definition: {name}')
        # Source's FindSoundscapeByName scans from the end: last-loaded wins.
        # Preserve manifest order and repeated definitions, including stock
        # L4D2 overlaps. Resource files within one mount remain ambiguity-checked.
        visiting.add(name)
        result = []
        for key, value in candidates[-1]:
            if isinstance(value, list):
                value = layer(key.lower(), value, name)
                if value is None:
                    continue
            result.append((key, value))
        visiting.remove(name)
        # Source ignores entirely empty top-level definitions. Keep a concrete
        # silent entry when the only playback layers were weather. An empty
        # looping block has neither a wave nor volume, and leaves DSP untouched.
        finished[name] = result or [('playlooping', [])]

    for name in sorted(names):
        visit(name)
        mapping[name] = private(name)
    script = _serialize([(private(n), finished[n]) for n in sorted(finished)]) + '\n' if finished else ''
    payload = (local.decode('latin1') + '\n' if local else '') + script if script else ''
    snapshot.verify_current()
    return dict(schema_version=1, policy=POLICY, map_name=map_name,
                source_sha256=_sha(source), mode_sha256={k: _sha(v) for k, v in sorted(modes.items())},
                mapping=mapping, script=script, script_sha256=_sha(script.encode('latin1')),
                payload_script=payload, payload_sha256=_sha(payload.encode('latin1')),
                definitions=[dict(source=n, generated=private(n), scripts=provenance[n],
                                  selected_occurrence=len(provenance[n]) - 1) for n in sorted(finished)],
                removed_layers=removed, warnings=warnings, source_inventory=snapshot.inventory(),
                map_script_absent=local_absent)


def verify_source_soundscapes(plan, roots, embedded):
    if (plan.get('schema_version') != 1 or plan.get('policy') != POLICY
            or _sha(plan['script'].encode('latin1')) != plan.get('script_sha256')
            or _sha(plan['payload_script'].encode('latin1')) != plan.get('payload_sha256')):
        raise ValueError('Source soundscape policy/script identity changed')
    verify_resource_inventory(roots, embedded, plan['source_inventory'])
    if plan.get('map_script_absent'):
        current = ResourceSnapshot(roots, embedded)
        if _optional(current, f'scripts/soundscapes_{plan["map_name"]}.txt') is not None:
            raise ValueError('Source map soundscape script changed from absent to present')


def source_soundscape_assets(plan, map_name):
    if not re.fullmatch(r'[a-z0-9_-]{1,64}', map_name):
        raise ValueError('Invalid soundscape package map name')
    raw = plan['payload_script'].encode('latin1')
    if _sha(raw) != plan['payload_sha256']:
        raise ValueError('Source soundscape script identity changed')
    return {f'scripts/soundscapes_{map_name}.txt': raw} if raw else {}


def embed_source_soundscapes(data, plan):
    """Bind the generated definition in BSP too, avoiding an old embedded map
    script shadowing the VPK script. Original definitions remain a byte prefix.
    All non-PAK lumps and every unrelated PAK resource remain unchanged.
    """
    parsed = BspFile.parse(data)
    entries = _pak_entries(parsed.lump_bytes(40))
    assets = source_soundscape_assets(plan, plan['map_name'])
    if not assets:
        return data, dict(input_sha256=_sha(data), output_sha256=_sha(data),
                          paths=[], unrelated_pak_payloads_unchanged=True)
    for name, value in assets.items():
        if name in entries and not value.startswith(entries[name]):
            raise ValueError('Original embedded soundscape definition would be replaced')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, value in sorted((entries | assets).items()):
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), value)
    payload = buffer.getvalue()
    output = bytearray(data)
    output.extend(b'\0' * (-len(output) % 4))
    offset = len(output)
    if offset + len(payload) > 2**31 - 1:
        raise ValueError('Soundscape BSP offset overflow')
    output.extend(payload)
    struct.pack_into('<ii', output, 8 + 40 * 16 + 4, offset, len(payload))
    final = BspFile.parse(bytes(output))
    if any(parsed.lumps[i] != final.lumps[i] or parsed.lump_bytes(i) != final.lump_bytes(i)
           for i in range(64) if i != 40):
        raise ValueError('Non-PAK data changed during soundscape embedding')
    if _pak_entries(final.lump_bytes(40)) != entries | assets:
        raise ValueError('Unplanned soundscape PAK change')
    return bytes(output), dict(input_sha256=_sha(data), output_sha256=_sha(output),
                              paths=sorted(assets), unrelated_pak_payloads_unchanged=True)
