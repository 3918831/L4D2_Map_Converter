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
        return dict(id=self.id, schema_version=1, sha256=self.sha256,
                    source_path=str(self.source_path) if self.source_path else None)


def parse_preset(data: bytes, *, source_path=None) -> StylePreset:
    """Validate packaged/snapshotted JSON; custom preset IDs are not supported."""
    try:
        document = json.loads(data.decode('utf-8'), object_pairs_hook=_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid preset JSON') from exc
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
        tuple(resources['clear']), tuple(soundscapes.items()))


def load_preset(identifier=PRESET_ID) -> StylePreset:
    """Load the installed C5 preset, without accessing any donor map file."""
    if identifier != PRESET_ID:
        raise ValueError(f'Unsupported preset: {identifier}')
    path = Path(__file__).resolve().parent / 'preset_data' / (PRESET_ID + '.json')
    return parse_preset(path.read_bytes(), source_path=path)
