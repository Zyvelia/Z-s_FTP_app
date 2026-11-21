"""Offline, dependency-free regression tests for v1.0 UX support and packaging."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zftp.ui.ui_theme import APP_STYLESHEET, status_label


class ThemeTests(unittest.TestCase):
    def test_status_counts(self):
        self.assertEqual(status_label(0), '0 connected')
        self.assertEqual(status_label(3, 2), '3 connected · 2 transferring')
        with self.assertRaises(ValueError): status_label(-1)

    def test_theme_has_focus_and_selection(self):
        for token in ('QLineEdit:focus', 'QTableWidget::item:selected',
                      'QLabel#statusBadge', 'QTabBar::tab:selected'):
            self.assertIn(token, APP_STYLESHEET)


class PackagingTests(unittest.TestCase):
    def test_batch_builds_include_error_checks(self):
        exe = (ROOT / 'build_exe.bat').read_text(encoding='utf-8')
        installer = (ROOT / 'build_installer.bat').read_text(encoding='utf-8')
        self.assertIn('--onedir', exe)
        self.assertIn('--windowed', exe)
        self.assertIn('exit /b 1', exe)
        self.assertIn('ISCC.exe', installer)
        self.assertIn('exit /b 1', installer)

    def test_installer_uses_per_user_install(self):
        spec = (ROOT / 'packaging' / 'ZFTP.iss').read_text(encoding='utf-8')
        self.assertIn('PrivilegesRequired=lowest', spec)
        self.assertIn('recursesubdirs', spec)
        self.assertIn('ZFTP.exe', spec)

    def test_legacy_components_retained(self):
        for name in ('zftp/servers/server_core.py', 'zftp/ui/server_ui.py', 'zftp/protocols/ftp_engine.py',
                     'zftp/protocols/sftp_engine.py', 'zftp/transfers/reliable_transfer.py', 'zftp/services/sync_engine.py',
                     'zftp/transfers/transfer_queue.py', 'zftp/services/remote_management.py'):
            self.assertTrue((ROOT / name).is_file(), name)


if __name__ == '__main__':
    unittest.main()
