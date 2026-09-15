"""Create a source toolkit from an explicit public-file allowlist, no game files."""
import argparse
import hashlib
from pathlib import Path
import zipfile


PUBLIC_FILES = ('README.md', 'NOTICE.md', 'CHANGELOG.md', 'pyproject.toml', '.gitignore',
                'docs/guide.zh-CN.md', 'docs/architecture.md', 'docs/validation.md', 'docs/release.md',
                'docs/presets.md', 'docs/c4m3-guide.zh-CN.md', 'docs/c4m3-preparation.md',
                'docs/auto-capture.zh-CN.md',
                'docs/native-tools.zh-CN.md',
                'docs/evolution-strategy.zh-CN.md', 'docs/generic-analysis.zh-CN.md',
                'l4d2_bsp/preset_data/c5m1-daylight-v1.json',
                'l4d2_bsp/preset_data/c4m3-overcast-static-v1.json')


def build_release(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    paths = [root / name for name in PUBLIC_FILES]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f'Missing public release file: {path}')
    for directory in ('l4d2_bsp', 'tests', 'scripts'):
        paths.extend(sorted((root / directory).glob('*.py')))
    paths.extend(sorted((root / 'examples').glob('*.example.json')))
    if any(path.is_symlink() or not path.resolve().is_relative_to(root) for path in paths):
        raise ValueError('Release source symlink or path escape')
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in paths}
    if output in paths:
        raise ValueError('Release archive must not replace a source file')
    inventory = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name, data in sorted(files.items()))
    files['FILES.sha256'] = inventory.encode('utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(files):
            raise ValueError('Release archive failed CRC/inventory verification')
        for name, data in files.items():
            if archive.read(name) != data:
                raise ValueError(f'Release byte verification failed: {name}')
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + '.sha256').write_text(f'{digest}  {output.name}\n', encoding='ascii')
    return {'path': str(output), 'sha256': digest, 'files': len(files)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(build_release(Path(__file__).resolve().parents[1], args.output))
