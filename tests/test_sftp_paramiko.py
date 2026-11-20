"""Optional Paramiko integration tests: run on Windows after installing requirements."""
import tempfile
import unittest
from pathlib import Path

try:
    import paramiko
except ImportError:
    paramiko = None

from zftp.servers.server_core import ServerAccount
from zftp.servers.sftp_server_core import (SFTPServerConfig, SFTPServerController, generate_host_key,
                              host_key_fingerprint)


@unittest.skipUnless(paramiko is not None, 'Paramiko must be installed')
class LiveSFTPTests(unittest.TestCase):
    def setUp(self):
        import socket
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.share = self.root / 'share'; self.share.mkdir()
        self.key = self.root / 'host_rsa'
        self.fp = generate_host_key(str(self.key))
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        self.controller = SFTPServerController()
        cfg = SFTPServerConfig(host_key=str(self.key), port=self.port)
        self.controller.start(cfg, [ServerAccount('test', 'sample-passphrase-12', str(self.share), True)])

    def tearDown(self):
        self.controller.stop()
        self.temp.cleanup()

    def test_key_is_stable_and_never_overwritten(self):
        self.assertEqual(host_key_fingerprint(str(self.key)), self.fp)
        with self.assertRaises(FileExistsError): generate_host_key(str(self.key))

    def test_connection_upload_download_and_readonly(self):
        import socket
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=5)
        transport = paramiko.Transport(sock)
        try:
            transport.start_client(timeout=5)
            self.assertEqual(transport.get_remote_server_key().get_fingerprint(),
                             paramiko.RSAKey.from_private_key_file(str(self.key)).get_fingerprint())
            transport.auth_password('test', 'sample-passphrase-12')
            sftp = paramiko.SFTPClient.from_transport(transport)
            with sftp.file('/hello.txt', 'wb') as f: f.write(b'hello world')
            with sftp.file('/hello.txt', 'rb') as f: self.assertEqual(f.read(), b'hello world')
            self.assertEqual((self.share / 'hello.txt').read_bytes(), b'hello world')
            self.assertIn('hello.txt', sftp.listdir('/'))
            with self.assertRaises(IOError): sftp.open('/../unauthorized.txt', 'wb')
            sftp.close()
        finally:
            transport.close()


if __name__ == '__main__': unittest.main()
