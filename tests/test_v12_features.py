import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zftp.transfers.integrity import local_sha256, remote_sha256, verify_transfer, IntegrityMismatch
from zftp.services.transfer_history import TransferHistory
from zftp.transfers.transfer_queue import Transfer

class FakeFTP:
    def __init__(self, data):
        self.data = data
        self.names = []
    def retrbinary(self, cmd, callback, blocksize):
        self.names.append(cmd)
        for start in range(0, len(self.data), 11):
            callback(self.data[start:start+11])

class FTPBackend:
    def __init__(self, data): self.conn = FakeFTP(data)
    def _ftp(self): return self.conn

class FakeSFTP:
    def __init__(self, data): self.data = data
    def open(self, name, mode):
        import io
        return io.BytesIO(self.data)

class SFTPBackend:
    def __init__(self, data): self.conn = FakeSFTP(data)
    def _sftp(self): return self.conn
    def _path(self, name): return '/root/' + name

class IntegrityTests(unittest.TestCase):
    def test_match_ftp_and_sftp(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'test.bin'
            content = b'hello\x00' * 10000
            file.write_bytes(content)
            for backend in (FTPBackend(content), SFTPBackend(content)):
                digest = verify_transfer(backend, 'upload', file, 'target.bin')
                self.assertEqual(digest, hashlib.sha256(content).hexdigest())
            self.assertEqual(backend._sftp().data, content)

    def test_mismatch_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'test'
            file.write_bytes(b'local')
            with self.assertRaises(IntegrityMismatch):
                verify_transfer(FTPBackend(b'other'), 'download', file, 'test')

    def test_cancelled_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'test'
            f.write_bytes(b'data')
            with self.assertRaises(InterruptedError): local_sha256(f, lambda: True)
            with self.assertRaises(InterruptedError): remote_sha256(FTPBackend(b'data'), 'x', lambda: True)

    def test_bad_operation(self):
        with self.assertRaises(ValueError): verify_transfer(FTPBackend(b''), 'mkdir_local', Path('/n'), 'n')

class HistoryTests(unittest.TestCase):
    def test_persistence_and_upsert(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'history.sqlite'
            history = TransferHistory(path)
            transfer = Transfer('id', 'upload', '/a.txt', '/remote/a.txt', '/', '/tmp')
            with self.assertRaises(ValueError): history.record(transfer)
            transfer.status = 'Completed'; transfer.count = 42; transfer.attempts = 2
            history.record(transfer, 'example.org', 'SFTP', 'abc')
            history.record(transfer, 'example.org', 'SFTP', 'abc')
            other = TransferHistory(path)
            rows = other.recent()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['sha256'], 'abc')
            self.assertEqual(rows[0]['bytes_done'], 42)
            self.assertEqual(rows[0]['protocol'], 'SFTP')
            target = Path(tmp) / 'export.csv'
            other.export_csv(target)
            self.assertIn('example.org', target.read_text(encoding='utf-8-sig'))
            with self.assertRaises(FileExistsError): other.export_csv(target)

    def test_failed_history_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TransferHistory(Path(tmp) / 'db')
            job = Transfer('x', 'download', 'a', 'b', '/', '/tmp')
            job.status = 'Failed'; job.error = 'error'; store.record(job)
            self.assertEqual(store.recent()[0]['status'], 'Failed')
            with self.assertRaises(ValueError): store.recent(0)


if __name__ == '__main__': unittest.main()
