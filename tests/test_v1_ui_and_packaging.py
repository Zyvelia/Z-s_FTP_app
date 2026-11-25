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
    def test_notification_sender_matches_the_windows_shortcuts(self):
        import re
        entry_point = (ROOT / 'main.py').read_text(encoding='utf-8')
        installer = (ROOT / 'packaging' / 'ZFTP.iss').read_text(encoding='utf-8')
        app_id = re.search(r'^WINDOWS_APP_ID = "([^"]+)"', entry_point, re.MULTILINE)
        self.assertIsNotNone(app_id)
        self.assertIn(f'#define AppUserModelID "{app_id[1]}"', installer)
        self.assertEqual(installer.count('AppUserModelID: "{#AppUserModelID}"'), 2)
        self.assertIn('ValueName: "DisplayName"; ValueData: "{#AppName}"', installer)
        self.assertLess(entry_point.index('    register_windows_notification_identity(icon)'),
                        entry_point.index('    app = QApplication(sys.argv)'))

    @unittest.skipUnless(sys.platform == 'win32', 'Requires the Windows shell')
    def test_windows_process_registers_the_notification_sender(self):
        import ctypes
        from uuid import uuid4
        import winreg
        from unittest.mock import patch

        from main import APP_NAME, WINDOWS_APP_ID, register_windows_notification_identity

        key_path = f'Software\\ZFTP-Test-{uuid4().hex}'
        icon = ROOT / 'assets' / 'zftp_icon.png'
        value = ctypes.c_wchar_p()
        try:
            with patch('main.WINDOWS_APP_KEY', key_path):
                register_windows_notification_identity(icon)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                self.assertEqual(winreg.QueryValueEx(key, 'DisplayName')[0], APP_NAME)
                self.assertEqual(winreg.QueryValueEx(key, 'IconUri')[0], str(icon))
            get_id = ctypes.WinDLL('shell32').GetCurrentProcessExplicitAppUserModelID
            get_id.argtypes = (ctypes.POINTER(ctypes.c_wchar_p),)
            get_id.restype = ctypes.c_long
            self.assertEqual(get_id(ctypes.byref(value)), 0)
            self.assertEqual(value.value, WINDOWS_APP_ID)
        finally:
            if value:
                free = ctypes.WinDLL('ole32').CoTaskMemFree
                free.argtypes = (ctypes.c_void_p,)
                free.restype = None
                free(ctypes.cast(value, ctypes.c_void_p))
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            except FileNotFoundError:
                pass

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
