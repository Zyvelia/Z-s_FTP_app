"""Z FTP v2.1.12 — main window composition; UI behaviors live in dedicated modules."""
from __future__ import annotations

import sys
import os
import time
import posixpath
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt, QTimer, QFile, QSize, QSettings, QLockFile
from PySide6.QtGui import QShortcut, QKeySequence, QCursor, QIcon, QColor
from zftp.services.directory_performance import scan_directory, batches, RenderGate, UpdateThrottle
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget, QSizePolicy, QListWidget, QListWidgetItem, QSpinBox, QCheckBox, QPlainTextEdit,
    QFrame, QGridLayout, QSystemTrayIcon, QStyle, QToolButton, QMenu, QFormLayout,
)
from zftp.protocols.ftp_engine import FTPBackend, clean_remote_name, parent_remote
from zftp.services.remote_cache import RemoteDirectoryCache
from zftp.protocols.sftp_engine import SFTPBackend, UntrustedHostKey
from zftp.services.profiles import ProfileStore
from zftp.ui.profile_dialog import ProfileDialog
from zftp.services.sessions import ConnectionSession
from zftp.transfers.transfer_queue import TransferQueue, Transfer
from zftp.ui.transfer_tables import TransferTable
from zftp.transfers.reliable_transfer import SharedRateLimiter, TransferSettings, download as resilient_download, upload as resilient_upload, with_retries
from zftp.transfers.eta_estimator import ETAEstimator, format_eta
from zftp.services.remote_management import (validate_name, read_editor_snapshot, safe_save_editor, find_remote, RemoteConflictError)
from zftp.ui.server_ui import ServerPanel
from zftp.ui.sftp_server_ui import SFTPServerPanel
from zftp.ui.nfs_server_ui import NFSServerPanel
from zftp.ui.phone_drop import PhoneDropPanel
from zftp.transfers.integrity import verify_transfer
from zftp.services.transfer_history import TransferHistory
from zftp.services.workflows import WorkflowStore
from zftp.ui.workflow_dialog import WorkflowDialog
from zftp.ui.ui_theme import APP_STYLESHEET, status_label
from zftp.services.sync_engine import (Inventory, Change, inventory_local, inventory_remote, plan,
                         safe_local_target, relpath, unique_name)


from zftp.ui.ui_workers import Job, Events
from zftp.ui.remote_tab import RemoteTab
from zftp.ui.help_dialog import HelpDialog
from zftp.ui.tray_controls import TrayControlsMixin
from zftp.ui.file_browser import FileBrowserMixin
from zftp.ui.transfers_view import TransfersViewMixin
from zftp.ui.workflow_views import WorkflowViewsMixin
from zftp.ui.shutdown_controls import ShutdownControlsMixin
from zftp.ui.profile_controls import ProfileControlsMixin
from zftp.ui.units import format_rate, format_size
from zftp.services.queue_recovery import QueueRecovery
from zftp.services.storage import portable_data


