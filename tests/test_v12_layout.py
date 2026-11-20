from ui_source_support import ui_text
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
class V12Layout(unittest.TestCase):
    def test_v12_new_features_wired_into_queue(self):
        source = ui_text(ROOT)
        for piece in ('verify_transfer(backend', 'self.history.record(', 'self.schedules.claim(',
                      'self.schedules.finish(', 'self.queue.add(task)', 'inventory_remote(',
                      'self.bottom_tabs.addTab(self.history_panel',
                      'self.bottom_tabs.addTab(self.schedules_panel'):
            self.assertIn(piece, source)
    def test_servers_and_packaging_preserved(self):
        source = ui_text(ROOT)
        self.assertIn('self.server_panel = ServerPanel(self)', source)
        self.assertIn('self.sftp_server_panel = SFTPServerPanel(self)', source)
        self.assertIn('build_exe.bat', (ROOT/'README.md').read_text(encoding='utf8'))
        self.assertIn('2.1.8', (ROOT/'packaging/ZFTP.iss').read_text(encoding='utf8'))
