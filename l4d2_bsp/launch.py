"""Validated, recoverable Windows launch adapters for automatic capture."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess


_KEYS = {
    'backend', 'executable', 'game_executable', 'arguments', 'ini_path',
    'listenserver_cfg', 'startup_timeout_seconds', 'timeout_seconds',
    'stop_timeout_seconds', 'stable_seconds',
}
_DEFAULTS = {
    'arguments': [],
    'listenserver_cfg': 'listenserver.cfg',
    'startup_timeout_seconds': 60,
    'timeout_seconds': 600,
    'stop_timeout_seconds': 60,
    'stable_seconds': 5,
}
_TIME_LIMITS = {
    'startup_timeout_seconds': 3600,
    'timeout_seconds': 3600,
    'stop_timeout_seconds': 3600,
    'stable_seconds': 60,
}
_FORBIDDEN_OPTIONS = {'+exec', '+map', '+lservercfgfile'}
_BOOT_RE = re.compile(r'^lmc_[a-z0-9_]+$')
_CFG_RE = re.compile(r'^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*(?:\.cfg)?$')


def _resolve_file(value, base, name):
    if not isinstance(value, str) or not value:
        raise ValueError(f'{name} must be a non-empty path string')
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f'{name} is not a file: {path}')
    return path


def _same_path(left, right):
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def load_launcher(path, game_dir) -> dict:
    """Load and strictly validate a launcher configuration."""
    config_path = Path(path).resolve()
    try:
        supplied = json.loads(config_path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'Cannot load launcher configuration: {config_path}') from exc
    if not isinstance(supplied, dict):
        raise ValueError('Launcher configuration must be a JSON object')
    unknown = set(supplied) - _KEYS
    if unknown:
        raise ValueError(f'Unknown launcher configuration keys: {sorted(unknown)}')
    config = {**_DEFAULTS, **supplied}
    backend = config.get('backend')
    if backend not in {'coldclient', 'executable'}:
        raise ValueError("backend must be 'coldclient' or 'executable'")

    base = config_path.parent
    executable = _resolve_file(config.get('executable'), base, 'executable')
    game_executable = _resolve_file(config.get('game_executable'), base, 'game_executable')
    expected_game = Path(game_dir).resolve().parent / 'left4dead2.exe'
    if not _same_path(game_executable, expected_game):
        raise ValueError(f'game_executable must be the configured installation executable: {expected_game}')

    arguments = config['arguments']
    if (not isinstance(arguments, list) or
            any(not isinstance(item, str) or not item for item in arguments)):
        raise ValueError('arguments must be a list of non-empty strings')
    for argument in arguments:
        if ';' in argument or any(ord(character) < 32 for character in argument):
            raise ValueError('arguments cannot contain console separators or control characters')
        for token in argument.split():
            option = token.split('=', 1)[0].lower()
            if option in _FORBIDDEN_OPTIONS:
                raise ValueError(f'{option} is controlled by automatic capture')

    cfg = config['listenserver_cfg']
    if not isinstance(cfg, str) or not _CFG_RE.fullmatch(cfg):
        raise ValueError('listenserver_cfg must be a safe CFG path token')
    if not cfg.lower().endswith('.cfg'):
        cfg += '.cfg'
    config['listenserver_cfg'] = cfg
    for name, maximum in _TIME_LIMITS.items():
        value = config[name]
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or value < 1 or value > maximum):
            raise ValueError(f'{name} must be finite and between 1 and {maximum} seconds')

    result = dict(config)
    result['executable'] = str(executable)
    result['game_executable'] = str(game_executable)
    result['arguments'] = list(arguments)
    if backend == 'coldclient':
        result['ini_path'] = str(_resolve_file(config.get('ini_path'), base, 'ini_path'))
    elif 'ini_path' in supplied:
        raise ValueError('ini_path is valid only for the coldclient backend')
    elif not _same_path(executable, game_executable):
        raise ValueError('executable backend must launch game_executable directly')
    return result


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _write_exclusive(path, data):
    with Path(path).open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _powershell():
    system_root = os.environ.get('SystemRoot', r'C:\Windows')
    candidate = Path(system_root) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    return str(candidate)


_LAUNCH_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
$old = @(Get-CimInstance Win32_Process -Filter "Name='left4dead2.exe'")
if ($old.Count -ne 0) { throw 'Left 4 Dead 2 is already running' }
if ($p.PSObject.Properties.Name -contains 'argument_line') {
    $started = Start-Process -FilePath $p.executable -WorkingDirectory $p.working_directory -ArgumentList $p.argument_line -WindowStyle Hidden -PassThru
} else {
    $started = Start-Process -FilePath $p.executable -WorkingDirectory $p.working_directory -WindowStyle Hidden -PassThru
}
$deadline = [DateTime]::UtcNow.AddSeconds([double]$p.startup_timeout_seconds)
do {
    $matches = @(Get-CimInstance Win32_Process -Filter "Name='left4dead2.exe'" | Where-Object {
        $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath) -ieq [IO.Path]::GetFullPath($p.game_executable))
    })
    if ($matches.Count -gt 1) { throw 'Multiple matching game processes observed' }
    if ($matches.Count -eq 1) {
        $item = $matches[0]
        [ordered]@{process_id=[int]$item.ProcessId; executable_path=[string]$item.ExecutablePath; command_line=[string]$item.CommandLine} | ConvertTo-Json -Compress
        exit 0
    }
    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $deadline)
throw 'Timed out waiting for the game process'
'''

_OBSERVE_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
$item = Get-CimInstance Win32_Process -Filter ("ProcessId=" + [int]$p.process_id)
if ($null -ne $item) {
    [ordered]@{process_id=[int]$item.ProcessId; executable_path=[string]$item.ExecutablePath; command_line=[string]$item.CommandLine} | ConvertTo-Json -Compress
}
'''

_RECOVER_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$items = @(Get-CimInstance Win32_Process -Filter "Name='left4dead2.exe'" | ForEach-Object {
    [ordered]@{process_id=[int]$_.ProcessId; executable_path=[string]$_.ExecutablePath; command_line=[string]$_.CommandLine}
})
ConvertTo-Json -InputObject $items -Compress
'''


def _run_powershell(script, payload, timeout):
    try:
        result = subprocess.run(
            [_powershell(), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
            input=json.dumps(payload), capture_output=True, text=True, errors='replace',
            timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Cannot execute the Windows process observer') from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or 'unknown PowerShell failure'
        raise ValueError(f'Game launch failed: {detail}')
    return result.stdout.strip()


def _command_tokens(command_line):
    # CommandLineToArgvW provides the actual Windows token boundaries used by
    # process inspection, avoiding substring acceptance of privileged options.
    if os.name == 'nt':
        import ctypes
        count = ctypes.c_int()
        parser = ctypes.windll.shell32.CommandLineToArgvW
        parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
        argv = parser(command_line, ctypes.byref(count))
        if not argv:
            return []
        try:
            return [argv[index] for index in range(count.value)]
        finally:
            ctypes.windll.kernel32.LocalFree(argv)
    return command_line.split()


def _validate_identity(observed, game_executable, intended):
    if not isinstance(observed, dict) or isinstance(observed.get('process_id'), bool):
        raise ValueError('Invalid observed game process identity')
    try:
        process_id = int(observed['process_id'])
        executable_path = observed['executable_path']
        command_line = observed['command_line']
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Invalid observed game process identity') from exc
    if process_id <= 0 or not isinstance(executable_path, str) or not isinstance(command_line, str):
        raise ValueError('Invalid observed game process identity')
    tokens = _command_tokens(command_line)
    intended_lower = [item.lower() for item in intended]
    token_lower = [item.lower() for item in tokens]
    found = any(token_lower[index:index + len(intended_lower)] == intended_lower
                for index in range(len(token_lower) - len(intended_lower) + 1))
    if not _same_path(executable_path, game_executable) or not found:
        raise ValueError('The observed game process path or launch arguments did not match')
    return {'process_id': process_id, 'executable_path': executable_path,
            'command_line': command_line}


def _replace_journal(path, journal):
    path = Path(path)
    temporary = path.with_name(f'.launch-{os.getpid()}-{secrets.token_hex(8)}.tmp')
    try:
        _write_exclusive(temporary, json.dumps(journal, indent=2).encode('utf-8'))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def launch(config, boot, attempt_dir) -> dict:
    """Launch once and return the verified native game process identity."""
    if not isinstance(boot, str) or not _BOOT_RE.fullmatch(boot):
        raise ValueError('boot must be a unique lmc_* CFG token')
    attempt = Path(attempt_dir).resolve()
    if not attempt.is_dir():
        raise ValueError('attempt_dir must already exist')
    journal_path = attempt / 'launch.json'
    if journal_path.exists() or (attempt / 'launcher-original.ini').exists():
        raise ValueError('Launch attempt already has a launcher journal')

    intended = [*config['arguments'], '-insecure', '+exec', boot]
    journal = {
        'schema_version': 1, 'backend': config['backend'],
        'game_executable': config['game_executable'], 'boot': boot,
        'intended_arguments': intended,
    }
    restore_required = False
    if config['backend'] == 'coldclient':
        ini = Path(config['ini_path'])
        original = ini.read_bytes()
        suffix = (' ' + subprocess.list2cmdline(intended)).encode('ascii')
        pattern = re.compile(br'(?m)^(ExeCommandLine=[^\r\n]*)')
        changed, count = pattern.subn(lambda match: match.group(1) + suffix, original)
        if count != 1:
            raise ValueError('ColdClient INI must contain exactly one ExeCommandLine key')
        command_value = pattern.search(original).group(1).decode('ascii', errors='ignore')
        command_tokens = [item.lower() for item in _command_tokens(command_value.split('=', 1)[1])]
        if '+exec' in command_tokens or '+map' in command_tokens:
            raise ValueError('ColdClient ExeCommandLine contains an authoritative +exec or +map command')
        backup = attempt / 'launcher-original.ini'
        _write_exclusive(backup, original)
        journal.update({'ini_path': str(ini.resolve()), 'original_sha256': _hash(original),
                        'modified_sha256': _hash(changed), 'backup_path': str(backup)})
        _write_exclusive(journal_path, json.dumps(journal, indent=2).encode('utf-8'))
        launch_arguments = []
    elif config['backend'] == 'executable':
        _write_exclusive(journal_path, json.dumps(journal, indent=2).encode('utf-8'))
        launch_arguments = intended
    else:
        raise ValueError('Unvalidated launcher backend')

    payload = {
        'executable': config['executable'],
        'working_directory': str(Path(config['executable']).parent),
        'arguments': config['arguments'], 'boot': boot,
        'game_executable': config['game_executable'],
        'startup_timeout_seconds': config['startup_timeout_seconds'],
    }
    if launch_arguments:
        payload['argument_line'] = subprocess.list2cmdline(launch_arguments)
    try:
        if config['backend'] == 'coldclient':
            restore_required = True
            ini.write_bytes(changed)
        output = _run_powershell(_LAUNCH_SCRIPT, payload, config['startup_timeout_seconds'] + 15)
        try:
            observed = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ValueError('Windows process observer returned invalid output') from exc
        identity = _validate_identity(observed, config['game_executable'], intended)
        identity['boot'] = boot
        identity['intended_arguments'] = intended
        journal['identity'] = identity
        _replace_journal(journal_path, journal)
        return identity
    finally:
        if restore_required and not restore_launcher(attempt):
            raise ValueError('Launcher INI changed externally; saved original was retained')


def restore_launcher(attempt_dir) -> bool:
    """Restore a journaled ColdClient INI only when its content is still ours."""
    attempt = Path(attempt_dir).resolve()
    journal_path = attempt / 'launch.json'
    if not journal_path.is_file():
        return True
    try:
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Cannot read launcher recovery journal') from exc
    if journal.get('schema_version') != 1:
        raise ValueError('Unsupported launcher recovery journal')
    if journal.get('backend') != 'coldclient':
        return True
    ini = Path(journal['ini_path']).resolve()
    backup = Path(journal['backup_path']).resolve()
    if backup.parent != attempt or backup.name != 'launcher-original.ini':
        raise ValueError('Launcher recovery backup escaped attempt directory')
    original = backup.read_bytes()
    if _hash(original) != journal.get('original_sha256'):
        raise ValueError('Launcher recovery backup hash mismatch')
    current = ini.read_bytes()
    current_hash = _hash(current)
    if current_hash == journal.get('original_sha256'):
        return True
    if current_hash != journal.get('modified_sha256'):
        return False
    ini.write_bytes(original)
    return True


def is_running(identity) -> bool:
    """Return whether the same verified PID, executable and command line exists."""
    if not isinstance(identity, dict):
        raise ValueError('Invalid game process identity')
    payload = {'process_id': identity.get('process_id')}
    output = _run_powershell(_OBSERVE_SCRIPT, payload, 15)
    if not output:
        return False
    try:
        observed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError('Windows process observer returned invalid output') from exc
    return (observed.get('process_id') == identity.get('process_id') and
            isinstance(observed.get('executable_path'), str) and
            _same_path(observed['executable_path'], identity.get('executable_path', '')) and
            observed.get('command_line') == identity.get('command_line'))


def recover_identity(attempt_dir) -> dict | None:
    """Recover an owned live process from a pre-launch durable journal."""
    journal_path = Path(attempt_dir).resolve() / 'launch.json'
    try:
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Cannot read launcher recovery journal') from exc
    if journal.get('schema_version') != 1:
        raise ValueError('Unsupported launcher recovery journal')
    game_executable = journal.get('game_executable')
    intended = journal.get('intended_arguments')
    boot = journal.get('boot')
    if (not isinstance(game_executable, str) or not isinstance(intended, list) or
            not isinstance(boot, str) or not _BOOT_RE.fullmatch(boot)):
        raise ValueError('Invalid launcher recovery journal')
    output = _run_powershell(_RECOVER_SCRIPT, {}, 15)
    try:
        observed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError('Windows process observer returned invalid output') from exc
    if not isinstance(observed, list):
        raise ValueError('Windows process observer returned invalid output')
    if not observed:
        return None
    if len(observed) != 1:
        raise ValueError('Live game process does not match the journaled launch identity')
    try:
        identity = _validate_identity(observed[0], game_executable, intended)
    except ValueError as exc:
        raise ValueError('Live game process does not match the journaled launch identity') from exc
    identity['boot'] = boot
    identity['intended_arguments'] = intended
    journal['identity'] = identity
    _replace_journal(journal_path, journal)
    return identity
