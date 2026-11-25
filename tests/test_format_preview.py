import unittest
from io import BytesIO
from struct import pack_into
from zipfile import ZipFile

from zftp.services.file_preview import can_preview, is_text_name
from zftp.services.format_preview import ArchiveReader, inspect_format, inspect_package, zip_listing, zip_member


class FormatPreviewTests(unittest.TestCase):
    def test_supported_files_and_case_independent_text_names(self):
        for name in ('sample.dat', 'settings.ini', 'test.ZIP', 'test.zips', 'system.db', 'system.db-journal',
                     'test.pkg', 'sample.ttf', 'sample.otf', 'test.a', 'test.rif', 'test.img', 'test.pbm',
                     'test.backup', 'test.idx', 'test.nvs', 'test.rec', 'test.elf',
                     'test.json', 'test.yaml', 'test.xml', 'test.csv', 'test.md', 'test.toml', 'Makefile', '.env'):
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
