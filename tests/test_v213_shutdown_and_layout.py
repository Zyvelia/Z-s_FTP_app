"""Static regression checks for graceful exit and compact transfer-panel wiring."""
from ui_source_support import ui_text

import pathlib, unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
class ShutdownAndLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = ui_text(ROOT)
    def test_auto_disconnect_idle_connections(self):
        self.assertIn('def _advance_shutdown(self):', self.code)
        self.assertIn('self.close_tab(tab)', self.code)
        self.assertIn('self._quit_timer.start()', self.code)
    def test_confirmation_for_active_work(self):
        self.assertIn("'Transfers still running'", self.code)
        self.assertIn('state.cancelled = True', self.code)
        self.assertIn('QMessageBox.No)', self.code)
    def test_server_safety_stays(self):
        self.assertIn('self.server_panel.active_or_stopping or self.sftp_server_panel.active_or_stopping', self.code)
    def test_compact_settings_grid(self):
        self.assertIn('options = QGridLayout()', self.code)
        self.assertIn('wf_toolbar = QHBoxLayout()', self.code)
        self.assertIn('self.verify_checksums = QCheckBox(', self.code)
if __name__ == '__main__': unittest.main()
