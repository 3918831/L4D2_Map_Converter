"""Validated, versioned visual data; no donor BSP or entity geometry is needed.

The compatibility Entity view exists only for the bounded C2/C6 role selectors.
It cannot supply arbitrary entity classes, target names or gameplay outputs.
"""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

from .entities import Entity, Pair
from .lighting import ENVIRONMENT_FIELDS, _numeric_value
from .patch import _validate_value


PRESET_ID = 'c5m1-daylight-v1'
_BUILTINS = {PRESET_ID: 1, 'c4m3-overcast-static-v1': 2, 'c7m1-hazy-static-v1': 3}
_TONEMAP_ROLES = {'env_tonemap_controller': 'survivor',
    'env_tonemap_controller_infected': 'infected', 'env_tonemap_controller_ghost': 'ghost'}
_TONEMAP_ACTIONS = (('SetAutoExposureMin', 'minimum'), ('SetAutoExposureMax', 'maximum'),
    ('SetTonemapRate', 'rate'), ('SetTonemapPercentBrightPixels', 'bright_pixels'),
    ('SetBloomScale', 'bloom_scale'), ('SetBloomExponent', 'bloom_exponent'),
    ('SetBloomSaturation', 'bloom_saturation'))
_FOG = ('fogcolor', 'fogcolor2', 'fogstart', 'fogend', 'fogmaxdensity',
        'foglerptime', 'HDRColorScale', 'farz')
_ROLES = {
    'world': ('worldspawn', None, ('skyname',)),
    'environment': ('light_environment', None, tuple(ENVIRONMENT_FIELDS)),
    'directional': ('light_directional', None,
                    ('_light', '_lightHDR', '_lightscaleHDR', 'angles', 'pitch', 'SunSpreadAngle')),
    'shadow': ('shadow_control', None, ('color', 'angles', 'distance')),
    'fog_outdoor': ('env_fog_controller', 'fog_master', _FOG),
    'fog_interior': ('env_fog_controller', 'foginteriorcontroller',
                     ('fogcolor', 'fogcolor2', 'HDRColorScale')),
    'sky': ('sky_camera', None, tuple(k for k in _FOG if k not in ('farz', 'foglerptime'))),
    'color_main': ('color_correction', 'color_correction_main', ('filename',)),
    'color_intro': ('color_correction', 'color_correction_intro', ('filename',)),
    'sun': ('env_sun', None, ('use_angles', 'angles', 'pitch', 'size', 'rendercolor',
                            'overlaysize', 'overlaymaterial', 'overlaycolor', 'material', 'HDRColorScale')),
    'postprocess_exterior': ('postprocess_controller', 'fx_settings_exterior',
        ('vignettestart', 'vignetteend', 'vignetteblurstrength', 'topvignettestrength',
         'localcontraststrength', 'localcontrastedgestrength', 'grainstrength',
         'fadetoblackstrength', 'fadetime')),
}

_FULL_FOG = _FOG + ('fogenable', 'fogblend', 'fogdir', 'use_angles', 'angles',
    'heightFogStart', 'heightFogMaxDensity', 'heightFogDensity')
_ROLES_V2 = dict(_ROLES, **{
    'shadow': ('shadow_control', None, _ROLES['shadow'][2] +
               ('disableallshadows', 'enableshadowsfromlocallights')),
    'fog_outdoor': ('env_fog_controller', 'fog_master', _FULL_FOG),
    'fog_interior': ('env_fog_controller', 'foginteriorcontroller', _FULL_FOG),
    'sky': ('sky_camera', None, _ROLES['sky'][2] + ('fogenable', 'fogdir')),
    'color_checkpoint': ('color_correction', 'colorcorrection_checkpoint', ('filename',)),
})
_WIND_FIELDS = ('windradius', 'minwind', 'maxwind', 'mingust', 'maxgust',
    'mingustdelay', 'maxgustdelay', 'gustduration', 'gustdirchange', 'angles')


def _keys(value, expected, label):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError(f'Unexpected or missing preset keys in {label}')


