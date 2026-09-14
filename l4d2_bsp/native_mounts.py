"""Generate a compile-local Source gameinfo with explicit resource mounts."""

from pathlib import Path


def write_resource_root_gameinfo(directory: Path, resource_roots: list[Path]) -> Path:
    """Write an exclusive ASCII gameinfo.txt preserving resource_roots order."""
    directory = Path(directory)
    if not isinstance(resource_roots, list) or not resource_roots:
        raise ValueError('resource_roots must be a nonempty list of directories')
    roots = []
    for value in resource_roots:
        raw = str(value)
        if '"' in raw:
            raise ValueError('Resource root paths must not contain a quote')
        if any(ord(character) < 32 for character in raw):
            raise ValueError('Resource root paths must not contain control characters')
        if not raw.isascii():
            raise ValueError('Resource root paths must use ASCII for native tools')
        path = Path(value).resolve()
        if not path.is_dir():
            raise ValueError(f'Resource root is not a directory: {path}')
        rendered = path.as_posix()
        if '"' in rendered or any(ord(character) < 32 for character in rendered):
            raise ValueError('Resolved resource root paths must not contain quotes or control characters')
        if not rendered.isascii():
            raise ValueError('Resolved resource root paths must use ASCII for native tools')
        roots.append(rendered)
    lines = [
        '"GameInfo"',
        '{',
        '    "game" "LMC Compile"',
        '    "FileSystem"',
        '    {',
        '        "SteamAppId" "550"',
        '        "ToolsAppId" "563"',
        '        "SearchPaths"',
        '        {',
        *[f'            "Game" "{root}"' for root in roots],
        '        }',
        '    }',
        '}',
        '',
    ]
    output = directory / 'gameinfo.txt'
    with output.open('x', encoding='ascii', newline='\n') as stream:
        stream.write('\n'.join(lines))
    return output
