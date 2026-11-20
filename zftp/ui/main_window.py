"""Z FTP v2.1.11 — main window composition; UI behaviors live in dedicated modules."""
from __future__ import annotations

import sys
import os
import time
import posixpath
from pathlib import Path
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt, QTimer, QFile, QSize
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
from zftp.transfers.reliable_transfer import TransferSettings, download as resilient_download, upload as resilient_upload, with_retries
from zftp.transfers.ps3_performance import benchmark_ftp_upload, PRESETS
from zftp.transfers.eta_estimator import ETAEstimator, format_eta
from zftp.services.remote_management import (validate_name, read_editor_snapshot, safe_save_editor, find_remote, RemoteConflictError)
from zftp.ui.server_ui import ServerPanel
from zftp.ui.sftp_server_ui import SFTPServerPanel
from zftp.ui.notifications import NotificationHistory
from zftp.transfers.integrity import verify_transfer
from zftp.services.transfer_history import TransferHistory
from zftp.services.scheduler import ScheduleStore
from zftp.services.workflows import WorkflowStore
from zftp.ui.workflow_dialog import WorkflowDialog
from zftp.transfers.cross_server import copy_file
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


class MainWindow(TrayControlsMixin, FileBrowserMixin, TransfersViewMixin,
                 WorkflowViewsMixin, ShutdownControlsMixin, ProfileControlsMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Z FTP  •  v2.1.11')
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[2] / 'assets' / 'zftp_icon.png')))
        self.resize(1500, 920)
        self.setMinimumSize(1040, 680)
        self.notices = NotificationHistory()
        self.history = TransferHistory()
        self.schedules = ScheduleStore()
        self.workflows = WorkflowStore()
        self.cross_operations = []
        self.workflow_running = set()
        self.tray = None
        self._tray_force_quit = False
        self._close_to_tray = False
        self._tray_status_action = None
        self._tray_visibility_action = None
        self.pool = QThreadPool(self); self.pool.setMaxThreadCount(8)
        self.jobs: list[Job] = []
        # Graceful close is a two-phase process: request cancellation, then let
        # workers finish before disconnecting and destroying their Qt owners.
        self._quit_requested = False
        self._quit_timer = QTimer(self)
        self._quit_timer.setInterval(250)
        self._quit_timer.timeout.connect(self._advance_shutdown)
        self.store = ProfileStore()
        self.local_path = Path.home()
        self.sessions: dict[RemoteTab, ConnectionSession] = {}
        self.queue = TransferQueue()
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
        # Keep the sidebar focused on the pages that benefit most from persistent
        # navigation. Secondary tools such as schedules, workflows and sync stay
        # available in the bottom panel instead of permanently consuming sidebar space.
        for key, label in (
            ('connection', 'Connection'),
            ('browser', 'File Browser'),
            ('transfers', 'Transfers'),
            ('ftp_server', 'FTP/FTPS Server'),
            ('sftp_server', 'SFTP Server'),
            ('settings', 'Settings'),
            ('about', 'About'),
        ):
            button = QPushButton(label)
            # Native Qt icons follow the platform DPI, rather than fragile text glyphs.
            nav_icons = {
                'connection': QStyle.SP_DriveNetIcon,
                'browser': QStyle.SP_DirIcon,
                'transfers': QStyle.SP_ArrowUp,
                'sync': QStyle.SP_BrowserReload,
                'schedules': QStyle.SP_FileDialogDetailedView,
                'workflows': QStyle.SP_FileDialogListView,
                'ftp_server': QStyle.SP_DriveNetIcon,
                'sftp_server': QStyle.SP_DriveNetIcon,
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
                button.setToolTip('Transfer settings, activity, schedules, workflows and sync preview live in the lower panel.')
        side.addStretch(1)
        side_info = QLabel('SECURE FILE TRANSFER\nLower panel: Sync · Schedules · Workflows')
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
        self.activity_toggle = QToolButton()
        self.activity_toggle.setText('Activity ▸')
        self.activity_toggle.setCheckable(True)
        self.activity_toggle.setToolTip('Expand activity, history, schedules and workflows')
        queue_header.addWidget(self.activity_toggle)
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
        for col, (caption, control) in enumerate((
            ('Limit · KiB/s', self.bandwidth_limit),
            ('Chunk', self.chunk_select),
            ('Retries', self.retry_limit),
            ('Delay · s', self.backoff_delay),
        )):
            options.addWidget(QLabel(caption), 0, col)
            options.addWidget(control, 1, col)
            options.setColumnStretch(col, 1)
        advanced.addLayout(options)
        ps3_controls = QHBoxLayout()
        ps3_controls.setSpacing(6)
        self.ps3_preset = QPushButton('PS3 Wi-Fi preset')
        self.ps3_preset.setToolTip('FTP uploads: 256 KiB chunks, unlimited bandwidth. SHA-256 checks remain enabled.')
        self.ps3_preset.clicked.connect(self.apply_ps3_preset)
        ps3_controls.addWidget(self.ps3_preset)
        self.ps3_benchmark = QPushButton('Benchmark PS3…')
        self.ps3_benchmark.setToolTip('Upload and delete an 8 MiB random test file on the connected FTP server, after approval.')
        self.ps3_benchmark.clicked.connect(self.benchmark_ps3)
        ps3_controls.addWidget(self.ps3_benchmark)
        ps3_controls.addStretch(1)
        advanced.addLayout(ps3_controls)
        self.transfer_options.setVisible(False)
        self.transfer_options_toggle.toggled.connect(self.transfer_options.setVisible)
        self.transfer_options_toggle.toggled.connect(lambda state: self.transfer_options_toggle.setText('⚙ Settings  ▾' if state else '⚙ Settings  ▸'))
        transfer_layout.addWidget(self.transfer_options)
        self.stage_ribbon = QLabel('○ Uploading   ›   ○ Verifying   ›   ○ Renaming   ›   ○ Completed')
        self.stage_ribbon.setObjectName('stageRibbon')
        self.stage_ribbon.setToolTip('Shows the stage of the first running transfer; verification may take additional time on PS3 Wi-Fi.')
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
        self.queue_table.setToolTip('Select a job for details, cancel or clear finished operations')
        transfer_layout.addWidget(self.queue_table)
        self.verify_checksums = QCheckBox('Verify transfers with SHA-256 (downloads remote file again)')
        self.verify_checksums.setChecked(True)
        self.verify_checksums.setToolTip('Compares SHA-256 of both completed files; may double network traffic. Fails closed if remote file cannot be read.')
        advanced.addWidget(self.verify_checksums)
        self.cancel_job_button.clicked.connect(self.cancel_selected_job)
        self.clear_jobs_button.clicked.connect(self.clear_finished_jobs)
        self.logs = QTextEdit(); self.logs.setReadOnly(True); self.logs.setMaximumHeight(115)
        self.logs.setPlaceholderText('Session activity will appear here…')
        self.bottom_tabs = QTabWidget(); self.bottom_tabs.setMaximumHeight(260)
        self.bottom_tabs.addTab(self.logs, 'Activity')
        self.notifications_view = QListWidget()
        self.notifications_view.setToolTip('Recent connection and transfer notifications for this session')
        self.bottom_tabs.addTab(self.notifications_view, 'Notifications')
        self.history_panel = QWidget()
        history_layout = QVBoxLayout(self.history_panel)
        history_toolbar = QHBoxLayout()
        history_toolbar.addWidget(QLabel('Recent transfer outcomes (stored locally)'))
        self.history_refresh = QPushButton('Refresh')
        self.history_export = QPushButton('Export CSV')
        history_toolbar.addStretch(1)
        history_toolbar.addWidget(self.history_refresh)
        history_toolbar.addWidget(self.history_export)
        history_layout.addLayout(history_toolbar)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(['Time (UTC)', 'Server', 'File', 'Status', 'SHA-256'])
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        history_layout.addWidget(self.history_table)
        self.bottom_tabs.addTab(self.history_panel, 'History')
        self.history_refresh.clicked.connect(self.refresh_history)
        self.history_export.clicked.connect(self.export_history)
        self.schedules_panel = QWidget()
        sched_layout = QVBoxLayout(self.schedules_panel)
        sched_toolbar = QHBoxLayout()
        self.schedule_add = QPushButton('Add schedule')
        self.schedule_toggle = QPushButton('Pause / enable')
        self.schedule_remove = QPushButton('Remove')
        self.schedule_refresh = QPushButton('Refresh')
        for button in (self.schedule_add, self.schedule_toggle, self.schedule_remove, self.schedule_refresh):
            sched_toolbar.addWidget(button)
        sched_toolbar.addStretch(1)
        sched_layout.addLayout(sched_toolbar)
        self.schedules_table = QTableWidget(0, 5)
        self.schedules_table.setHorizontalHeaderLabels(['Task', 'Action', 'Next (local)', 'Repeat', 'State'])
        self.schedules_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        sched_layout.addWidget(self.schedules_table)
        self.bottom_tabs.addTab(self.schedules_panel, 'Schedules')
        self.workflows_panel = QWidget()
        wf_layout = QVBoxLayout(self.workflows_panel)
        wf_toolbar = QGridLayout()
        wf_toolbar.setHorizontalSpacing(6)
        wf_toolbar.setVerticalSpacing(4)
        self.workflow_create = QPushButton('Create workflow…')
        self.workflow_edit = QPushButton('Edit steps…')
        self.workflow_duplicate = QPushButton('Duplicate')
        self.workflow_run = QPushButton('Run / continue')
        self.workflow_pause = QPushButton('Pause')
        self.workflow_remove = QPushButton('Delete')
        self.workflow_refresh = QPushButton('Refresh')
        for i, button in enumerate((self.workflow_create, self.workflow_edit, self.workflow_duplicate,
                                    self.workflow_run, self.workflow_pause, self.workflow_remove,
                                    self.workflow_refresh)):
            wf_toolbar.addWidget(button, i // 4, i % 4)
        wf_layout.addLayout(wf_toolbar)
        self.workflow_table = QTableWidget(0, 4)
        self.workflow_table.setHorizontalHeaderLabels(['Workflow', 'Steps done', 'State', 'Last error'])
        self.workflow_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        wf_layout.addWidget(self.workflow_table)
        self.bottom_tabs.addTab(self.workflows_panel, 'Workflows')
        self.cross_panel = QWidget()
        cross_layout = QVBoxLayout(self.cross_panel)
        cross_layout.addWidget(QLabel('Remote-to-remote copies · both servers must remain connected'))
        self.cross_table = QTableWidget(0, 5)
        self.cross_table.setHorizontalHeaderLabels(['Source', 'Destination', 'Status', 'Bytes', 'Error'])
        self.cross_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        cross_layout.addWidget(self.cross_table)
        self.bottom_tabs.addTab(self.cross_panel, 'Cross-server')
        self.workflow_create.clicked.connect(self.create_workflow)
        self.workflow_edit.clicked.connect(self.edit_selected_workflow)
        self.workflow_duplicate.clicked.connect(self.duplicate_selected_workflow)
        self.workflow_run.clicked.connect(self.run_selected_workflow)
        self.workflow_pause.clicked.connect(self.pause_selected_workflow)
        self.workflow_remove.clicked.connect(self.remove_selected_workflow)
        self.workflow_refresh.clicked.connect(self.refresh_workflows)

        self.schedule_add.clicked.connect(self.add_schedule)
        self.schedule_toggle.clicked.connect(self.toggle_schedule)
        self.schedule_remove.clicked.connect(self.remove_schedule)
        self.schedule_refresh.clicked.connect(self.refresh_schedules)

        # Activity/history/workflow controls remain available, but do not
        # steal vertical space from file browsing until explicitly expanded.
        self.bottom_tabs.setVisible(False)
        self.activity_toggle.toggled.connect(self.bottom_tabs.setVisible)
        self.activity_toggle.toggled.connect(
            lambda shown: self.activity_toggle.setText('Activity ▾' if shown else 'Activity ▸'))
        transfer_layout.addWidget(self.bottom_tabs)
        layout.addWidget(self.transfer_card, 2)
        self.notifications_enabled = QCheckBox('Desktop notifications')
        self.notifications_enabled.setChecked(True)
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
        self.setup_tray_controls()
        self.server_status_timer = QTimer(self)
        self.server_status_timer.timeout.connect(self.refresh_server_badge)
        self.server_status_timer.start(1000)
        self.schedule_timer = QTimer(self)
        self.schedule_timer.timeout.connect(self.tick_schedules)
        self.schedule_timer.start(15000)
        self.refresh_history()
        self.refresh_schedules()
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
                              ('Ctrl+R', self.refresh_current_remote)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(handler)
            self._key_shortcuts.append(shortcut)
        self.update_active()
        self.setStyleSheet(APP_STYLESHEET)
        self.sidebar_buttons['connection'].setProperty('selected', True)
        self.refresh_server_badge()
        self.update_active()

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
                'Z FTP v2.1.11\nFTP, FTPS and SFTP clients and servers.\n'
                'Includes optional PS3/mmCM compatibility and performance settings.')
            return
        for key, button in self.sidebar_buttons.items():
            selected = key == section
            button.setProperty('selected', selected)
            button.style().unpolish(button)
            button.style().polish(button)
        if section in ('ftp_server', 'sftp_server'):
            self.main_sections.setCurrentIndex(1 if section == 'ftp_server' else 2)
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
        elif section in ('schedules', 'workflows'):
            self.activity_toggle.setChecked(True)
            self.bottom_tabs.setCurrentWidget(self.schedules_panel if section == 'schedules' else self.workflows_panel)
        elif section == 'settings':
            self.transfer_options_toggle.setChecked(True)
            self.bandwidth_limit.setFocus()

    def toggle_connection_setup(self):
        visible = not self.connection_setup.isVisible()
        self.connection_setup.setVisible(visible)
        self.toggle_setup.setText('Connection settings  ▴' if visible else 'Connection settings  ▾')

    def refresh_server_badge(self):
        is_running = self.server_panel.controller.running
        self.server_badge.setText('Server running' if is_running else 'Server off')
        self.server_badge.setProperty('status', 'on' if is_running else 'off')
        self.server_badge.style().unpolish(self.server_badge)
        self.server_badge.style().polish(self.server_badge)
        self.main_sections.setTabText(1, 'FTP/FTPS · ON' if is_running else 'FTP/FTPS · OFF')
        self.main_sections.setTabText(2, 'SFTP · ON' if self.sftp_server_panel.controller.running else 'SFTP · OFF')
        if self.sftp_server_panel.controller.running and not is_running:
            self.server_badge.setText('SFTP server running')
            self.server_badge.setProperty('status', 'on')
            self.server_badge.style().unpolish(self.server_badge)
            self.server_badge.style().polish(self.server_badge)


    def notify(self, level, title, message):
        notice = self.notices.add(level, title, message)
        self.notifications_view.insertItem(0, f'{title}: {notice.message}')
        while self.notifications_view.count() > self.notices.capacity:
            self.notifications_view.takeItem(self.notifications_view.count() - 1)
        self.statusBar().showMessage(f'{title} · {notice.message[:150]}', 8000)
        if self.tray is not None and self.notifications_enabled.isChecked():
            icon = {'error': QSystemTrayIcon.Critical, 'warning': QSystemTrayIcon.Warning}.get(level, QSystemTrayIcon.Information)
            if QSystemTrayIcon.supportsMessages():
                self.tray.showMessage(title, notice.message, icon, 4000)


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
        self.logs.append(message)

    def current_tab(self):
        widget = self.tabs.currentWidget()
        return widget if widget in self.sessions else None

    def update_active(self):
        tab = self.current_tab()
        self.upload_button.setEnabled(bool(tab and tab.state.connected and not tab.state.closing))
        self.empty.setVisible(not self.sessions)
        self.sync_button.setEnabled(bool(tab and tab.state.connected and not tab.state.closing))
        self.sync_action.setEnabled(self.sync_button.isEnabled())
        active = sum(1 for t in self.sessions if t.state.busy)
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
        self.footer_jobs.setText(f'{len(running)} active · {done} completed')
        self.footer_speed.setText(f'↑ {upload / 1024:.1f} KiB/s  ↓ {download / 1024:.1f} KiB/s')

    def protocol_changed(self, protocol):
        self.port.setText('22' if protocol == 'SFTP' else '21')
        self.ssh_row.setVisible(protocol == 'SFTP')


    # v0.3 saved connection management is intentionally independent of active tabs.