def _visual_value(key, value):
    if type(value) is not str:
        raise ValueError(f'Preset visual field {key} must preserve its original string')
    _validate_value(key, value)  # ASCII, printable, safe path/color syntax.
    if key in ('skyname', 'filename', 'fogcolor', 'fogcolor2'):
        return
    if key in ('material', 'overlaymaterial'):
        if value != 'sprites/light_glow02_add_noz':
            raise ValueError('Unsupported preset sun material')
        return
    if key in ENVIRONMENT_FIELDS:
        _numeric_value(key, value)
        return
    numbers = [float(part) for part in value.split()]
    count = 3 if key in ('color', 'rendercolor', 'overlaycolor') else 1
    if len(numbers) != count or not all(math.isfinite(number) for number in numbers):
        raise ValueError(f'Invalid preset numeric components in {key}')
    if count == 3 and not all(0 <= number <= 255 for number in numbers):
        raise ValueError('Invalid preset RGB color')
    if key == 'fogmaxdensity' and not 0 <= numbers[0] <= 1:
        raise ValueError('Invalid preset fog density')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate preset key: {key}')
        result[key] = value
    return result


def _entity(pairs):
    # Offsets deliberately have no byte-buffer meaning: this is a read-only
    # semantic view, never a serialized or fabricated donor BSP.
    return Entity(tuple(Pair(key, value, -1, -1) for key, value in pairs), -1, -1)


@dataclass(frozen=True)
class StylePreset:
    id: str
    sha256: str
    source_path: Path | None
    _entities: tuple[Entity, ...]
    capture_exposure_max: float
    _common_resources: tuple[str, ...]
    _clear_resources: tuple[str, ...]
    _soundscapes: tuple[tuple[str, str], ...]
    schema_version: int = 1
    supported_sources: tuple[str, ...] = ('c2m1_highway', 'c6m1_riverbank')
    atmosphere_policy: str | None = None
    _role_data: tuple = ()
    _exposure_data: tuple = ()
    _wind_data: tuple | None = None
    _soundscape_json: str | None = None
    _tonemap_data: tuple = ()

    def role_values(self, name):
        """Return a detached semantic role, never donor identity or geometry."""
        values = dict(self._role_data).get(name)
        return dict(values) if values is not None else None

    @property
    def exposure_values(self):
        return dict(self._exposure_data)

    def tonemap_inputs(self, classname):
        """Return bounded initialization inputs by player role, not target name."""
        if classname not in _TONEMAP_ROLES:
            raise ValueError('Unsupported tonemap controller class')
        if self._tonemap_data:
            values = dict(dict(self._tonemap_data)[_TONEMAP_ROLES[classname]])
            actions = _TONEMAP_ACTIONS
        else:
            values = self.exposure_values
            actions = _TONEMAP_ACTIONS[:4 if classname == 'env_tonemap_controller' else 2]
        return [(action, values[field]) for action, field in actions if values[field] is not None]

    @property
    def wind_values(self):
        return dict(self._wind_data) if self._wind_data is not None else None

    @property
    def soundscape_definition(self):
        return json.loads(self._soundscape_json) if self._soundscape_json is not None else None

    def entities(self, kind='bsp'):
        """Return the shared base/mode role view, with no source coordinates."""
        if kind not in ('bsp', 'lmp'):
            raise ValueError('Expected bsp or lmp preset view')
        return list(self._entities)

    def required_resources(self, atmosphere_policy='preserve'):
        if atmosphere_policy not in ('preserve', 'replace'):
            raise ValueError('atmosphere_policy must be replace or preserve')
        return list(self._common_resources +
                    (self._clear_resources if atmosphere_policy == 'replace' else ()))

    @property
    def soundscape_mapping(self):
        """Target category names; source map ownership stays in its adapter."""
        return dict(self._soundscapes)

    def metadata(self):
        return dict(id=self.id, schema_version=self.schema_version, sha256=self.sha256,
                    source_path=str(self.source_path) if self.source_path else None)


