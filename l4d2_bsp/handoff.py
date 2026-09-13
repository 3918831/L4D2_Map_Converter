"""Explicit user-invoked installation and archival of disposable capture files."""
import json
from pathlib import Path
import subprocess

from .configuration import file_hash


def require_game_closed():
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq left4dead2.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, text=True, errors='replace', timeout=15,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode != 0:
        raise ValueError('Cannot verify game process state; installation was not performed')
    if 'left4dead2.exe' in result.stdout.lower():
        raise ValueError('Exit Left 4 Dead 2 completely before installing/removing capture files')


def install_files(game_dir, payloads):
    game_dir = Path(game_dir).resolve()
    destinations = {}
    for name, data in payloads.items():
        path = (game_dir / name).resolve()
        if not path.is_relative_to(game_dir) or path == game_dir:
            raise ValueError('Capture file path escapes game directory')
        if path.exists() or path.is_symlink():
            raise ValueError(f'Capture destination already exists; refusing overwrite: {path}')
        if path in destinations:
            raise ValueError('Duplicate capture destination')
        destinations[path] = (name, data)
    created = []
    try:
        for path, (name, data) in destinations.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                created.append(path)
                stream.write(data)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return [{'name': name, 'path': str(path), 'sha256': file_hash(path)} for path, (name, _) in destinations.items()]


def install(root):
    from .workflow import load_run, write_json
    from .resources import vpk_index
    root = Path(root).resolve()
    report = load_run(root)
    if report['status'] != 'capture_prepared':
        raise ValueError('install-capture requires capture_prepared')
    require_game_closed()
    game = Path(report['config']['game_dir']).resolve()
    payloads = {item['name']: Path(item['path']).read_bytes() for item in report['capture_files']}
    # Check configured mounts and base addons for exact alias/resource collisions.
    # Runtime path verification is still necessary for unconfigured custom mounts.
    wanted = {name.lower() for name in payloads}
    roots = {game, *[Path(p).resolve() for p in report['config']['resource_roots']]}
    for base in roots:
        for name in wanted:
            if (base / name).exists():
                raise ValueError(f'Capture name already exists in a resource root: {base / name}')
        packages = list(base.glob('*_dir.vpk')) + list((base / 'addons').glob('*.vpk'))
        for package in packages:
            # Split archives are payloads; their *_dir.vpk is the indexed owner.
            if package.stem[-4:-3] == '_' and package.stem[-3:].isdigit():
                continue
            collision = wanted.intersection(vpk_index(package))
            if collision:
                raise ValueError(f'Capture alias collision in {package}: {sorted(collision)}')
    report['capture_installation'] = install_files(game, payloads)
    report['status'] = 'capture_installed'
    write_json(root / 'run.json', report)
    prefix = 'lmc_' + report['run_id']
    steps = '\n'.join([
        'CAPTURE HANDOFF / 临时采样操作',
        '1. Start the same full game installation with your usual working launcher; open console.',
        f'2. exec {prefix}_load',
        '3. Wait until the intro ends and exposure/weather is stable. Inspect vehicles/materials first.',
        '4. mat_specular 0   (wait for material reload)',
        f'5. exec {prefix}_check   (must show GUARD_PASSED, no REFUSED)',
        f'6. exec {prefix}_capture   (wait for native cubemap processing to finish)',
        f'7. exec {prefix}_finish   (restores mat_specular 1)',
        '8. Exit game completely, then run collect-capture and finish-capture as described in the guide.',
        'C6 storm may change exposure during capture; a guard pass checks only the starting state.',
        'Do not load this alias for gameplay validation; validate the final original-name package.',
    ]) + '\n'
    (root / 'CAPTURE-STEPS.txt').write_text(steps, encoding='utf-8')
    print(steps)


def collect(root):
    from .workflow import load_run, write_json
    root = Path(root).resolve()
    report = load_run(root)
    if report['status'] != 'capture_installed':
        raise ValueError('collect-capture requires this run to have installed capture files')
    require_game_closed()
    game = Path(report['config']['game_dir'])
    source = game / f'maps/{report["capture_alias"]}.bsp'
    log = game / f'lmc_{report["run_id"]}_capture.log'
    data, log_data = source.read_bytes(), log.read_bytes()
    evidence = root / 'evidence'
    evidence.mkdir(exist_ok=True)
    # Repeated attempts are separately archived; they never overwrite an earlier capture.
    number = 1
    while (evidence / f'attempt-{number:02}').exists():
        number += 1
    attempt = evidence / f'attempt-{number:02}'
    attempt.mkdir()
    (attempt / 'captured.bsp').write_bytes(data)
    (attempt / 'capture.log').write_bytes(log_data)
    report['latest_capture_evidence'] = str(attempt)
    write_json(root / 'run.json', report)
    print(f'Archived, not yet accepted: {attempt}\nRun finish-capture --run "{root}" --bsp "{attempt / "captured.bsp"}" --log "{attempt / "capture.log"}"')


def remove(root):
    from .workflow import write_json
    root = Path(root).resolve()
    # Cleanup authenticates exact installed paths/content, independently of
    # original build inputs that Steam or the user may have since changed.
    report = json.loads((root / 'run.json').read_text(encoding='utf-8'))
    if report.get('schema_version') != 1:
        raise ValueError('Unsupported installation receipt manifest')
    require_game_closed()
    receipt = report.get('capture_installation')
    if not receipt:
        raise ValueError('No installation receipt for this run')
    game = Path(report['config']['game_dir']).resolve()
    collected = Path(report.get('latest_capture_evidence', root / 'no-evidence')) / 'captured.bsp'
    pending = []
    for item in receipt:
        path = Path(item['path']).resolve()
        if not path.is_relative_to(game) or path != (game / item['name']).resolve():
            raise ValueError('Installation receipt path escaped game root')
        if not path.exists():
            continue
        expected = item['sha256']
        if item['name'] == f'maps/{report["capture_alias"]}.bsp' and collected.is_file():
            expected = file_hash(collected)
        if file_hash(path) != expected:
            raise ValueError(f'Installed file changed and is not archived; collect it before cleanup: {path}')
        pending.append(path)
    for path in pending:
        path.unlink()  # Exact previously-created, validated paths; no recursive deletion.
    report['capture_files_removed'] = True
    write_json(root / 'run.json', report)
    print('Removed recorded temporary capture files. Final/offline addon packages were not modified.')
