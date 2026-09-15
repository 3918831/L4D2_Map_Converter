"""Render owned dry ambience definitions; audio remains in the installed game.

Map-specific scripts follow Source's scripts/soundscapes_<map>.txt convention.
No shared soundscape manifest or Valve script is distributed or overridden.
"""
import re


_CATEGORIES = ('outdoor', 'indoor')


def _keys(value, expected, label):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError(f'Unexpected or missing soundscape keys in {label}')


def _number(value, minimum, maximum, label):
    # Only fixed decimal scalars: Source also accepts random ranges, which this
    # static definition intentionally cannot express.
    if (type(value) is not str or
            not re.fullmatch(r'(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)', value) or
            not minimum <= float(value) <= maximum):
        raise ValueError(f'Invalid soundscape {label}')


def validate_soundscape_definitions(definitions, mapping):
    """Validate bounded dry loops and return their complete installed resources.

    Nested soundscapes, scripts, random sounds and spatial origins are excluded,
    so the returned wave paths are the complete dependency closure. Validation
    also runs during rendering to protect callers holding modified preset data.
    """
    _keys(definitions, _CATEGORIES, 'definitions')
    _keys(mapping, _CATEGORIES, 'mapping')
    names = list(mapping.values())
    if (any(type(name) is not str or
            not re.fullmatch(r'lmc_[a-z0-9_]+\.[a-z0-9_]+', name)
            for name in names) or len(set(names)) != len(names)):
        raise ValueError('Invalid or duplicate owned soundscape identifier')
    resources = set()
    for category in _CATEGORIES:
        definition = definitions[category]
        _keys(definition, ('dsp', 'loops'), category)
        if definition['dsp'] != '1':
            raise ValueError('Unsupported dry soundscape DSP')
        loops = definition['loops']
        if type(loops) is not list:
            raise ValueError('Soundscape loops must be a list')
        waves = set()
        for loop in loops:
            _keys(loop, ('wave', 'volume', 'pitch'), 'loop')
            wave = loop['wave']
            if (type(wave) is not str or
                    not re.fullmatch(r'ambient/(?:[a-z0-9_]+/)*[a-z0-9_]+\.wav', wave) or
                    any(word in wave for word in ('rain', 'thunder', 'storm', 'fire', 'hail'))):
                raise ValueError('Unsupported or non-dry soundscape wave')
            _number(loop['volume'], 0, 1, 'volume')
            _number(loop['pitch'], 1, 255, 'pitch')
            if wave in waves:
                raise ValueError('Duplicate dry soundscape loop wave')
            waves.add(wave)
            resources.add('sound/' + wave)
    return tuple(sorted(resources))


def preset_soundscape_assets(preset, map_name):
    """Return deterministic package paths/bytes for a map or its capture alias."""
    if type(map_name) is not str or not re.fullmatch(r'[a-z0-9_-]{1,64}', map_name):
        raise ValueError('Invalid soundscape map name')
    definitions = getattr(preset, 'soundscape_definition', None)
    if definitions is None:
        return {}
    mapping = preset.soundscape_mapping
    validate_soundscape_definitions(definitions, mapping)
    lines = []
    for category in _CATEGORIES:
        definition = definitions[category]
        lines.extend((f'"{mapping[category]}"', '{', f'\t"dsp" "{definition["dsp"]}"'))
        for loop in definition['loops']:
            lines.extend(('\t"playlooping"', '\t{'))
            lines.extend(f'\t\t"{key}" "{loop[key]}"' for key in ('volume', 'pitch', 'wave'))
            lines.append('\t}')
        lines.extend(('}', ''))
    return {f'scripts/soundscapes_{map_name}.txt': '\n'.join(lines).encode('ascii')}
