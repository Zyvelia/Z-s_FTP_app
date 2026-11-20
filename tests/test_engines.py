import base64
import hashlib
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import paramiko
    from zftp.protocols.sftp_engine import SFTPBackend, UntrustedHostKey, _SessionTrustPolicy
except ImportError:
    paramiko = None
from zftp.protocols.ftp_engine import clean_remote_name, parent_remote


@unittest.skipUnless(paramiko is not None, "requires paramiko")
class EngineTests(unittest.TestCase):
    def test_remote_sanitization(self):
        for name in ('../file', '.', '..', 'one/two', 'one\\two', 'bad\nname', ''):
            with self.assertRaises(ValueError):
                clean_remote_name(name)
        self.assertEqual(clean_remote_name('my file.txt'), 'my file.txt')
        self.assertEqual(parent_remote('/folder/sub'), '/folder')

    def test_sftp_remote_paths(self):
        engine = SFTPBackend()
        engine._cwd = '/home/a'
        self.assertEqual(engine._path('test.txt'), '/home/a/test.txt')
        with self.assertRaises(ValueError):
            engine._path('../escape')

    def test_sftp_sort(self):
        import stat
        from types import SimpleNamespace
        engine = SFTPBackend()
        engine.conn = MagicMock()
        engine.conn.listdir_attr.return_value = [
            SimpleNamespace(filename='z.txt', st_mode=stat.S_IFREG, st_size=17),
            SimpleNamespace(filename='Folder', st_mode=stat.S_IFDIR, st_size=2),
            SimpleNamespace(filename='a.txt', st_mode=stat.S_IFREG, st_size=1),
        ]
        self.assertEqual([e.name for e in engine.list_dir()], ['Folder', 'a.txt', 'z.txt'])

    def test_unknown_key_rejected_unless_approved(self):
        key = paramiko.RSAKey.generate(1024)
        policy = _SessionTrustPolicy({})
        with self.assertRaises(UntrustedHostKey) as caught:
            policy.missing_host_key(MagicMock(), '[example.org]:22', key)
        exc = caught.exception
        expected = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        self.assertEqual(exc.fingerprint, expected)
        client = MagicMock()
        policy.trusted['[example.org]:22'] = key.get_base64()
        policy.missing_host_key(client, '[example.org]:22', key)
        client.get_host_keys().add.assert_called_once()


if __name__ == '__main__':
    unittest.main()
