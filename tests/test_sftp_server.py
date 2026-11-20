"""v1.1 SFTP server security and lifecycle regressions (dependency free)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zftp.servers.server_core import ServerAccount
from zftp.servers.sftp_server_core import (SFTPServerConfig, SFTPServerController,
                              resolve_share, validate_sftp_setup)


class SFTPConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = self.root / 'host.key'
        self.key.write_text('dummy-key-for-validation-only', encoding='utf-8')
        self.share = self.root / 'share'
        self.share.mkdir()
        self.account = ServerAccount('alice', 'more-than-12-characters', str(self.share))
        self.cfg = SFTPServerConfig(host_key=str(self.key))

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_local_configuration(self):
        self.assertTrue(validate_sftp_setup(self.cfg, [self.account]))

    def test_remote_opt_in_required(self):
        with self.assertRaisesRegex(ValueError, 'consent'):
            validate_sftp_setup(SFTPServerConfig(host='0.0.0.0', host_key=str(self.key)), [self.account])
        cfg = SFTPServerConfig(host='0.0.0.0', host_key=str(self.key), allow_remote=True)
        self.assertTrue(validate_sftp_setup(cfg, [self.account]))

    def test_missing_key_refused(self):
        with self.assertRaisesRegex(ValueError, 'existing'):
            validate_sftp_setup(SFTPServerConfig(), [self.account])

    def test_invalid_port_and_limits(self):
        for cfg in (SFTPServerConfig(host_key=str(self.key), port=22),
                    SFTPServerConfig(host_key=str(self.key), max_clients=0),
                    SFTPServerConfig(host_key=str(self.key), max_clients=2, max_per_ip=3)):
            with self.assertRaises(ValueError): validate_sftp_setup(cfg, [self.account])

    def test_password_and_share_policy_preserved(self):
        with self.assertRaisesRegex(ValueError, '12 characters'):
            validate_sftp_setup(self.cfg, [ServerAccount('bob', 'weak', str(self.share))])
        with self.assertRaises(ValueError):
            validate_sftp_setup(self.cfg, [self.account, self.account])

    def test_no_start_during_configuration(self):
        with patch.object(SFTPServerController, '_serve', side_effect=AssertionError('network used')):
            validate_sftp_setup(self.cfg, [self.account])

    def test_sftp_path_confinement(self):
        (self.share / 'safe').mkdir()
        (self.share / 'safe' / 'file.txt').write_text('hello')
        self.assertEqual(resolve_share(self.share, '/safe/file.txt'), self.share / 'safe' / 'file.txt')
        self.assertEqual(resolve_share(self.share, '/new.txt', allow_missing=True), self.share / 'new.txt')
        with self.assertRaises(PermissionError): resolve_share(self.share, '/../outside', allow_missing=True)
        with self.assertRaises(PermissionError): resolve_share(self.share, '/safe/../../outside', allow_missing=True)
        with self.assertRaises(PermissionError): resolve_share(self.share, 'bad\\name', allow_missing=True)
        with self.assertRaises(FileNotFoundError): resolve_share(self.share, '/missing.txt')

    def test_symlinks_not_followed(self):
        outside = self.root / 'outside'
        outside.mkdir()
        target = self.share / 'link'
        try: target.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError): self.skipTest('symlink unavailable')
        with self.assertRaises(PermissionError):
            resolve_share(self.share, '/link/secret.txt', allow_missing=True)

    def test_no_dependency_required_for_inactive_server(self):
        controller = SFTPServerController()
        self.assertFalse(controller.running)
        controller.stop()
        self.assertFalse(controller.running)


if __name__ == '__main__': unittest.main()
