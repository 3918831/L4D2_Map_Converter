"""Serial, read-only batch preflight using the existing single-map check."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import traceback

from . import __version__
from .configuration import input_inventory, verify_inventory, load_config, file_hash


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _json(data):
    return json.loads(data.decode('utf-8-sig'), object_pairs_hook=_object)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _path(base, value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError('Expected a nonempty path without control characters')
    return (base / value).resolve()


def _overlaps(a, b):
    return a.is_relative_to(b) or b.is_relative_to(a)


def _declared_inputs(path, raw):
    """Best-effort evidence even when normal configuration validation fails."""
    if not isinstance(raw, dict):
        return []
    values = [(key, value) for key, value in raw.items()
              if key in ('source_bsp', 'reference_bsp', 'reference_lmp', 'nav', 'exclude') and value]
    if isinstance(raw.get('mode_lmps'), dict):
        values.extend(('mode_lmps.' + key, value) for key, value in raw['mode_lmps'].items())
    if 'preset' in raw:
        values.append(('preset', raw['preset']))
    evidence = []
    for name, value in values:
        entry = dict(name=name, declared=value)
        try:
            if name == 'preset':
                from .presets import load_preset
                source = load_preset(value).source_path
            else:
                source = _path(path.parent, value)
            entry['path'] = str(source)
            entry.update(size=source.stat().st_size, sha256=file_hash(source), status='read')
        except (OSError, ValueError) as exc:
            entry.update(status='unavailable', error=dict(type=type(exc).__name__, message=str(exc)))
        evidence.append(entry)
    return evidence


def _manifest(path, output):
    data = path.read_bytes()
    value = _json(data)
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'jobs'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value['jobs'], list) or not value['jobs']):
        raise ValueError('Expected schema_version 1 and a nonempty jobs list')
    jobs, ids, run_dirs = [], set(), []
    for item in value['jobs']:
        if (not isinstance(item, dict) or set(item) != {'id', 'config'}
                or not isinstance(item['id'], str)
                or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', item['id'])
                or item['id'] in ids):
            raise ValueError('Jobs require unique lowercase safe ids and a config path')
        ids.add(item['id'])
        cfg_path = _path(path.parent, item['config'])
        if cfg_path.is_relative_to(output):
            raise ValueError('Report directory must not contain an input config')
        # Only inspect declared paths here. A bad individual config remains a
        # per-job failure, so other jobs can still run.
        try:
            raw = _json(cfg_path.read_bytes())
        except (OSError, ValueError, UnicodeError):
            raw = None
        declared = {}
        if isinstance(raw, dict):
            for key in ('output_dir', 'game_dir', 'tools_dir'):
                try:
                    declared[key] = _path(cfg_path.parent, raw.get(key))
                except (ValueError, OSError):
                    pass  # Invalid job configuration is reported by check.
        for key in ('game_dir', 'tools_dir'):
            if key in declared and output.is_relative_to(declared[key].parent):
                raise ValueError('Batch report must be outside game/tools installation')
        if 'output_dir' in declared:
            target = declared['output_dir']
            if _overlaps(output, target):
                raise ValueError('Batch report and future build output_dir overlap')
            if any(_overlaps(target, previous) for previous in run_dirs):
                raise ValueError('Jobs require independent, non-overlapping output_dir values')
            run_dirs.append(target)
        jobs.append(dict(id=item['id'], config=str(cfg_path), status='pending', human_acceptance='not_run'))
    return data, jobs


def _write(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')
    temporary.replace(path)


def _cell(value):
    return str(value).replace('|', '\\|').replace('\r', ' ').replace('\n', ' ').replace('`', "'")


def _save(output, report):
    report['counts'] = dict(passed=sum(j['status'] == 'passed' for j in report['jobs']),
                           failed=sum(j['status'] == 'failed' for j in report['jobs']),
                           pending=sum(j['status'] not in ('passed', 'failed') for j in report['jobs']))
    _write(output / 'summary.json', report)
    lines = ['# Batch preflight / 批量预检查', '',
             '只检查输入和修改方案；未烘焙、未启动游戏、未进行人工验收。', '',
             '| Job | Map | Preset | Rule | Check | Warnings | Error category |',
             '|---|---|---|---|---|---|---|']
    for j in report['jobs']:
        lines.append('| ' + ' | '.join(_cell(j.get(k, '')) for k in
            ('id', 'map_name', 'preset', 'conversion', 'status', 'warning_count', 'error_category')) + ' |')
    lines.extend(['', '每项完整证据见 jobs/<id>.json；错误保留阶段、类型与原文。',
                  '通过只代表当前单图 check 通过，不保证编译、完整资源依赖、运行时效果或玩法。', ''])
    temporary = output / 'summary.md.tmp'
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write('\n'.join(lines))
    temporary.replace(output / 'summary.md')


def batch_check(manifest, output):
    """Keep independent results and checkpoints; never build or launch a game."""
    from .workflow import check
    manifest, output = Path(manifest).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(f'Batch report already exists: {output}; choose a new directory')
    data, jobs = _manifest(manifest, output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'jobs').mkdir()
    (output / 'manifest.json').write_bytes(data)
    report = dict(schema_version=1, tool_version=__version__, operation='batch-check',
                  created_at=datetime.now().astimezone().isoformat(), status='running',
                  manifest=dict(path=str(manifest), sha256=_hash(data)), jobs=jobs,
                  implementation=[dict(name=p.name, sha256=_hash(p.read_bytes()))
                                  for p in sorted(Path(__file__).parent.glob('*.py'))],
                  runtime_accepted=False)
    _save(output, report)
    for summary in jobs:
        path = Path(summary['config'])
        job = dict(id=summary['id'], config_path=str(path), status='running',
                   stages=dict(preflight='running', bake='not_run', capture='not_run', packaging='not_run'),
                   human_acceptance='not_run', warnings=[])
        summary['status'] = 'running'
        _save(output, report)
        stage = 'configuration_and_inputs'

        def progress(value):
            nonlocal stage
            stage = value

        try:
            config_bytes = path.read_bytes()
            job['config_sha256'] = _hash(config_bytes)
            job['config_snapshot'] = _json(config_bytes)
            job['declared_inputs'] = _declared_inputs(path, job['config_snapshot'])
            before_cfg = load_config(path)
            job['input_inventory_before'] = input_inventory(before_cfg)
            cfg, preflight, _, _ = check(path, progress=progress)
            job['preflight'] = preflight
            stage = 'input_identity'
            inventory = input_inventory(cfg)
            job['input_inventory'] = inventory
            if path.read_bytes() != config_bytes:
                raise ValueError('Job config changed during preflight; rerun in a new report directory')
            if inventory != job['input_inventory_before']:
                raise ValueError('Inputs changed during preflight; rerun in a new report directory')
            # Discovery and plans record bytes read before conversion. Compare
            # those identities to the inventory taken after check as well.
            by_path = {item['path']: item['sha256'] for item in inventory}
            if by_path[str(path)] != job['config_sha256']:
                raise ValueError('Job config changed while inventorying inputs')
            if cfg.get('preset_file') and by_path[str(cfg['preset_file'])] != preflight['preset']['sha256']:
                raise ValueError('Preset changed during preflight')
            if by_path[str(cfg['source_bsp'])] != preflight['base_style']['source_sha256']:
                raise ValueError('Source BSP changed during preflight')
            if cfg.get('reference_bsp') and by_path[str(cfg['reference_bsp'])] != preflight['base_style']['reference_sha256']:
                raise ValueError('Reference BSP changed during preflight')
            for key, p in cfg['mode_lmps'].items():
                if by_path[str(p)] != preflight['mode_styles'][key]['source_sha256']:
                    raise ValueError('Mode patch changed during preflight')
                if cfg.get('reference_lmp') and by_path[str(cfg['reference_lmp'])] != preflight['mode_styles'][key]['reference_sha256']:
                    raise ValueError('Reference mode patch changed during preflight')
            verify_inventory(inventory)
            warnings = list(dict.fromkeys(preflight.get('limitations', []) +
                [w for a in [preflight['base_style'], *preflight['mode_styles'].values()]
                 for w in a.get('coverage_warnings', [])]))
            job.update(status='passed', preflight=preflight, input_inventory=inventory, warnings=warnings,
                       future_output_dir=str(cfg['output_dir']), future_output_exists=cfg['output_dir'].exists())
            job['stages']['preflight'] = 'passed'
            summary.update(status='passed', map_name=preflight['map_name'],
                           preset=(preflight.get('preset') or {}).get('id'),
                           conversion=cfg.get('conversion', cfg['profile']), warning_count=len(warnings))
        except KeyboardInterrupt:
            job['status'] = summary['status'] = 'interrupted'
            job['stages']['preflight'] = 'interrupted'
            report['status'] = 'interrupted'
            _write(output / 'jobs' / (job['id'] + '.json'), job)
            _save(output, report)
            raise
        except Exception as exc:
            expected = isinstance(exc, (OSError, ValueError))
            category = ('missing_file' if isinstance(exc, FileNotFoundError) else
                        {'conversion_plan': 'conversion_rejected', 'resources': 'resource_check'}.get(stage, stage))
            error = dict(stage=stage, category=category if expected else 'internal_error',
                         type=type(exc).__name__, message=str(exc))
            if not expected:
                error['traceback'] = traceback.format_exc()
            job.update(status='failed', error=error)
            job['stages']['preflight'] = 'failed'
            summary.update(status='failed', error_category=error['category'])
        _write(output / 'jobs' / (job['id'] + '.json'), job)
        _save(output, report)
    report['status'] = 'completed_with_failures' if report['counts']['failed'] else 'complete'
    _save(output, report)
    return report
