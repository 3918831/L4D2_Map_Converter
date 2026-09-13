"""Run with python -m l4d2_bsp; all output files are exclusive creates."""
import argparse
import json
from pathlib import Path
import sys

from .inspect import compare_bytes, container, inspect_bytes, sha256
from .patch import patch_visual


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, indent=2)+'\n').encode('utf-8')


def write_outputs(outputs, inputs=()):
    """Reserve all files exclusively before writing; never overwrite existing data."""
    protected = {Path(p).resolve() for p in inputs}
    seen, opened = set(), []
    for path, _ in outputs:
        resolved = Path(path).resolve()
        if resolved in protected or resolved in seen:
            raise ValueError('Output aliases an input or another output')
        if Path(path).exists() or Path(path).is_symlink():
            raise ValueError(f'Output already exists: {path}')
        seen.add(resolved)
    try:
        for path, data in outputs:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open('xb')
            opened.append((path, handle))
        for (_, data), (_, handle) in zip(outputs, opened):
            handle.write(data)
            handle.flush()
        for _, handle in opened:
            handle.close()
    except BaseException:
        for path, handle in opened:
            handle.close()
            path.unlink(missing_ok=True)
        raise


def parser():
    root = argparse.ArgumentParser(description='Loss-preserving L4D2 BSP visual experiments')
    commands = root.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'roundtrip', 'patch', 'compare'):
        cmd = commands.add_parser(name)
        cmd.add_argument('input', type=Path)
        cmd.add_argument('--kind', choices=('bsp', 'lmp'))
        if name == 'compare':
            cmd.add_argument('other', type=Path)
        cmd.add_argument('--output', type=Path, required=name in ('roundtrip', 'patch'))
        if name in ('roundtrip', 'patch'):
            cmd.add_argument('--report', type=Path, required=name == 'patch')
        if name == 'patch':
            cmd.add_argument('--classname', required=True)
            cmd.add_argument('--targetname')
            cmd.add_argument('--key', required=True)
            cmd.add_argument('--expected', required=True)
            cmd.add_argument('--value', required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        data = args.input.read_bytes()
        kind = args.kind or args.input.suffix.lower().removeprefix('.')
        inputs = [args.input]
        if args.command == 'inspect':
            result = inspect_bytes(data, kind)
        elif args.command == 'compare':
            result = compare_bytes(data, args.other.read_bytes(), kind)
            inputs.append(args.other)
        elif args.command == 'roundtrip':
            container(data, kind)
            result = dict(identical=True, sha256=sha256(data), size=len(data))
            outputs = [(args.output, data)]
            if args.report:
                outputs.append((args.report, json_bytes(result)))
            write_outputs(outputs, inputs)
            print(json_bytes(result).decode('utf-8'), end='')
            return 0
        else:
            changed, patch = patch_visual(data, kind=kind, classname=args.classname,
                targetname=args.targetname, key=args.key, expected=args.expected, value=args.value)
            comparison = compare_bytes(data, changed, kind)
            start, end = patch['start'], patch['end']
            verification = dict(io_unchanged=comparison['io_unchanged'],
                outside_patch_unchanged=data[:start] == changed[:start] and data[end:] == changed[end:],
                same_size=len(data) == len(changed), header_unchanged=comparison['header_unchanged'],
                non_entity_lumps_unchanged=comparison['non_entity_lumps_unchanged'])
            if not all(verification.values()):
                raise ValueError('Patch failed preservation verification')
            result = dict(input=str(args.input.resolve()), output=str(args.output.resolve()),
                          patch=patch, verification=verification, comparison=comparison)
            write_outputs([(args.output, changed), (args.report, json_bytes(result))], inputs)
            print(json_bytes(result).decode('utf-8'), end='')
            return 0
        if args.output:
            write_outputs([(args.output, json_bytes(result))], inputs)
            print(json.dumps({'report': str(args.output.resolve()), 'sha256': result.get('sha256')}))
        else:
            print(json_bytes(result).decode('utf-8'), end='')
        return 0
    except (OSError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
