"""Optional Qt offscreen smoke checks; skipped when PySide6 unavailable."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from PySide6.QtWidgets import QApplication
    from zftp.ui.main_window import MainWindow
except ImportError:
    QApplication = None
    MainWindow = None


@unittest.skipUnless(QApplication is not None, 'PySide6 and required GUI dependencies unavailable')
class GUIv1SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_client_and_server_controls_present(self):
        window = MainWindow()
        self.assertIsNotNone(window.server_panel)
        self.assertEqual(window.main_sections.count(), 3)
        self.assertEqual(window.connection_badge.text(), '0 connected')
        self.assertFalse(window.server_panel.controller.running)
        window.toggle_connection_setup()
        self.assertTrue(window.connection_setup.isHidden())
        window.toggle_connection_setup()
        self.assertFalse(window.connection_setup.isHidden())
        window.close()

    def test_notification_history_visible(self):
        window = MainWindow()
        window.notify('success', 'Test', 'Done')
        self.assertEqual(window.notices.items[-1].title, 'Test')
        self.assertEqual(window.notifications_view.count(), 1)
        window.close()


if __name__ == '__main__':
    unittest.main()
