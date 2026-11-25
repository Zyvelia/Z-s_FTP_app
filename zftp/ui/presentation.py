"""Desktop notification controls and masking for client screen sharing."""

from collections import deque
from ipaddress import ip_address
from pathlib import Path
import re
import sqlite3

from PySide6.QtCore import QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QMenu, QMessageBox, QSystemTrayIcon, QToolButton

NOTIFICATIONS = (
    ('completion', 'Transfer completions'),
    ('failure', 'Failures and warnings'),
    ('connection', 'Connections and disconnects'),
    ('other', 'Other alerts'),
)
IP_CANDIDATE = re.compile(r'(?<![\w:])(?:[0-9a-f]{0,4}:){2,}[\da-f:.]+|(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])|(?<![\w:])::(?![\w:])',
                          re.IGNORECASE)


def mask_connection_text(message, values):
    def replace_ip(match):
        try:
            ip_address(match[0])
        except ValueError:
            return match[0]
        return '[hidden]'

    message = IP_CANDIDATE.sub(replace_ip, str(message))
    values = sorted({value for value in values if value}, key=len, reverse=True)
    if values:
        pattern = r'(?<!\w)(?:' + '|'.join(map(re.escape, values)) + r')(?!\w)'
        message = re.sub(pattern, '[hidden]', message, flags=re.IGNORECASE)
    return message


class PresentationMixin:
    def setup_presentation(self):
        self._activity_lines = deque(maxlen=500)
        self._connection_values = {Path.home().name}
        self._recent_downloads = deque(maxlen=20)
        self._completion_toast = False
        self.streamer_enabled = False

    def setup_presentation_controls(self):
        self.notification_options = QToolButton()
        self.notification_options.setText('Toast settings')
        self.notification_options.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.notification_options)
        self.toast_categories = {}
        for key, label in NOTIFICATIONS:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.preferences.value(f'toasts/{key}', True, type=bool))
            action.toggled.connect(lambda checked, key=key: self.preferences.setValue(f'toasts/{key}', checked))
            self.toast_categories[key] = action
        self.notification_options.setMenu(menu)
        self.notifications_enabled.setChecked(self.preferences.value('toasts/enabled', True, type=bool))
        self.notifications_enabled.toggled.connect(lambda checked: self.preferences.setValue('toasts/enabled', checked))
        self.streamer = QCheckBox('Streamer mode')
        self.streamer.setToolTip('Mask client connection details and hide server setup while sharing your screen.')
        self.streamer.toggled.connect(self.set_streamer_mode)
        self.statusBar().addPermanentWidget(self.notification_options)
        self.statusBar().addPermanentWidget(self.streamer)

    def set_streamer_mode(self, enabled):
        self.streamer_enabled = enabled
        self.preferences.setValue('privacy/streamer', enabled)
        for field in (self.host, self.user, self.port, self.ssh_key):
            field.setEchoMode(field.EchoMode.Password if enabled else field.EchoMode.Normal)
        self._connection_values.update(value for state in self.sessions.values()
                                       for value in (state.host, state.username))
        self._connection_values.update((self.host.text().strip(), self.user.text().strip()))
        self.statusBar().clearMessage()
        for tab in self.sessions:
            self.update_private_tab(tab)
        selected = self.profile_select.currentData()
        self.refresh_profiles()
        with QSignalBlocker(self.profile_select):
            index = self.profile_select.findData(selected)
            self.profile_select.setCurrentIndex(max(0, index))
        if enabled:
            self.main_sections.setCurrentIndex(0)
            if self.bottom_tabs.currentWidget() is self.workflows_panel:
                self.bottom_tabs.setCurrentIndex(0)
        for index in range(1, self.main_sections.count()):
            self.main_sections.setTabEnabled(index, not enabled)
        for key in ('ftp_server', 'sftp_server', 'nfs_server'):
            self.sidebar_buttons[key].setEnabled(not enabled)
        self.bottom_tabs.setTabEnabled(self.bottom_tabs.indexOf(self.workflows_panel), not enabled)
        self.logs.setPlainText('\n'.join(self.private_text(line) for line in self._activity_lines))
        self.logs.verticalScrollBar().setValue(self.logs.verticalScrollBar().maximum())
        self.update_local_caption('Local: ' + str(self.local_path))
        self.render_queue()
        self.refresh_history()

    def update_private_tab(self, tab):
        index = self.tabs.indexOf(tab)
        state = tab.state
        label = f'{state.protocol} · Server {index + 1}' if self.streamer_enabled else f'{state.protocol} {state.host}'
        if index >= 0:
            self.tabs.setTabText(index, label)
            self.tabs.setTabToolTip(index, label)
        tab.status.setText(self.private_text(tab.status.text()))
        if state.connected:
            tab.path_label.setText(self.private_text('Remote: ' + state.remote_path))

    def private_text(self, message):
        if not self.streamer_enabled:
            return str(message)
        return mask_connection_text(message, self._connection_values)

    def update_local_caption(self, message):
        self.local_label.setText(self.private_text(message))

    def setup_completion_menu(self, layout):
        self.completion_menu = QMenu(self)
        self.completion_menu.setToolTipsVisible(True)
        self.completion_menu.aboutToShow.connect(self.populate_completion_menu)
        button = QToolButton()
        button.setText('Open download folder')
        button.setPopupMode(QToolButton.InstantPopup)
        button.setMenu(self.completion_menu)
        layout.addWidget(button, alignment=Qt.AlignRight)

    def populate_completion_menu(self):
        self.completion_menu.clear()
        downloads = list(self._recent_downloads)
        try:
            records = self.history.recent(200)
        except (OSError, ValueError, sqlite3.Error):
            records = []
        for row in records:
            if row['status'] == 'Completed' and row['direction'] in ('download', 'download_folder'):
                target = Path(row['destination'])
                downloads.append((Path(row['source']).name, target.parent if row['direction'] == 'download' else target))
        seen = set()
        for name, folder in downloads:
            if (name, folder) in seen:
                continue
            seen.add((name, folder))
            label = self.private_text(f'{name} · {folder.name or folder}')
            action = self.completion_menu.addAction(label.replace('&', '&&'))
            action.setToolTip(self.private_text(folder))
            action.triggered.connect(lambda _, folder=folder: self.open_download_folder(folder))
            if len(seen) == 20:
                break
        if not seen:
            self.completion_menu.addAction('No completed downloads').setEnabled(False)

    def open_download_folder(self, folder):
        if not folder.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.information(self, 'Download folder unavailable',
                                    'The destination folder could not be opened.')

    def toast_clicked(self):
        if self._completion_toast:
            self.show_tray_activity()

    def show_notification(self, level, title, message, category, download_dir):
        if (self.tray is None or not self.notifications_enabled.isChecked()
                or not self.toast_categories[category].isChecked() or not QSystemTrayIcon.supportsMessages()):
            return
        self._completion_toast = download_dir is not None
        icon = {'error': QSystemTrayIcon.Critical, 'warning': QSystemTrayIcon.Warning}.get(level, QSystemTrayIcon.Information)
        self.tray.showMessage(title, message, icon, 4000)
