import unittest

from zftp.services.profile_import import filezilla_profiles, openssh_profiles
from zftp.ui.units import format_size


class ConnectionImportTests(unittest.TestCase):
    def test_filezilla_host_and_paths_without_credentials(self):
        xml = '''<FileZilla3><Servers><Server><Host>nas.local</Host><Protocol>1</Protocol><Port>22</Port>
        <Name>My NAS</Name><User>tester</User><Pass>fixture</Pass>
        <RemoteDir>1 0 4 home 6 tester</RemoteDir><LocalDir>C:/downloads</LocalDir>
        </Server></Servers></FileZilla3>'''
        profile, = filezilla_profiles(xml)
        self.assertEqual((profile.name, profile.protocol, profile.remote_path), ('My NAS', 'SFTP', '/home/tester'))
        self.assertEqual(profile.local_path, 'C:/downloads')
        self.assertFalse(profile.save_password)

    def test_openssh_alias_and_safe_pattern_handling(self):
        data = '''Host files docs
    HostName %h.example.test
    User alice
    Port 2222
Host *
    User other
Match exec "should-never-run"
    User bait
'''
        first, second = openssh_profiles(data)
        self.assertEqual((first.name, first.host, first.username, first.port),
                         ('files', 'files.example.test', 'alice', 2222))
        self.assertEqual(second.host, 'docs.example.test')

    def test_unit_bases_are_distinct(self):
        self.assertEqual(format_size(1024), '1.0 KiB')
        self.assertEqual(format_size(1000000, True), '1.0 MB')
