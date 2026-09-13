"""Reproducible native VRAD probe. Results remain unvalidated experimental outputs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from l4d2_bsp.inspect import compare_bytes, inspect_bytes, sha256
from l4d2_bsp.lighting import prepare_environment_input


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--game-root', type=Path, required=True)
    parser.add_argument('--tools-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', choices=('fast', 'normal-onebounce', 'preview'), default='fast')
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.output.is_symlink():
            raise ValueError('Output directory must not already exist')
        if args.timeout < 1:
            raise ValueError('Timeout must be positive')
        if args.source.suffix.lower() != '.bsp':
            raise ValueError('Source must have a .bsp filename')
        stage = args.output.resolve()
        game_root, tools_root = args.game_root.resolve(), args.tools_root.resolve()
        if any(stage.is_relative_to(root) for root in (game_root, tools_root)):
            raise ValueError('Output must not be inside either game or tools installation')
        source = args.source.read_bytes()
        info = inspect_bytes(source)
        if 'entity_error' in info or 'game_lump_error' in info:
            raise ValueError('Input failed structure inspection')
        data, preparation = source, None
        if args.reference:
            data, preparation = prepare_environment_input(source, args.reference.read_bytes())
        tool = tools_root/'bin'/'vrad.exe'
        game = game_root/'left4dead2'
        if not tool.is_file() or not (game/'gameinfo.txt').is_file():
            raise ValueError('VRAD executable or test gameinfo.txt missing')
        tool_hash = sha256(tool.read_bytes())
        stage.mkdir(parents=True, exist_ok=False)
        dest = stage/args.source.name
        dest.write_bytes(data)
        (stage/'input.bsp.snapshot').write_bytes(data)
        profiles = {
            'fast': ['-fast', '-bounce', '1'],
            'normal-onebounce': ['-bounce', '1'],
            'preview': ['-bounce', '4', '-StaticPropLighting', '-StaticPropPolys', '-TextureShadows'],
        }
        options = profiles[args.profile]
        command = [str(tool), '-game', str(game), '-novconfig', '-hdr', *options,
                   '-threads', '4', '-low', str(dest)]
        report = dict(argv=command, cwd=str(stage), tool_sha256=tool_hash,
            source=str(args.source.resolve()), source_sha256=sha256(source),
            compiler_input_sha256=sha256(data), preparation=preparation,
            profile=args.profile, status='launching',
            limitations=['No VMF/VBSP/VVIS', 'No adjacent LMP overlay applied to compiler input',
                        'Preview quality, not final production bake', 'Cubemaps not recaptured',
                        'Gameplay and visual consistency unverified'])
        logpath = stage/'vrad.log'
        reportpath = stage/'run.json'
        def record():
            reportpath.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        record()
        start = time.monotonic()
        with logpath.open('wb') as log:
            try:
                process = subprocess.Popen(command, cwd=stage, env=dict(os.environ, VPROJECT=str(game)),
                    stdout=log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError as exc:
                report.update(status='launch_failed', error=str(exc),
                              elapsed_seconds=round(time.monotonic()-start, 3))
                record()
                raise
            report['pid'] = process.pid
            report['status'] = 'running'
            record()
            print(json.dumps({'pid': process.pid, 'log': str(logpath)}), flush=True)
            try:
                report['exit_code'] = process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                report['exit_code'] = 124
                report['timed_out'] = True
        report['elapsed_seconds'] = round(time.monotonic()-start, 3)
        try:
            report['source_unchanged'] = sha256(args.source.read_bytes()) == report['source_sha256']
        except OSError as exc:
            report['source_unchanged'] = False
            report['source_read_error'] = str(exc)
        if not report['source_unchanged']:
            report['status'] = 'source_changed'
        elif report.get('timed_out'):
            report['status'] = 'compiler_timeout'
        elif report['exit_code'] != 0:
            report['status'] = 'compiler_failed'
        else:
            report['status'] = 'validating_output'
        comparison = None
        try:
            compiled = dest.read_bytes()
            report['output_sha256'] = sha256(compiled)
            inspection = inspect_bytes(compiled)
            (stage/'inspect.json').write_text(json.dumps(inspection, indent=2)+'\n', encoding='utf-8')
            errors = {key: inspection[key] for key in ('entity_error', 'game_lump_error', 'pak_error')
                      if key in inspection}
            if errors:
                raise ValueError(f'Compiler output failed structure inspection: {errors}')
            comparison = compare_bytes(data, compiled)
            (stage/'comparison.json').write_text(json.dumps(comparison, indent=2)+'\n', encoding='utf-8')
        except (ValueError, OSError) as exc:
            report['output_validation_error'] = str(exc)
            if report['status'] == 'validating_output':
                report['status'] = 'output_validation_failed'
        else:
            if report['status'] == 'validating_output':
                report['status'] = 'compiled_pending_runtime_validation'
        record()
        print(json.dumps({'status': report['status'], 'elapsed_seconds': report['elapsed_seconds'],
                          'changed_lumps': comparison['changed_lumps'] if comparison else None,
                          'report': str(reportpath)}))
        return 0 if report['status'] == 'compiled_pending_runtime_validation' else 2
    except (ValueError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
