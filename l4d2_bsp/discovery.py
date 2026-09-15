"""Discover loose map inputs without assuming a campaign or mode set."""
from pathlib import Path
import re

from .configuration import file_hash


def _record(path):
    return dict(path=str(path.resolve()), size=path.stat().st_size, sha256=file_hash(path))


def discover_inputs(source_bsp, *, search_dirs=()):
    """Use the explicit BSP and ordered companion directories (not game mounts).

    Only loose files are discovered. Absence is scoped to these directories;
    no statement about mode availability in VPKs or the running game is made.
    """
    source = Path(source_bsp).resolve(strict=True)
    if not source.is_file():
        raise ValueError('source_bsp must be a file')
    if source.suffix.lower() != '.bsp' or not re.fullmatch(r'[A-Za-z0-9_-]+', source.stem):
        raise ValueError('Expected a BSP with an ASCII letter/digit/underscore/hyphen map name')
    name = source.stem.lower()
    roots = []
    for raw in (*search_dirs, source.parent):
        path = Path(raw).resolve(strict=True)
        if not path.is_dir():
            raise ValueError(f'Search path is not a directory: {path}')
        if path not in roots:
            roots.append(path)
    candidates, unknown = {}, []
    pattern = re.compile(re.escape(name) + r'_([a-z])_(0|[1-9][0-9]*)\.lmp')
    for root in roots:
        seen = set()
        for path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            lower = path.name.lower()
            if not (lower in (name + '.nav', name + '_exclude.lst') or
                    lower.startswith(name + '_') and lower.endswith('.lmp')):
                continue
            if not path.is_file():
                raise ValueError(f'Expected companion file: {path}')
            if lower in seen:
                raise ValueError(f'Ambiguous case-insensitive companion name in {root}: {lower}')
            seen.add(lower)
            if lower.endswith('.lmp') and not pattern.fullmatch(lower):
                unknown.append(_record(path))
            else:
                candidates.setdefault(lower, []).append(_record(path))

    def choice(filename):
        items = candidates.get(filename, [])
        return dict(status='found' if items else 'not_found_in_search_dirs',
                    selected=items[0] if items else None, candidates=items)

    modes = {}
    for filename in sorted(candidates):
        match = pattern.fullmatch(filename)
        if match:
            modes['_'.join(match.groups())] = choice(filename)
    return dict(map_name=name, source_bsp=_record(source),
                search_dirs=[str(p) for p in roots], mode_lmps=modes,
                mode_discovery='found' if modes else 'none_found_in_search_dirs',
                nav=choice(name + '.nav'), exclude=choice(name + '_exclude.lst'),
                unrecognized_companions=unknown,
                limitation='Loose companion files only; explicit directory order is not verified runtime mount order.')
