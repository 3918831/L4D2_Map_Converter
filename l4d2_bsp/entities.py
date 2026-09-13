"""Strict entity token reader. No serializer or escape normalization is used."""

from dataclasses import dataclass


class EntityError(ValueError):
    """Malformed entity text or a non-unique requested key."""


@dataclass(frozen=True)
class Pair:
    key: str
    value: str
    value_start: int
    value_end: int


@dataclass(frozen=True)
class Entity:
    pairs: tuple[Pair, ...]
    start: int
    end: int

    def values(self, key: str) -> list[str]:
        return [pair.value for pair in self.pairs if pair.key == key]

    def one(self, key: str) -> str | None:
        values = self.values(key)
        if len(values) > 1:
            raise EntityError(f'Duplicate entity key: {key!r}')
        return values[0] if values else None


def _tokens(data: bytes):
    index = 0
    size = len(data)
    while index < size:
        byte = data[index]
        if byte == 0:
            if any(value not in b'\0 \t\r\n\v\f' for value in data[index:]):
                raise EntityError(f'Non-padding data after entity NUL terminator at {index}')
            return
        if byte in b' \t\r\n\v\f':
            index += 1
            continue
        if data[index:index + 2] == b'//':
            while index < size and data[index] not in b'\n\r\0':
                index += 1
            continue
        if byte in b'{}':
            yield chr(byte), index, index + 1
            index += 1
            continue
        if byte != 34:
            raise EntityError(f'Expected quoted token or brace at byte {index}')
        start = index + 1
        index = start
        while index < size and data[index] != 34:
            if data[index] == 0:
                raise EntityError(f'NUL within quoted token at byte {index}')
            if data[index:index + 2] == b'\\"':
                raise EntityError(f'Ambiguous backslash/quote sequence at byte {index}; escapes are unsupported')
            index += 1
        if index == size:
            raise EntityError(f'Unterminated quoted token at byte {start - 1}')
        yield 'string', start, index
        index += 1


def parse_entities(data: bytes) -> list[Entity]:
    """Return ordered entities with Latin1 raw values and interior byte offsets.

    Ordinary backslashes are retained literally; backslash followed by a quote
    is rejected rather than guessing the engine's escape handling. Duplicate
    keys and ESC-separated outputs are retained as independent ordered pairs.
    """
    tokens = iter(_tokens(data))
    entities = []
    for token, start, end in tokens:
        if token != '{':
            raise EntityError(f'Expected entity opening brace at byte {start}')
        pairs = []
        for token, key_start, key_end in tokens:
            if token == '}':
                entities.append(Entity(tuple(pairs), start, key_end))
                break
            if token != 'string':
                raise EntityError(f'Expected quoted key at byte {key_start}')
            value_token = next(tokens, None)
            if value_token is None or value_token[0] != 'string':
                raise EntityError(f'Missing quoted value for key at byte {key_start}')
            _, value_start, value_end = value_token
            pairs.append(Pair(data[key_start:key_end].decode('latin1'),
                              data[value_start:value_end].decode('latin1'),
                              value_start, value_end))
        else:
            raise EntityError(f'Unclosed entity at byte {start}')
    return entities
