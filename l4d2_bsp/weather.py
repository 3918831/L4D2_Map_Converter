"""Audited C6 weather removal and uniform target atmosphere.

Only the measured C6 entity identities and control signatures are removable.
The signatures include OutAnger, templates and scripts, not just On* events.
Brush data remains in the BSP; removing its weather entity does not rebuild it.
"""
import fnmatch
import hashlib
import json
import struct

from .entities import parse_entities
from .inspect import sha256
from .style import _read, _unique, _validate

# (classname, targetname, control-pair SHA256), matched by original Hammer ID.
# Measured identical in C6 base/h/l/s. Origin and render fields are not ownership.
WEATHER_NODES = {
    '291546': ('env_wind', None, '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'),
    '478037': ('func_precipitation', 'rain', '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'),
    '1027699': ('logic_relay', 'relay_storm_start', '6ff0961a83eaed05ea98d038acbe22e8fa6118dc297b532f458e807516cdf0c2'),
    '1027701': ('ambient_generic', 'sound_drip', '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'),
    '1027704': ('logic_director_query', 'ldq_stormstart', '3999f06ddcaf438e84794410d130328352c8fb424dc1cd32ed0e7012ab065a07'),
    '1027706': ('logic_relay', 'relay_tonemap_flash', '7b45912fbd3dd8727de0d9b8bebfb78fd83338fc8351833c7cdfc15349f7bef0'),
    '1027708': ('logic_relay', 'relay_storm_blendin', '59fe95601e70ac9c1f4517a10aa90cead6cbefd1c5d9d47c5d4fd4002d857cd4'),
    '1027710': ('logic_relay', 'relay_storm_blendout', '810d84f1108c1e412ce789cd98cb5dd99555f152251d59db4242c904b17fd0e1'),
    '1027712': ('logic_timer', 'timer_storm_lightning_strike', 'cad346e117f7b4e3140bdd8ef53a8b628f4e5b9d42f528a04fdc44cf9dfe1a36'),
    '1027714': ('point_broadcastclientcommand', 'skyboxfog', '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'),
    '1027716': ('logic_relay', 'relay_skyboxfogin', '1b8694500ef96ae5622ee11dc8ff139c9365234ad2c8b42201c0131a77e70d9a'),
    '1027718': ('logic_relay', 'relay_skyboxfogout', '2871136214a1e116ea55521def495dd4515518069148623ebabd9449782f9613'),
    '1027720': ('ambient_generic', 'sound_thunder', '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'),
    '1027723': ('ambient_generic', 'sound_drip', '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'),
    '1027726': ('logic_relay', 'relay_localcontrast_fadeout', '923ddfd4dc71cd8c1280ad216dae7caa0d1fffedeaede9ef95800a881c66ba92'),
    '1027728': ('logic_relay', 'relay_localcontrast_fadein', '40e2d5756963b8a8061cde9d88ec1bfda01f1444ca7e46e6e2a1f0685c4e5c8b'),
    '1027730': ('env_shake', 'shake_lightning', '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'),
    '1027733': ('logic_timer', 'timer_stormtime', 'fef2abc12ba472074d5d35ea9a686e039c80abb06eb145aa73a0acdb50700e36'),
    '1027735': ('logic_director_query', 'ldq_stormtime', '26631b582e82c546c06f4abad99f4bb8c413e7fe1519b096c9aef2cdfccd5b04'),
    '1027737': ('env_wind', 'wind_storm', '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'),
    '1027740': ('point_template', 'template_wind_storm', '83216f5fa5e208c0b9e278f572b4cfac41f964a8ad6862964570d44f3badba1e'),
    '1027742': ('sound_mix_layer', 'rainLayer_coop', '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'),
    '1027744': ('logic_auto', None, 'f058e95f3ccb613e313310b8251595097a25067b933c08ba55ce8a52944968bc'),
    '1027746': ('logic_relay', 'relay_mix_blendin_coop', '3ec46062a0a0140d8060cdfd10988da7bc456890c8deefb75441b64592248efe'),
    '1027748': ('logic_relay', 'relay_mix_blendout_coop', '91d3e64417f4505a5e113bb306a67f7435670659e3d0371d3a2331bf5eedb5c9'),
    '1027750': ('logic_relay', 'relay_mix_blendout_voip', '679a02c0a1222e580006564b6acd77ad716c78b49a98f07531713d9b5b0a7c4a'),
    '1027752': ('logic_relay', 'relay_mix_blendin_voip', 'a44d05a7465959200a554af874063b4ca7993d4eddcfdca3eaa6a9f0b383af5f'),
    '1027754': ('sound_mix_layer', 'rainLayer_voip', '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'),
    '1027756': ('info_gamemode', None, '866088e00a673a7428489a89e5c776fdd2491ffa41f1ab1cfadf17a68e1d8641'),
    '1027762': ('logic_director_query', None, '29c250081d698ef971fafabb1a70f37d8a17fa367652ac158187fade2e60b439'),
    '1027778': ('logic_relay', 'relay_ldq_storm', 'f7de3b8c78ede2166f2d57197f1c9fea78fb674d7079bb05875717dc36977b2e'),
    '1027853': ('logic_case', 'case_ldq_storm', '4b37f5160fd6d36b8bbe2c0c520a25cdc8224847c83fd868eb30423032f52265'),
    '1030118': ('env_wind', 'wind_normal', '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'),
    '1030121': ('point_template', 'template_wind_normal', '3485518673f6f26e3d49a42c58dc0106cc04669bae84189fd36e39697e2c34ce'),
}

