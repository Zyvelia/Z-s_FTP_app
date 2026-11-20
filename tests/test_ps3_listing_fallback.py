"""PS3 homebrew directory-listing encoding compatibility regressions."""
import ftplib
import unittest
from unittest.mock import patch
from zftp.protocols.ftp_engine import FTPBackend


class LegacyListingFTP:
    attempts = []
    def __init__(self, *args, **kwargs):
        self.encoding = 'utf-8'
        self.location = '/'
        self.closed = False
        self.calls = []
    def connect(self, host, port):
        self.attempts.append(self)
    def login(self, username, password):
        self.username = username
        self.password = password
    def set_pasv(self, value):
        pass
    def prot_p(self):
        pass
    def pwd(self):
        return self.location
    def cwd(self, path):
        self.location = path
    def mlsd(self):
        if self.encoding == 'utf-8':
            raise UnicodeDecodeError('utf-8', b'abc\xb0def', 3, 4, 'invalid start byte')
        return [('PS3\xb0FILE', {'type': 'file', 'size': '20'}), ('GAMEZ', {'type': 'dir'})]
    def close(self):
        self.closed = True
    def quit(self):
        self.close()


class ListingFallbackTests(unittest.TestCase):
    def setUp(self):
        LegacyListingFTP.attempts = []

    def test_login_succeeds_but_listing_fails_and_reconnects(self):
        with patch('zftp.protocols.ftp_engine.ftplib.FTP', LegacyListingFTP):
            backend = FTPBackend()
            backend.connect('192.0.4.10', 21, 'anonymous', '', tls=False)
            backend.cwd('/dev_hdd0')
            listing = backend.list_dir()
            self.assertEqual([item.name for item in listing], ['GAMEZ', 'PS3\xb0FILE'])
            self.assertEqual(backend.pwd(), '/dev_hdd0')
            self.assertEqual(backend.control_encoding, 'latin-1')
            self.assertEqual(len(LegacyListingFTP.attempts), 2)
            self.assertTrue(LegacyListingFTP.attempts[0].closed)
            self.assertFalse(LegacyListingFTP.attempts[1].closed)
            self.assertEqual(LegacyListingFTP.attempts[1].password, '')
            backend.disconnect()
            self.assertIsNone(backend._reconnect_settings)

    def test_does_not_retry_authentication_failure(self):
        class Reject(LegacyListingFTP):
            def login(self, username, password):
                raise ftplib.error_perm('530 Not logged in')
        with patch('zftp.protocols.ftp_engine.ftplib.FTP', Reject):
            with self.assertRaises(ftplib.error_perm):
                FTPBackend().connect('192.0.4.10', 21, 'anonymous', '', tls=False)
            self.assertEqual(len(LegacyListingFTP.attempts), 1)

    def test_explicit_latin1_does_not_reconnect_repeatedly(self):
        with patch('zftp.protocols.ftp_engine.ftplib.FTP', LegacyListingFTP):
            backend = FTPBackend()
            backend.connect('192.0.4.10', 21, 'anonymous', '', tls=False, encoding='latin-1')
            backend.list_dir()
            self.assertEqual(len(LegacyListingFTP.attempts), 1)
            backend.disconnect()

    def test_failed_restore_closes_reconnected_socket(self):
        class FailRestore(LegacyListingFTP):
            def cwd(self, path):
                if self.encoding == 'latin-1' and path != '/':
                    raise ftplib.error_perm('550 folder unavailable')
                return super().cwd(path)
        with patch('zftp.protocols.ftp_engine.ftplib.FTP', FailRestore):
            backend = FTPBackend()
            backend.connect('192.0.4.10', 21, 'anonymous', '', tls=False)
            backend.cwd('/dev_hdd0')
            with self.assertRaises(ftplib.error_perm):
                backend.list_dir()
            self.assertIsNone(backend.conn)
            self.assertTrue(LegacyListingFTP.attempts[-1].closed)


if __name__ == '__main__':
    unittest.main()