def parse_preset(data: bytes, *, source_path=None) -> StylePreset:
    """Validate packaged/snapshotted JSON; custom preset IDs are not supported."""
    try:
        document = json.loads(data.decode('utf-8'), object_pairs_hook=_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid preset JSON') from exc
    if type(document) is dict and type(document.get('schema_version')) is int and document['schema_version'] in (2, 3):
        return _parse_modern(document, data, source_path)
    _keys(document, ('schema_version', 'id', 'roles', 'exposure', 'resources', 'soundscapes'), 'root')
    if type(document['schema_version']) is not int or document['schema_version'] != 1:
        raise ValueError('Unsupported preset schema version')
    if document['id'] != PRESET_ID:
        raise ValueError('Unsupported preset identity')
    roles = document['roles']
    _keys(roles, _ROLES, 'roles')
    entities = []
    for role, (classname, name, fields) in _ROLES.items():
        values = roles[role]
        _keys(values, fields, role)
        pairs = [('classname', classname)]
        if name:
            pairs.append(('targetname', name))
        if role in ('fog_outdoor', 'fog_interior'):
            pairs.append(('spawnflags', '1' if role == 'fog_outdoor' else '0'))
        for key, value in values.items():
            _visual_value(key, value)
            pairs.append((key, value))
        entities.append(_entity(pairs))

    exposure = document['exposure']
    _keys(exposure, ('minimum', 'maximum', 'bright_pixels', 'rate'), 'exposure')
    for key, value in exposure.items():
        _visual_value('exposure', value)
        if not 0 < float(value) <= 100:
            raise ValueError('Preset exposure outside bounds')
    if float(exposure['minimum']) > float(exposure['maximum']):
        raise ValueError('Preset exposure minimum exceeds maximum')
    outputs = []
    for suffix in ('_infected', '_ghost', ''):
        target = 'tonemap_global' + suffix
        entities.append(_entity([('classname', 'env_tonemap_controller' + suffix), ('targetname', target)]))
        actions = [('SetAutoExposureMin', 'minimum'), ('SetAutoExposureMax', 'maximum')]
        if not suffix:
            actions = [('SetTonemapPercentBrightPixels', 'bright_pixels'), ('SetTonemapRate', 'rate')] + actions
        outputs.extend(('OnMapSpawn', '\x1b'.join((target, action, exposure[key], '0', '-1')))
                       for action, key in actions)
    entities.append(_entity([('classname', 'logic_auto'), *outputs]))

    resources = document['resources']
    _keys(resources, ('common', 'clear'), 'resources')
    for category, paths in resources.items():
        if type(paths) is not list or not paths or any(type(path) is not str for path in paths):
            raise ValueError('Preset resources must be nonempty path lists')
        if len(paths) != len(set(paths)):
            raise ValueError('Duplicate preset resource')
        for path in paths:
            if not re.fullmatch(r'(?:materials/[a-zA-Z0-9_/]+\.(?:raw|vmt)|'
                                r'scripts/soundscapes_[a-zA-Z0-9_]+\.txt|'
                                r'sound/ambient/[a-zA-Z0-9_/]+\.wav)', path):
                raise ValueError(f'Unsupported preset resource path: {path}')
            if '//' in path:
                raise ValueError('Empty preset resource path component')
    expected_common = {roles['color_main']['filename'], roles['color_intro']['filename'],
                       'materials/' + roles['sun']['material'] + '.vmt',
                       'materials/' + roles['sun']['overlaymaterial'] + '.vmt'}
    expected_common.update('materials/skybox/' + roles['world']['skyname'] + face + '.vmt'
                           for face in ('bk', 'dn', 'ft', 'lf', 'rt', 'up'))
    if set(resources['common']) != expected_common:
        raise ValueError('Preset resources do not cover the visual roles')
    # This release supports these two audited C5 soundscapes only. Requiring
    # their measured dependency set prevents a truncated preset from quietly
    # weakening preflight; broader soundscape discovery belongs in a new schema.
    expected_clear = {
        'scripts/soundscapes_campaign5.txt', 'scripts/soundscapes_urban2.txt',
        'sound/ambient/atmosphere/crucial_indoor1.wav',
        'sound/ambient/c5m4/crucial_c5m4_waves_amb_loop.wav',
        *(f'sound/ambient/random_amb_sfx/rur5b_seagull{i:02}.wav' for i in range(1, 7)),
        *(f'sound/ambient/random_amb_sounds/rand_gulls_{i:02}.wav' for i in range(1, 7)),
    }
    if set(resources['clear']) != expected_clear:
        raise ValueError('Preset resources do not cover the C5 soundscapes')
    soundscapes = document['soundscapes']
    _keys(soundscapes, ('outdoor', 'indoor'), 'soundscapes')
    if soundscapes != {'outdoor': 'c5m1.waterfront', 'indoor': 'c5m1.smallstore'}:
        raise ValueError('Unsupported C5 preset soundscape targets')
    return StylePreset(document['id'], hashlib.sha256(data).hexdigest(),
        Path(source_path).resolve() if source_path is not None else None,
        tuple(entities), float(exposure['maximum']), tuple(resources['common']),
        tuple(resources['clear']), tuple(soundscapes.items()),
        _role_data=tuple((role, tuple(values.items())) for role, values in roles.items()),
        _exposure_data=tuple(exposure.items()))


def load_preset(identifier=PRESET_ID) -> StylePreset:
    """Load a registered preset, without accessing any donor map file."""
    if type(identifier) is not str or identifier not in _BUILTINS:
        raise ValueError(f'Unsupported preset: {identifier}')
    path = Path(__file__).resolve().parent / 'preset_data' / (identifier + '.json')
    return parse_preset(path.read_bytes(), source_path=path)


def _visual_value_v2(key, value):
    if key != 'fogdir':
        _visual_value(key, value)
    else:
        if type(value) is not str:
            raise ValueError('Preset fog direction must be a string')
        _validate_value(key, value)
        numbers = [float(part) for part in value.split()]
        if len(numbers) != 3 or not all(math.isfinite(n) for n in numbers):
            raise ValueError('Invalid preset fog direction')
        if not any(numbers):
            raise ValueError('Preset fog direction must be nonzero')
    # The engine stores visual scalars as 32-bit floats. Finite Python doubles
    # alone would still admit values that overflow when the map is loaded.
    if key not in ('skyname', 'filename', 'material', 'overlaymaterial'):
        if any(abs(float(part)) > 3.4028234663852886e38 for part in value.split()):
            raise ValueError(f'Preset {key} exceeds engine numeric range')
    if key in ('fogenable', 'fogblend', 'use_angles', 'disableallshadows',
               'enableshadowsfromlocallights') and value not in ('0', '1'):
        raise ValueError(f'Preset {key} must be 0 or 1')
    if key in ('heightFogDensity', 'heightFogMaxDensity', 'fogmaxdensity') and not 0 <= float(value) <= 1:
        raise ValueError(f'Preset {key} must be in [0, 1]')
    if key in ('distance', 'foglerptime', 'HDRColorScale', 'fadetime', 'grainstrength',
               'fadetoblackstrength', 'vignetteblurstrength', 'topvignettestrength') and float(value) < 0:
        raise ValueError(f'Preset {key} must be nonnegative')


def _resource_paths(paths):
    if type(paths) is not list or any(type(path) is not str for path in paths):
        raise ValueError('Preset resources must be path lists')
    if len(paths) != len(set(paths)):
        raise ValueError('Duplicate preset resource')
    for path in paths:
        if not re.fullmatch(r'(?:materials/[a-zA-Z0-9_/]+\.(?:raw|vmt)|'
                            r'sound/ambient/[a-zA-Z0-9_/]+\.wav)', path) or '//' in path:
            raise ValueError(f'Unsupported preset resource path: {path}')


def _parse_role_exposures(value):
    _keys(value, ('survivor', 'infected', 'ghost'), 'exposure roles')
    for role, fields in value.items():
        _keys(fields, [field for _, field in _TONEMAP_ACTIONS], 'exposure ' + role)
        for key, number in fields.items():
            if number is None and key in ('rate', 'bright_pixels'):
                continue  # Donor has no explicit override; never borrow another preset's value.
            _visual_value('exposure', number)
            lower = 0 if key.startswith('bloom_') else 0.000001
            if not lower <= float(number) <= 100:
                raise ValueError('Preset role exposure outside bounds')
        if float(fields['minimum']) > float(fields['maximum']):
            raise ValueError('Preset role exposure minimum exceeds maximum')
    return value


def _parse_modern(document, data, source_path):
    from .soundscapes import validate_soundscape_definitions

    _keys(document, ('schema_version', 'id', 'supported_sources', 'atmosphere_policy',
        'roles', 'exposure', 'wind', 'resources', 'soundscapes', 'soundscape_definition'), 'root')
    version = document['schema_version']
    if type(document['id']) is not str or _BUILTINS.get(document['id']) != version:
        raise ValueError('Unsupported preset identity')
    if document['supported_sources'] != ([] if version == 3 else ['c2m1_highway']):
        raise ValueError('Unsupported preset source capabilities')
    if document['atmosphere_policy'] != 'replace':
        raise ValueError('Schema 2 requires full atmosphere replacement')
    roles = document['roles']
    _keys(roles, _ROLES_V2, 'roles')
    entities = []
    for role, (classname, name, fields) in _ROLES_V2.items():
        values = roles[role]
        if values is None and (role == 'sun' or (version == 3 and role == 'directional')):
            continue
        _keys(values, fields, role)
        pairs = [('classname', classname)]
        if name:
            pairs.append(('targetname', name))
        if role in ('fog_outdoor', 'fog_interior'):
            pairs.append(('spawnflags', '1' if role == 'fog_outdoor' else '0'))
        for key, value in values.items():
            if version == 3 and key in ('material', 'overlaymaterial'):
                if type(value) is not str or not re.fullmatch(r'sprites/[a-zA-Z0-9_]+', value):
                    raise ValueError('Unsupported preset sun material path')
            else:
                _visual_value_v2(key, value)
            pairs.append((key, value))
        entities.append(_entity(pairs))
    role_exposures = _parse_role_exposures(document['exposure']) if version == 3 else None
    exposure = role_exposures['survivor'] if role_exposures else document['exposure']
    if version == 2:
        _keys(exposure, ('minimum', 'maximum', 'bright_pixels', 'rate'), 'exposure')
        for key, value in exposure.items():
            if key == 'bright_pixels' and value is None:
                continue
            _visual_value('exposure', value)
            if not 0 < float(value) <= 100:
                raise ValueError('Preset exposure outside bounds')
        if float(exposure['minimum']) > float(exposure['maximum']):
            raise ValueError('Preset exposure minimum exceeds maximum')
    outputs = []
    for suffix in ('_infected', '_ghost', ''):
        target = 'tonemap_global' + suffix
        entities.append(_entity([('classname', 'env_tonemap_controller' + suffix), ('targetname', target)]))
        if role_exposures is not None:
            values = role_exposures[_TONEMAP_ROLES['env_tonemap_controller' + suffix]]
            outputs.extend(('OnMapSpawn', '\x1b'.join((target, action, values[field], '0', '-1')))
                           for action, field in _TONEMAP_ACTIONS if values[field] is not None)
            continue
        actions = [('SetAutoExposureMin', 'minimum'), ('SetAutoExposureMax', 'maximum')]
        if not suffix:
            actions = [('SetTonemapRate', 'rate')] + actions
            if exposure['bright_pixels'] is not None:
                actions.insert(0, ('SetTonemapPercentBrightPixels', 'bright_pixels'))
        outputs.extend(('OnMapSpawn', '\x1b'.join((target, action, exposure[key], '0', '-1')))
                       for action, key in actions)
    entities.append(_entity([('classname', 'logic_auto'), *outputs]))
    wind = document['wind']
    if wind is not None:
        _keys(wind, _WIND_FIELDS, 'wind')
        for key, value in wind.items():
            _visual_value_v2(key, value)
            if key != 'angles' and float(value) < (-1 if key == 'windradius' else 0):
                raise ValueError(f'Preset wind {key} outside bounds')
        if -1 < float(wind['windradius']) < 0:
            raise ValueError('Preset wind radius must be -1 or nonnegative')
        for low, high in (('minwind', 'maxwind'), ('mingust', 'maxgust'), ('mingustdelay', 'maxgustdelay')):
            if float(wind[low]) > float(wind[high]):
                raise ValueError('Preset wind minimum exceeds maximum')
        if float(wind['gustdirchange']) > 180:
            raise ValueError('Preset gust direction change outside bounds')
        entities.append(_entity([('classname', 'env_wind'), ('targetname', 'wind_normal'), *wind.items()]))
    resources = document['resources']
    _keys(resources, ('common', 'clear'), 'resources')
    for paths in resources.values():
        _resource_paths(paths)
    expected_common = {roles[role]['filename'] for role in ('color_main', 'color_intro', 'color_checkpoint')}
    expected_common.update('materials/skybox/' + roles['world']['skyname'] + face + '.vmt'
                           for face in ('bk', 'dn', 'ft', 'lf', 'rt', 'up'))
    if roles['sun'] is not None:
        expected_common.update('materials/' + roles['sun'][key] + '.vmt' for key in ('material', 'overlaymaterial'))
    if set(resources['common']) != expected_common:
        raise ValueError('Preset resources do not cover the visual roles')
    soundscapes = document['soundscapes']
    definitions = document['soundscape_definition']
    expected_clear = validate_soundscape_definitions(definitions, soundscapes)
    if set(resources['clear']) != set(expected_clear):
        raise ValueError('Preset resources do not cover the dry soundscapes')
    return StylePreset(document['id'], hashlib.sha256(data).hexdigest(),
        Path(source_path).resolve() if source_path is not None else None,
        tuple(entities), float(exposure['maximum']), tuple(resources['common']),
        tuple(resources['clear']), tuple(soundscapes.items()), schema_version=version,
        supported_sources=tuple(document['supported_sources']), atmosphere_policy=document['atmosphere_policy'],
        _role_data=tuple((role, tuple(values.items()) if values is not None else None) for role, values in roles.items()),
        _exposure_data=tuple(exposure.items()), _wind_data=tuple(wind.items()) if wind is not None else None,
        _soundscape_json=json.dumps(definitions),
        _tonemap_data=tuple((role, tuple(values.items())) for role, values in role_exposures.items()) if role_exposures else ())