class MainWindow(TrayControlsMixin, FileBrowserMixin, TransfersViewMixin,
                 WorkflowViewsMixin, ShutdownControlsMixin, ProfileControlsMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Z FTP  •  v2.1.12')
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[2] / 'assets' / 'zftp_icon.png')))
        self.resize(1500, 920)
        self.setMinimumSize(1040, 680)
        portable = portable_data()
        self.history = TransferHistory(portable / 'transfer_history.sqlite3') if portable else TransferHistory()
        self.workflows = WorkflowStore(portable / 'workflows.sqlite3') if portable else WorkflowStore()
        self.workflow_running = set()
        self.tray = None
        self._tray_force_quit = False
        self._pending_updater = None
        self._close_to_tray = False
        self._tray_status_action = None
        self._tray_visibility_action = None
        self.pool = QThreadPool(self); self.pool.setMaxThreadCount(8)
        self.download_pool = QThreadPool(self)
        self.download_pool.setMaxThreadCount(4)
        self.jobs: list[Job] = []
        # Graceful close is a two-phase process: request cancellation, then let
        # workers finish before disconnecting and destroying their Qt owners.
        self._quit_requested = False
        self._quit_timer = QTimer(self)
        self._quit_timer.setInterval(250)
        self._quit_timer.timeout.connect(self._advance_shutdown)
        self.store = ProfileStore()
        self.loaded_profile = None
        self.local_path = Path.home()
        self.sessions: dict[RemoteTab, ConnectionSession] = {}
        self.preferences = (QSettings(str(portable / 'settings.ini'), QSettings.IniFormat) if portable
                            else QSettings('Zyvelia', 'ZFTP'))
        self.setup_presentation()
        self.queue = TransferQueue()
        self.queue.paused = self.preferences.value('queue/paused', False, type=bool)
        queue_path = self.store.path.with_name('queue.json')
        self._queue_lock = QLockFile(str(queue_path.with_suffix('.lock')))
        self._queue_lock.setStaleLockTime(0)
        recovery_disabled = ''
        try:
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._queue_lock.tryLock(0):
                recovery_disabled = 'The recovery queue is unavailable or in use by another Z FTP instance.'
        except OSError as error:
            recovery_disabled = str(error)
        self.queue_recovery = QueueRecovery(queue_path, recovery_disabled)
        self._queue_checkpoint = None
        self._queue_save_warning = False
        self.global_limiter = SharedRateLimiter()
        self.transfer_metrics = {}  # transfer id -> ETAEstimator
        self.transfer_tabs: dict[str, RemoteTab] = {}
        self.main_sections = QTabWidget()
        self.setCentralWidget(self.main_sections)
        self.main_sections.setDocumentMode(True)
        # The client uses sidebar navigation; expose the tab bar on server pages
        # so the user can always return to Client without losing navigation.
        self.main_sections.tabBar().hide()
        self.main_sections.currentChanged.connect(
            lambda index: self.main_sections.tabBar().setVisible(index != 0))
        root = QWidget(); self.main_sections.addTab(root, "Client")
        self.server_panel = ServerPanel(self)
        self.main_sections.addTab(self.server_panel, "FTP/FTPS · OFF")
        self.sftp_server_panel = SFTPServerPanel(self)
        self.main_sections.addTab(self.sftp_server_panel, "SFTP · OFF")
        self.nfs_server_panel = NFSServerPanel(self)
        self.main_sections.addTab(self.nfs_server_panel, "NFS · OFF")
        self.phone_drop_panel = PhoneDropPanel(self)
        self.main_sections.addTab(self.phone_drop_panel, 'Phone Drop')

        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('appSidebar')
        sidebar.setFixedWidth(190)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 16, 12, 14)
        side.setSpacing(6)
        sidebar_brand = QLabel('◈  Z FTP')
        sidebar_brand.setObjectName('sidebarBrand')
        side.addWidget(sidebar_brand)
        side.addSpacing(16)
        self.sidebar_buttons = {}
        for key, label in (
            ('connection', 'Connection'),
            ('browser', 'File Browser'),
            ('transfers', 'Transfers'),
            ('ftp_server', 'FTP/FTPS Server'),
            ('sftp_server', 'SFTP Server'),
            ('nfs_server', 'NFS Server'),
            ('phone_drop', 'Phone Drop'),
            ('settings', 'Settings'),
            ('about', 'About'),
        ):
            button = QPushButton(label)
            # Native Qt icons follow the platform DPI, rather than fragile text glyphs.
            nav_icons = {
                'connection': QStyle.SP_DriveNetIcon,
                'browser': QStyle.SP_DirIcon,
                'transfers': QStyle.SP_ArrowUp,
                'ftp_server': QStyle.SP_DriveNetIcon,
                'sftp_server': QStyle.SP_DriveNetIcon,
                'nfs_server': QStyle.SP_DriveNetIcon,
                'phone_drop': QStyle.SP_ArrowDown,
                'settings': QStyle.SP_FileDialogContentsView,
                'about': QStyle.SP_MessageBoxInformation,
            }
            button.setIcon(self.style().standardIcon(nav_icons[key]))
            button.setIconSize(QSize(19, 19))
            button.setObjectName('sidebarItem')
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty('selected', key == 'connection')
            side.addWidget(button)
            self.sidebar_buttons[key] = button
            button.clicked.connect(lambda _=False, section=key: self.navigate_sidebar(section))
            if key == 'settings':
                button.setToolTip('Open transfer settings')
        side.addStretch(1)
        side_info = QLabel('SECURE FILE TRANSFER\nClient · FTP/FTPS · SFTP')
        side_info.setObjectName('sidebarFooter')
        side.addWidget(side_info)
        shell.addWidget(sidebar)

        content = QWidget()
        content.setObjectName('mainContent')
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)
        shell.addWidget(content, 1)
        header = QFrame(); header.setObjectName('appHeader')
        header_layout = QHBoxLayout(header)
        brand = QVBoxLayout()
        heading = QLabel('Z FTP  /  FILE TRANSFER'); heading.setObjectName('appTitle')
        subtitle = QLabel('Manage your connections, local files and remote servers')
        subtitle.setObjectName('subTitle')
        brand.addWidget(heading); brand.addWidget(subtitle)
        header_layout.addLayout(brand)
        header_layout.addStretch(1)
        self.connection_badge = QLabel('0 connected')
        self.connection_badge.setObjectName('statusBadge')
        header_layout.addWidget(self.connection_badge)
        self.server_badge = QLabel('Server off')
        self.server_badge.setObjectName('statusBadge')
        header_layout.addWidget(self.server_badge)
        self.toggle_setup = QPushButton('Connection settings  ▴')
        self.toggle_setup.setToolTip('Collapse connection and saved-profile controls')
        header_layout.addWidget(self.toggle_setup)
        self.help_button = QPushButton('Help')
        self.help_button.setIcon(self.style().standardIcon(QStyle.SP_DialogHelpButton))
        self.help_button.setToolTip('Open the Z FTP guide (F1)')
        self.help_button.clicked.connect(self.show_help)
        header_layout.addWidget(self.help_button)
        layout.addWidget(header)
        self.connection_setup = QFrame()
        setup_layout = QVBoxLayout(self.connection_setup)
        setup_layout.setContentsMargins(14, 12, 14, 12)
        setup_layout.setSpacing(10)
        self.toggle_setup.clicked.connect(self.toggle_connection_setup)
        self.connection_setup.setObjectName('connectionPanel')
        conn = QGridLayout()
        conn.setHorizontalSpacing(7)
        conn.setVerticalSpacing(6)
        self.protocol_select = QComboBox(); self.protocol_select.addItems(['FTPS', 'SFTP', 'FTP'])
        self.protocol_select.setMinimumWidth(88)
        self.connection_name = QLineEdit()
        self.connection_name.setPlaceholderText('Connection name (for example PS4 or NAS)')
        self.connection_name.setMaxLength(100)
        self.connection_limit = QSpinBox()
        self.connection_limit.setRange(0, 1048576)
        self.connection_limit.setSpecialValueText('Use global limit')
        self.connection_limit.setSuffix(' KiB/s')
        self.connection_limit.setToolTip('Combined limit for this connection’s transfers.')
        conn.addWidget(self.connection_name, 2, 0, 1, 4)
        conn.addWidget(self.connection_limit, 2, 4, 1, 2)
        self.host = QLineEdit(); self.host.setPlaceholderText('Server hostname or IP address')
        self.port = QLineEdit('21'); self.port.setFixedWidth(66)
        self.user = QLineEdit('anonymous'); self.user.setPlaceholderText('Username')
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText('Password')
        self.connect_button = QPushButton('＋ Connect'); self.connect_button.setObjectName('primaryAction')
        self.connect_button.setMinimumWidth(115)
        conn.addWidget(self.protocol_select, 0, 0)
        conn.addWidget(self.host, 0, 1, 1, 3)
        conn.addWidget(self.port, 0, 4)
        conn.addWidget(self.connect_button, 0, 5)
        conn.addWidget(self.user, 1, 0, 1, 2)
        conn.addWidget(self.password, 1, 2, 1, 4)
        conn.setColumnStretch(1, 3)
        conn.setColumnStretch(2, 2)
        conn.setColumnStretch(3, 2)
        setup_layout.addLayout(conn)
        profiles = QHBoxLayout()
        profiles.setSpacing(6)
        self.profile_select = QComboBox(); self.profile_select.setMinimumWidth(150)
        self.manage_profiles_btn = QPushButton('Manage')
        self.save_profile_btn = QPushButton('Save profile')
        profiles.addWidget(QLabel('Saved connections'))
        profiles.addWidget(self.profile_select, 1)
        profiles.addWidget(self.manage_profiles_btn)
        profiles.addWidget(self.save_profile_btn)
        setup_layout.addLayout(profiles)
        self.ssh_row = QWidget()
        key_row = QHBoxLayout(self.ssh_row)
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(6)
        self.key_label = QLabel('SSH key')
        self.ssh_key = QLineEdit(); self.ssh_key.setPlaceholderText('Private key path')
        self.ssh_key_button = QPushButton('Browse')
        self.passphrase = QLineEdit(); self.passphrase.setEchoMode(QLineEdit.Password)
        self.passphrase.setPlaceholderText('Key passphrase (not saved)')
        key_row.addWidget(self.key_label)
        key_row.addWidget(self.ssh_key, 3)
        key_row.addWidget(self.ssh_key_button)
        key_row.addWidget(self.passphrase, 2)
        setup_layout.addWidget(self.ssh_row)
        self.connection_title = QLabel('CONNECTION  /  Quick connect')
        self.connection_title.setObjectName('cardTitle')
        setup_layout.insertWidget(0, self.connection_title)
        layout.addWidget(self.connection_setup)
        splitter = QSplitter(Qt.Horizontal)
        local = QFrame(); local.setObjectName('browserCard'); ll = QVBoxLayout(local)
        self.local_label = QLabel(); self.local_label.setObjectName('sectionHeading'); self.local_label.setWordWrap(True)
        local_actions = QHBoxLayout()
        local_actions.setSpacing(5)
        self.local_up = QPushButton('↑ Up')
        self.local_browse = QPushButton('Browse')
        self.upload_button = QPushButton('↑ Upload')
        self.upload_button.setObjectName('primaryAction')
        self.sync_button = QPushButton('Preview folder sync…')
        local_actions.addWidget(self.local_up)
        local_actions.addWidget(self.local_browse)
        local_actions.addStretch(1)
        local_actions.addWidget(self.upload_button)
        self.local_tools = QToolButton()
        self.local_tools.setText('More  ▾')
        self.local_tools.setPopupMode(QToolButton.InstantPopup)
        self.local_menu = QMenu(self)
        self.sync_action = self.local_menu.addAction('Preview folder synchronization…')
        self.sync_action.triggered.connect(self.sync_button.click)
        self.local_tools.setMenu(self.local_menu)
        local_actions.addWidget(self.local_tools)
        self.local_table = self.make_table("local")
        self.local_table.setAlternatingRowColors(True)
        self.local_table.verticalHeader().setDefaultSectionSize(29)
        self.local_table.setMinimumHeight(220)
        self.local_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        ll.setContentsMargins(6, 6, 6, 6)
        ll.setSpacing(5)
        ll.addWidget(self.local_label)
        ll.addLayout(local_actions)
        destination_hint = QLabel('Use Download to choose a destination, or drop here to download into this folder.')
        destination_hint.setObjectName('subTitle')
        destination_hint.setWordWrap(True)
        ll.addWidget(destination_hint)
        ll.addWidget(self.local_table)
        remote = QFrame(); remote.setObjectName('browserCard'); rl = QVBoxLayout(remote)
        rl.setContentsMargins(6, 6, 6, 6)
        rl.setSpacing(4)
        self.tabs = QTabWidget(); self.tabs.setTabsClosable(True)
        self.tabs.setMinimumHeight(280)
        self.tabs.setToolTip('Connected servers: switch tabs without disconnecting')
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.empty = QLabel('No server connected\n\nUse the connection bar above to open your FTP, FTPS or SFTP server.')
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName('emptyState')
        rl.addWidget(self.tabs, 1); rl.addWidget(self.empty)
        # Dedicated file-transfer actions match the visual concept; reuse original handlers.
        between = QWidget()
        between.setFixedWidth(92)
        between_layout = QVBoxLayout(between)
        between_layout.setContentsMargins(5, 8, 5, 8)
        between_layout.addStretch(1)
        self.center_upload = QPushButton('→ Upload')
        self.center_upload.setObjectName('primaryAction')
        self.center_upload.setToolTip('Upload selected local files to the active server')
        self.center_upload.clicked.connect(self.upload)
        self.center_download = QPushButton('← Get')
        self.center_download.setToolTip('Download selected remote files to the local folder')
        self.center_download.clicked.connect(self.download_active)
        between_layout.addWidget(self.center_upload)
        between_layout.addWidget(self.center_download)
        between_layout.addStretch(1)
        splitter.addWidget(local); splitter.addWidget(between); splitter.addWidget(remote)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([580, 92, 660])
        splitter.setObjectName('fileBrowserSplitter')
        self.file_browser_splitter = splitter
        layout.addWidget(splitter, 8)
        self.status = QLabel('Ready — each connection runs independently')
        layout.addWidget(self.status)
        self.transfer_card = QFrame()
        self.transfer_card.setObjectName('transferCard')
        transfer_layout = QVBoxLayout(self.transfer_card)
        transfer_layout.setContentsMargins(11, 8, 11, 8)
        transfer_layout.setSpacing(6)
        queue_header = QHBoxLayout()
        queue_label = QLabel('Transfers')
        queue_label.setObjectName('sectionHeading')
        queue_header.addWidget(queue_label)
        self.queue_pause_button = QPushButton('Resume queue' if self.queue.paused else 'Pause queue')
        self.queue_pause_button.setToolTip('Active transfers keep running while the queue is paused.')
        self.queue_pause_button.clicked.connect(lambda: self.set_queue_paused(not self.queue.paused))
        queue_header.addWidget(self.queue_pause_button)
        self.activity_toggle = QToolButton()
        self.activity_toggle.setText('Activity…')
        self.activity_toggle.setCheckable(True)
        self.activity_toggle.setToolTip('Open session activity, transfer history and workflows')
        queue_header.addWidget(self.activity_toggle)
        self.history_button = QToolButton()
        self.history_button.setText('History')
        self.history_button.setToolTip('Open saved transfer history')
        self.history_button.clicked.connect(self.show_history)
        queue_header.addWidget(self.history_button)
        self.cancel_job_button = QPushButton('Cancel selected job')
        self.clear_jobs_button = QPushButton('Clear finished')
        queue_header.addStretch(1)
        queue_header.addWidget(self.cancel_job_button)
        queue_header.addWidget(self.clear_jobs_button)
        transfer_layout.addLayout(queue_header)
        self.transfer_options_toggle = QToolButton()
        self.transfer_options_toggle.setText('Transfer settings  ▸')
        self.transfer_options_toggle.setCheckable(True)
        queue_header.insertWidget(1, self.transfer_options_toggle)
        self.transfer_options = QWidget()
        self.transfer_options.setObjectName('advancedPanel')
        advanced = QVBoxLayout(self.transfer_options)
        advanced.setContentsMargins(9, 6, 9, 6)
        # A predictable two-row grid prevents the settings from overflowing
        # the transfer panel at narrower Windows DPI/window widths.
        options = QGridLayout()
        options.setHorizontalSpacing(8)
        options.setVerticalSpacing(5)
        self.bandwidth_limit = QSpinBox()
        self.bandwidth_limit.setRange(0, 1048576)
        self.bandwidth_limit.setValue(0)
        self.bandwidth_limit.setMaximumWidth(112)
        self.chunk_select = QComboBox()
        for kib in (64, 128, 256, 512, 1024):
            self.chunk_select.addItem(f'{kib} KiB', kib)
        self.chunk_select.setToolTip('Transfer block size. Larger is not necessarily faster on Wi-Fi.')
        self.chunk_select.setMaximumWidth(116)
        self.retry_limit = QSpinBox()
        self.retry_limit.setRange(0, 10)
        self.retry_limit.setValue(3)
        self.retry_limit.setMaximumWidth(82)
        self.backoff_delay = QSpinBox()
        self.backoff_delay.setRange(0, 60)
        self.backoff_delay.setValue(1)
        self.backoff_delay.setMaximumWidth(82)
        self.parallel_download_limit = QSpinBox()
        self.parallel_download_limit.setRange(1, 32)
        self.parallel_download_limit.setValue(self.download_pool.maxThreadCount())
        self.parallel_download_limit.setToolTip('Maximum simultaneous background downloads. Extra files wait in the queue.')
        self.parallel_download_limit.valueChanged.connect(self.set_parallel_downloads)
        for col, (caption, control) in enumerate((
            ('Limit · KiB/s', self.bandwidth_limit),
            ('Chunk', self.chunk_select),
            ('Retries', self.retry_limit),
            ('Delay · s', self.backoff_delay),
            ('Parallel downloads', self.parallel_download_limit),
        )):
            options.addWidget(QLabel(caption), 0, col)
            options.addWidget(control, 1, col)
            options.setColumnStretch(col, 1)
        advanced.addLayout(options)
        self.transfer_options.setVisible(False)
        self.transfer_options_toggle.toggled.connect(self.transfer_options.setVisible)
        self.transfer_options_toggle.toggled.connect(lambda state: self.transfer_options_toggle.setText('⚙ Settings  ▾' if state else '⚙ Settings  ▸'))
        transfer_layout.addWidget(self.transfer_options)
        self.compact_queue = QCheckBox('Compact queue')
        self.compact_queue.setChecked(self.preferences.value('queue/compact', True, type=bool))
        self.compact_queue.toggled.connect(lambda checked: self.change_transfer_view('queue/compact', checked))
        self.decimal_units = QCheckBox('Decimal units (MB)')
        self.decimal_units.setChecked(self.preferences.value('display/decimal', False, type=bool))
        self.decimal_units.toggled.connect(lambda checked: self.change_transfer_view('display/decimal', checked))
        self.interactive_reserve = QSpinBox()
        self.interactive_reserve.setRange(0, 90)
        self.interactive_reserve.setValue(self.preferences.value('transfer/reserve', 15, type=int))
        self.interactive_reserve.setSuffix('%')
        self.interactive_reserve.setToolTip('Bandwidth left free for browsing when a transfer limit is set.')
        self.interactive_reserve.valueChanged.connect(lambda value: self.preferences.setValue('transfer/reserve', value))
        self.bandwidth_limit.setValue(self.preferences.value('transfer/globalLimit', 0, type=int))
        self.bandwidth_limit.valueChanged.connect(lambda value: self.preferences.setValue('transfer/globalLimit', value))
        self.bandwidth_limit.valueChanged.connect(self.update_transfer_limits)
        self.interactive_reserve.valueChanged.connect(self.update_transfer_limits)
        self.update_transfer_limits()
        options.addWidget(self.compact_queue, 2, 0, 1, 2)
        options.addWidget(self.decimal_units, 2, 2)
        options.addWidget(QLabel('Browsing reserve'), 2, 3)
        options.addWidget(self.interactive_reserve, 2, 4)
        self.stage_ribbon = QLabel('0 active · 0 queued · 0 failed')
        self.stage_ribbon.setObjectName('stageRibbon')
        self.stage_ribbon.setToolTip('Queue totals and combined speed across running jobs.')
        transfer_layout.addWidget(self.stage_ribbon)
        self.queue_table = QTableWidget(0, 8)
        self.queue_table.setHorizontalHeaderLabels(['Server', 'Direction', 'File', 'Progress', 'Speed', 'ETA', 'Attempts', 'Status'])
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.queue_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.queue_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.queue_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.queue_table.setMaximumHeight(150)
        self.queue_table.setMinimumHeight(100)
        self.queue_table.setAlternatingRowColors(True)
        self.queue_table.verticalHeader().setDefaultSectionSize(25)
        self.queue_table.setShowGrid(False)
        self.queue_table.verticalHeader().setDefaultSectionSize(42)
        self.queue_table.setMinimumHeight(100)
        self.queue_table.setColumnWidth(1, 105)
        self.queue_table.setColumnWidth(3, 145)
        self.queue_table.setColumnWidth(4, 175)
        self.queue_table.setColumnWidth(7, 106)
        self.queue_table.verticalHeader().hide()
        self.queue_table.setToolTip('Select a job for details, cancel or clear finished operations')
        transfer_layout.addWidget(self.queue_table)
        self.verify_checksums = QCheckBox('Full SHA-256 verification (slower)')
        self.verify_checksums.setToolTip(
            'Optional for new transfers and workflow steps. Reads each remote file in full again to compare checksums.')
        advanced.addWidget(self.verify_checksums)
        self.cancel_job_button.clicked.connect(self.cancel_selected_job)
        self.clear_jobs_button.clicked.connect(self.clear_finished_jobs)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(500)
        self.logs.setPlaceholderText('Connection events, transfers and alerts will appear here.')
        self.transfer_details = QDialog(self)
        self.transfer_details.setWindowTitle('Transfer activity')
        self.transfer_details.resize(840, 440)
        details_layout = QVBoxLayout(self.transfer_details)
        self.bottom_tabs = QTabWidget()
        details_layout.addWidget(self.bottom_tabs)
        activity_panel = QWidget()
        activity_layout = QVBoxLayout(activity_panel)
        self.setup_completion_menu(activity_layout)
        activity_layout.addWidget(self.logs, 1)
        self.bottom_tabs.addTab(activity_panel, 'Activity')
        self.history_panel = QWidget()
        history_layout = QVBoxLayout(self.history_panel)
        history_toolbar = QHBoxLayout()
        self.history_summary = QLabel()
        history_toolbar.addWidget(self.history_summary)
        self.history_refresh = QPushButton('Refresh')
        self.history_export = QPushButton('Export CSV')
        history_toolbar.addStretch(1)
        history_toolbar.addWidget(self.history_refresh)
        history_toolbar.addWidget(self.history_export)
        history_layout.addLayout(history_toolbar)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(['Time (UTC)', 'Server', 'File', 'Status', 'SHA-256'])
        history_header = self.history_table.horizontalHeader()
        history_header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.history_table.setColumnWidth(0, 180)
        self.history_table.setColumnWidth(4, 150)
        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.verticalHeader().hide()
        history_layout.addWidget(self.history_table)
        self.bottom_tabs.addTab(self.history_panel, 'History')
        self.history_refresh.clicked.connect(self.refresh_history)
        self.history_export.clicked.connect(self.export_history)
        self.workflows_panel = QWidget()
        wf_layout = QVBoxLayout(self.workflows_panel)
        wf_toolbar = QHBoxLayout()
        self.workflow_create = QPushButton('New workflow…')
        self.workflow_create.setObjectName('primaryAction')
        self.workflow_edit = QPushButton('Edit…')
        self.workflow_run = QPushButton('Run')
        self.workflow_pause = QPushButton('Pause after step')
        self.workflow_more = QToolButton()
        self.workflow_more.setText('More')
        self.workflow_more.setPopupMode(QToolButton.InstantPopup)
        workflow_menu = QMenu(self.workflow_more)
        self.workflow_duplicate = workflow_menu.addAction('Duplicate', self.duplicate_selected_workflow)
        self.workflow_remove = workflow_menu.addAction('Delete…', self.remove_selected_workflow)
        self.workflow_more.setMenu(workflow_menu)
        for button in (self.workflow_create, self.workflow_run, self.workflow_pause,
                       self.workflow_edit, self.workflow_more):
            wf_toolbar.addWidget(button)
        wf_toolbar.addStretch(1)
        wf_layout.addLayout(wf_toolbar)
        self.workflow_table = QTableWidget(0, 3)
        self.workflow_table.setHorizontalHeaderLabels(['Workflow', 'Progress', 'Status'])
        self.workflow_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.workflow_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.workflow_table.setSelectionMode(QTableWidget.SingleSelection)
        self.workflow_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.workflow_table.setAlternatingRowColors(True)
        self.workflow_table.verticalHeader().hide()
        wf_layout.addWidget(self.workflow_table)
        self.workflow_summary = QLabel()
        self.workflow_summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.workflow_summary.setTextFormat(Qt.PlainText)
        self.workflow_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        wf_layout.addWidget(self.workflow_summary)
        self.bottom_tabs.addTab(self.workflows_panel, 'Workflows')
        self.workflow_rows = []
        self.workflow_create.clicked.connect(self.create_workflow)
        self.workflow_edit.clicked.connect(self.edit_selected_workflow)
        self.workflow_run.clicked.connect(self.run_selected_workflow)
        self.workflow_pause.clicked.connect(self.pause_selected_workflow)
        self.workflow_table.itemSelectionChanged.connect(self.update_workflow_actions)
        self.workflow_table.cellDoubleClicked.connect(
            lambda *_: self.edit_selected_workflow() if self.workflow_edit.isEnabled() else None)
        self.bottom_tabs.currentChanged.connect(self.refresh_history_if_visible)

        self.activity_toggle.toggled.connect(self.set_transfer_details_visible)
        self.transfer_details.finished.connect(lambda _: self.activity_toggle.setChecked(False))
        layout.addWidget(self.transfer_card, 2)
        self.notifications_enabled = QCheckBox('Desktop notifications')
        # Persistent counters derive from session and queue state; no polling or
        # additional network calls are needed to keep them synchronized.
        self.footer_connection = QLabel('● Offline')
        self.footer_connection.setObjectName('footerConnection')
        self.footer_jobs = QLabel('0 active · 0 completed')
        self.footer_jobs.setObjectName('footerMetric')
        self.footer_speed = QLabel('↑ 0 KiB/s  ↓ 0 KiB/s')
        self.footer_speed.setObjectName('footerMetric')
        self.statusBar().addPermanentWidget(self.footer_connection)
        self.statusBar().addPermanentWidget(self.footer_jobs)
        self.statusBar().addPermanentWidget(self.footer_speed)
        self.statusBar().addPermanentWidget(self.notifications_enabled)
        self.statusBar().showMessage('Ready · Server disabled by default')
        self.setup_presentation_controls()
        self.setup_tray_controls()
        self.streamer.setChecked(self.preferences.value('privacy/streamer', False, type=bool))
        if self.queue_recovery.load_error:
            self.log(f'Could not load the recovery queue: {self.queue_recovery.load_error}')
        self.server_status_timer = QTimer(self)
        self.server_status_timer.timeout.connect(self.refresh_server_badge)
        self.server_status_timer.start(1000)
        self.refresh_history()
        self.refresh_workflows()
        self.connect_button.clicked.connect(self.connect_new)
        self.protocol_select.currentTextChanged.connect(self.protocol_changed)
        self.profile_select.currentIndexChanged.connect(self.load_selected_profile)
        self.manage_profiles_btn.clicked.connect(self.manage_profiles)
        self.save_profile_btn.clicked.connect(self.save_current_profile)
        self.ssh_key_button.clicked.connect(self.browse_ssh_key)
        self.local_up.clicked.connect(lambda: self.set_local_path(self.local_path.parent))
        self.local_browse.clicked.connect(self.pick_folder)
        self.local_table.cellDoubleClicked.connect(self.open_local)
        self.local_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.local_table.customContextMenuRequested.connect(self.local_context_menu)
        self.local_table.remoteDropped.connect(self.receive_remote_drop)
        self.upload_button.clicked.connect(self.upload)
        self.sync_button.clicked.connect(self.preview_sync)
        self.tabs.currentChanged.connect(lambda _: self.update_active())
        self.tabs.tabCloseRequested.connect(self.close_tab_at)
        self.refresh_profiles()
        self.protocol_changed(self.protocol_select.currentText())
        self._local_render_gate = RenderGate()
        self._queue_redraw_throttle = UpdateThrottle()
        self._key_shortcuts = []
        self.set_local_path(self.local_path)
        for keys, handler in (('F1', self.show_help),
                              ('F5', lambda: self.set_local_path(self.local_path)),
                              ('Ctrl+L', self.focus_local_path),
                              ('Ctrl+R', self.refresh_current_remote),
                              ('Ctrl+Shift+H', lambda: self.streamer.toggle())):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(handler)
            self._key_shortcuts.append(shortcut)
        self.update_active()
        self.setStyleSheet(APP_STYLESHEET)
        self.sidebar_buttons['connection'].setProperty('selected', True)
        self.refresh_server_badge()
        self.update_active()
        self.render_queue()

    def show_help(self):
        dialog = HelpDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def download_active(self):
        tab = self.current_tab()
        if tab is not None:
            self.download(tab)

    def navigate_sidebar(self, section: str):
        """Navigate without duplicating any transfer or hosting controller."""
        if section == 'about':
            QMessageBox.information(self, 'About Z FTP',
                'Z FTP v2.1.12\nFTP, FTPS and SFTP clients and servers. Optional NFSv3 hosting.\n'
                'Includes transfer settings and remote file tools.')
            return
        for key, button in self.sidebar_buttons.items():
            selected = key == section
            button.setProperty('selected', selected)
            button.style().unpolish(button)
            button.style().polish(button)
        server_pages = {
            'ftp_server': self.server_panel,
            'sftp_server': self.sftp_server_panel,
            'nfs_server': self.nfs_server_panel,
            'phone_drop': self.phone_drop_panel,
        }
        if section in server_pages:
            if self.streamer_enabled:
                return
            self.main_sections.setCurrentWidget(server_pages[section])
            return
        self.main_sections.setCurrentIndex(0)
        if section == 'connection':
            self.connection_setup.show()
            self.toggle_setup.setText('Connection settings  ▴')
            self.host.setFocus()
        elif section == 'browser':
            self.local_table.setFocus()
        elif section == 'transfers':
            self.queue_table.setFocus()
        elif section == 'sync':
            self.preview_sync()
        elif section == 'settings':
            self.transfer_options_toggle.setChecked(True)
            self.bandwidth_limit.setFocus()

    def toggle_connection_setup(self):
        visible = not self.connection_setup.isVisible()
        self.connection_setup.setVisible(visible)
        self.toggle_setup.setText('Connection settings  ▴' if visible else 'Connection settings  ▾')

    def refresh_server_badge(self):
        servers = (
            ('FTP/FTPS', self.server_panel, self.server_panel.controller.running),
            ('SFTP', self.sftp_server_panel, self.sftp_server_panel.controller.running),
            ('NFS', self.nfs_server_panel, self.nfs_server_panel.running),
            ('Phone Drop', self.phone_drop_panel, self.phone_drop_panel.active_or_stopping),
        )
        running = []
        for protocol, panel, active in servers:
            state = 'ON' if active else 'OFF'
            self.main_sections.setTabText(self.main_sections.indexOf(panel), f'{protocol} · {state}')
            if active:
                running.append(protocol)
        self.server_badge.setText(f"{', '.join(running)} running" if running else 'Server off')
        self.server_badge.setProperty('status', 'on' if running else 'off')
        self.server_badge.style().unpolish(self.server_badge)
        self.server_badge.style().polish(self.server_badge)


    def notify(self, level, title, message, *, category=None, download_dir=None, download_name=None):
        title = self.private_text(title)[:140]
        message = self.private_text(message)[:1000]
        self.log(f'{title} · {message}')
        self.statusBar().showMessage(f'{title} · {message[:150]}', 8000)
        if download_dir is not None:
            self._recent_downloads.appendleft((download_name or title, Path(download_dir)))
            message = message + '\nOpen the destination from Activity.'
        if category is None:
            category = 'failure' if level in ('error', 'warning') else 'other'
        self.show_notification(level, title, message, category, download_dir)

    @staticmethod
    def make_table(side="local"):
        table = TransferTable(side)
        table.setHorizontalHeaderLabels(['Name', 'Type', 'Size'])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.ExtendedSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        return table

    @staticmethod
    def add_row(table, name, typ, size):
        row = table.rowCount(); table.insertRow(row)
        for col, value in enumerate((name, typ, size)):
            table.setItem(row, col, QTableWidgetItem(str(value)))

    def log(self, message):
        timestamp = datetime.now().strftime('%H:%M:%S')
        line = ' '.join(str(message).splitlines())
        entry = f'{timestamp}  {line}'
        self._activity_lines.append(entry)
        self.logs.appendPlainText(self.private_text(entry))

    def current_tab(self):
        widget = self.tabs.currentWidget()
        return widget if widget in self.sessions else None

    def update_active(self):
        tab = self.current_tab()
        self.upload_button.setEnabled(bool(tab and tab.state.connected and not tab.state.closing))
        self.empty.setVisible(not self.sessions)
        self.sync_button.setEnabled(bool(tab and tab.state.connected and not tab.state.closing))
        self.sync_action.setEnabled(self.sync_button.isEnabled())
        active = sum(int(state.busy) + len(state.downloads) for state in self.sessions.values())
        count = sum(1 for t in self.sessions if t.state.connected)
        summary = status_label(count, active)
        self.connection_badge.setText(summary)
        self.connection_badge.setProperty('status', 'on' if count else 'off')
        self.connection_badge.style().unpolish(self.connection_badge)
        self.connection_badge.style().polish(self.connection_badge)
        self.status.setText(summary + f' · {len(self.sessions)} tabs open')
        self.refresh_live_footer()

    def refresh_live_footer(self):
        """Refresh UI-only counters; never contact a remote server here."""
        connected = [tab for tab in self.sessions if tab.state.connected]
        jobs = self.queue.jobs
        running = [job for job in jobs if job.status == 'Running']
        done = sum(job.status == 'Completed' for job in jobs)
        upload = sum(max(0., job.speed_bps) for job in running if job.direction == 'upload')
        download = sum(max(0., job.speed_bps) for job in running if job.direction == 'download')
        self.footer_connection.setText(f'● {len(connected)} connected' if connected else '● Offline')
        self.footer_connection.setProperty('online', bool(connected))
        self.footer_connection.style().unpolish(self.footer_connection)
        self.footer_connection.style().polish(self.footer_connection)
        remaining = sum(job.status == 'Queued' for job in jobs) + len(self.queue_recovery.waiting)
        pause = 'Paused · ' if self.queue.paused else ''
        self.footer_jobs.setText(f'{pause}{len(running)} active · {remaining} waiting · {done} completed')
        self.footer_speed.setText(f'↑ {self.display_rate(upload)}  ↓ {self.display_rate(download)}')

    def update_transfer_limits(self, *_):
        reserve = self.interactive_reserve.value()
        self.global_limiter.configure(self.bandwidth_limit.value(), reserve)
        for state in self.sessions.values():
            state.limiter.configure(state.bandwidth_kib, reserve)

    def change_transfer_view(self, key, enabled):
        self.preferences.setValue(key, enabled)
        self.render_queue()
        if key == 'display/decimal':
            self.set_local_path(self.local_path)
            for tab in self.sessions:
                tab.show_entries()
            self.refresh_tray_status()
            self.phone_drop_panel.refresh(force=True)

    def display_size(self, value):
        return format_size(value, self.decimal_units.isChecked())

    def display_rate(self, value):
        return format_rate(value, self.decimal_units.isChecked())

    def protocol_changed(self, protocol):
        self.port.setText('22' if protocol == 'SFTP' else '21')
        self.ssh_row.setVisible(protocol == 'SFTP')


    # v0.3 saved connection management is intentionally independent of active tabs.


