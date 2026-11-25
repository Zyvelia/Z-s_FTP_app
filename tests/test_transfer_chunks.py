"""Offline, dependency-free performance and cleanup regression tests."""
import ftplib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from zftp.transfers.reliable_transfer import TransferSettings, upload as reliable_upload


class MemoryFTP:
    def __init__(self):
        self.files = {}
        self.blocksize = None

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
        return '226 OK'

    def rename(self, start, finish):
        if finish in self.files:
            raise FileExistsError(finish)
        self.files[finish] = self.files.pop(start)

    def retrbinary(self, cmd, callback, blocksize=8192, rest=None):
        name = cmd.split(' ', 1)[1]
        for offset in range(0, len(self.files[name]), blocksize):
            callback(self.files[name][offset:offset + blocksize])

class MemoryBackend:
    def __init__(self, ftp):
        self.conn = ftp
        self.ftp = ftp

    def _ftp(self):
        return self.ftp

class TransferChunkTests(unittest.TestCase):
    def test_supported_chunk_sizes(self):
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


if __name__ == '__main__':
    unittest.main()
