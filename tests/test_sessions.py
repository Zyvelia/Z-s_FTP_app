"""Session isolation and cleanup invariants — dependency-free tests."""
import unittest
from unittest.mock import MagicMock
from zftp.services.sessions import ConnectionSession
from zftp.protocols.ftp_engine import RemoteEntry


class SessionTests(unittest.TestCase):
    def make(self, host):
        return ConnectionSession(MagicMock(), 'SFTP', host, 22, 'user')

    def test_two_sessions_have_independent_paths_and_entries(self):
        first, second = self.make('host-a'), self.make('host-b')
        first.connected = second.connected = True
        first.remote_path = '/a/docs'
        first.entries.append(RemoteEntry('private.txt', False, 3))
        self.assertEqual(second.remote_path, '/')
        self.assertEqual(second.entries, [])
        second.entries.append(RemoteEntry('different', True))
        self.assertEqual(len(first.entries), 1)

    def test_busy_state_is_per_session(self):
        first, second = self.make('a'), self.make('b')
        one = first.begin()
        two = second.begin()
        self.assertTrue(first.busy and second.busy)
        self.assertFalse(first.request_close())
        self.assertTrue(first.complete(one))
        self.assertTrue(second.busy)
        self.assertTrue(first.request_close())
        self.assertFalse(second.request_close())
        self.assertTrue(second.complete(two))

    def test_cleanup_is_session_local(self):
        first, second = self.make('a'), self.make('b')
        first.connected = second.connected = True
        first.entries.append(RemoteEntry('x', False))
        second.entries.append(RemoteEntry('y', False))
        self.assertTrue(first.request_close())
        first.backend.disconnect()
        first.clear()
        self.assertFalse(first.connected)
        self.assertEqual(first.entries, [])
        self.assertTrue(second.connected)
        self.assertEqual(second.entries[0].name, 'y')
        second.backend.disconnect.assert_not_called()

    def test_stale_completion_does_not_release_busy_session(self):
        session = self.make('a')
        token = session.begin()
        self.assertTrue(session.complete(token))
        new_token = session.begin()
        self.assertFalse(session.complete(token))
        self.assertTrue(session.busy)
        self.assertTrue(session.complete(new_token))

    def test_closing_disallows_new_jobs(self):
        session = self.make('a')
        self.assertTrue(session.request_close())
        with self.assertRaises(RuntimeError):
            session.begin()

if __name__ == '__main__':
    unittest.main()
