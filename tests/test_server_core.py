import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zftp.servers.server_core import ServerAccount, ServerConfig, ServerController, sanitize_log, validate_setup


class ServerConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.account = ServerAccount('alice', 'a-long-password-123', str(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_plain_ftp_allowed(self):
        self.assertTrue(validate_setup(ServerConfig(), [self.account]))

    def test_no_accounts_refused(self):
        with self.assertRaises(ValueError): validate_setup(ServerConfig(), [])

    def test_remote_plaintext_refused(self):
        with self.assertRaisesRegex(ValueError, 'FTPS'):
            validate_setup(ServerConfig(host='0.0.0.0'), [self.account])

    def test_remote_tls_explicit_consent_required(self):
        cert = self.root / 'cert.pem'; key = self.root / 'key.pem'
        cert.touch(); key.touch()
        with self.assertRaisesRegex(ValueError, 'consent'):
            validate_setup(ServerConfig(host='0.0.0.0', tls=True, certfile=str(cert), keyfile=str(key)), [self.account])
        self.assertTrue(validate_setup(ServerConfig(host='0.0.0.0', tls=True, certfile=str(cert), keyfile=str(key), allow_remote_tls=True), [self.account]))

    def test_tls_requires_files(self):
        with self.assertRaisesRegex(ValueError, 'certificate'):
            validate_setup(ServerConfig(tls=True), [self.account])

    def test_invalid_passive_range(self):
        with self.assertRaises(ValueError):
            validate_setup(ServerConfig(passive_start=30020, passive_end=30000), [self.account])
        with self.assertRaises(ValueError):
            validate_setup(ServerConfig(port=30001, passive_start=30000, passive_end=30019), [self.account])

    def test_weak_password_rejected(self):
        with self.assertRaisesRegex(ValueError, '12 characters'):
            validate_setup(ServerConfig(), [ServerAccount('bob', 'weak', str(self.root))])

    def test_no_anonymous_or_duplicates(self):
        with self.assertRaises(ValueError):
            validate_setup(ServerConfig(), [ServerAccount('anonymous', self.account.password, str(self.root))])
        with self.assertRaises(ValueError):
            validate_setup(ServerConfig(), [self.account, ServerAccount('ALICE', self.account.password, str(self.root))])

    def test_no_missing_root(self):
        with self.assertRaises(ValueError):
            validate_setup(ServerConfig(), [ServerAccount('bob', self.account.password, str(self.root / 'missing'))])

    def test_no_symlink_root(self):
        link = self.root / 'link'
        try: link.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError): self.skipTest('symlinks unavailable')
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            validate_setup(ServerConfig(), [ServerAccount('bob', self.account.password, str(link))])

    def test_log_sanitize(self):
        self.assertEqual('hello??world', sanitize_log('hello\r\nworld'))
        self.assertEqual(360, len(sanitize_log('x'*500)))

    def test_validation_does_not_open_server(self):
        with patch.object(ServerController, '_serve', side_effect=AssertionError('should not launch')):
            validate_setup(ServerConfig(), [self.account])


if __name__ == '__main__': unittest.main()
