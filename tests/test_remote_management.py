import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from zftp.protocols.ftp_engine import FTPBackend
from zftp.services.remote_management import (RemoteConflictError, find_remote, read_editor_snapshot,
                               safe_save_editor, encode_editor_text, validate_name)


class MemoryBackend:
    def __init__(self):
        self.files = {'note.txt': b'old data'}
        self.cwd_path = '/home'
        self.fail_publish = False
        self.fail_rollback = False

    def read_bytes(self, name, limit):
        data = self.files[name]
        if len(data) > limit: raise ValueError('Too large')
        return data

    def write_new_bytes(self, name, data):
        if name in self.files: raise FileExistsError(name)
        self.files[name] = data

    def rename(self, before, after):
        if self.fail_publish and before.startswith('.zftp-edit-'):
            raise OSError('publish failed')
        if self.fail_rollback and before.startswith('.zftp-backup-'):
            raise OSError('rollback failed')
        if after in self.files: raise FileExistsError(after)
        self.files[after] = self.files.pop(before)

    def delete_file(self, name):
        del self.files[name]

    def pwd(self): return self.cwd_path
    def cwd(self, path): self.cwd_path = path
    def list_dir(self):
        return [SimpleNamespace(name='nested', is_dir=True, is_link=False, size=None),
                SimpleNamespace(name='report.txt', is_dir=False, is_link=False, size=3)] if self.cwd_path == '/home' else [
                SimpleNamespace(name='inside.txt', is_dir=False, is_link=False, size=3)]


class RemoteManagementTests(unittest.TestCase):
    def test_name_validation(self):
        for bad in ('..', '../a', 'a/b', 'a\\b', 'x\x00y', 'bad\tname', 'x\ny'):
            with self.subTest(bad=bad), self.assertRaises(ValueError): validate_name(bad)
        self.assertEqual(validate_name('file name.txt'), 'file name.txt')

    def test_text_snapshot(self):
        b = MemoryBackend()
        snapshot = read_editor_snapshot(b, 'note.txt')
        self.assertEqual(snapshot.text, 'old data')
        self.assertEqual(snapshot.sha256, hashlib.sha256(b'old data').hexdigest())
        b.files['note.txt'] = b'\x00bad'
        with self.assertRaises(ValueError): read_editor_snapshot(b, 'note.txt')
        b.files['note.txt'] = b'\xff'
        with self.assertRaises(ValueError): read_editor_snapshot(b, 'note.txt')
        b.files['note.txt'] = b'\xef\xbb\xbftext'
        with self.assertRaises(ValueError): read_editor_snapshot(b, 'note.txt')

    def test_save_preserves_backup(self):
        b = MemoryBackend()
        snapshot = read_editor_snapshot(b, 'note.txt')
        backup = safe_save_editor(b, snapshot, 'new data')
        self.assertEqual(b.files['note.txt'], b'new data')
        self.assertEqual(b.files[backup], b'old data')
        self.assertEqual(safe_save_editor(b, read_editor_snapshot(b, 'note.txt'), 'new data'), '')

    def test_edit_conflict_does_not_write(self):
        b = MemoryBackend()
        snapshot = read_editor_snapshot(b, 'note.txt')
        b.files['note.txt'] = b'other writer'
        with self.assertRaises(RemoteConflictError): safe_save_editor(b, snapshot, 'new')
        self.assertEqual(b.files, {'note.txt': b'other writer'})

    def test_failed_publish_restores_original(self):
        b = MemoryBackend()
        b.fail_publish = True
        snapshot = read_editor_snapshot(b, 'note.txt')
        with self.assertRaises(OSError): safe_save_editor(b, snapshot, 'updated')
        self.assertEqual(b.files['note.txt'], b'old data')
        self.assertTrue(any(x.startswith('.zftp-edit-') for x in b.files))

    def test_failed_rollback_preserves_recovery(self):
        b = MemoryBackend()
        b.fail_publish = b.fail_rollback = True
        snapshot = read_editor_snapshot(b, 'note.txt')
        with self.assertRaisesRegex(RuntimeError, 'Recovery file'):
            safe_save_editor(b, snapshot, 'updated')
        self.assertTrue(any(x.startswith('.zftp-backup-') for x in b.files))

    def test_text_size_limit(self):
        with self.assertRaises(ValueError): encode_editor_text('a' * (1024 * 1024 + 1))

    def test_recursive_search_restores_directory(self):
        b = MemoryBackend()
        results = find_remote(b, '/home', 'txt')
        self.assertEqual({x[0] for x in results}, {'/home/report.txt', '/home/nested/inside.txt'})
        self.assertEqual(b.pwd(), '/home')
        with self.assertRaises(ValueError): find_remote(b, '/home', 'x', max_entries=1)
        self.assertEqual(b.pwd(), '/home')

    def test_ftp_management_calls(self):
        b = FTPBackend()
        ftp = MagicMock()
        b.conn = ftp
        b.rename('x', 'y')
        ftp.rename.assert_called_once_with('x', 'y')
        b.chmod('file.txt', 0o644)
        ftp.sendcmd.assert_called_once_with('SITE CHMOD 644 file.txt')
        with self.assertRaises(ValueError): b.chmod('file.txt', 0o1000)
        ftp.mlsd.return_value = []
        b.write_new_bytes('new.txt', b'new')
        self.assertEqual(ftp.storbinary.call_args.args[0], 'STOR new.txt')
        ftp.retrbinary.side_effect = lambda command, callback, **kwargs: callback(b'hello')
        self.assertEqual(b.read_bytes('x', 100), b'hello')
        with self.assertRaises(ValueError): b.read_bytes('x', 2)


if __name__ == '__main__': unittest.main()
