"""Optional Qt offscreen smoke checks; skipped when PySide6 unavailable."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from PySide6.QtWidgets import QApplication
    from zftp.ui.main_window import MainWindow
    from zftp.services.transfer_history import TransferHistory
    from zftp.services.workflows import WorkflowStore
    from zftp.transfers.transfer_queue import Transfer
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
        window.show()
        window.toggle_connection_setup()
        self.assertTrue(window.connection_setup.isHidden())
        window.toggle_connection_setup()
        self.assertFalse(window.connection_setup.isHidden())
        window.close()

    def test_activity_combines_session_log_and_alerts(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.log('Queued upload')
        window.notify('success', 'Test', 'Done')
        entries = window.logs.toPlainText().splitlines()
        self.assertEqual(len(entries), 2)
        self.assertTrue(entries[0].endswith('Queued upload'))
        self.assertTrue(entries[1].endswith('Test · Done'))
        self.assertEqual([window.bottom_tabs.tabText(i) for i in range(window.bottom_tabs.count())],
                         ['Activity', 'History', 'Workflows'])

    def test_history_button_reveals_persisted_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.sqlite3'
            window = MainWindow()
            self.addCleanup(window.close)
            window.history = TransferHistory(path)
            window.show()
            window.history_button.click()
            self.assertEqual(window.history_summary.text(), 'No transfers recorded yet')
            self.assertFalse(window.history_export.isEnabled())

            transfer = Transfer('test', 'download', 'report.txt', 'report.txt', '/', directory)
            transfer.status = 'Completed'
            TransferHistory(path).record(transfer, 'example.org', 'SFTP')
            window.activity_toggle.click()
            self.assertFalse(window.transfer_details.isVisible())
            window.history_button.click()
            self.assertIs(window.bottom_tabs.currentWidget(), window.history_panel)
            self.assertTrue(window.history_table.isVisible())
            self.assertGreater(window.history_table.viewport().height(), 100)
            self.assertEqual(window.history_table.rowCount(), 1)
            self.assertEqual(window.history_table.item(0, 2).text(), 'report.txt')
            self.assertTrue(window.history_export.isEnabled())

    def test_workflow_actions_follow_selection_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow()
            self.addCleanup(window.close)
            window.workflows = WorkflowStore(Path(directory) / 'workflows.sqlite3')
            window.refresh_workflows()
            self.assertFalse(window.workflow_run.isEnabled())
            self.assertFalse(window.workflow_edit.isEnabled())
            ident = window.workflows.add('Copy report', [{
                'action': 'copy_remote',
                'source_endpoint': ['SFTP', 'source', '22', 'user'],
                'destination_endpoint': ['SFTP', 'destination', '22', 'user'],
                'source_dir': '/', 'destination_dir': '/',
                'source_name': 'report.txt', 'destination_name': 'report.txt',
            }])
            window.refresh_workflows(ident)
            self.assertTrue(window.workflow_run.isEnabled())
            self.assertTrue(window.workflow_edit.isEnabled())
            window.run_selected_workflow()
            self.assertEqual(window.workflow_run.text(), 'Resume')
            self.assertFalse(window.workflow_edit.isEnabled())
            self.assertEqual(window.selected_workflow()['id'], ident)


if __name__ == '__main__':
    unittest.main()
