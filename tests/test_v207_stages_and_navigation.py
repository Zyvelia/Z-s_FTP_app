"""v2.0.7 regression tests: staged verification and remote listing performance."""
from ui_source_support import ui_text

import ftplib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from zftp.protocols.ftp_engine import FTPBackend
from zftp.services.remote_cache import RemoteDirectoryCache
from zftp.transfers.reliable_transfer import upload, TransferSettings
from test_reliable_transfer import FakeBackend
from zftp.transfers.transfer_queue import TransferQueue, Transfer

class StagedUploadTests(unittest.TestCase):
    def test_verify_before_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'game.pkg'
            source.write_bytes(b'game_data' * 1024)
            fs = {}
            stages = []
            def verify(part):
                self.assertEqual(part, '/home/game.pkg.zftp.part')
                self.assertEqual(fs[part], source.read_bytes())
                self.assertNotIn('/home/game.pkg', fs)
                stages.append('hash-ok')
            upload(FakeBackend(fs), source, 'game.pkg', lambda *_: None, lambda: False,
                   TransferSettings(), verify_staged=verify, on_phase=stages.append)
            self.assertEqual(stages, ['Verifying', 'hash-ok', 'Renaming'])
            self.assertEqual(fs['/home/game.pkg'], source.read_bytes())

    def test_failed_hash_does_not_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'game.pkg'
            source.write_bytes(b'content')
            fs = {}
            def reject(_): raise IOError('SHA-256 mismatch')
            with self.assertRaisesRegex(IOError, 'mismatch'):
                upload(FakeBackend(fs), source, 'game.pkg', lambda *_: None, lambda: False,
                       TransferSettings(), verify_staged=reject)
            self.assertNotIn('/home/game.pkg', fs)
            self.assertIn('/home/game.pkg.zftp.part', fs)

    def test_phase_reflects_queue_completion(self):
        q = TransferQueue()
        item = q.add(Transfer('a', 'upload', '/a', 'x', '/', '/'))
        self.assertEqual(q.next_for('a').phase, 'Uploading')
        item.phase = 'Verifying'
        self.assertEqual(item.status, 'Running')
        q.finish(item)
        self.assertEqual((item.phase, item.status), ('Completed','Completed'))

class CacheTests(unittest.TestCase):
    def test_ttl_lru_and_copy(self):
        now = [0]
        cache = RemoteDirectoryCache(ttl=10,limit=2,clock=lambda:now[0])
        cache.put('/a', ['a'])
        result=cache.get('/a');result.append('bad')
        self.assertEqual(cache.get('/a'), ['a'])
        cache.put('/b',['b']);cache.put('/c',['c'])
        self.assertIsNone(cache.get('/a'))
        now[0]=11
        self.assertIsNone(cache.get('/b'))
    def test_cache_invalidation(self):
        cache=RemoteDirectoryCache();cache.put('/', ['x']);cache.invalidate('/')
        self.assertIsNone(cache.get('/'))

    def test_unsupported_mlsd_only_attempted_once(self):
        ftp=Mock()
        ftp.mlsd.side_effect=ftplib.error_perm('500 unknown command')
        ftp.retrlines.side_effect=lambda command, cb: cb('-rw-r--r-- 1 root root 3 Jan 1 2020 file.pkg')
        backend=FTPBackend();backend.conn=ftp
        self.assertEqual(backend.list_dir()[0].name,'file.pkg')
        self.assertEqual(backend.list_dir()[0].name,'file.pkg')
        self.assertEqual(ftp.mlsd.call_count,1)
        self.assertEqual(ftp.retrlines.call_count,2)

    def test_ui_navigation_and_status_wired(self):
        text=ui_text(Path(__file__).resolve().parents[1])
        for fragment in ('navigate_history', 'directory_cache.get', 'directory_cache.invalidate',
                         "@stage:Verifying", "stage == 'Verifying'", 'tab.back.clicked.connect'):
            self.assertIn(fragment,text)

if __name__ == '__main__':unittest.main()
