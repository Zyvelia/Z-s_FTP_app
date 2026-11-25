"""Bounded inspection of common archives, executables, and disk formats."""

from dataclasses import dataclass
from io import RawIOBase
from pathlib import Path
import plistlib
import re
from reprlib import Repr
from struct import unpack_from
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from zftp.services.ps3_file_tools import inspect_pkg


ZIP_MAGIC = (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')
AR_MAGIC = b'!<arch>\n'
ELF_MAGIC = b'\x7fELF'
SELF_MAGIC = bytes.fromhex('4f 15 3d 1d')
JOURNAL_MAGIC = bytes.fromhex('d9 d5 05 f9 20 a1 63 d7')
FONT_SUFFIXES = frozenset({'.ttf', '.otf'})
IMAGE_SUFFIXES = frozenset({'.pbm', '.pgm', '.ppm', '.png', '.jpg', '.jpeg',
                            '.bmp', '.gif', '.webp', '.ico', '.tif', '.tiff', '.dds'})
PLAYBACK_SUFFIXES = frozenset({'.wav', '.ogg', '.mp4'})
HEADER_SUFFIXES = frozenset({'.pkg', '.elf', '.self', '.sprx', '.prx', '.img', '.iso', '.rif',
                             '.db-journal', '.db-wal', '.db-shm', '.a'})
ARCHIVE_SUFFIXES = frozenset({'.zip', '.zips', '.7z', '.rar', '.tar', '.tgz'})
SPECIAL_SUFFIXES = FONT_SUFFIXES | IMAGE_SUFFIXES | HEADER_SUFFIXES | ARCHIVE_SUFFIXES | PLAYBACK_SUFFIXES
HEADER_LIMIT = 64 * 1024
PKG_HEADER_SIZE = 0x438
ARCHIVE_ENTRY_LIMIT = 2000
ARCHIVE_READ_LIMIT = 16 * 1024 * 1024
ARCHIVE_MEMBER_LIMIT = 1024 * 1024


def archive_kind(name):
    suffix = Path(name).suffix.lower()
    if name.lower().endswith('.tar.gz') or suffix == '.tgz':
        return '.tgz'
    return suffix if suffix in ARCHIVE_SUFFIXES else None


@dataclass(frozen=True)
class Report:
    title: str
    text: str


@dataclass(frozen=True)
class ArchiveEntry:
    index: int
    name: str
    size: int
    compressed: int | None
    crc: int | None
    is_directory: bool
    is_file: bool = True


def clean_label(data):
    return ''.join(character if character.isprintable() else '?'
                   for character in data.decode('utf-8', errors='replace'))


def hex_preview(data):
    lines = []
    for offset in range(0, len(data), 16):
        block = data[offset:offset + 16]
        text = ''.join(chr(byte) if 32 <= byte < 127 else '.' for byte in block)
        lines.append(f"{offset:08X}  {block.hex(' '):47}  {text}")
    return '\n'.join(lines)


def inspect_package(data, size):
    if data.startswith(b'\x7fCNT') and len(data) >= PKG_HEADER_SIZE:
        identifier = data[0x40:0x70].split(b'\0', 1)[0]
        declared = unpack_from('>Q', data, 0x430)[0]
        entries, = unpack_from('>I', data, 0x10)
        entry_offset, = unpack_from('>I', data, 0x18)
        fields = [('Format', 'PS4 PKG'), ('Content ID', clean_label(identifier) or 'Unavailable'),
                  ('Header entries', str(entries)), ('Entry table offset', hex(entry_offset)),
                  ('Declared bytes', str(declared))]
        if size is not None:
            fields.extend([('File bytes', str(size)), ('Size comparison', 'Matches' if size == declared else 'Mismatch')])
        if PKG_HEADER_SIZE <= entry_offset < len(data):
            available = min(entries, (len(data) - entry_offset) // 32, ARCHIVE_ENTRY_LIMIT)
            for i in range(available):
                record = entry_offset + i * 32
                entry_id = unpack_from('>I', data, record)[0]
                offset, length = unpack_from('>II', data, record + 16)
                fields.append((f'Entry 0x{entry_id:08X}', f'Offset 0x{offset:X} · {length:,} bytes'))
        return fields
    if data.startswith(b'\x7fPKG'):
        return [('Format', 'PS3 PKG'), *inspect_pkg(data, size)]
    raise ValueError('Unrecognized or truncated PKG header')


def inspect_elf(data):
    if len(data) < 16 or data[:4] != ELF_MAGIC or data[4] not in (1, 2) or data[5] not in (1, 2):
        raise ValueError('Unrecognized ELF header')
    bits = 32 if data[4] == 1 else 64
    if len(data) < (52 if bits == 32 else 64):
        raise ValueError('Truncated ELF header')
    order = '<' if data[5] == 1 else '>'
    kind, machine = unpack_from(order + 'HH', data, 16)
    address = unpack_from(order + ('I' if bits == 32 else 'Q'), data, 24)[0]
    program_count, _, section_count = unpack_from(order + 'HHH', data, 44 if bits == 32 else 56)
    return [('Class', f'ELF{bits}'), ('Byte order', 'Little endian' if order == '<' else 'Big endian'),
            ('Type', {1: 'Relocatable', 2: 'Executable', 3: 'Shared object', 4: 'Core'}.get(kind, hex(kind))),
            ('Architecture', {3: 'x86', 40: 'ARM', 62: 'x86-64', 183: 'AArch64'}.get(machine, str(machine))),
            ('Entry point', hex(address)), ('Program headers', str(program_count)), ('Sections', str(section_count))]


def inspect_journal(data):
    if not data:
        return [('Header', 'Empty journal')]
    if not any(data[:8]):
        return [('Header', 'Zeroed journal header')]
    if len(data) < 28 or not data.startswith(JOURNAL_MAGIC):
        raise ValueError('Unrecognized SQLite rollback journal header')
    pages, _, original_size, sector_size, page_size = unpack_from('>IIIII', data, 8)
    return [('Header', 'SQLite rollback journal'),
            ('Page records', 'Through end of file' if pages == 0xffffffff else str(pages)),
            ('Original database pages', str(original_size)),
            ('Sector bytes', str(sector_size)), ('Page bytes', str(page_size))]


def inspect_self(data):
    if len(data) < 32 or not data.startswith(SELF_MAGIC):
        return [('Header', 'Unrecognized or truncated SELF/ELF header')]
    version, mode, endian, attributes, category, program_type = data[4:10]
    header_size, metadata_size, file_size = unpack_from('<HHI', data, 12)
    segments, = unpack_from('<H', data, 24)
    return [('Format', 'PlayStation SELF'), ('Version', str(version)), ('Mode', hex(mode)),
            ('Byte order flag', hex(endian)), ('Attributes', hex(attributes)),
            ('Category', hex(category)), ('Program type', hex(program_type)),
            ('Header bytes', str(header_size)), ('Metadata bytes', str(metadata_size)),
            ('Declared file bytes', str(file_size)), ('Segments', str(segments))]


def inspect_wal(data, size):
    if not data:
        return [('Header', 'Empty write-ahead log')]
    if len(data) < 32 or data[:4] not in (b'\x37\x7f\x06\x82', b'\x37\x7f\x06\x83'):
        return [('Header', 'Unrecognized SQLite write-ahead log header')]
    magic, version, page_size, sequence, salt_a, salt_b, _, _ = unpack_from('>8I', data)
    fields = [('Version', str(version)), ('Page bytes', str(page_size)),
              ('Checkpoint sequence', str(sequence)),
              ('Checksum byte order', 'Big endian' if magic & 1 else 'Little endian'),
              ('Salt', f'{salt_a:08X} {salt_b:08X}')]
    if size is not None and size >= 32 and 512 <= page_size <= 65536 and not page_size & (page_size - 1):
        fields.append(('Complete frames in file', str((size - 32) // (page_size + 24))))
    return fields


def inspect_shm(data):
    if not any(data[:96]):
        return [('Header', 'Empty or zeroed WAL-index')]
    for offset in (0, 48):
        header = data[offset:offset + 48]
        if len(header) < 48:
            continue
        for order in ('<', '>'):
            version, _, changes, ready, _, page_size, frames, pages = unpack_from(order + 'IIIBBHII', header)
            if version == 3007000 and ready == 1:
                return [('Version', str(version)), ('Byte order', 'Little endian' if order == '<' else 'Big endian'),
                        ('Page bytes', str(65536 if page_size == 1 else page_size)),
                        ('Change counter', str(changes)), ('Last committed frame', str(frames)),
                        ('Database pages', str(pages))]
    return [('Header', 'Unrecognized or uninitialized SQLite WAL-index header')]


def inspect_disk(data):
    pvd = data[16 * 2048:]
    if pvd.startswith(b'\x01CD001\x01') and len(pvd) >= 128:
        return [('Format', 'ISO 9660'), ('Volume', clean_label(pvd[40:72].rstrip(b' '))),
                ('Logical blocks', str(unpack_from('<I', pvd, 80)[0]))]
    if data[512:520] == b'EFI PART' or data[4096:4104] == b'EFI PART':
        return [('Format', 'GUID partition table')]
    if data[510:512] == b'\x55\xaa':
        partition_types = [data[offset + 4] for offset in range(446, 510, 16)]
        return [('Header', 'Boot-sector signature 55 AA'),
                ('Nonempty MBR partition type bytes', ', '.join(hex(kind) for kind in partition_types if kind) or 'None')]
    return [('Header', 'Unrecognized disk image format')]


def ar_members(data):
    if not data.startswith(AR_MAGIC):
        raise ValueError('Unrecognized ar archive header')
    members = []
    names = b''
    offset = len(AR_MAGIC)
    while offset + 60 <= len(data) and len(members) < ARCHIVE_ENTRY_LIMIT:
        header = data[offset:offset + 60]
        if header[58:60] != b'`\n' or not header[48:58].strip().isdigit():
            raise ValueError('Invalid ar member header')
        count = int(header[48:58])
        name = header[:16].rstrip()
        start = offset + 60
        if name == b'//':
            names = data[start:start + count]
        elif name not in (b'/', b'/SYM64/'):
            metadata_bytes = 0
            if name.startswith(b'#1/') and name[3:].isdigit():
                name_length = int(name[3:])
                if name_length > count or start + name_length > len(data):
                    break
                name = data[start:start + name_length].rstrip(b'\0')
                metadata_bytes = name_length
            elif name.startswith(b'/') and name[1:].isdigit():
                name = names[int(name[1:]):].split(b'/\n', 1)[0] or name
            else:
                name = name.rstrip(b'/')
            members.append((clean_label(name), count - metadata_bytes))
        offset = start + count + (count & 1)
    return members


def inspect_binary_plist(data):
    try:
        document = plistlib.loads(data, fmt=plistlib.FMT_BINARY)
    except (plistlib.InvalidFileException, RecursionError, OverflowError) as error:
        raise ValueError('Invalid binary property list') from error
    renderer = Repr()
    renderer.maxlevel = 4
    renderer.maxlist = renderer.maxtuple = renderer.maxdict = 8
    renderer.maxstring = renderer.maxother = 256
    return renderer.repr(document)



def inspect_format(name, data, size):
    suffix = Path(name).suffix.lower()
    title, fields = 'File header', []
    members = []
    try:
        if suffix == '.pkg' or data.startswith((b'\x7fPKG', b'\x7fCNT')):
            title, fields = 'Package metadata', inspect_package(data, size)
        elif suffix == '.elf' or data.startswith(ELF_MAGIC):
            title, fields = 'ELF metadata', inspect_elf(data)
        elif suffix in ('.self', '.sprx', '.prx') or data.startswith(SELF_MAGIC):
            title, fields = 'SELF metadata', inspect_self(data)
        elif suffix == '.db-wal':
            title, fields = 'SQLite write-ahead log', inspect_wal(data, size)
        elif suffix == '.db-shm':
            title, fields = 'SQLite WAL-index', inspect_shm(data)
        elif suffix == '.db-journal' or data.startswith(JOURNAL_MAGIC):
            title, fields = 'SQLite journal', inspect_journal(data)
        elif suffix in ('.img', '.iso'):
            title, fields = 'Disk image', inspect_disk(data)
        elif suffix == '.a' or data.startswith(AR_MAGIC):
            title = 'Static archive'
            members = ar_members(data)
            fields = [('Members shown', str(len(members)))]
            fields.extend((member, f'{member_size:,} bytes') for member, member_size in members)
            if size is None or size > len(data):
                fields.append(('Listing', 'Limited to archive headers present in the preview'))
        elif suffix == '.plist' and data.startswith(b'bplist00'):
            title = 'Binary property list · Read-only'
            return Report(title, inspect_binary_plist(data))
        elif suffix == '.rif':
            title, fields = 'RIF data', [('Format', 'License file bytes')]
            identifier = data[0x20:0x50].split(b'\0', 1)[0]
            if len(data) >= 0x56 and re.fullmatch(rb'[A-Z]{2}[0-9]{4}-[A-Z0-9]{9}_[0-9]{2}-[A-Z0-9_-]{16}', identifier):
                license_type, drm_type, content_type = unpack_from('>HHH', data, 0x50)
                fields.extend([('Content ID', identifier.decode('ascii')), ('License type', hex(license_type)),
                               ('DRM type', hex(drm_type)), ('Content type', hex(content_type))])
        else:
            return None
    except ValueError as error:
        fields = [('Header', str(error))]
    if size is not None:
        fields.append(('Size', f'{size:,} bytes'))
    text = '\n'.join(f'{key}  {value}' for key, value in fields)
    if data and not members:
        shown = data[:4096]
        text += f'\n\nFirst {len(shown):,} bytes\n\n{hex_preview(shown)}'
    return Report(title, text)


class ArchiveBudgetExceeded(OSError):
    pass


class ArchiveReader(RawIOBase):
    """Seekable archive input with a byte budget for metadata and entry reads."""

    def __init__(self, size, read_at):
        self.size = size
        self.position = 0
        self.read_at = read_at
        self.bytes_read = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        if whence not in (0, 1, 2):
            raise ValueError('Unknown seek origin')
        start = (0, self.position, self.size)[whence]
        position = start + offset
        if position < 0:
            raise OSError('Archive tried to seek before the beginning of the file')
        self.position = position
        return position

    def read(self, length=-1):
        left = max(0, self.size - self.position)
        count = left if length < 0 else min(left, length)
        if not count:
            return b''
        if self.bytes_read + count > ARCHIVE_READ_LIMIT:
            raise ArchiveBudgetExceeded('Archive directory or member exceeds the 16 MiB preview budget')
        data = self.read_at(self.position, count)
        self.position += len(data)
        self.bytes_read += len(data)
        return data


def zip_listing(source):
    with ZipFile(source) as archive:
        entries = archive.infolist()
        return [
            ArchiveEntry(i, entry.filename, entry.file_size, entry.compress_size, entry.CRC, entry.is_dir())
            for i, entry in enumerate(entries[:ARCHIVE_ENTRY_LIMIT])
        ], len(entries)


def zip_member(source, member):
    with ZipFile(source) as archive:
        entries = archive.infolist()
        if member.index >= len(entries):
            raise ValueError('ZIP contents changed. Reopen the archive.')
        item = entries[member.index]
        if (item.filename, item.CRC, item.file_size) != (member.name, member.crc, member.size):
            raise ValueError('ZIP contents changed. Reopen the archive.')
        if item.is_dir() or item.flag_bits & 1:
            raise ValueError('This ZIP entry is a folder or is password protected.')
        if item.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
            raise ValueError('This compression method is unavailable for entry previews.')
        if item.file_size > ARCHIVE_MEMBER_LIMIT or item.compress_size > ARCHIVE_MEMBER_LIMIT:
            raise ValueError('ZIP entry previews are limited to 1 MiB.')
        with archive.open(item) as stream:
            data = stream.read(ARCHIVE_MEMBER_LIMIT + 1)
        if len(data) > ARCHIVE_MEMBER_LIMIT:
            raise ValueError('ZIP entry exceeds the preview limit.')
        return data
