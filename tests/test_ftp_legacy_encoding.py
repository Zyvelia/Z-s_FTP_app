"""Compatibility regression tests for legacy FTP control-channel encodings."""
import ftplib
import unittest
from unittest.mock import patch
from zftp.protocols.ftp_engine import FTPBackend


class FakeFTP:
    attempts = []
    def __init__(self, *args, **kwargs):
        self.encoding = 'utf-8'
        self.closed = False
    def connect(self, host, port):
        self.attempts.append((self, self.encoding, host, port))
        if self.encoding == 'utf-8':
            raise UnicodeDecodeError('utf-8', b'\xb0', 0, 1, 'invalid start byte')
    def login(self, username, password):
        self.credentials = (username, password)
    def prot_p(self):
        pass
    def set_pasv(self, enabled):
        pass
    def pwd(self):
        return '/dev_hdd0'
    def close(self):
        self.closed = True
    def quit(self):
        self.close()


class AuthRejectFTP(FakeFTP):
    def connect(self, host, port):
        self.attempts.append((self, self.encoding, host, port))
    def login(self, username, password):
        raise ftplib.error_perm('530 Not logged in')


class LegacyEncodingTests(unittest.TestCase):
    def setUp(self):
        FakeFTP.attempts = []
    def test_utf8_decode_failure_reconnects_as_latin1(self):
        with patch('zftp.protocols.ftp_engine.ftplib.FTP', FakeFTP):
            backend = FTPBackend()
            self.assertEqual(backend.connect('192.0.4.3', 21, 'anonymous', '', tls=False), '/dev_hdd0')
            self.assertEqual([a[1] for a in FakeFTP.attempts], ['utf-8', 'latin-1'])
            self.assertTrue(FakeFTP.attempts[0][0].closed)
            self.assertEqual(backend.control_encoding, 'latin-1')
            backend.disconnect()
    def test_auth_rejection_is_not_retried_as_encoding_issue(self):
        with patch('zftp.protocols.ftp_engine.ftplib.FTP', AuthRejectFTP):
            with self.assertRaisesRegex(ftplib.error_perm, '530'):
                FTPBackend().connect('192.0.4.3', 21, 'baduser', 'badpass', tls=False)
            self.assertEqual(len(FakeFTP.attempts), 1)
    def test_ftps_keeps_tls_and_fallback(self):
        with patch('zftp.protocols.ftp_engine.ftplib.FTP_TLS', FakeFTP):
            backend = FTPBackend()
            self.assertEqual(backend.connect('192.0.4.3', 21, 'user', 'password', tls=True), '/dev_hdd0')
            self.assertEqual([a[1] for a in FakeFTP.attempts], ['utf-8', 'latin-1'])
            backend.disconnect()

if __name__ == '__main__':
    unittest.main()
