"""Optional Qt offscreen smoke checks; skipped when PySide6 unavailable."""
import os
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from PySide6.QtCore import QCoreApplication, QEvent, QUrl
    from PySide6.QtTest import QSignalSpy, QTest
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPlainTextEdit, QScrollArea
    from zftp.ui.main_window import MainWindow
    from zftp.protocols.ftp_engine import RemoteEntry
    from zftp.services.sessions import ConnectionSession
    from zftp.ui.remote_tab import RemoteTab
    from zftp.services.transfer_history import TransferHistory
    from zftp.services.workflows import WorkflowStore
    from zftp.transfers.transfer_queue import FolderProgress, Transfer
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
        self.assertEqual(window.main_sections.count(), 4)
        self.assertEqual(window.connection_badge.text(), '0 connected')
        self.assertFalse(window.server_panel.controller.running)
        self.assertFalse(window.nfs_server_panel.active_or_stopping)
        window.navigate_sidebar('nfs_server')
        self.assertIs(window.main_sections.currentWidget(), window.nfs_server_panel)
        window.navigate_sidebar('connection')
        window.show()
        window.toggle_connection_setup()
        self.assertTrue(window.connection_setup.isHidden())
        window.toggle_connection_setup()
        self.assertFalse(window.connection_setup.isHidden())
        window.close()

    def test_winnfsd_managed_process_receives_bind_args_and_quits(self):
        window = MainWindow()
        self.addCleanup(window.close)
        panel = window.nfs_server_panel
        worker = panel.process
        panel._supported = True
        address = '192.168.1.51'
        panel.address.addItem(address, address)
        panel.address.setCurrentIndex(panel.address.count() - 1)
        set_program = worker.setProgram
        set_arguments = worker.setArguments
        fake_server = (
            "import sys\n"
            "print('NFS started', flush=True)\n"
            "for line in sys.stdin:\n"
            "    if line.strip() == 'quit':\n"
            "        sys.exit(0 if next(sys.stdin).strip() == 'y' else 1)\n"
        )

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'PS4 dumps'
            folder.mkdir()
            executable = Path(tmp) / 'WinNFSd.exe'
            executable.touch()
            panel.folder.setText(str(folder))
            with (patch.object(panel, 'refresh_addresses'),
                  patch('zftp.ui.nfs_server_ui.backend_path', return_value=executable),
                  patch('zftp.ui.nfs_server_ui.has_verified_backend', return_value=True),
                  patch.object(worker, 'setProgram', side_effect=lambda _: set_program(sys.executable)) as program,
                  patch.object(worker, 'setArguments',
                               side_effect=lambda _: set_arguments(['-u', '-c', fake_server])) as arguments):
                panel.start()
                try:
                    self.assertTrue(worker.waitForStarted(3000))
                    program.assert_called_once_with(str(executable))
                    arguments.assert_called_once_with(['-addr', address, str(folder.resolve()), '/ps4'])
                    self.assertEqual(panel.url.text(), f'nfs://{address}/ps4')
                    self.assertTrue(panel.active_or_stopping)
                    panel.stop()
                    self.assertTrue(worker.waitForFinished(3000))
                    self.assertEqual(worker.exitCode(), 0)
                    self.assertEqual(panel.status.text(), 'Stopped')
                    self.assertFalse(panel.active_or_stopping)
                    self.assertFalse(panel.url.text())
                    self.assertIn('NFS started', panel.output.toPlainText())
                finally:
                    if panel.active_or_stopping:
                        worker.kill()
                        worker.waitForFinished(3000)

    def test_nfs_download_rejects_changed_payload_before_launch(self):
        window = MainWindow()
        self.addCleanup(window.close)
        panel = window.nfs_server_panel
        data = b'expected binary'
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'download.exe'
            executable = folder / 'cache' / 'WinNFSd.exe'
            source.write_bytes(data)
            with (patch('zftp.ui.nfs_server_ui.BACKEND_URL', QUrl.fromLocalFile(str(source)).toString()),
                  patch('zftp.ui.nfs_server_ui.BACKEND_SIZE', len(data)),
                  patch('zftp.servers.nfs_backend.BACKEND_SIZE', len(data)),
                  patch('zftp.servers.nfs_backend.BACKEND_SHA256', sha256(data).hexdigest()),
                  patch.object(panel, '_launch') as launch):
                panel._download_backend(executable, folder)
                self.assertTrue(QSignalSpy(panel._download.finished).wait(3000))
                # QNetworkReply holds the local fixture open until its deferred deletion.
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                launch.assert_called_once_with(executable, folder)
                self.assertEqual(executable.read_bytes(), data)
                self.assertFalse(panel.active_or_stopping)

                launch.reset_mock()
                source.write_bytes(b'altered binary!')
                panel._download_backend(executable, folder)
                self.assertTrue(QSignalSpy(panel._download.finished).wait(3000))
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                launch.assert_not_called()
                self.assertIn('checksum', panel.status.text())
                self.assertEqual(executable.read_bytes(), data)
                self.assertFalse(panel.active_or_stopping)

    def test_transfers_only_reread_remote_file_when_verification_is_enabled(self):
        window = MainWindow()
        self.addCleanup(window.close)
        self.assertFalse(window.verify_checksums.isChecked())
        backend = Mock()
        tab = Mock()
        tab.make_download_connection = None
        state = ConnectionSession(backend, 'FTP', 'localhost', 21, 'test', connected=True)
        tab.state = state
        window.sessions[tab] = state
        self.addCleanup(window.sessions.clear)

        def upload(*args, verify_staged, **_):
            if verify_staged is not None:
                verify_staged('package.pkg.zftp.part')
            return 1024

        with (patch.object(window, 'run_session_job') as start_job,
              patch('zftp.ui.transfers_view.resilient_upload', side_effect=upload),
              patch('zftp.ui.transfers_view.resilient_download', return_value=1024),
              patch('zftp.ui.transfers_view.verify_transfer', return_value='digest') as verify):
            for enabled in (False, True):
                window.verify_checksums.setChecked(enabled)
                for direction in ('upload', 'download'):
                    with self.subTest(enabled=enabled, direction=direction):
                        item = Transfer(str(id(tab)), direction, 'package.pkg', 'copy.pkg', '/', '.')
                        window.queue.add(item)
                        window.pump_queue(tab)
                        operation = start_job.call_args.args[2]
                        result = operation(Mock())
                        self.assertEqual(result, (1024, 'digest' if enabled else None))
                        self.assertEqual(verify.call_count, int(enabled))
                        verify.reset_mock()
                        window.queue.finish(item)
                        window.render_queue()
                        self.assertEqual('Verifying' in window.stage_ribbon.text(), enabled)

    def test_two_downloads_allow_an_unrelated_delete_and_cancel_independently(self):
        window = MainWindow()
        self.addCleanup(window.close)
        primary = Mock()
        primary.list_dir.return_value = []
        state = ConnectionSession(primary, 'FTP', 'localhost', 21, 'test', connected=True)
        tab = RemoteTab(state, window)
        window.sessions[tab] = state
        self.addCleanup(window.sessions.clear)
        downloads = [Mock(), Mock()]
        tab.make_download_connection = Mock(side_effect=[(backend, Mock()) for backend in downloads])
        window.verify_checksums.setChecked(False)
        window.refresh_history = Mock()
        window.record_transfer = Mock()
        started = {'a.bin': Event(), 'b.bin': Event()}
        release = Event()

        def wait_for(condition):
            for _ in range(250):
                if condition():
                    return
                QTest.qWait(10)
            self.fail('Timed out waiting for background job')

        def download(backend, filename, target, progress, cancelled, settings):
            self.assertIs(backend, downloads[0 if filename == 'a.bin' else 1])
            started[filename].set()
            while not release.wait(0.01):
                if cancelled():
                    raise InterruptedError('Download cancelled')
            target.write_bytes(filename.encode())
            return target.stat().st_size

        with tempfile.TemporaryDirectory() as directory, patch(
                'zftp.ui.transfers_view.resilient_download', side_effect=download):
            root = Path(directory)
            window.enqueue_downloads(tab, ['a.bin', 'b.bin'], '/', root)
            try:
                wait_for(lambda: all(event.is_set() for event in started.values()))
                self.assertTrue(tab.download.isEnabled())
                self.assertTrue(tab.delete.isEnabled())

                with (patch.object(tab, 'selected', return_value=RemoteEntry('a.bin', False, 5)),
                      patch.object(QMessageBox, 'warning') as warning):
                    window.delete(tab)
                    warning.assert_called_once()
                    primary.delete_file.assert_not_called()

                with (patch.object(tab, 'selected', return_value=RemoteEntry('unused.bin', False, 5)),
                      patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes)):
                    window.delete(tab)
                wait_for(lambda: not state.busy)
                primary.delete_file.assert_called_once_with('unused.bin')
                self.assertEqual([item.status for item in window.queue.jobs], ['Running', 'Running'])

                window.queue_table.setCurrentCell(0, 0)
                window.cancel_selected_job()
                wait_for(lambda: window.queue.jobs[0].status == 'Cancelled')
                self.assertEqual(window.queue.jobs[1].status, 'Running')
            finally:
                release.set()
                wait_for(lambda: not window.jobs)
            self.assertEqual(window.queue.jobs[1].status, 'Completed')
            self.assertEqual((root / 'b.bin').read_bytes(), b'b.bin')
            self.assertFalse((root / 'a.bin').exists())
            for backend in downloads:
                backend.disconnect.assert_called_once()

    def test_remote_txt_double_click_shows_read_only_plain_text(self):
        window = MainWindow()
        self.addCleanup(window.close)
        backend = Mock()
        backend.read_bytes.return_value = '<html>café</html>'.encode('utf-16')
        state = ConnectionSession(backend, 'FTP', 'localhost', 21, 'test', connected=True)
        state.entries = [RemoteEntry('Readme.TXT', False, 32)]
        tab = RemoteTab(state, window)
        window.sessions[tab] = state
        self.addCleanup(window.sessions.clear)
        with patch.object(window, 'run_session_job') as start:
            window.open_remote(tab, 0)
        _, _, read, show = start.call_args.args
        show(read(None))
        dialog = next(d for d in window.findChildren(QDialog) if d.windowTitle() == 'Readme.TXT')
        self.addCleanup(dialog.close)
        text = dialog.findChild(QPlainTextEdit)
        self.assertTrue(text.isReadOnly())
        self.assertEqual(text.toPlainText(), '<html>café</html>')

    def test_remote_ini_edits_preserve_encoding_and_use_remote_backup(self):
        window = MainWindow()
        self.addCleanup(window.close)
        backend = Mock()
        original = '﻿name=café\r\n'.encode('utf-8')
        backend.read_bytes.return_value = original
        state = ConnectionSession(backend, 'FTP', 'localhost', 21, 'test', connected=True)
        state.entries = [RemoteEntry('settings.INI', False, len(original))]
        tab = RemoteTab(state, window)
        window.sessions[tab] = state
        self.addCleanup(window.sessions.clear)

        with patch.object(window, 'run_session_job') as start:
            window.open_remote(tab, 0)
            _, _, read, show = start.call_args.args
            show(read(None))
            dialog = next(d for d in window.findChildren(QDialog) if d.windowTitle() == 'settings.INI')
            self.addCleanup(dialog.saved)
            dialog.edit.click()
            dialog.viewer.setPlainText('name=résumé\n')
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
                dialog.save_button.click()
            _, _, publish, *_ = start.call_args.args
            backup = publish(None)
        staged, payload = backend.write_new_bytes.call_args.args
        self.assertEqual(payload, '﻿name=résumé\r\n'.encode('utf-8'))
        self.assertEqual([call.args for call in backend.rename.call_args_list],
                         [('settings.INI', backup), (staged, 'settings.INI')])

    def test_vscode_requires_current_diff_and_guards_remote_upload(self):
        from zftp.services.remote_management import RemoteConflictError

        window = MainWindow()
        self.addCleanup(window.close)
        backend = Mock()
        original = 'name=café\r\n'.encode('utf-16')
        backend.read_bytes.return_value = original
        state = ConnectionSession(backend, 'FTP', 'localhost', 21, 'test', remote_path='/config', connected=True)
        state.entries = [RemoteEntry('settings.ini', False, len(original))]
        tab = RemoteTab(state, window)
        window.sessions[tab] = state
        self.addCleanup(window.sessions.clear)
        with (patch.object(window, 'run_session_job') as start,
              patch('zftp.ui.file_browser.find_vscode', return_value='Code.exe'),
              patch('zftp.ui.external_editor.subprocess.Popen') as launch):
            window.edit_remote_in_vscode(tab, state.entries[0])
            _, _, read, show = start.call_args.args
            show(read(None))
            dialog = next(d for d in window.findChildren(QDialog) if d.windowTitle() == 'Edit in VS Code · settings.ini')
            self.addCleanup(dialog.temporary.cleanup)
            self.assertEqual(launch.call_args.args[0][-1], str(dialog.path))
            self.assertEqual(dialog.path.read_bytes(), b'name=caf\xc3\xa9\r\n')
            self.assertFalse(dialog.upload.isEnabled())

            dialog.path.write_bytes(b'name=first\r\n')
            dialog.review()
            self.assertIn('-name=café', dialog.diff.toPlainText())
            self.assertIn('+name=first', dialog.diff.toPlainText())
            dialog.path.write_bytes(b'name=second\r\n')
            dialog.upload.click()
            start.assert_called_once()
            self.assertFalse(dialog.upload.isEnabled())
            backend.rename.assert_not_called()

            dialog.review()
            state.remote_path = '/other'
            dialog.upload.click()
            _, _, publish, saved, failed = start.call_args.args
            backend.read_bytes.return_value = 'name=changed'.encode('utf-16')
            with self.assertRaises(RemoteConflictError):
                publish(None)
            backend.write_new_bytes.assert_not_called()
            failed(None)

            backend.read_bytes.return_value = original
            dialog.upload.click()
            backup = publish(None)
            staged, payload = backend.write_new_bytes.call_args.args
            self.assertEqual(payload, 'name=second\r\n'.encode('utf-16'))
            self.assertEqual([call.args for call in backend.rename.call_args_list],
                             [('settings.ini', backup), (staged, 'settings.ini')])
            self.assertEqual([call.args for call in backend.cwd.call_args_list][-2:],
                             [('/config',), ('/other',)])
            path = dialog.path
            with patch.object(window, 'refresh'):
                saved(backup)
            self.assertFalse(path.exists())

    def test_folder_eta_and_bar_update_before_any_file_finishes(self):
        window = MainWindow()
        self.addCleanup(window.close)
        child = Transfer('test', 'download', 'file.bin', 'file.bin', '/', '.', total=4096)
        folder = Transfer('test', 'download_folder', 'game', '.', '/', '.', children=(child,), total=1)
        window.queue.add(folder)
        window.queue.next_for('test')
        with patch('zftp.ui.transfers_view.time.monotonic', side_effect=[10.0, 12.0]):
            window.on_transfer_progress(folder, FolderProgress(0, 0, 4096, '1/1'), None)
            window.on_transfer_progress(folder, FolderProgress(0, 1024, 4096, '1/1'), None)
        self.assertEqual(folder.count, 0)
        self.assertEqual(folder.speed_bps, 512)
        self.assertEqual(folder.eta_seconds, 6)
        window.render_queue()
        self.assertEqual(window.queue_table.item(0, 5).text(), '00:06')
        window.on_transfer_progress(folder, FolderProgress(0, 1024, None, '1/1'), None)
        window.render_queue()
        self.assertEqual(window.queue_table.item(0, 5).text(), 'Size unknown')

    def test_file_tools_are_only_offered_for_files(self):
        window = MainWindow()
        self.addCleanup(window.close)
        menu = Mock()
        window.remote_ps3_tools(menu, None, RemoteEntry('games', True))
        window.remote_ps3_tools(menu, None, None)
        menu.addMenu.assert_not_called()
        window.remote_ps3_tools(menu, None, RemoteEntry('settings.ini', False))
        menu.addMenu.assert_called_once_with('File tools')
        self.assertEqual([call.args[0] for call in menu.addMenu.return_value.addAction.call_args_list],
                         ['Edit config', 'View / edit hex'])

    def test_pbm_preview_opens_an_image_dialog(self):
        window = MainWindow()
        self.addCleanup(window.close)
        self.assertTrue(window.show_format_preview('bitmap.pbm', b'P1\n2 2\n0 1\n1 0\n', 17))
        dialog = next(d for d in window.findChildren(QDialog) if d.windowTitle() == 'bitmap.pbm')
        image = dialog.findChild(QScrollArea).widget().pixmap()
        self.assertEqual((image.width(), image.height()), (2, 2))
        self.addCleanup(dialog.close)

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
