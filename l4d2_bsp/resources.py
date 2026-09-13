"""Read-only VPK directory evidence, not a generalized Source mount resolver."""
from pathlib import Path
import hashlib
import struct


def vpk_index(path):
    """Read v1/v2 trees only. Payload metadata is evidence, not a CRC validation."""
    path = Path(path)
    with path.open('rb') as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise ValueError('Truncated VPK header')
        magic, version, size = struct.unpack('<III', header)
        if magic != 0x55aa1234 or version not in (1, 2):
            raise ValueError('Unsupported VPK magic or version')
        file_data_section_size = None
        if version == 2:
            sections = stream.read(16)
            if len(sections) != 16:
                raise ValueError('Truncated VPK v2 header')
            file_data_section_size, _, _, _ = struct.unpack('<4I', sections)
        if size > 64*1024*1024:
            raise ValueError('VPK directory exceeds supported 64 MiB limit')
        tree = stream.read(size)
        if len(tree) != size:
            raise ValueError('Truncated VPK directory tree')
    cursor = 0

    def string():
        nonlocal cursor
        end = tree.find(b'\0', cursor)
        if end < 0:
            raise ValueError('Unterminated VPK tree string')
        # Retain opaque legacy path bytes without making unrelated ASCII lookups fail.
        result = tree[cursor:end].decode('utf-8', errors='surrogateescape')
        cursor = end+1
        return result

    result = {}
    while extension := string():
        while directory := string():
            while basename := string():
                if cursor+18 > size:
                    raise ValueError('Truncated VPK entry')
                crc, preload, archive, offset, length, end = struct.unpack_from('<IHHIIH', tree, cursor)
                cursor += 18
                if end != 0xffff or cursor+preload > size:
                    raise ValueError('Invalid VPK entry terminator or preload span')
                cursor += preload
                name = (('' if directory == ' ' else directory+'/') + basename +
                        ('' if extension == ' ' else '.'+extension)).replace('\\', '/').lower()
                if name in result:
                    raise ValueError(f'Duplicate VPK entry: {name}')
                result[name] = dict(package=str(path.resolve()), vpk_version=version,
                    crc32=f'{crc:08x}', preload_bytes=preload, archive_index=archive,
                    offset=offset, length=length, tree_size=size,
                    file_data_section_size=file_data_section_size)
    if cursor != size:
        raise ValueError('Unexpected bytes after VPK tree terminator')
    return result


def lookup_resources(roots, names):
    """List every package candidate; deliberately does not claim runtime priority."""
    wanted = {name.lower(): [] for name in names}
    packages, errors = [], []
    for root in roots:
        for name in wanted:
            loose = Path(root)/name
            if not loose.resolve().is_relative_to(Path(root).resolve()):
                raise ValueError('Resource path escapes search root')
            if loose.is_file():
                data = loose.read_bytes()
                wanted[name].append(dict(kind='loose_file', path=str(loose.resolve()),
                    sha256=hashlib.sha256(data).hexdigest(), size=len(data), payload_available=True))
        for path in sorted(Path(root).glob('*_dir.vpk')):
            packages.append(str(path.resolve()))
            try:
                entries = vpk_index(path)
            except (OSError, ValueError) as exc:
                errors.append(dict(package=str(path.resolve()), error=str(exc)))
                continue
            for name in wanted:
                if name in entries:
                    entry = dict(entries[name])
                    entry['kind'] = 'vpk_entry'
                    archive = entry['archive_index']
                    payload = path if archive == 0x7fff else path.with_name(path.name[:-7]+f'{archive:03d}.vpk')
                    base = (12 if entry['vpk_version'] == 1 else 28) if archive == 0x7fff else 0
                    if archive == 0x7fff:
                        base += entry['tree_size']
                    internal_bounds_ok = (archive != 0x7fff or entry['vpk_version'] != 2 or
                        entry['offset']+entry['length'] <= entry['file_data_section_size'])
                    entry['payload_package'] = str(payload.resolve())
                    entry['payload_available'] = entry['length'] == 0 or (
                        internal_bounds_ok and payload.is_file() and
                        base+entry['offset']+entry['length'] <= payload.stat().st_size)
                    wanted[name].append(entry)
    return dict(resources={name: dict(found=any(e['payload_available'] for e in entries), candidates=entries)
                           for name, entries in wanted.items()}, packages_scanned=packages, errors=errors,
                limitation='Directory names and payload bounds checked; CRC/content and transitive material dependencies are not validated. Runtime mounts are unverified.')
