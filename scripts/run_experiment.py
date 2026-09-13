"""Build independent, audited C2M1 experiments without touching game installs."""
import argparse
from pathlib import Path
import re
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l4d2_bsp.__main__ import json_bytes, write_outputs
from l4d2_bsp.inspect import compare_bytes, container, inspect_bytes, sha256
from l4d2_bsp.patch import patch_visual
from l4d2_bsp.resources import lookup_resources


VARIANTS = {
    'c5-color': dict(classname='color_correction', targetname='color_correction_main',
        key='filename', expected='materials/correction/cc_c2_main.raw', value='materials/correction/cc_c5_main.raw'),
    'fog-warm': dict(classname='env_fog_controller', targetname='fog_master',
        key='fogcolor', expected='18 29 33', value='90 75 60'),
    'sky-warm': dict(classname='worldspawn', targetname=None,
        key='skyname', expected='sky_l4d_c2m1_hdr', value='sky_l4d_c5_1_hdr'),
}
FACES = ('bk', 'dn', 'ft', 'lf', 'rt', 'up')


def game_paths(game_root):
    game_root = Path(game_root).resolve()
    info = game_root/'left4dead2/gameinfo.txt'
    raw = info.read_bytes()
    clean = re.sub(r'//[^\r\n]*', '', raw.decode('utf-8-sig'))
    matches = re.findall(r'"?SearchPaths"?\s*\{([^{}]*)\}', clean, flags=re.I)
    if len(matches) != 1:
        raise ValueError('Expected one simple gameinfo SearchPaths block')
    tokens = re.findall(r'"([^"\r\n]*)"|([^\s"]+)', matches[0])
    tokens = [quoted or plain for quoted, plain in tokens]
    if len(tokens) % 2:
        raise ValueError('Malformed gameinfo search path pairs')
    paths = []
    for key, value in zip(tokens[::2], tokens[1::2]):
        if key.lower() != 'game':
            continue
        if value.lower().startswith('|gameinfo_path|'):
            path = info.parent/value[len('|gameinfo_path|'):]
        else:
            path = game_root/value
        path = path.resolve()
        if not path.is_relative_to(game_root):
            raise ValueError('Game search path escapes the supplied game root')
        paths.append(path)
    if not paths:
        raise ValueError('No supported Game search paths')
    return paths, dict(path=str(info), sha256=sha256(raw), search_paths=[str(p) for p in paths])


def origin(path, data):
    return dict(path=str(Path(path).resolve()), sha256=sha256(data), size=len(data))


