import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from zftp.services.profiles import Profile, ProfileStore, APP_SERVICE


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'profiles.json'
        self.keys = Mock()
        self.store = ProfileStore(self.path, self.keys)

    def profile(self):
        return Profile.create('Work', 'example.com', 22, 'SFTP', 'alex', True, '~/.ssh/id_ed25519')

    def test_create_save_and_favorites(self):
        p = self.profile()
        self.store.put(p)
        self.assertEqual(self.store.list(), [p])
        self.assertNotIn('SECRET_PASSWORD_X', self.path.read_text())
        self.assertNotIn('\"password\":', self.path.read_text())
        self.assertEqual(json.loads(self.path.read_text())['version'], 1)

    def test_secure_password_no_plaintext(self):
        p = self.profile()
        p.save_password = True
        self.store.put(p, 'SECRET_PASSWORD_X')
        self.keys.set_password.assert_called_once_with(APP_SERVICE, p.id, 'SECRET_PASSWORD_X')
        self.assertNotIn('SECRET_PASSWORD_X', self.path.read_text())
        self.keys.get_password.return_value = 'SECRET_PASSWORD_X'
        self.assertEqual(self.store.get_password(p), 'SECRET_PASSWORD_X')
        self.store.delete(p.id)
        self.keys.delete_password.assert_called_once_with(APP_SERVICE, p.id)
        self.assertEqual(self.store.list(), [])

    def test_failed_keyring_does_not_write_file(self):
        p = self.profile()
        p.save_password = True
        self.keys.set_password.side_effect = RuntimeError('credential manager unavailable')
        with self.assertRaises(RuntimeError): self.store.put(p, 'secret')
        self.assertFalse(self.path.exists())

    def test_password_opt_in_requires_password(self):
        p = self.profile(); p.save_password = True
        with self.assertRaises(ValueError): self.store.put(p)

    def test_edit_keeps_id_and_credential(self):
        p = self.profile(); p.save_password = True
        self.store.put(p, 'old')
        p.name = 'Renamed'
        self.store.put(p)
        self.assertEqual(self.store.list()[0].name, 'Renamed')
        self.keys.set_password.assert_called_once()

    def test_invalid_profile_rejected(self):
        p = self.profile(); p.protocol = 'TELNET'
        with self.assertRaises(ValueError): self.store.put(p)
        p = self.profile(); p.port = 70000
        with self.assertRaises(ValueError): self.store.put(p)

    def test_corrupt_json_not_overwritten(self):
        self.path.write_text('not-json', encoding='utf-8')
        with self.assertRaises(json.JSONDecodeError): self.store.put(self.profile())
        self.assertEqual(self.path.read_text(), 'not-json')


if __name__ == '__main__': unittest.main()
