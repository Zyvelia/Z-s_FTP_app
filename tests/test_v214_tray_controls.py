"""Static regression checks for tray controls without requiring Qt in CI."""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]
from ui_source_support import ui_text
SOURCE = ui_text(ROOT)
# Parse each module separately so Python's AST segment offsets remain valid.
METHODS = {}
SOURCES = {}
for name in ("main_window.py", "tray_controls.py", "shutdown_controls.py"):
    module_source = (ROOT / "zftp/ui" / name).read_text(encoding="utf-8")
    for cls in ast.parse(module_source).body:
        if isinstance(cls, ast.ClassDef):
            for method in cls.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    METHODS[method.name] = method
                    SOURCES[method.name] = module_source

def method_text(name):
    return ast.get_source_segment(SOURCES[name], METHODS[name])


class TrayControlsTests(unittest.TestCase):
    def test_tray_actions_are_available(self):
        for command in ('Hide Z FTP', 'Open Transfers', 'Open Activity',
                        'Cancel queued transfers…', 'Cancel active transfers…',
                        'Close button hides to tray', 'Check for updates', 'Quit Z FTP'):
            with self.subTest(command=command):
                self.assertIn(command, SOURCE)

    def test_tray_has_full_control_methods(self):
        for method in ('setup_tray_controls', 'refresh_tray_status', 'hide_to_tray',
                       'show_from_tray', 'toggle_tray_visibility', 'show_tray_transfers',
                       'show_tray_activity', 'tray_cancel_queued', 'tray_cancel_active',
                       'quit_from_tray', 'on_tray_activated'):
            self.assertIn(method, METHODS)

    def test_closing_defaults_to_disconnect_not_hide(self):
        constructor = method_text('__init__')
        self.assertIn('self._close_to_tray = False', constructor)
        close = method_text('closeEvent')
        self.assertIn('self._close_to_tray', close)
        self.assertIn('self.hide_to_tray()', close)
        self.assertIn('self._tray_force_quit', close)
        self.assertIn('self._advance_shutdown()', close)

    def test_tray_quit_invokes_normal_close(self):
        body = method_text('quit_from_tray')
        self.assertIn('self._tray_force_quit = True', body)
        self.assertIn('self.close()', body)

    def test_tray_update_waits_for_graceful_shutdown(self):
        request = method_text('update_from_tray')
        self.assertIn("('Queued', 'Running')", request)
        self.assertIn('self.quit_from_tray()', request)
        self.assertIn('self.launch_pending_updater()', method_text('_finish_close'))
        self.assertIn("'-WaitForProcessId'", method_text('launch_pending_updater'))
        updater = (ROOT / 'Update-ZFTP.ps1').read_text(encoding='utf-8')
        self.assertLess(updater.index('.WaitForExit(30000)'), updater.index('Invoke-WebRequest'))

    def test_cancellation_requires_confirmation(self):
        for method in ('tray_cancel_queued', 'tray_cancel_active'):
            body = method_text(method)
            self.assertIn('QMessageBox.question(', body)
            self.assertIn('QMessageBox.No', body)
        self.assertIn('tab.state.cancelled = True',
                      method_text('tray_cancel_active'))

    def test_background_mode_does_not_close_network_sessions(self):
        body = method_text('hide_to_tray')
        self.assertIn('self.hide()', body)
        self.assertNotIn('.disconnect(', body)
        self.assertNotIn('.cancel(', body)

    def test_release_version_updated(self):
        installer = (ROOT / 'packaging' / 'ZFTP.iss').read_text(encoding='utf-8')
        self.assertIn('AppVersion "2.1.12"', installer)
        self.assertIn('ZFTP_Setup_v2.1.12', installer)


if __name__ == '__main__':
    unittest.main()
