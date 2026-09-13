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


def capture_steps(run_id):
    """Render bilingual instructions without changing any installation state."""
    prefix = 'lmc_' + run_id
    return '\n'.join([
        '临时采样操作 / CAPTURE HANDOFF',
        f'运行编号 / Run ID: {run_id}',
        '',
        '1. 完全退出游戏。在平时能够正常运行的启动器中加入启动参数 -insecure，再启动本次配置指定的完整游戏安装。',
        '   Exit the game completely. Add -insecure to your usual working launcher, then restart the same full game installation.',
        '   确认参数实际传给 left4dead2.exe；可在 PowerShell 查看进程路径和命令行，再打开游戏开发者控制台。',
        '   Verify that left4dead2.exe actually receives the option; inspect the process in PowerShell, then open the developer console.',
        '   Get-CimInstance Win32_Process -Filter "Name=\'left4dead2.exe\'" | Select-Object ExecutablePath,CommandLine',
        '   -insecure 是进程启动参数；sv_cheats 1 和 sv_lan 1 都不能代替它。仅用于本地采样，不连接 VAC 服务器。',
        '   -insecure is a process launch option; neither sv_cheats 1 nor sv_lan 1 replaces it. Use this session for local capture only.',
        '',
        '2. 在游戏控制台执行以下命令，加载本次临时采样地图。',
        '   Run the following command in the game console to load this run\'s temporary capture map.',
        f'   exec {prefix}_load',
        '',
        '3. 等待开场结束、曝光和天气稳定。先检查车辆和材质是否正常。',
        '   Wait until the intro ends and exposure/weather is stable. Inspect vehicles and materials first.',
        '',
        '4. 执行以下命令关闭镜面反射，并等待材质重新加载完成。',
        '   Disable specular reflections with the following command, then wait for material reloading to finish.',
        '   mat_specular 0',
        '',
        '5. 检查本次输出：status 必须显示本地 Windows Listen、正确地图别名、已连接玩家和 insecure。',
        '   Inspect this check output: status must show the local Windows Listen server, exact map alias, connected player and insecure.',
        '   必须看到 GUARD_PASSED（检查通过），本次检查没有 REFUSED（拒绝执行）；否则先停止采样。',
        '   Require GUARD_PASSED and no REFUSED in this check. Otherwise stop before requesting capture.',
        '   守卫检查局域网模式和本地主机，但不验证启动参数或 VAC 状态；这些需按上面的步骤人工核对。',
        '   The guard checks LAN mode and a connected local host; launcher flags and VAC state require the manual checks above.',
        f'   exec {prefix}_check',
        '',
        '6. 只请求一次采样，看到 CAPTURE_REQUESTED 后等待原生 cubemap 处理和地图重新加载。',
        '   Request capture once. After CAPTURE_REQUESTED, wait for native cubemap processing and the map reload.',
        f'   exec {prefix}_capture',
        '   地图重新加载会重置单次请求保护。不要再次执行采样命令，也不要因为回到菜单就重新加载地图。',
        '   A map reload resets the once-per-session guard. Do not request capture again or reload the map merely because the menu appears.',
        '   若采样后重新连接被拒绝、要求移除插件或显示 0 玩家，先保留现状并继续归档；不要删除插件。',
        '   If reconnect is rejected after capture, asks to remove plugins, or shows zero players, preserve the attempt and archive it; do not remove plugins.',
        '   后续 REFUSED 不代表首次采样失败；48/48 进度也不证明写入成功。以 finish-capture 的 BSP 审计为准。',
        '   A later REFUSED does not establish first-request failure; 48/48 progress does not prove the BSP was written. Use the finish-capture BSP audit.',
        '',
        '7. 恢复镜面反射设置。此命令将 mat_specular 恢复为 1。',
        '   Restore specular reflections. This command restores mat_specular to 1.',
        f'   exec {prefix}_finish',
        '',
        '8. 完全退出游戏，然后按中文指南在 PowerShell 中执行 collect-capture 和 finish-capture。',
        '   Exit the game completely, then run collect-capture and finish-capture in PowerShell as described in the guide.',
        '',
        '若显式使用 preserve 天气策略，保留的风暴可能在采样中改变曝光；GUARD_PASSED 只验证采样开始前的状态。',
        'If using the preserve atmosphere policy, retained storms may change exposure during capture; GUARD_PASSED checks only the starting state.',
        '需要重试时，先归档本次 BSP 和日志，再使用新的运行编号和临时地图；不要覆盖既有证据。',
        'For a retry, archive this BSP and log first, then use a fresh run ID and capture alias without overwriting existing evidence.',
        '',
        '临时地图别名只用于采样；玩法验收应使用最终生成的原地图名安装包。',
        'The temporary map alias is for capture only; validate gameplay using the final original-name package.',
        '',
    ])


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
    steps = capture_steps(report['run_id'])
    # BOM keeps Chinese readable in Windows PowerShell 5 Get-Content.
    (root / 'CAPTURE-STEPS.txt').write_text(steps, encoding='utf-8-sig')
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
