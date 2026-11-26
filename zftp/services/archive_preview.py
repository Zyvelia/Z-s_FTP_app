"""Browse archive directories and read small entries into memory."""

from contextlib import contextmanager
from gzip import GzipFile
from itertools import islice
import os
from pathlib import Path
from shutil import which
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from threading import Timer
from zlib import crc32

from py7zr import SevenZipFile
from py7zr.io import BytesIOFactory
from py7zr.exceptions import PasswordRequired, Bad7zFile, CrcError
from lzma import LZMAError
import rarfile

from zftp.services.format_preview import (
    ARCHIVE_ENTRY_LIMIT, ARCHIVE_MEMBER_LIMIT, ARCHIVE_READ_LIMIT,
    ArchiveBudgetExceeded, ArchivePasswordRequired, ArchiveEntry, ArchiveReader, zip_listing, zip_member,
)


# Staged RAR files are read as complete archives by the decompression utility.
rarfile.USE_EXTRACT_HACK = 0


def system_tar():
    if os.name == 'nt':
        executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'tar.exe'
        if executable.is_file():
            return str(executable)
    return which('bsdtar')


def read_rar_with_tar(executable, path, member):
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with subprocess.Popen(
        [executable, '-xOf', str(path), '--', member.name],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=flags,
    ) as process:
        timeout = Timer(15, process.kill)
        timeout.start()
        try:
            data = process.stdout.read(ARCHIVE_MEMBER_LIMIT + 1)
            if len(data) != member.size:
                process.kill()
            if process.wait() or len(data) != member.size:
                raise ValueError('The system tar utility could not preview this RAR entry.')
        finally:
            timeout.cancel()
    if member.crc is not None and crc32(data) != member.crc:
        raise ValueError('The RAR entry failed its checksum.')
    return data


@contextmanager
def open_tar(source, kind):
    if kind == '.tar':
        with tarfile.open(fileobj=source, mode='r:') as archive:
            yield archive
    else:
        with GzipFile(fileobj=source) as gzip:
            # Cap inflated bytes too, including the bytes skipped between TAR headers.
            with ArchiveReader(ARCHIVE_READ_LIMIT + 1, lambda _, count: gzip.read(count)) as bounded:
                with tarfile.open(fileobj=bounded, mode='r|') as archive:
                    yield archive


def tar_entry(index, info):
    return ArchiveEntry(index, info.name, info.size, None, None, info.isdir(),
                        info.isfile() and not info.issparse())


def rar_entry(index, info):
    return ArchiveEntry(index, info.filename, info.file_size, info.compress_size, info.CRC,
                        info.is_dir(), info.is_file() and not info.is_symlink() and info.file_redir is None)


def seven_zip_entry(index, info):
    return ArchiveEntry(index, info.filename, info.uncompressed, info.compressed, info.crc32,
                        info.is_directory, info.is_file and not info.is_symlink)


@contextmanager
def open_7z(source, password):
    try:
        archive = SevenZipFile(source, password=password, max_extract_size=ARCHIVE_READ_LIMIT)
    except PasswordRequired as error:
        raise ArchivePasswordRequired('Enter the 7z password.') from error
    except (Bad7zFile, CrcError, LZMAError, TypeError, ValueError, EOFError) as error:
        if password is not None:
            raise ArchivePasswordRequired('The 7z password did not work.') from error
        raise
    with archive:
        yield archive


@contextmanager
def open_rar(source, password):
    with rarfile.RarFile(source, errors='strict') as archive:
        if password:
            archive.setpassword(password)
        yield archive


def archive_listing(source, kind, password=None):
    if kind in ('.zip', '.zips'):
        return zip_listing(source)
    if kind == '.rar':
        with open_rar(source, password) as archive:
            infos = archive.infolist()
            if not infos and archive.needs_password():
                raise ArchivePasswordRequired('Enter the RAR password.' if password is None else 'The RAR password did not work.')
            return [rar_entry(i, info) for i, info in enumerate(infos[:ARCHIVE_ENTRY_LIMIT])], len(infos)
    if kind == '.7z':
        with open_7z(source, password) as archive:
            infos = archive.list()
            return [seven_zip_entry(i, info) for i, info in enumerate(infos[:ARCHIVE_ENTRY_LIMIT])], len(infos)

    with open_tar(source, kind) as archive:
        entries = []
        try:
            for index, info in enumerate(archive):
                if index == ARCHIVE_ENTRY_LIMIT:
                    return entries, None
                entries.append(tar_entry(index, info))
        except ArchiveBudgetExceeded:
            if not entries:
                raise
            return entries, None
        return entries, len(entries)


