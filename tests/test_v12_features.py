import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zftp.transfers.integrity import local_sha256, remote_sha256, verify_transfer, IntegrityMismatch
from zftp.services.scheduler import ScheduleStore
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

class SchedulingTests(unittest.TestCase):
    def endpoint(self): return ScheduleStore.endpoint('SFTP', 'example.org', 22, 'user')

    def test_due_claim_complete_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 's.sqlite'
            s = ScheduleStore(db)
            sid = s.add('Upload', 'upload', self.endpoint(), 'x', '/x', 100, 120)
            self.assertFalse(s.due(99))
            self.assertEqual(len(s.due(100)), 1)
            self.assertTrue(s.claim(sid, 100))
            self.assertFalse(s.claim(sid, 100))
            self.assertTrue(s.finish(sid, True, now=110))
            self.assertFalse(s.due(200))
            self.assertEqual(len(s.due(230)), 1)
            self.assertEqual(ScheduleStore(db).all()[0]['state'], 'enabled')

    def test_failure_pauses_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = ScheduleStore(Path(tmp) / 'sched.db')
            sid = s.add('D', 'download', self.endpoint(), 'local', '/remote', 10)
            self.assertTrue(s.claim(sid, 10))
            s.finish(sid, False, 'destination exists', now=10)
            self.assertFalse(s.due(20))
            self.assertEqual(s.all()[0]['state'], 'paused')
            self.assertTrue(s.set_enabled(sid, True))
            self.assertTrue(s.due(20))
            self.assertTrue(s.delete(sid))
            self.assertEqual(s.all(), [])

    def test_interrupted_dispatch_does_not_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'sched.db'
            s = ScheduleStore(db)
            sid = s.add('Sync', 'sync_two_way', self.endpoint(), '/local', '/remote', 10)
            self.assertTrue(s.claim(sid, 10))
            after = ScheduleStore(db)
            self.assertEqual(after.all()[0]['state'], 'paused')
            self.assertIn('Interrupted dispatch', after.all()[0]['last_error'])
            self.assertFalse(after.due(20))

    def test_invalid_actions_and_intervals(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = ScheduleStore(Path(tmp) / 'db')
            for action, repeat in [('invalid', 0), ('upload', 59)]:
                with self.assertRaises(ValueError):
                    s.add('test', action, self.endpoint(), 'file', '/file', 100, repeat)

if __name__ == '__main__': unittest.main()
