"""Protocol-simulation tests for FTP / FTPS staged transfer behavior.

No live socket is used: FTP and FTPS share ftplib transfer commands, so these
exercise the same resume and promotion code that a TLS connection invokes.
"""
import io
import tempfile
import unittest
from pathlib import Path

from zftp.transfers.reliable_transfer import download, upload, TransferSettings, UnsafeResumeError


class SimulatedFTPConnection:
    def __init__(self):
        self.files = {}
        self.command_log = []
    def size(self, name):
        import ftplib
        if name not in self.files:
            raise ftplib.error_perm('550 missing')
        return len(self.files[name])
    def retrbinary(self, command, callback, blocksize=8192, rest=None):
        self.command_log.append(('RETR', rest))
        name = command.removeprefix('RETR ')
        content = self.files[name]
        for i in range(rest or 0, len(content), blocksize):
            callback(content[i:i+blocksize])
    def storbinary(self, command, file, blocksize=8192, callback=None, rest=None):
        self.command_log.append(('STOR', rest))
        name = command.removeprefix('STOR ')
        existing = self.files.get(name, b'')
        data = existing[:rest] if rest is not None else b''
        while True:
            chunk = file.read(blocksize)
            if not chunk: break
            data += chunk
            if callback: callback(chunk)
        self.files[name] = data
    def rename(self, src, dest):
        if dest in self.files: raise FileExistsError(dest)
        self.files[dest] = self.files.pop(src)


class SimulatedFTPBackend:
    def __init__(self):
        self.conn = SimulatedFTPConnection()
    def _ftp(self): return self.conn


class FTPIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = SimulatedFTPBackend()
        self.settings = TransferSettings()
        self.progress = lambda *_: None
        self.cancelled = lambda: False

    def test_ftp_upload_download_with_staging(self):
        source = self.root/'original.bin'
        data = b'a\x00b' * 75000
        source.write_bytes(data)
        self.assertEqual(upload(self.backend,source,'uploaded.bin',self.progress,self.cancelled,self.settings),len(data))
        self.assertEqual(self.backend.conn.files['uploaded.bin'],data)
        self.assertNotIn('uploaded.bin.zftp.part',self.backend.conn.files)
        target = self.root/'download.bin'
        self.assertEqual(download(self.backend,'uploaded.bin',target,self.progress,self.cancelled,self.settings),len(data))
        self.assertEqual(target.read_bytes(),data)
        self.assertFalse((self.root/'download.bin.zftp.part').exists())

    def test_ftp_download_resumes_only_matching_prefix(self):
        self.backend.conn.files['data.bin'] = b'hello world'
        (self.root/'data.bin.zftp.part').write_bytes(b'hello')
        download(self.backend,'data.bin',self.root/'data.bin',self.progress,self.cancelled,self.settings)
        self.assertEqual((self.root/'data.bin').read_bytes(),b'hello world')
        self.assertIn(('RETR',5),self.backend.conn.command_log)

    def test_ftp_upload_resume_rejects_mismatch(self):
        source = self.root/'src.bin'; source.write_bytes(b'hello world')
        self.backend.conn.files['remote.bin.zftp.part'] = b'wrong'
        with self.assertRaises(UnsafeResumeError):
            upload(self.backend,source,'remote.bin',self.progress,self.cancelled,self.settings)
        self.assertNotIn('remote.bin',self.backend.conn.files)

    def test_ftp_collision_blocks_upload(self):
        source = self.root/'source'; source.write_bytes(b'new')
        self.backend.conn.files['file.txt'] = b'old'
        with self.assertRaises(FileExistsError):
            upload(self.backend,source,'file.txt',self.progress,self.cancelled,self.settings)
        self.assertEqual(self.backend.conn.files['file.txt'],b'old')

if __name__ == '__main__': unittest.main()
