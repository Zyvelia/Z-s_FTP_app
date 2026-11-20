from ui_source_support import ui_text
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

class V13Layout(unittest.TestCase):
    def test_gui_cross_server_and_workflows_wired(self):
        source = ui_text(ROOT)
        for fragment in ('self.copy_to_server = QPushButton(',
                         'self.bottom_tabs.addTab(self.workflows_panel',
                         'self.bottom_tabs.addTab(self.cross_panel',
                         'self.workflows.claim(', 'self.workflows.advance(',
                         'self.workflows.pause(', 'copy_file(src.backend, dst.backend',
                         'src.begin()', 'dst.begin()', 'self.history.record(transfer'):
            self.assertIn(fragment, source)
    def test_modules_and_installer(self):
        for filename in ('zftp/transfers/cross_server.py','zftp/services/workflows.py','zftp/services/scheduler.py',
                         'zftp/servers/sftp_server_core.py','zftp/servers/server_core.py','zftp/transfers/integrity.py',
                         'zftp/services/remote_management.py', 'build_exe.bat'):
            self.assertTrue((ROOT/filename).exists(), filename)
        installer = (ROOT/'packaging/ZFTP.iss').read_text(encoding='utf8')
        self.assertIn('2.1.7', installer)
        self.assertIn('ZFTP_Setup_v2.1.7', installer)
