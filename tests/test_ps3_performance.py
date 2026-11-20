"""Offline, dependency-free performance and cleanup regression tests."""
from ui_source_support import ui_text

import ftplib
import inspect
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from zftp.transfers.ps3_performance import PRESETS, benchmark_ftp_upload
from zftp.transfers.reliable_transfer import TransferSettings, upload as reliable_upload


class MemoryFTP:
    def __init__(self, fail_upload=False, fail_delete=False):
        self.files = {}
        self.blocksize = None
        self.fail_upload = fail_upload
        self.fail_delete = fail_delete
        self.cwd_path = '/'

    def size(self, name):
        if name not in self.files:
            raise ftplib.error_perm('550 Not found')
        return len(self.files[name])

    def storbinary(self, cmd, local, blocksize=8192, callback=None, rest=None):
        self.blocksize = blocksize
        name = cmd.split(' ', 1)[1]
        data = bytearray(self.files.get(name, b'')) if rest else bytearray()
        while True:
            block = local.read(blocksize)
            if not block:
                break
            data.extend(block)
            self.files[name] = bytes(data)
            if callback:
                callback(block)
            if self.fail_upload:
                raise OSError('Simulated connection failure')
        return '226 OK'

    def rename(self, start, finish):
        if finish in self.files:
            raise FileExistsError(finish)
        self.files[finish] = self.files.pop(start)

    def retrbinary(self, cmd, callback, blocksize=8192, rest=None):
        name = cmd.split(' ', 1)[1]
        for offset in range(0, len(self.files[name]), blocksize):
            callback(self.files[name][offset:offset + blocksize])

    def delete(self, name):
        if self.fail_delete:
            raise ftplib.error_perm('550 Refusing delete')
        if name not in self.files:
            raise ftplib.error_perm('550 Not found')
        del self.files[name]


class MemoryBackend:
    def __init__(self, ftp):
        self.conn = ftp
        self.ftp = ftp

    def _ftp(self):
        return self.ftp

    def delete_file(self, name):
        self.ftp.delete(name)


class PS3PerformanceTests(unittest.TestCase):
    def test_presets_and_chunks(self):
        self.assertEqual(PRESETS['PS3 Wi-Fi (balanced)'], 256)
        self.assertEqual(TransferSettings().chunk_bytes, 65536)
        self.assertEqual(TransferSettings(chunk_kib=256).chunk_bytes, 262144)
        for size in (64, 128, 256, 512, 1024):
            TransferSettings(chunk_kib=size)
        for bad in (0, 12, 8192, -1):
            with self.assertRaises(ValueError):
                TransferSettings(chunk_kib=bad)

    def test_upload_uses_selected_chunk_and_preserves_staging(self):
        import tempfile
        ftp = MemoryFTP()
        backend = MemoryBackend(ftp)
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'sample.bin'
            source.write_bytes(b'a' * (512 * 1024 + 1))
            written = reliable_upload(backend, source, 'sample.bin', lambda *_: None,
                                      lambda: False, TransferSettings(chunk_kib=256))
            self.assertEqual(written, source.stat().st_size)
            self.assertEqual(ftp.blocksize, 256 * 1024)
            self.assertEqual(ftp.files['sample.bin'], source.read_bytes())
            self.assertNotIn('sample.bin.zftp.part', ftp.files)

    def test_benchmark_upload_and_cleanup(self):
        ftp = MemoryFTP()
        result = benchmark_ftp_upload(MemoryBackend(ftp), size_mib=4,
                        settings=TransferSettings(chunk_kib=256),
                        clock=iter([2.0, 4.0]).__next__)
        self.assertEqual(result.byte_count, 4 * 1024 * 1024)
        self.assertEqual(result.mib_per_second, 2.0)
        self.assertTrue(result.remote_cleanup)
        self.assertEqual(ftp.files, {})
        self.assertEqual(ftp.blocksize, 262144)

    def test_cleanup_failure_is_reported(self):
        ftp = MemoryFTP(fail_delete=True)
        result = benchmark_ftp_upload(MemoryBackend(ftp), size_mib=4,
                        settings=TransferSettings(chunk_kib=256),
                        clock=iter([1.0, 2.0]).__next__)
        self.assertFalse(result.remote_cleanup)
        self.assertTrue(any('zftp_benchmark_' in n for n in ftp.files))

    def test_interrupted_benchmark_does_not_keep_remote_test_data(self):
        ftp = MemoryFTP(fail_upload=True)
        with self.assertRaises(OSError):
            benchmark_ftp_upload(MemoryBackend(ftp), size_mib=4,
                                 settings=TransferSettings(chunk_kib=128))
        self.assertEqual(ftp.files, {})

    def test_gui_opt_in_and_verification_guard(self):
        source = ui_text(ROOT)
        self.assertIn('self.ps3_preset.clicked.connect(self.apply_ps3_preset)', source)
        self.assertIn('self.ps3_benchmark.clicked.connect(self.benchmark_ps3)', source)
        self.assertIn('QMessageBox.Yes | QMessageBox.No, QMessageBox.No', source)
        section = source.split('def apply_ps3_preset(self):', 1)[1].split('def benchmark_ps3(self):', 1)[0]
        self.assertNotIn('self.verify_checksums.setChecked(False)', section)
        self.assertIn('self.chunk_select.currentData()', source)

    def test_invalid_benchmark_size_fails_before_network(self):
        with self.assertRaises(ValueError):
            benchmark_ftp_upload(MemoryBackend(MemoryFTP()), size_mib=2048)


if __name__ == '__main__':
    unittest.main()
