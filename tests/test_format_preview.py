import plistlib
import sqlite3
import tarfile
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from struct import pack_into
from zipfile import ZipFile

from py7zr import SevenZipFile

from zftp.services.archive_preview import archive_listing, archive_member
from zftp.services.file_preview import can_preview, is_text_name
from zftp.services.format_preview import ArchiveReader, archive_kind, inspect_format, inspect_package, zip_listing, zip_member


class FormatPreviewTests(unittest.TestCase):
    def test_zip_and_7z_password_prompts_preserve_entry_preview(self):
        from pyzipper import AESZipFile, WZ_AES
        from zftp.services.format_preview import ArchivePasswordRequired

        for kind in ('.zip', '.7z'):
            with self.subTest(kind=kind):
                buffer = BytesIO()
                if kind == '.zip':
                    with AESZipFile(buffer, 'w', encryption=WZ_AES) as archive:
                        archive.setpassword(b'fixture')
                        archive.writestr('inside.txt', b'contents')
                else:
                    with SevenZipFile(buffer, 'w', password='fixture', header_encryption=True) as archive:
                        archive.writestr(b'contents', 'inside.txt')
                payload = buffer.getvalue()
                if kind == '.7z':
                    with self.assertRaises(ArchivePasswordRequired):
                        archive_listing(BytesIO(payload), kind)
                entries, _ = archive_listing(BytesIO(payload), kind, 'fixture')
                with self.assertRaises(ArchivePasswordRequired):
                    archive_member(BytesIO(payload), kind, entries[0], 'wrong')
                self.assertEqual(archive_member(BytesIO(payload), kind, entries[0], 'fixture'), b'contents')

    def test_supported_files_and_case_independent_text_names(self):
        for name in ('sample.dat', 'settings.ini', 'test.ZIP', 'test.zips', 'system.db', 'system.db-journal',
                     'test.pkg', 'sample.ttf', 'sample.otf', 'test.a', 'test.rif', 'test.img', 'test.pbm',
                     'test.backup', 'test.idx', 'test.nvs', 'test.rec', 'test.elf',
                     'test.7z', 'test.rar', 'test.tar', 'test.TAR.GZ', 'test.tgz', 'PARAM.SFO',
                     'test.self', 'test.sprx', 'test.prx', 'test.dds', 'test.wav', 'test.ogg', 'test.mp4',
                     'test.db-wal', 'test.db-shm', 'test.nfo', 'test.plist', 'test.reg', 'test.srt', 'test.vtt',
                     '.editorconfig', '.gitmodules', 'test.json', 'test.yaml', 'test.xml', 'test.csv', 'test.md', 'test.toml', 'Makefile', '.env'):
            with self.subTest(name=name):
                self.assertTrue(can_preview(name))
        self.assertFalse(can_preview('file.unknown'))
        self.assertTrue(is_text_name('settings.JSON'))
        self.assertFalse(is_text_name('cache.dat'))

    def test_zip_reads_ranges_and_previews_small_member(self):
        buffer = BytesIO()
        with ZipFile(buffer, 'w') as archive:
            archive.writestr('large-image.img', bytes(9 * 1024 * 1024))
            archive.writestr('config/options.ini', b'width=720')
            archive.writestr('empty/', b'')
        data = buffer.getvalue()
        allocations = []

        def read_at(offset, size):
            allocations.append((offset, size))
            return data[offset:offset + size]

        with ArchiveReader(len(data), read_at) as source:
            entries, total = zip_listing(source)
        self.assertEqual(total, 3)
        self.assertTrue(entries[2].is_directory)
        self.assertLess(sum(count for _, count in allocations), 128 * 1024)
        with ArchiveReader(len(data), read_at) as source:
            self.assertEqual(zip_member(source, entries[1]), b'width=720')
            with self.assertRaisesRegex(ValueError, '1 MiB'):
                zip_member(source, entries[0])

    def test_tar_gzip_and_7z_read_entries_without_extracting(self):
        content = b'name=value'
        for name, mode in (('demo.tar', 'w'), ('demo.TAR.GZ', 'w:gz'), ('demo.7z', 'w')):
            with self.subTest(name=name):
                source = BytesIO()
                kind = archive_kind(name)
                if kind == '.7z':
                    with SevenZipFile(source, mode) as archive:
                        archive.writestr(b'skip', 'skip.txt')
                        archive.writestr(content, 'nested/settings.ini')
                else:
                    with tarfile.open(fileobj=source, mode=mode) as archive:
                        link = tarfile.TarInfo('link')
                        link.type = tarfile.SYMTYPE
                        link.linkname = '/etc/passwd'
                        archive.addfile(link)
                        entry = tarfile.TarInfo('nested/settings.ini')
                        entry.size = len(content)
                        archive.addfile(entry, BytesIO(content))
                data = source.getvalue()
                entries, count = archive_listing(BytesIO(data), kind)
                self.assertEqual(count, 2)
                if kind != '.7z':
                    self.assertFalse(entries[0].is_file)
                self.assertEqual(archive_member(BytesIO(data), kind, entries[1]), content)

    def test_wal_and_shm_headers_from_sqlite(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'data.db'
            connection = sqlite3.connect(path)
            try:
                connection.execute('PRAGMA page_size=4096')
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('CREATE TABLE items (id INTEGER)')
                connection.execute('INSERT INTO items VALUES (1)')
                connection.commit()
                wal = path.with_suffix('.db-wal').read_bytes()
                shm = path.with_suffix('.db-shm').read_bytes()
            finally:
                connection.close()
        self.assertIn('Complete frames in file  3', inspect_format('data.db-wal', wal, len(wal)).text)
        self.assertIn('Last committed frame  3', inspect_format('data.db-shm', shm, len(shm)).text)

    def test_self_and_binary_plist_metadata(self):
        header = bytearray(32)
        header[:4] = bytes.fromhex('4f 15 3d 1d')
        pack_into('<HHI', header, 12, 200, 512, 8192)
        pack_into('<H', header, 24, 3)
        report = inspect_format('module.sprx', header, 8192)
        self.assertIn('PlayStation SELF', report.text)
        self.assertIn('Segments  3', report.text)
        data = plistlib.dumps({'Theme': 'Dark'}, fmt=plistlib.FMT_BINARY)
        self.assertIn("'Theme': 'Dark'", inspect_format('settings.plist', data, len(data)).text)

    def test_ps4_header_entries_and_sqlite_journal(self):
        pkg = bytearray(0x480)
        pkg[:4] = b'\x7fCNT'
        pack_into('>I', pkg, 0x10, 1)
        pack_into('>I', pkg, 0x18, 0x440)
        pkg[0x40:0x46] = b'UP0001'
        pack_into('>Q', pkg, 0x430, 4096)
        pack_into('>I', pkg, 0x440, 0x1000)
        pack_into('>II', pkg, 0x450, 0x900, 200)
        fields = dict(inspect_package(bytes(pkg), 4096))
        self.assertEqual(fields['Format'], 'PS4 PKG')
        self.assertEqual(fields['Size comparison'], 'Matches')
        self.assertEqual(fields['Entry 0x00001000'], 'Offset 0x900 · 200 bytes')

        journal = bytearray.fromhex('d9 d5 05 f9 20 a1 63 d7') + bytearray(20)
        pack_into('>IIIII', journal, 8, 3, 0, 40, 512, 4096)
        report = inspect_format('save.db-journal', bytes(journal), len(journal))
        self.assertIn('SQLite rollback journal', report.text)
        self.assertIn('Page bytes  4096', report.text)

    def test_elf_disk_and_static_archive(self):
        elf = bytearray(64)
        elf[:6] = b'\x7fELF\x02\x01'
        pack_into('<HH', elf, 16, 2, 62)
        self.assertIn('x86-64', inspect_format('program.elf', bytes(elf), len(elf)).text)

        sector = bytearray(32768 + 128)
        sector[32768:32775] = b'\x01CD001\x01'
        self.assertIn('ISO 9660', inspect_format('disc.img', bytes(sector), None).text)

        member = f"{'main.o/':16}{0:12}{0:6}{0:6}{'644':8}{10:10}`\n".encode()
        archive = b'!<arch>\n' + member + b'1234567890'
        self.assertIn('main.o  10 bytes', inspect_format('lib.a', archive, len(archive)).text)


if __name__ == '__main__':
    unittest.main()
