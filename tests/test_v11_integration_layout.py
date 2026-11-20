from ui_source_support import ui_text
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class IntegrationLayoutTests(unittest.TestCase):
    def test_v10_components_kept_and_sftp_added(self):
        for name in ('zftp/ui/main_window.py','zftp/servers/server_core.py','zftp/ui/server_ui.py','zftp/servers/sftp_server_core.py',
                     'zftp/ui/sftp_server_ui.py','zftp/protocols/ftp_engine.py','zftp/protocols/sftp_engine.py','zftp/services/sync_engine.py',
                     'zftp/transfers/reliable_transfer.py','zftp/transfers/transfer_queue.py','zftp/services/remote_management.py'):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_main_has_both_independent_server_pages(self):
        source = ui_text(ROOT)
        self.assertIn('self.server_panel = ServerPanel(self)', source)
        self.assertIn('self.sftp_server_panel = SFTPServerPanel(self)', source)
        self.assertIn('self.server_panel.active_or_stopping or self.sftp_server_panel.active_or_stopping', source)

    def test_installer_version_and_scripts(self):
        spec = (ROOT / 'packaging' / 'ZFTP.iss').read_text(encoding='utf-8')
        self.assertIn('2.1.11', spec)
        self.assertIn('ZFTP_Setup_v2.1.11', spec)
        self.assertTrue((ROOT / 'build_exe.bat').is_file())
        self.assertTrue((ROOT / 'build_installer.bat').is_file())

if __name__ == '__main__': unittest.main()
