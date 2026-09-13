"""Read structural ranges while retaining the original file as authority."""

from dataclasses import dataclass
import struct


BSP_HEADER_SIZE = 1036
LMP_HEADER_SIZE = 20


class FormatError(ValueError):
    """Malformed or unsupported binary layout."""


def _check_range(offset: int, length: int, size: int, header_size: int) -> None:
    if offset < 0 or length < 0 or offset > size or length > size - offset:
        raise FormatError(f'Invalid file range: offset={offset}, length={length}, size={size}')
    if length and offset < header_size:
        raise FormatError(f'Payload range overlaps the {header_size}-byte header')


@dataclass(frozen=True)
class Lump:
    index: int
    version: int
    offset: int
    length: int
    fourcc: bytes


@dataclass(frozen=True)
class BspFile:
    data: bytes
    version: int
    revision: int
    lumps: tuple[Lump, ...]

    @classmethod
    def parse(cls, data: bytes) -> 'BspFile':
        data = bytes(data)
        if len(data) < BSP_HEADER_SIZE:
            raise FormatError('Truncated BSP header')
        magic, version = struct.unpack_from('<4si', data)
        if magic != b'VBSP':
            raise FormatError('Invalid BSP magic; expected VBSP')
        if version != 21:
            raise FormatError(f'Unsupported BSP version {version}; only L4D2 v21 is supported')
        lumps = []
        for index in range(64):
            # L4D2 v21 uses version first, unlike the general Source layout.
            lump_version, offset, length, fourcc = struct.unpack_from('<iii4s', data, 8 + 16 * index)
            _check_range(offset, length, len(data), BSP_HEADER_SIZE)
            lumps.append(Lump(index, lump_version, offset, length, fourcc))
        occupied = sorted((lump.offset, lump.offset + lump.length, lump.index)
                          for lump in lumps if lump.length)
        for previous, current in zip(occupied, occupied[1:]):
            if current[0] < previous[1]:
                raise FormatError(f'Overlapping lumps {previous[2]} and {current[2]}')
        revision, = struct.unpack_from('<i', data, 1032)
        return cls(data, version, revision, tuple(lumps))

    def lump_bytes(self, index: int) -> bytes:
        if not 0 <= index < 64:
            raise ValueError(f'Invalid lump index: {index}')
        lump = self.lumps[index]
        return self.data[lump.offset:lump.offset + lump.length]

    @property
    def entity_span(self) -> tuple[int, int]:
        entity_lump = self.lumps[0]
        return entity_lump.offset, entity_lump.length


@dataclass(frozen=True)
class LumpFile:
    data: bytes
    offset: int
    lump_id: int
    version: int
    length: int
    revision: int

    @classmethod
    def parse(cls, data: bytes) -> 'LumpFile':
        data = bytes(data)
        if len(data) < LMP_HEADER_SIZE:
            raise FormatError('Truncated LMP header')
        offset, lump_id, version, length, revision = struct.unpack_from('<5i', data)
        if lump_id != 0:
            raise FormatError(f'Unsupported LMP lump id {lump_id}; expected entities (0)')
        _check_range(offset, length, len(data), LMP_HEADER_SIZE)
        return cls(data, offset, lump_id, version, length, revision)

    @property
    def entity_span(self) -> tuple[int, int]:
        return self.offset, self.length