POSTPROCESS_FIELDS = ('vignettestart', 'vignetteend', 'vignetteblurstrength',
    'topvignettestrength', 'localcontraststrength', 'localcontrastedgestrength',
    'grainstrength', 'fadetoblackstrength', 'fadetime')
POSTPROCESS_NAMES = ('fx_settings_storm', 'fx_settings_interior', 'fx_settings_exterior')
SOUNDSCAPES = {
    'c6m1_spawn_river': 'c5m1.waterfront',
    'c6m1_outdoor_01': 'c5m1.waterfront',
    'c6m1_building_interior': 'c5m1.smallstore',
    'c6m1_tent_int': 'c5m1.smallstore',
}
SOUNDSCAPE_RESOURCES = (
    'scripts/soundscapes_campaign5.txt', 'scripts/soundscapes_urban2.txt',
    'sound/ambient/atmosphere/crucial_indoor1.wav',
    'sound/ambient/c5m4/crucial_c5m4_waves_amb_loop.wav',
    *(f'sound/ambient/random_amb_sfx/rur5b_seagull{i:02}.wav' for i in range(1, 7)),
    *(f'sound/ambient/random_amb_sounds/rand_gulls_{i:02}.wav' for i in range(1, 7)),
)
VISUAL_NAMES = {'fog_storm', 'fog_master', 'foginteriorcontroller',
    'colorcorrection_checkpoint', 'color_correction_main', *POSTPROCESS_NAMES,
    'tonemap_global', 'tonemap_global_infected', 'tonemap_global_ghost'}
WITCH_STORM_OUTPUT = ('Onstartled', 'relay_storm_start\x1bTrigger\x1b\x1b0\x1b-1')


def _control_pairs(entity):
    return [(p.key, p.value) for p in entity.pairs if
            p.key.startswith(('On', 'Out', 'Template')) or '\x1b' in p.value or
            p.key.lower() in ('vscripts', 'thinkfunction')]


