"""Serial composition of audited single-map build and automatic capture."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from datetime import datetime
import json
from pathlib import Path
import subprocess
import traceback

from . import __version__
from .batch import _manifest, _write, _cell, _hash, batch_check
from .configuration import file_hash, load_config, verify_inventory


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _save(output, report):
    jobs = report['jobs']
    report['counts'] = dict(ready=sum(j['status'] == 'ready' for j in jobs),
                           failed=sum(j['status'] == 'failed' for j in jobs),
                           pending=sum(j['status'] not in ('ready', 'failed') for j in jobs))
    for job in jobs:
        _write(output / 'jobs' / (job['id'] + '.json'), job)
    _write(output / 'summary.json', report)
    _write(output / 'acceptance.json', dict(schema_version=1, runtime_accepted=False,
        note='Machine completion is not human acceptance. Record observations separately.',
        jobs=[{k: j[k] for k in ('id', 'map_name', 'run_dir', 'run_id', 'final_package', 'human_acceptance') if k in j}
              for j in jobs if j['status'] == 'ready']))
    lines = ['# Batch build / 批量构建与采样', '',
             'ready 仅表示机器流程完成，最终包尚未安装、尚未人工验收。', '',
             '| Job | Map | Status | Stage | Human acceptance |', '|---|---|---|---|---|']
    for job in jobs:
        lines.append('| ' + ' | '.join(_cell(job.get(k, '')) for k in
                     ('id', 'map_name', 'status', 'stage', 'human_acceptance')) + ' |')
    lines += ['', '最终包路径和 SHA256 见 acceptance.json；逐项日志见 jobs/<id>.log。',
              '采样或共享环境失败会停止队列；不要重复使用已有运行或报告目录。', '']
    temp = output / 'summary.md.tmp'
    with temp.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write('\n'.join(lines))
    temp.replace(output / 'summary.md')


def _same_json(a, b):
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _built_identity(run, evidence, root):
    if Path(run['config']['output_dir']).resolve() != root:
        raise ValueError('Built run output directory differs from batch job')
    if (not _same_json(run['input_inventory'], evidence['input_inventory']) or
            not _same_json(run['preflight'], evidence['preflight'])):
        raise ValueError('Built run inputs or conversion plan differ from batch preflight')


def _final(root, evidence):
    from .workflow import load_run
    run = load_run(root)
    _built_identity(run, evidence, root)
    journal = _read(root / 'auto-capture' / 'attempt.json')
    if (run['status'] != 'final_ready_pending_user_validation' or
            not run.get('capture_files_removed') or journal.get('status') != 'complete' or
            not journal.get('cleaned') or journal.get('run_id') != run['run_id']):
        raise ValueError('Final package or automatic capture cleanup is not complete')
    package = run['final_package']
    path = Path(package['vpk']).resolve()
    if not path.is_relative_to(root) or not package.get('native_extract_verified'):
        raise ValueError('Final package is outside run or missing extraction verification')
    if file_hash(path) != package['sha256']:
        raise ValueError('Final package bytes differ from audited hash')
    return run


def batch_run(manifest, output, launcher):
    """New batch only. Build errors continue; shared-state uncertainty stops."""
    from . import auto_capture, handoff, launch, workflow
    manifest, output, launcher = (Path(p).resolve() for p in (manifest, output, launcher))
    if output.exists():
        raise FileExistsError(f'Batch report already exists: {output}; choose a new directory')
    data, declared = _manifest(manifest, output)
    if launcher.is_relative_to(output):
        raise ValueError('Batch report must not contain launcher configuration')
    launcher_bytes = launcher.read_bytes()
    launcher_identity = dict(path=str(launcher), sha256=_hash(launcher_bytes))
    output.mkdir(parents=True, exist_ok=False)
    (output / 'jobs').mkdir()
    (output / 'manifest.json').write_bytes(data)
    (output / 'launcher.json').write_bytes(launcher_bytes)
    report = dict(schema_version=1, tool_version=__version__, operation='batch-run',
        created_at=datetime.now().astimezone().isoformat(), status='preflight',
        runtime_accepted=False, launcher=launcher_identity,
        jobs=[dict(id=j['id'], config=j['config'], status='pending', stage='preflight',
                   stages=dict(preflight='pending', build='not_run', capture='not_run'),
                   human_acceptance='not_run') for j in declared])
    _save(output, report)
    with auto_capture.run_lock(output, '.batch-run.lock'):
        try:
            checked = batch_check(manifest, output / 'preflight')
            if (manifest.read_bytes() != data or checked['manifest']['sha256'] != _hash(data) or
                    [(j['id'], j['config']) for j in checked['jobs']] !=
                    [(j['id'], j['config']) for j in declared]):
                raise ValueError('Batch manifest changed during preflight; choose a new batch')
            configs = {}
            for job, summary in zip(report['jobs'], checked['jobs']):
                evidence = _read(output / 'preflight' / 'jobs' / (job['id'] + '.json'))
                job['preflight_evidence'] = str(output / 'preflight' / 'jobs' / (job['id'] + '.json'))
                if summary['status'] != 'passed':
                    job.update(status='failed', error=evidence['error'])
                    job['stages']['preflight'] = 'failed'
                    continue
                job['stages']['preflight'] = 'passed'
                job['map_name'] = summary['map_name']
                try:
                    cfg = load_config(job['config'])
                    verify_inventory(evidence['input_inventory'])
                    if cfg['atmosphere_policy'] != 'replace':
                        raise ValueError('Batch automatic capture requires replace atmosphere policy')
                    launch.load_launcher(launcher, cfg['game_dir'])
                    if cfg['output_dir'].exists():
                        raise ValueError('output_dir already exists; choose a fresh run')
                    if launcher.is_relative_to(cfg['output_dir']):
                        raise ValueError('Run output must not contain launcher configuration')
                    configs[job['id']] = cfg
                    job['run_dir'] = str(cfg['output_dir'])
                    job['stage'] = 'queued'
                except (OSError, ValueError) as exc:
                    job.update(status='failed', stage='eligibility',
                               error=dict(stage='eligibility', type=type(exc).__name__, message=str(exc)))
            if any(j.get('error', {}).get('category') == 'internal_error' for j in report['jobs']):
                report.update(status='blocked', error=dict(type='PreflightInternalError',
                    message='Unexpected preflight exception; inspect evidence before any build/capture'))
                _save(output, report)
                return report
            report['status'] = 'running'
            _save(output, report)
            with ExitStack() as locks:
                games = sorted({cfg['game_dir'] for cfg in configs.values()})
                for game in games:
                    locks.enter_context(auto_capture.run_lock(game, '.lmc-batch.lock'))
                for job in report['jobs']:
                    if job['status'] == 'failed':
                        continue
                    cfg = configs[job['id']]
                    root = cfg['output_dir']
                    evidence = _read(job['preflight_evidence'])
                    job.update(status='running', stage='environment')
                    _save(output, report)
                    print(f"Batch {job['id']}: build/capture; log {output / 'jobs' / (job['id'] + '.log')}", flush=True)
                    try:
                        handoff.require_game_closed()
                        verify_inventory([launcher_identity])
                        # Keep native logs and controller events per job, without
                        # interleaving or swallowing the durable single-run files.
                        with (output / 'jobs' / (job['id'] + '.log')).open('x', encoding='utf-8', buffering=1) as log:
                            with redirect_stdout(log), redirect_stderr(log):
                                job['stage'] = 'input_identity'
                                verify_inventory(evidence['input_inventory'])
                                if root.exists():
                                    raise ValueError('output_dir appeared after preflight; preserved')
                                job['stage'] = 'build'
                                job['stages']['build'] = 'running'
                                _save(output, report)
                                workflow.build(Path(job['config']))
                                built = workflow.load_run(root)
                                _built_identity(built, evidence, root)
                                if built['status'] != 'offline_ready':
                                    raise ValueError('Build did not produce an offline-ready run')
                                job['run_id'] = built['run_id']
                                job['stages']['build'] = 'passed'
                                job['stage'] = 'capture'
                                job['stages']['capture'] = 'running'
                                _save(output, report)
                                verify_inventory([launcher_identity])
                                auto_capture.run(root, launcher)
                                job['stage'] = 'final_verification'
                                _save(output, report)
                                final = _final(root, evidence)
                                handoff.require_game_closed()
                                job['final_package'] = {k: final['final_package'][k] for k in
                                                        ('vpk', 'sha256', 'native_extract_verified')}
                                job.update(status='ready', stage='awaiting_human_acceptance')
                                job['stages']['capture'] = 'passed'
                    except KeyboardInterrupt:
                        job['status'] = 'interrupted'
                        raise
                    except Exception as exc:
                        expected = isinstance(exc, (OSError, ValueError, subprocess.SubprocessError))
                        error = dict(stage=job['stage'], type=type(exc).__name__, message=str(exc))
                        if not expected:
                            error['traceback'] = traceback.format_exc()
                        job.update(status='failed', error=error)
                        stage = 'capture' if job['stage'] == 'final_verification' else job['stage']
                        if stage in job['stages']:
                            job['stages'][stage] = 'failed'
                        if job['stage'] not in ('build', 'input_identity') or not expected:
                            report['status'] = 'blocked'
                            job['recovery_hint'] = (
                                'Keep all evidence. If auto-capture/attempt.json exists, exit the game and run '
                                f'python -m l4d2_bsp.workflow recover-auto-capture --run "{root}". '
                                'Otherwise inspect the recorded stage/environment error. '
                                'Retry only with a fresh config/output directory and batch report.')
                    _save(output, report)
                    print(f"Batch {job['id']}: {job['status']}", flush=True)
                    if report['status'] == 'blocked':
                        break
            if report['status'] != 'blocked':
                report['status'] = 'completed_with_failures' if report['counts']['failed'] else 'complete'
        except KeyboardInterrupt:
            report['status'] = 'interrupted'
            _save(output, report)
            raise
        except Exception as exc:
            report.update(status='blocked', error=dict(type=type(exc).__name__, message=str(exc)))
            _save(output, report)
            raise
        _save(output, report)
    return report
