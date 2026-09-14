"""Explicit local dependencies and source identity for the stable workflow."""
import hashlib
import json
from pathlib import Path

MAPS = {'c2-c5': 'c2m1_highway', 'c6-c5': 'c6m1_riverbank'}
SOURCE_ADAPTERS = {'c2m1_highway': 'c2m1_highway'}
MAPS.update(SOURCE_ADAPTERS)
PATH_KEYS = ('source_bsp', 'reference_bsp', 'reference_lmp', 'nav', 'exclude', 'game_dir', 'tools_dir', 'output_dir')


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_config(path):
    path = Path(path).resolve()
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Configuration must be a JSON object')
    unknown = set(value) - set(PATH_KEYS) - {'profile', 'source_profile', 'preset', 'mode_lmps', 'threads', 'timeout_seconds', 'resource_roots', 'atmosphere_policy', 'native_mounts'}
    if unknown:
        raise ValueError(f'Unknown configuration keys: {sorted(unknown)}')
    preset = None
    if 'source_profile' in value or 'preset' in value:
        if any(key in value for key in ('profile', 'reference_bsp', 'reference_lmp')):
            raise ValueError('Do not mix source_profile/preset with legacy profile/reference paths')
        if not all(key in value for key in ('source_profile', 'preset')):
            raise ValueError('source_profile and preset must be provided together')
        if value['source_profile'] not in MAPS.values():
            raise ValueError('source_profile must be c2m1_highway or c6m1_riverbank')
        from .presets import load_preset
        preset = load_preset(value['preset'])
        if value['source_profile'] not in preset.supported_sources:
            raise ValueError('Unsupported source/preset combination')
        if preset.schema_version >= 2:
            profile = SOURCE_ADAPTERS.get(value['source_profile'])
            if profile is None:
                raise ValueError('Source adapter does not support full atmosphere replacement')
        else:
            profile = next(key for key, name in MAPS.items() if name == value['source_profile'])
    else:
        profile = value.get('profile')
    if not isinstance(profile, str) or profile not in MAPS:
        raise ValueError('profile must be c2-c5 or c6-c5; other maps are not supported yet')

    def resolve(raw):
        if not isinstance(raw, str) or not raw or any(ord(c) < 32 for c in raw):
            raise ValueError('Paths must be nonempty strings without control characters')
        return (path.parent / raw).resolve()

    cfg = {'profile': profile, 'config_file': path}
    if preset:
        cfg.update(source_profile=value['source_profile'], preset=preset.id, preset_file=preset.source_path)
    full_preset = preset is not None and preset.schema_version >= 2
    policy = value.get('atmosphere_policy', preset.atmosphere_policy if full_preset else
                       ('replace' if cfg['profile'] == 'c6-c5' else 'preserve'))
    if not isinstance(policy, str) or policy not in ('replace', 'preserve'):
        raise ValueError('atmosphere_policy must be replace or preserve')
    if full_preset and policy != preset.atmosphere_policy:
        raise ValueError('Preset requires atmosphere_policy=replace')
    if not full_preset and cfg['profile'] != 'c6-c5' and policy != 'preserve':
        raise ValueError('atmosphere_policy=replace currently supports c6-c5 only; C2 retains its accepted profile')
    cfg['atmosphere_policy'] = policy
    native_mounts = value.get('native_mounts', 'gameinfo')
    if not isinstance(native_mounts, str) or native_mounts not in ('gameinfo', 'resource_roots'):
        raise ValueError('native_mounts must be gameinfo or resource_roots')
    cfg['native_mounts'] = native_mounts
    for key in PATH_KEYS:
        if preset and key in ('reference_bsp', 'reference_lmp'):
            cfg[key] = None
            continue
        if key == 'exclude' and not value.get(key):
            cfg[key] = None
            continue
        if key not in value:
            raise ValueError(f'Missing configuration key: {key}')
        cfg[key] = resolve(value[key])
    for key, default, maximum in (('threads', 4, 64), ('timeout_seconds', 3600, 86400)):
        number = value.get(key, default)
        if type(number) is not int or not 1 <= number <= maximum:
            raise ValueError(f'{key} must be an integer from 1 to {maximum}')
        cfg[key] = number
    modes = value.get('mode_lmps')
    if not isinstance(modes, dict) or set(modes) != set('hls'):
        raise ValueError('mode_lmps must explicitly provide h, l and s mode files')
    cfg['mode_lmps'] = {key: resolve(raw) for key, raw in modes.items()}
    name = MAPS[cfg['profile']]
    if cfg['source_bsp'].name.lower() != name + '.bsp' or (not preset and cfg['reference_bsp'].name.lower() != 'c5m1_waterfront.bsp'):
        raise ValueError('BSP filenames do not match the selected supported source/reference profile')
    if not preset and cfg['reference_lmp'].name.lower() not in {f'c5m1_waterfront_{mode}_0.lmp' for mode in 'hl'}:
        raise ValueError('reference_lmp must be an explicit C5 campaign mode entity patch')
    for mode, source in cfg['mode_lmps'].items():
        if source.name.lower() != f'{name}_{mode}_0.lmp':
            raise ValueError(f'Wrong map/mode filename for mode {mode}')
    if cfg['nav'].name.lower() != name + '.nav':
        raise ValueError('NAV filename does not match source map')
    if cfg['exclude'] and cfg['exclude'].name.lower() != name + '_exclude.lst':
        raise ValueError('exclude filename does not match source map')
    inputs = [cfg[k] for k in ('source_bsp', 'reference_bsp', 'reference_lmp', 'nav', 'exclude') if cfg[k]] + list(cfg['mode_lmps'].values())
    for source in inputs:
        if not source.is_file():
            raise ValueError(f'Missing input: {source}')
    if not (cfg['game_dir'] / 'gameinfo.txt').is_file():
        raise ValueError('game_dir must contain gameinfo.txt (normally the left4dead2 directory)')
    for executable in ('vrad.exe', 'bspzip.exe', 'vpk.exe'):
        if not (cfg['tools_dir'] / executable).is_file():
            raise ValueError(f'Missing required tool: {cfg["tools_dir"] / executable}')
    for install in (cfg['game_dir'].parent, cfg['tools_dir'].parent):
        if cfg['output_dir'].is_relative_to(install):
            raise ValueError('output_dir must be outside each game/tools installation')
    if not str(cfg['output_dir']).isascii():
        raise ValueError('output_dir must use an ASCII path for the native BSPZIP replacement list')
    if any(source.is_relative_to(cfg['output_dir']) for source in inputs + [path]):
        raise ValueError('output_dir must not contain any input/config file')
    if 'resource_roots' in value:
        roots = value['resource_roots']
        if not isinstance(roots, list) or not roots:
            raise ValueError('resource_roots must be a nonempty list of directories')
        if cfg['native_mounts'] == 'resource_roots':
            for raw in roots:
                if isinstance(raw, str) and '"' in raw:
                    raise ValueError('Resource root paths must not contain a quote')
                if isinstance(raw, str) and not raw.isascii():
                    raise ValueError('Resource root paths must use ASCII for native tools')
        cfg['resource_roots'] = [resolve(x) for x in roots]
        if any(not p.is_dir() for p in cfg['resource_roots']):
            raise ValueError('A configured resource root is not a directory')
    else:
        parent = cfg['game_dir'].parent
        cfg['resource_roots'] = [p for p in (parent / 'update', parent / 'left4dead2_dlc3', parent / 'left4dead2_dlc2', parent / 'left4dead2_dlc1', cfg['game_dir']) if p.is_dir()]
    if cfg['native_mounts'] == 'resource_roots':
        if not cfg['resource_roots']:
            raise ValueError('resource_roots must be a nonempty list of directories')
        for root in cfg['resource_roots']:
            raw = str(root)
            if '"' in raw:
                raise ValueError('Resource root paths must not contain a quote')
            if any(ord(character) < 32 for character in raw):
                raise ValueError('Resource root paths must not contain control characters')
            if not raw.isascii():
                raise ValueError('Resource root paths must use ASCII for native tools')
    return cfg


def input_inventory(cfg):
    paths = [cfg['config_file'], *[cfg[k] for k in ('source_bsp', 'reference_bsp', 'reference_lmp', 'nav', 'exclude') if cfg[k]],
             *cfg['mode_lmps'].values(), cfg['game_dir'] / 'gameinfo.txt']
    if cfg.get('preset_file'):
        paths.append(cfg['preset_file'])
    paths.extend(cfg['tools_dir'] / name for name in ('vrad.exe', 'bspzip.exe', 'vpk.exe'))
    paths.extend(p for p in cfg['tools_dir'].glob('*.dll'))
    paths.extend(p for base in (cfg['tools_dir'], cfg['game_dir']) for p in base.glob('*.rad'))
    return [{'path': str(p), 'size': p.stat().st_size, 'sha256': file_hash(p)} for p in dict.fromkeys(paths)]


def verify_inventory(inventory):
    for entry in inventory:
        path = Path(entry['path'])
        if not path.is_file() or file_hash(path) != entry['sha256']:
            raise ValueError(f'Input changed or missing; use a new run: {path}')
