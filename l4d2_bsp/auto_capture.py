"""Optional native capture coordinator; game files remain disposable and audited."""
from contextlib import contextmanager, ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

from .configuration import file_hash


@contextmanager
def run_lock(root, name='.auto-capture.lock'):
    """Kernel-owned lock releases on coordinator crash; the journal does not."""
    path = safe_path(root, name)
    with path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('Another automatic capture coordinator owns this run') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def save_attempt(attempt, journal, **changes):
    from .workflow import write_json
    journal.update(changes)
    write_json(attempt / 'attempt.json', journal)


def event(attempt, journal, name, **values):
    row = dict(event=name, time=time.time(), **values)
    journal.setdefault('events', []).append(row)
    save_attempt(attempt, journal)
    print(json.dumps(row), flush=True)


def output_names(run_id):
    marker = token(run_id)
    return [f'{marker}_capture.log', f'ems/{marker}_state.txt',
            f'ems/{marker}_command.txt', f'ems/{marker}_command.tmp']


def _archive_cleanup(root, journal):
    """Archive first, then remove only exact attempt paths after game exit."""
    from . import handoff
    from .workflow import write_json
    handoff.require_game_closed()
    attempt = root / 'auto-capture'
    report = json.loads((root / 'run.json').read_text(encoding='utf-8'))
    game = Path(journal['game_dir'])
    archive = attempt / 'cleanup-evidence'
    archive.mkdir(exist_ok=True)
    # Includes a partially installed attempt whose normal receipt was not saved
    # before an interruption. Planned files were checked absent before install.
    receipts = journal.get('planned_installation', [])
    owned = []
    map_name = f'maps/{report["capture_alias"]}.bsp'
    for item in receipts:
        path = safe_path(game, item['name'])
        if not path.exists():
            continue
        digest = file_hash(path)
        if digest != item['sha256'] and item['name'] != map_name:
            raise ValueError(f'Installed file changed; preserved for recovery: {path}')
        if item['name'] == map_name:
            copy = archive / 'captured.bsp'
            _archive_exact(path, copy)
        owned.append(dict(name=item['name'], sha256=digest))
    for name in output_names(journal['run_id']):
        path = safe_path(game, name)
        if path.exists():
            copy = archive / name.replace('/', '__')
            _archive_exact(path, copy)
            owned.append(dict(name=name, sha256=file_hash(copy)))
    # Save complete recovery receipt before the first unlink. A second recovery
    # tolerates already absent paths and still checks the remaining bytes.
    save_attempt(attempt, journal, cleanup_receipt=owned)
    remove_owned(game, owned)
    report['capture_files_removed'] = True
    write_json(root / 'run.json', report)
    save_attempt(attempt, journal, cleaned=True)