def run_experiment(source, game_root, output):
    source, game_root, output = Path(source).resolve(), Path(game_root).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(f'Output root already exists: {output}')
    resolved_output = output.resolve()
    if resolved_output.is_relative_to(game_root) or source.is_relative_to(resolved_output):
        raise ValueError('Output must not be in the game install or contain the source')
    if source.name.lower() != 'c2m1_highway.bsp':
        raise ValueError('This experiment is scoped to c2m1_highway.bsp')
    paths, gameinfo = game_paths(game_root)
    data = source.read_bytes()
    parsed = container(data, 'bsp')
    base = game_root/'left4dead2/maps'/source.name
    base_data = base.read_bytes()
    if sha256(data) != sha256(base_data):
        raise ValueError('Source BSP hash does not match base-game BSP')
    inputs = [source, base, Path(gameinfo['path'])]
    candidates = {}

    def select(name):
        items = []
        for root in paths:
            path = root/'maps'/name
            if path.is_file():
                raw = path.read_bytes()
                items.append((path, raw))
        candidates[name] = [origin(path, raw) for path, raw in items]
        if not items:
            raise ValueError(f'Missing required sidecar: {name}')
        inputs.extend(path for path, _ in items)
        return items[0]

    # Record alternate BSP candidates without claiming the base file wins in game.
    select(source.name)
    selected = [(source, data, 'bsp')]
    for mode in ('l', 'h', 's'):
        path, raw = select(f'c2m1_highway_{mode}_0.lmp')
        container(raw, 'lmp')
        selected.append((path, raw, 'lmp'))
    nav, nav_data = select('c2m1_highway.nav')
    exclude = game_root/'left4dead2/maps/c2m1_highway_exclude.lst'
    exclude_data = exclude.read_bytes()
    inputs.append(exclude)
    requirements = {
        'c5-color': ['materials/correction/cc_c5_main.raw'],
        'fog-warm': [],
        'sky-warm': [f'materials/skybox/sky_l4d_c5_1_hdr{face}.vmt' for face in FACES],
    }
    names = ['materials/correction/cc_c2_main.raw'] + [name for group in requirements.values() for name in group]
    resources = lookup_resources(paths, names)
    warnings = [
        'Source selection assumes first existing loose map sidecar in gameinfo Game search-path order. Actual engine/mode mount priority, addons and language paths require runtime evidence.',
        'BSP baseline is explicitly the supplied base-game BSP; alternate BSP candidates are recorded, not silently substituted.',
        'No runtime, graphics, gameplay, navigation or resource-content validation has been performed.',
        'Fog warm RGB 90 75 60 is an illustrative independent variant, not an exact C5 parameter copy.',
        resources['limitation'],
    ]
    for path, raw, kind in selected[1:]:
        revision = container(raw, kind).revision
        if revision != parsed.revision:
            warnings.append(f'{path.name}: LMP revision {revision} differs from BSP revision {parsed.revision}; preserved verbatim, runtime acceptance unverified.')
    report = dict(status='prepared_pending_runtime_validation', source={**origin(source, data),
        'base_game': origin(base, base_data), 'matches_base_game': True}, game_root=str(game_root),
        gameinfo=gameinfo, source_resolution_assumption=warnings[0], candidates=candidates,
        selected_nav=origin(nav, nav_data), base_exclude=origin(exclude, exclude_data),
        resources=resources, warnings=warnings, variants={})
    outputs = []

    def unchanged_files(folder, files):
        for path, raw in ((nav, nav_data), (exclude, exclude_data)):
            relative = Path(folder)/'maps'/path.name
            outputs.append((output/relative, raw))
            files.append(dict(output=relative.as_posix(), source=origin(path, raw),
                              sha256=sha256(raw), size=len(raw), identical=True))

    baseline = []
    for path, raw, kind in selected:
        relative = Path('baseline/maps')/path.name
        outputs.append((output/relative, raw))
        audit = inspect_bytes(raw, kind)
        if 'entity_error' in audit:
            raise ValueError(f'Cannot inspect baseline entities: {path}: {audit["entity_error"]}')
        audit_path = Path('baseline/audit')/(path.name+'.json')
        outputs.append((output/audit_path, json_bytes(audit)))
        baseline.append(dict(output=relative.as_posix(), audit=audit_path.as_posix(),
                             source=origin(path, raw), sha256=sha256(raw), identical=True))
    unchanged_files('baseline', baseline)
    report['baseline'] = dict(files=baseline)
    for name, patch_args in VARIANTS.items():
        missing = [resource for resource in requirements[name] if not resources['resources'][resource]['found']]
        if missing:
            report['variants'][name] = dict(status='blocked', reason='Required resources absent or unsupported', missing_resources=missing, files=[])
            report['status'] = 'partially_blocked'
            continue
        files = []
        for path, raw, kind in selected:
            changed, patch = patch_visual(raw, kind=kind, **patch_args)
            comparison = compare_bytes(raw, changed, kind)
            outside = raw[:patch['start']] == changed[:patch['start']] and raw[patch['end']:] == changed[patch['end']:]
            if not all((comparison['same_size'], comparison['header_unchanged'],
                        comparison['non_entity_lumps_unchanged'], comparison['io_unchanged'],
                        comparison['entity_count_unchanged'], outside)) or comparison['changed_lumps'] != [0]:
                raise ValueError(f'Failed preservation check: {name}/{path.name}')
            relative = Path(name)/'maps'/path.name
            outputs.append((output/relative, changed))
            files.append(dict(output=relative.as_posix(), source=origin(path, raw),
                sha256=sha256(changed), patch=patch, comparison=comparison, outside_patch_unchanged=outside))
        unchanged_files(name, files)
        report['variants'][name] = dict(status='prepared_pending_runtime_validation',
                                        patch=patch_args, required_resources=requirements[name], files=files)
    # All validation and byte preparation precede the first filesystem mutation.
    outputs.append((output/'experiment.json', json_bytes(report)))
    output.mkdir(parents=True, exist_ok=False)
    write_outputs(outputs, inputs)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--game-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = run_experiment(args.source, args.game_root, args.output)
    except (OSError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    print(json_bytes(dict(status=report['status'], report=str((args.output/'experiment.json').resolve()),
                          variants={name: item['status'] for name, item in report['variants'].items()})).decode(), end='')
    return 0 if report['status'] == 'prepared_pending_runtime_validation' else 3


if __name__ == '__main__':
    raise SystemExit(main())