def _control_hash(entity):
    return hashlib.sha256(json.dumps(_control_pairs(entity),
        ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest()


def _is_output(pair):
    return pair.key.startswith(('On', 'Out')) or '\x1b' in pair.value


def _named(entities, classname, name):
    found = [e for e in entities if e.one('targetname') == name]
    if len(found) != 1 or found[0].one('classname') != classname:
        raise ValueError(f'Unexpected atmosphere controller {classname}/{name}')
    return found[0]


def replace_c6_weather(data, reference, *, kind='bsp', reference_kind='bsp'):
    """Apply after the C6 global transfer; leave every unowned byte/field intact."""
    parsed, text, entities = _read(data, kind)
    _, _, donor = _read(reference, reference_kind)
    names = {name for _, name, _ in WEATHER_NODES.values() if name}
    deleted, seen, removed_entities, removed_outputs = set(), set(), [], []
    for i, ent in enumerate(entities):
        hid, name, cls = ent.one('hammerid'), ent.one('targetname'), ent.one('classname')
        spec = WEATHER_NODES.get(hid)
        if spec:
            if hid in seen or (cls, name) != spec[:2] or _control_hash(ent) != spec[2]:
                raise ValueError(f'Changed/ambiguous weather ownership or control signature: {hid}/{name}')
            seen.add(hid)
            deleted.add(i)
            removed_entities.append(dict(entity_index=i, hammerid=hid, classname=cls,
                                         targetname=name, control_sha256=spec[2]))
            removed_outputs.extend(dict(entity_index=i, key=p.key, value=p.value, reason='exclusive_weather_entity')
                                   for p in ent.pairs if _is_output(p))
        elif name in names or cls in ('func_precipitation', 'env_wind'):
            raise ValueError(f'Unknown weather entity identity: {hid}/{cls}/{name}')

    post = _named(donor, 'postprocess_controller', 'fx_settings_exterior')
    lut = _named(donor, 'color_correction', 'color_correction_main').one('filename')
    if lut is None:
        raise ValueError('Missing target atmosphere LUT')
    _validate('filename', lut)
    for name in POSTPROCESS_NAMES:
        _named(entities, 'postprocess_controller', name)
    _named(entities, 'color_correction', 'colorcorrection_checkpoint')

    edits, expected, retained_indices, changed_indices, changes = [], [], [], set(), []
    protected_outputs = []
    for i, ent in enumerate(entities):
        if i in deleted:
            edits.append((ent.start, ent.end, b''))
            continue
        pairs = []
        name, cls = ent.one('targetname'), ent.one('classname')
        replacements = {}
        if cls == 'postprocess_controller':
            if name not in POSTPROCESS_NAMES:
                raise ValueError(f'Unknown local atmosphere postprocess controller: {name}')
            for key in POSTPROCESS_FIELDS:
                value = post.one(key)
                if value is not None:
                    _validate(key, value)
                    replacements[key] = value
        if name == 'colorcorrection_checkpoint':
            replacements['filename'] = lut
        if cls == 'env_soundscape':
            value = ent.one('soundscape')
            if value in SOUNDSCAPES:
                replacements['soundscape'] = SOUNDSCAPES[value]
            elif value not in SOUNDSCAPES.values():
                raise ValueError(f'Unknown C6 atmosphere soundscape: {value}')

        for key in replacements:
            ent.one(key)  # Reject duplicates before replacing one of several values.

        for p in ent.pairs:
            if _is_output(p):
                parts = p.value.split('\x1b')
                if len(parts) != 5:
                    raise ValueError(f'Unsupported atmosphere I/O format on entity {i}/{p.key}')
                target = parts[0]
                weather_target = any(fnmatch.fnmatchcase(n, target) for n in names)
                visual_target = any(fnmatch.fnmatchcase(n, target) for n in VISUAL_NAMES)
                if weather_target:
                    if (ent.one('hammerid'), cls, name, p.key, p.value) != (
                            '285747', 'info_zombie_spawn', 'bridewitch', *WITCH_STORM_OUTPUT):
                        raise ValueError(f'Unknown incoming weather branch: {i}/{p.key}/{target}')
                    removed_outputs.append(dict(entity_index=i, key=p.key, value=p.value, reason='witch_weather_branch'))
                    changed_indices.add(i)
                    continue
                if visual_target:
                    allowed_startup = (cls == 'logic_auto' and p.key == 'OnMapSpawn'
                        and target in ('tonemap_global', 'tonemap_global_infected', 'tonemap_global_ghost')
                        and (parts[1], parts[2]) in (
                            ('SetAutoExposureMax', '5'), ('SetAutoExposureMin', '1'),
                            ('SetTonemapRate', '.25'), ('SetTonemapPercentBrightPixels', '5'))
                        and parts[3:] == ['0', '-1'])
                    if not allowed_startup:
                        raise ValueError(f'Unknown atmosphere writer: {i}/{p.key}/{target}')
                protected_outputs.append((i, p.key, p.value))
            elif p.key.startswith('Template') and any(fnmatch.fnmatchcase(n, p.value) for n in names):
                raise ValueError(f'Unowned template references removed weather: {i}/{p.value}')
            value = replacements.pop(p.key, p.value)
            pairs.append((p.key, value))
            if value != p.value:
                changed_indices.add(i)
                changes.append(dict(entity_index=i, classname=cls, targetname=name,
                                    key=p.key, old=p.value, new=value, category='atmosphere_field'))
        for key, value in replacements.items():
            pairs.append((key, value))
            changed_indices.add(i)
            changes.append(dict(entity_index=i, classname=cls, targetname=name,
                                key=key, old=None, new=value, category='atmosphere_field'))
        retained_indices.append(i)
        expected.append(pairs)
        if i in changed_indices:
            # Only touched entities are serialized. Latin1 preserves original raw
            # values; the strict parser already rejects ambiguous quote escapes.
            replacement = ('{\n' + ''.join(f'"{k}" "{v}"\n' for k, v in pairs) + '}').encode('latin1')
            edits.append((ent.start, ent.end, replacement))

    updated = text
    for start, end, value in sorted(edits, reverse=True):
        updated = updated[:start] + value + updated[end:]
    after = parse_entities(updated)
    if [[(p.key, p.value) for p in e.pairs] for e in after] != expected:
        raise ValueError('Unexpected change outside C6 atmosphere allowlist')
    mapping = dict(zip(retained_indices, range(len(after))))
    for i in retained_indices:
        if i not in changed_indices and text[entities[i].start:entities[i].end] != updated[after[mapping[i]].start:after[mapping[i]].end]:
            raise ValueError('Protected non-weather entity bytes changed')
    actual_outputs = [(i, p.key, p.value) for i in retained_indices for p in after[mapping[i]].pairs if _is_output(p)]
    if actual_outputs != protected_outputs:
        raise ValueError('Protected gameplay I/O changed during weather replacement')

    output = data
    if updated != text:
        if kind == 'bsp':
            pad = b'\0' * (-len(data) % 4)
            offset = len(data) + len(pad)
            if offset + len(updated) > 2**31 - 1:
                raise ValueError('BSP entity offset exceeds signed range')
            header = bytearray(data[:1036])
            struct.pack_into('<ii', header, 12, offset, len(updated))
            output = bytes(header) + data[1036:] + pad + updated
        else:
            offset, length = parsed.entity_span
            header = bytearray(data[:20])
            struct.pack_into('<i', header, 12, len(updated))
            output = bytes(header) + data[20:offset] + updated + data[offset + length:]
    checked, _, _ = _read(output, kind)
    if kind == 'bsp' and (parsed.lumps[1:] != checked.lumps[1:] or any(
            parsed.lump_bytes(i) != checked.lump_bytes(i) for i in range(1, 64))):
        raise ValueError('Weather replacement changed non-entity BSP data')
    return output, dict(policy='replace', source_sha256=sha256(data), output_sha256=sha256(output),
        removed_entities=removed_entities, removed_outputs=removed_outputs, changes=changes,
        entity_counts=[len(entities), len(after)], protected_io_count=len(protected_outputs),
        protected_entity_bytes_unchanged=True, gameplay_io_unchanged=True,
        non_entity_payloads_unchanged=True, weather_control_signatures_verified=True,
        rain_entities_remaining=sum(e.one('classname') == 'func_precipitation' for e in after),
        limitations=['Bounded C6 entity/control identities only; arbitrary weather scripts are not interpreted.',
                     'Original gameplay scripts and local lights/materials remain; runtime validation required.',
                     'Original brush data is retained; no new weather geometry is generated.'])