def _archive_exact(source, destination):
    data = source.read_bytes()
    if destination.exists():
        if destination.read_bytes() != data:
            raise ValueError(f'Existing recovery evidence differs: {destination}')
    else:
        partial = destination.with_name(destination.name + '.partial')
        if partial.is_symlink() or (partial.exists() and not data.startswith(partial.read_bytes())):
            raise ValueError(f'Foreign partial recovery evidence preserved: {partial}')
        with partial.open('wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        partial.replace(destination)


def _stop(root, journal):
    from . import launch, handoff
    attempt = root / 'auto-capture'
    identity = journal.get('process')
    if identity is None and (attempt / 'launch.json').exists():
        identity = launch.recover_identity(attempt)
        if identity:
            save_attempt(attempt, journal, process=identity)
    if identity and launch.is_running(identity):
        mailbox(Path(journal['game_dir']), journal['run_id'], 'STOP')
        event(attempt, journal, 'STOP_SENT')
        deadline = time.monotonic() + journal['launcher']['stop_timeout_seconds']
        while time.monotonic() < deadline and launch.is_running(identity):
            time.sleep(1)
        if launch.is_running(identity):
            raise ValueError('Game did not exit normally; exit it manually, then recover-auto-capture')
    handoff.require_game_closed()
    if not launch.restore_launcher(attempt):
        raise ValueError('Launcher INI changed externally; original backup preserved; resolve conflict before recovery')
    _archive_cleanup(root, journal)


def recover(root):
    """Stop/clean an existing attempt. Never launches or requests capture."""
    root = Path(root).resolve()
    with run_lock(root):
        report = json.loads((root / 'run.json').read_text(encoding='utf-8'))
        attempt = root / 'auto-capture'
        journal = json.loads((attempt / 'attempt.json').read_text(encoding='utf-8'))
        if journal['run_id'] != report['run_id']:
            raise ValueError('Automatic capture receipt identity differs from run')
        if (journal.get('game_dir') != str(Path(report['config']['game_dir']).resolve()) or
                journal.get('alias') != report['capture_alias']):
            raise ValueError('Automatic capture receipt identity differs from installation')
        with run_lock(Path(journal['game_dir']), '.lmc-auto-capture.lock'):
            _stop(root, journal)
        save_attempt(attempt, journal, status='recovered', runtime_accepted=False)
        return journal


def monitor(root, report, journal):
    from . import launch
    from .reflections import audit_capture_log, capture_replacements
    attempt = root / 'auto-capture'
    game, run_id = Path(journal['game_dir']), report['run_id']
    marker = token(run_id)
    cfg = journal['launcher']
    baseline = Path(report['offline_map']).read_bytes()
    gate = CaptureGate(report['capture_alias'], marker)
    deadline = time.monotonic() + cfg['timeout_seconds']
    state_path = safe_path(game, f'ems/{marker}_state.txt')
    log_path = safe_path(game, marker + '_capture.log')
    bsp_path = safe_path(game, f'maps/{report["capture_alias"]}.bsp')
    last_state, digest, stable_at, finishing = None, None, None, False
    while time.monotonic() < deadline:
        if not launch.is_running(journal['process']):
            if not finishing:
                raise ValueError('Game exited before audited capture completed')
            event(attempt, journal, 'GAME_EXITED')
            return
        text = log_path.read_text(encoding='utf-8', errors='replace') if log_path.exists() else ''
        state = state_path.read_text(encoding='ascii').strip('\x00\r\n ') if state_path.exists() else None
        if state != last_state:
            event(attempt, journal, 'STATE', state=state)
            last_state = state
        command = gate.observe(state, text)
        if command:
            # Journal-before-send deliberately makes crash recovery STOP-only.
            save_attempt(attempt, journal, capture_authorized=True)
            mailbox(game, run_id, command)
            event(attempt, journal, 'CAPTURE_SENT')
        if state == 'RELOADED' and gate.sent and not finishing:
            blob = bsp_path.read_bytes()
            current = hashlib.sha256(blob).hexdigest()
            if current != digest:
                digest, stable_at = current, time.monotonic()
            elif time.monotonic() - stable_at >= 3:
                log_audit = audit_capture_log(text, alias=report['capture_alias'], marker=marker)
                _, audit = capture_replacements(baseline, blob, map_name=report['map_name'], alias=report['capture_alias'])
                save_attempt(attempt, journal, live_log_audit=log_audit, live_capture_audit=audit)
                mailbox(game, run_id, 'FINISH')
                event(attempt, journal, 'FINISH_SENT')
                finishing = True
        time.sleep(1)
    raise ValueError('Automatic capture timed out; this alias will not be retried')


def run(root, launcher_path):
    from . import handoff, launch
    from .workflow import load_run, prepare, tracked, write_json, run_preset, run_capture_tonemap, finish
    root = Path(root).resolve()
    with run_lock(root), ExitStack() as shared_locks:
        attempt = root / 'auto-capture'
        if attempt.exists():
            raise ValueError('Automatic capture attempt already exists; use recover-auto-capture and a fresh run to retry')
        report = load_run(root)
        if report['status'] not in ('offline_ready', 'capture_prepared'):
            raise ValueError('auto-capture requires offline_ready or capture_prepared')
        if report['config'].get('atmosphere_policy', 'replace') != 'replace':
            raise ValueError('Automatic capture currently requires replace atmosphere policy')
        handoff.require_game_closed()
        game = Path(report['config']['game_dir']).resolve()
        shared_locks.enter_context(run_lock(game, '.lmc-auto-capture.lock'))
        cfg = launch.load_launcher(launcher_path, game)
        token(report['run_id'])
        for name in output_names(report['run_id']):
            if safe_path(game, name).exists():
                raise ValueError(f'Previous automatic capture output exists: {name}')
        if report['status'] == 'offline_ready':
            prepare(root)
            report = load_run(root)
        attempt.mkdir()
        journal = dict(schema_version=1, run_id=report['run_id'], alias=report['capture_alias'],
                       game_dir=str(game), launcher=cfg, status='preparing', runtime_accepted=False)
        save_attempt(attempt, journal)
        try:
            (attempt / 'launcher-config.json').write_bytes(Path(launcher_path).read_bytes())
            preset = run_preset(report)
            payloads = controls(report['run_id'], report['capture_alias'],
                                preset.capture_exposure_max if preset else 5,
                                cfg['listenserver_cfg'], cfg['stable_seconds'],
                                tonemap_name=run_capture_tonemap(report))
            for name, data in payloads.items():
                path = attempt / 'files' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                report['capture_files'].append(dict(name=name, path=str(path)))
                report['tracked_outputs'].append(tracked(path))
            planned = []
            for item in report['capture_files']:
                path = safe_path(game, item['name'])
                if path.exists():
                    raise ValueError(f'Capture destination already exists: {path}')
                planned.append(dict(name=item['name'], sha256=file_hash(Path(item['path']))))
            save_attempt(attempt, journal, planned_installation=planned)
            write_json(root / 'run.json', report)
            handoff.install(root, print_steps=False)
            save_attempt(attempt, journal, status='launching')
            identity = launch.launch(cfg, token(report['run_id']) + '_boot', attempt)
            save_attempt(attempt, journal, process=identity, status='capturing')
            monitor(root, report, journal)
            handoff.collect(root, print_steps=False)
            report = load_run(root)
            evidence = Path(report['latest_capture_evidence'])
            result = finish(root, evidence / 'captured.bsp', evidence / 'capture.log')
            _archive_cleanup(root, journal)
            save_attempt(attempt, journal, status='complete', runtime_accepted=False)
            return result
        except BaseException as exc:
            save_attempt(attempt, journal, status='failed', error=str(exc), runtime_accepted=False)
            try:
                _stop(root, journal)
            except Exception as cleanup_error:
                save_attempt(attempt, journal, recovery_error=str(cleanup_error))
            raise


def safe_path(game, name):
    game = Path(game).resolve()
    path = game / name
    if not path.resolve().is_relative_to(game) or path.resolve() == game:
        raise ValueError('Automatic capture path escapes game directory')
    for parent in (path, *path.parents):
        if parent == game:
            break
        if parent.is_symlink() or (parent.exists() and getattr(parent.stat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Automatic capture paths cannot use symlinks or reparse points')
    return path


def token(run_id):
    if not isinstance(run_id, str) or not re.fullmatch('[0-9a-f]{16}', run_id):
        raise ValueError('Invalid automatic capture run ID')
    return 'lmc_' + run_id


def mailbox(game, run_id, command):
    if command not in ('CAPTURE', 'STOP', 'FINISH'):
        raise ValueError('Unknown automatic capture command')
    path = safe_path(game, f'ems/{token(run_id)}_command.txt')
    if path.exists():
        previous = path.read_text(encoding='ascii')
        if command == 'CAPTURE' or previous not in [c + ':' + run_id for c in ('CAPTURE', 'STOP', 'FINISH')]:
            raise ValueError('Mailbox belongs to an existing or foreign command')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = safe_path(game, f'ems/{token(run_id)}_command.tmp')
    if temp.exists() and command == 'STOP':
        pending = temp.read_text(encoding='ascii')
        # Only STOP can recover an interrupted write. Partial tokens cannot
        # authorize capture; the attempt journal still forbids all retries.
        if not any((value + ':' + run_id).startswith(pending) for value in ('CAPTURE', 'STOP', 'FINISH')):
            raise ValueError('Foreign temporary mailbox content; preserved')
        temp.unlink()
    with temp.open('x', encoding='ascii') as stream:
        stream.write(command + ':' + run_id)
        stream.flush()
    temp.replace(path)


def remove_owned(game, receipt):
    pending = []
    for item in receipt:
        path = safe_path(game, item['name'])
        if path.exists():
            if file_hash(path) != item['sha256']:
                raise ValueError(f'Owned file changed since archival: {path}')
            pending.append(path)
    for path in pending:
        path.unlink()


class CaptureGate:
    """Authorization gate only; completion always requires the full native audit."""
    def __init__(self, alias, marker):
        self.alias, self.prefix, self.sent = alias, marker.upper(), False

    def observe(self, state, text):
        prefix = self.prefix
        lines = [line.strip() for line in text.splitlines()]
        requests = lines.count(prefix + '_CAPTURE_REQUESTED')
        if requests > 1 or (requests and not self.sent):
            raise ValueError('Unexpected or repeated capture request; never retry this alias')
        if state in ('FAILED', 'TIMEOUT'):
            raise ValueError('Native automatic controller failed or timed out')
        # A refusal after the first request is handled by the existing chronology
        # audit; it can merely be a failed client reconnect after a valid capture.
        if not requests and (any(prefix + '_REFUSED:' in line for line in lines) or
                             'AN ERROR HAS OCCUR' in text or 'SCRIPT ERROR' in text):
            raise ValueError('Native guard or script failed before capture')
        if state != 'READY' or self.sent:
            return None
        matching = False
        for line in lines:
            if line.startswith(prefix + '_MAP='):
                matching = line == prefix + '_MAP=' + self.alias
            if matching and line == prefix + '_GUARD_PASSED_CAPTURE_NOT_VERIFIED':
                self.sent = True
                return 'CAPTURE'
        return None


def controls(run_id, alias, exposure, normal_cfg, stable_seconds, *, tonemap_name="tonemap_global"):
    marker = token(run_id)
    if not re.fullmatch('mc[0-9a-f]{8}', alias):
        raise ValueError('Invalid capture alias')
    if not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.cfg', normal_cfg):
        raise ValueError('Invalid normal listenserver CFG')
    if not isinstance(exposure, (int, float)) or not math.isfinite(exposure) or exposure <= 0:
        raise ValueError('Invalid capture exposure')
    if not isinstance(stable_seconds, (int, float)) or not math.isfinite(stable_seconds) or not 1 <= stable_seconds <= 60:
        raise ValueError('Invalid stability interval')
    if not isinstance(tonemap_name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", tonemap_name):
        raise ValueError("Invalid capture tonemap_name")
    script = _SCRIPT
    for key, value in {'RUN': run_id, 'MARKER': marker, 'ALIAS': alias,
                       'EXPOSURE': format(exposure, '.9g'), 'NORMAL': normal_cfg, 'TONEMAP': tonemap_name,
                       'STABLE': str(math.ceil(stable_seconds))}.items():
        script = script.replace('@' + key + '@', value)
    return {
        f'cfg/{marker}_boot.cfg': (f'con_logfile {marker}_capture.log\necho {marker.upper()}_AUTO_BOOT\n'
            f'sv_lan 1\nsv_cheats 1\nmat_hdr_level 2\nlservercfgfile {marker}_ready.cfg\nmap {alias} coop\n').encode('ascii'),
        f'cfg/{marker}_ready.cfg': (f'exec {normal_cfg}\nsv_cheats 1\nscript_execute {marker}_auto\n').encode('ascii'),
        f'scripts/vscripts/{marker}_auto.nut': script.encode('ascii'),
    }


_SCRIPT = r'''// Native, disposable controller. No permanent map entities are changed.
if (Director.GetMapName() != "@ALIAS@") throw "LMC_AUTO_WRONG_MAP";
if ("LmcAutoController" in getroottable()) throw "LMC_AUTO_DUPLICATE_CONTROLLER";
::LmcAutoController <- {statefile="@MARKER@_state.txt", commandfile="@MARKER@_command.txt",
    phase="WAIT_PLAYER", stable=0, ticks=0};
local previous = FileToString(LmcAutoController.statefile);
if (previous == "DISPATCHED" || previous == "RELOADED") LmcAutoController.phase = "RELOADED";
else if (previous != null) throw "LMC_AUTO_EXISTING_ATTEMPT";
StringToFile(LmcAutoController.statefile, LmcAutoController.phase);
::LmcAutoController.timer <- SpawnEntityFromTable("info_target", {targetname="@MARKER@_timer"});
LmcAutoController.timer.ValidateScriptScope();
LmcAutoController.timer.GetScriptScope().AutoTick <- function() {
    local job = ::LmcAutoController;
    try {
        job.ticks++;
        local command = FileToString(job.commandfile);
        if (command == "STOP:@RUN@" || command == "FINISH:@RUN@" || job.ticks > 3600 || job.phase == "FAILED") {
            StringToFile(job.statefile, job.ticks > 3600 ? "TIMEOUT" : "EXIT_REQUESTED");
            SendToConsole("mat_specular 1; lservercfgfile @NORMAL@; quit");
            return 5.0;
        }
        if (job.phase == "RELOADED" || job.phase == "DISPATCHED") return 1.0;
        local host = GetListenServerHost();
        local ready = host != null && NetProps.HasProp(host, "m_iTeamNum") && NetProps.HasProp(host, "m_iHealth") &&
            NetProps.GetPropInt(host, "m_iTeamNum") == 2 && NetProps.GetPropInt(host, "m_iHealth") > 0;
        if (ready) {
            if (!NetProps.HasProp(host, "m_hViewEntity") || !NetProps.HasProp(host, "m_fFlags")) throw "required player properties missing";
            ready = NetProps.GetPropInt(host, "m_hViewEntity") == -1 && (NetProps.GetPropInt(host, "m_fFlags") & 32) == 0;
        }
        if (!ready) { job.stable = 0; return 1.0; }
        if (job.phase == "WAIT_PLAYER") {
            job.stable++;
            if (job.stable >= @STABLE@) {
                job.phase = "WAIT_MATERIALS"; job.stable = 0;
                StringToFile(job.statefile, job.phase);
                SendToConsole("mat_specular 0");
            }
            return 1.0;
        }
        if (Convars.GetFloat("mat_specular") != 0 || Convars.GetFloat("mat_hdr_level") != 2) { job.stable = 0; return 1.0; }
        local tone = Entities.FindByName(null, "@TONEMAP@");
        if (tone == null || !NetProps.HasProp(tone, "m_flCustomAutoExposureMax") ||
            NetProps.GetPropFloat(tone, "m_flCustomAutoExposureMax") != @EXPOSURE@) { job.stable = 0; return 1.0; }
        if (job.phase == "WAIT_MATERIALS") {
            job.stable++;
            if (job.stable >= @STABLE@) {
                job.phase = "READY";
                StringToFile(job.statefile, "READY");
                SendToConsole("exec @MARKER@_check");
            }
            return 1.0;
        }
        if (job.phase == "READY" && command == "CAPTURE:@RUN@") {
            job.phase = "DISPATCHED";
            StringToFile(job.statefile, "DISPATCHED");
            SendToConsole("exec @MARKER@_capture");
        }
        return 1.0;
    } catch (error) {
        job.phase = "FAILED";
        StringToFile(job.statefile, "FAILED");
        printl("@MARKER@_AUTO_FAILED: " + error);
        return 1.0;
    }
};
AddThinkToEnt(LmcAutoController.timer, "AutoTick");
'''