def check_member(current, original):
    if current != original:
        raise ValueError('Archive contents changed. Reopen the archive.')
    if not current.is_file or current.is_directory:
        raise ValueError('Select a regular archive file.')


def indexed_member(infos, member):
    if member.index >= len(infos):
        raise ValueError('Archive contents changed. Reopen the archive.')
    return infos[member.index]


def read_member(stream, member):
    with stream:
        data = stream.read(ARCHIVE_MEMBER_LIMIT + 1)
    if len(data) != member.size:
        raise ValueError('The archive entry size does not match its directory record.')
    return data


def archive_member(source, kind, member, password=None):
    if kind in ('.zip', '.zips'):
        return zip_member(source, member, password)
    if member.size > ARCHIVE_MEMBER_LIMIT:
        raise ValueError('Archive entry previews are limited to 1 MiB.')

    if kind == '.rar':
        with open_rar(source, password) as archive:
            infos = archive.infolist()
            info = indexed_member(infos, member)
            check_member(rar_entry(member.index, info), member)
            if (info.compress_type != rarfile.RAR_M0 and archive.is_solid()
                    and sum(item.file_size for item in infos[:member.index + 1]) > ARCHIVE_READ_LIMIT):
                raise ValueError('This solid RAR entry exceeds the 16 MiB preview budget.')
            if archive.getinfo(info) is not info:
                raise ValueError('This RAR archive contains duplicate member names.')
            encrypted = info.needs_password()
            if encrypted and password is None:
                raise ArchivePasswordRequired('Enter the RAR password.')
            if info.compress_type == rarfile.RAR_M0 and not encrypted:
                return read_member(archive.open(info), member)
            if (member.name.startswith(('-', '@', '/'))
                    or any(character in '*?[]\\' or ord(character) < 32 for character in member.name)):
                raise ValueError('This compressed RAR entry has an unsupported name.')
            executable = None if encrypted else system_tar()
            if executable is None:
                try:
                    rarfile.tool_setup(bsdtar=False)
                except rarfile.RarCannotExec as error:
                    raise ValueError('Compressed RAR previews require the system tar utility, 7-Zip, or UnRAR.') from error
            if source.seek(0, 2) > ARCHIVE_READ_LIMIT:
                raise ValueError('Compressed RAR previews need an archive within the 16 MiB preview budget.')
            source.seek(0)
            data = source.read(ARCHIVE_READ_LIMIT + 1)
            with TemporaryDirectory(prefix='zftp-rar-') as temporary:
                path = Path(temporary) / 'archive.rar'
                path.write_bytes(data)
                if executable:
                    return read_rar_with_tar(executable, path, member)
                with open_rar(path, password) as staged:
                    try:
                        return read_member(staged.open(member.name, pwd=password), member)
                    except (rarfile.BadRarFile, rarfile.RarWrongPassword, rarfile.PasswordRequired) as error:
                        if encrypted:
                            raise ArchivePasswordRequired('The RAR password did not work.') from error
                        raise


    if kind == '.7z':
        with open_7z(source, password) as archive:
            infos = archive.list()
            info = indexed_member(infos, member)
            check_member(seven_zip_entry(member.index, info), member)
            if archive.needs_password() and password is None:
                raise ArchivePasswordRequired('Enter the 7z password.')
            if sum(item.filename == member.name for item in infos) != 1:
                raise ValueError('This 7z archive contains duplicate member names.')
            factory = BytesIOFactory(ARCHIVE_MEMBER_LIMIT)
            try:
                archive.extract(targets=[member.name], factory=factory)
            except (Bad7zFile, CrcError, LZMAError, PasswordRequired) as error:
                if archive.needs_password():
                    raise ArchivePasswordRequired('The 7z password did not work.') from error
                raise
            output, = factory.products.values()
            output.seek(0)
            data = output.read(ARCHIVE_MEMBER_LIMIT + 1)
            if len(data) != member.size:
                raise ValueError('The archive entry size does not match its directory record.')
            return data

    with open_tar(source, kind) as archive:
        info = next(islice(archive, member.index, None), None)
        if info is None:
            raise ValueError('Archive contents changed. Reopen the archive.')
        check_member(tar_entry(member.index, info), member)
        return read_member(archive.extractfile(info), member)
