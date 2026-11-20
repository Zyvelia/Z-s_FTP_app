"""PS3 PARAM.SFO metadata with in-place edits inside existing field slots."""
from dataclasses import dataclass
from struct import Struct

HEADER = Struct('<4sIIII')
INDEX = Struct('<HHIII')
UINT32 = Struct('<I')
TEXT, INTEGER = 0x0204, 0x0404


@dataclass(frozen=True)
class Field:
    name: str
    format: int
    index: int
    offset: int
    size: int
    capacity: int
    value: str

    @property
    def editable(self):
        return self.format in (TEXT, INTEGER)

    @property
    def kind(self):
        return {TEXT: 'Text', INTEGER: 'Unsigned integer'}.get(self.format, 'Raw data')

    def encode(self, value):
        if self.format == TEXT:
            if '\0' in value:
                raise ValueError('Text cannot contain a NUL character.')
            raw = value.encode('utf-8') + b'\0'
        elif self.format == INTEGER:
            try:
                number = int(value.strip(), 16 if value.strip().lower().startswith('0x') else 10)
                raw = UINT32.pack(number) if 0 <= number <= 0xffffffff else None
            except ValueError:
                raw = None
            if raw is None:
                raise ValueError('Enter a whole number from 0 to 4294967295, or a 0x-prefixed hex value.')
        else:
            raise ValueError('Raw data fields are read-only.')
        if len(raw) > self.capacity:
            raise ValueError(f'{self.name} allows {self.capacity - 1} UTF-8 bytes, excluding its terminator.')
        return raw


class ParamSfo:
    def __init__(self, raw):
        self.raw = bytes(raw)
        if not HEADER.size <= len(self.raw) <= 1024 * 1024:
            raise ValueError('PARAM.SFO must be between 20 bytes and 1 MiB.')
        magic, version, keys, data, count = HEADER.unpack_from(self.raw)
        if magic != b'\0PSF' or version != 0x101:
            raise ValueError('Expected a PS3 PARAM.SFO file with PSF version 1.1.')
        if count > 4096 or not HEADER.size + count * INDEX.size <= keys <= data <= len(self.raw):
            raise ValueError('PARAM.SFO contains invalid table offsets.')

        fields = []
        names = set()
        slots = []
        for row in range(count):
            index = HEADER.size + row * INDEX.size
            key_offset, kind, size, capacity, data_offset = INDEX.unpack_from(self.raw, index)
            key_start = keys + key_offset
            key_end = self.raw.find(b'\0', key_start, data)
            offset = data + data_offset
            if key_end <= key_start or size > capacity or offset + capacity > len(self.raw):
                raise ValueError('PARAM.SFO contains an invalid field.')
            try:
                name = self.raw[key_start:key_end].decode('ascii')
                if name in names or not name.isprintable():
                    raise ValueError('PARAM.SFO contains an invalid or duplicate field name.')
                contents = self.raw[offset:offset + size]
                if kind == TEXT:
                    if not contents.endswith(b'\0') or b'\0' in contents[:-1]:
                        raise ValueError(f'{name} is missing a valid string terminator.')
                    value = contents[:-1].decode('utf-8')
                elif kind == INTEGER:
                    if size != UINT32.size:
                        raise ValueError(f'{name} has an invalid integer length.')
                    value = str(UINT32.unpack(contents)[0])
                else:
                    value = contents[:48].hex(' ') + (' …' if size > 48 else '')
            except UnicodeDecodeError as exc:
                raise ValueError('PARAM.SFO contains invalid encoded text.') from exc
            names.add(name)
            if capacity:
                slots.append((offset, offset + capacity))
            fields.append(Field(name, kind, index, offset, size, capacity, value))

        slots.sort()
        if any(left[1] > right[0] for left, right in zip(slots, slots[1:])):
            raise ValueError('PARAM.SFO contains overlapping data fields.')
        self.fields = tuple(fields)

    def edited(self, changes):
        result = bytearray(self.raw)
        fields = {field.name: field for field in self.fields}
        for name, value in changes.items():
            field = fields[name]
            if value == field.value:
                continue
            payload = field.encode(value)
            if payload == self.raw[field.offset:field.offset + field.size]:
                continue
            length = max(field.size, len(payload))
            result[field.offset:field.offset + length] = payload.ljust(length, b'\0')
            UINT32.pack_into(result, field.index + 4, len(payload))
        return bytes(result)
