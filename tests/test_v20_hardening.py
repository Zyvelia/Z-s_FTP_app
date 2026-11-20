"""v2.0 reliability, security, and release regression tests (offline)."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zftp.protocols.ftp_engine import clean_remote_name
from zftp.transfers.reliable_transfer import download, UnsafeResumeError, TransferSettings
from zftp.services.scheduler import ScheduleStore
from zftp.services.transfer_history import TransferHistory
from zftp.servers.server_core import ServerAccount, ServerConfig, validate_setup


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_scheduler_connections_are_explicitly_closed(self):
        real = sqlite3.connect
        connections = []
        class CloseTracked:
            def __init__(self, conn): self.conn = conn; self.closed = False
            def __enter__(self): return self.conn.__enter__()
            def __exit__(self, *args): return self.conn.__exit__(*args)
            def close(self): self.closed = True; self.conn.close()
            def __getattr__(self, name): return getattr(self.conn, name)
            def __setattr__(self, name, value):
                if name in ("conn", "closed"): object.__setattr__(self, name, value)
                else: setattr(self.conn, name, value)
        def spy(*args, **kwargs):
            c = CloseTracked(real(*args, **kwargs)); connections.append(c); return c
        with patch('zftp.services.scheduler.sqlite3.connect', side_effect=spy):
            store = ScheduleStore(self.root/'jobs.db')
            store.all()
            store.add('backup', 'upload', store.endpoint('SFTP','localhost',2222,'alice'),
                      str(self.root/'input'), '/data', 1234)
            store.all()
        self.assertGreaterEqual(len(connections), 4)
        self.assertTrue(all(c.closed for c in connections))

    def test_history_connections_are_explicitly_closed(self):
        real = sqlite3.connect
        connections = []
        class CloseTracked:
            def __init__(self, conn): self.conn = conn; self.closed = False
            def __enter__(self): return self.conn.__enter__()
            def __exit__(self, *args): return self.conn.__exit__(*args)
            def close(self): self.closed = True; self.conn.close()
            def __getattr__(self, name): return getattr(self.conn, name)
            def __setattr__(self, name, value):
                if name in ("conn", "closed"): object.__setattr__(self, name, value)
                else: setattr(self.conn, name, value)
        def spy(*args, **kwargs):
            c = CloseTracked(real(*args, **kwargs)); connections.append(c); return c
        with patch('zftp.services.transfer_history.sqlite3.connect', side_effect=spy):
            store = TransferHistory(self.root/'history.db')
            store.recent()
            store.export_csv(self.root/'history.csv')
        self.assertTrue(all(c.closed for c in connections))

    def test_reject_symlink_partial_even_if_content_matches(self):
        target = self.root/'secret'
        target.write_bytes(b'same')
        stage = self.root/'copy.zftp.part'
        try: stage.symlink_to(target)
        except (OSError, NotImplementedError): self.skipTest('Symlinks unavailable')
        class Backend:
            conn = type('Remote', (), {'stat':lambda self, path:type('S',(),{'st_size':4})()})()
            def _sftp(self): return self.conn
            def _path(self, name): return '/'+name
        with self.assertRaises(UnsafeResumeError):
            download(Backend(), 'copy', self.root/'copy', lambda *_:None, lambda:False, TransferSettings())
        self.assertEqual(target.read_bytes(), b'same')

    def test_reject_hardlinked_partial(self):
        original = self.root/'original'; original.write_bytes(b'data')
        stage = self.root/'copy.zftp.part'
        try: os.link(original, stage)
        except (OSError, NotImplementedError): self.skipTest('Hard links unavailable')
        class Backend:
            conn = type('Remote', (), {'stat':lambda self, path:type('S',(),{'st_size':4})()})()
            def _sftp(self): return self.conn
            def _path(self, name): return '/'+name
        with self.assertRaises(UnsafeResumeError):
            download(Backend(), 'copy', self.root/'copy', lambda *_:None, lambda:False, TransferSettings())

    def test_filename_rejects_controls_and_non_strings(self):
        for name in ('bad\rname','bad\nname','bad\x00name','bad\tname','bad\x7fname', '../a', None, 7):
            with self.subTest(name=name), self.assertRaises(ValueError): clean_remote_name(name)
        self.assertEqual(clean_remote_name('a b.txt'), 'a b.txt')

    def test_server_rejects_junction_like_share(self):
        share = self.root/'share'; share.mkdir()
        account = ServerAccount('alice','really-secure-password',str(share))
        self.assertTrue(validate_setup(ServerConfig(),[account]))
        with patch.object(Path, 'is_junction', return_value=True, create=True):
            with self.assertRaisesRegex(ValueError, 'symbolic link'):
                validate_setup(ServerConfig(),[account])

    def test_installer_version_and_script_guards(self):
        root = Path(__file__).resolve().parents[1]
        spec = (root/'packaging/ZFTP.iss').read_text()
        self.assertIn('AppVersion "2.1.9"', spec)
        self.assertIn('ZFTP_Setup_v2.1.9', spec)
        self.assertIn('ZFTP.exe', (root/'build_exe.bat').read_text())
        self.assertIn('if not exist "installer_output\\ZFTP_Setup_v2.1.9.exe"', (root/'build_installer.bat').read_text())
        self.assertIn('PrivilegesRequired=lowest', spec)
        self.assertIn('DefaultDirName={localappdata}', spec)

if __name__ == '__main__': unittest.main()
