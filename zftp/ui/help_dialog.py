"""In-app guide to the client, transfer workflows, and PS3 tools."""
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLineEdit, QListWidget,
    QTextBrowser, QVBoxLayout,
)

TOPICS = (
    ('Downloads & folders', (
        ('Choose where files go', 'Select files or folders in the right Remote pane. Click Download, or right-click and choose Download folder. Pick the destination in the dialog. Z FTP creates the selected folder inside that destination.'),
        ('Dragging to the Local pane', 'Drops download into the folder shown on the left. Use Browse above the Local pane to change that folder before dragging.'),
        ('Folder progress', 'Review the folder preview, then queue it. The folder appears as one transfer and sends one completion notification after its queued items finish. Existing files can be skipped or kept as separate copies. Compare the preview before relying on the backup.'),
    )),
    ('Connections & profiles', (
        ('Connect', 'Choose FTP, FTPS, or SFTP to match your server. For a typical PS3 homebrew FTP server, choose FTP and enter the console’s LAN IP and port 21. Use the credentials configured on that server.'),
        ('Saved profiles', 'Save a connection’s settings if you use it regularly. Each connection gets its own Remote tab. Open Connection settings to get back to the connection form.'),
        ('SFTP keys', 'The SSH key field can point to your private key file. Verify a server’s host fingerprint before trusting it.'),
    )),
    ('File browser & uploads', (
        ('Local and remote', 'The left pane shows your PC. The right tabs show connected servers. Double-click a folder to open it. Use Up, Back, Forward, or Refresh to navigate.'),
        ('Upload', 'Select local files and click Upload, or drag them onto a Remote tab. Check the remote path first because that is the upload destination.'),
        ('File actions', 'Right-click a file or open File actions to rename it, edit supported text, change permissions, or delete it. Remote deletion is permanent.'),
    )),
    ('Queue & verification', (
        ('Transfers', 'The queue shows running and pending jobs, progress, and errors. Select a row to cancel that job, or use Clear finished to remove completed rows from the display.'),
        ('Settings', 'Adjust the bandwidth limit, retry count, backoff, and chunk size in transfer settings. SHA-256 verification can add transfer time because it reads the remote data again.'),
        ('Activity and history', 'Activity logs describe jobs and failures. Notifications include completion summaries. History can be exported as CSV. Desktop notifications can be toggled in the status bar.'),
    )),
    ('PS3 file tools', (
        ('Identify a game', 'Right-click PARAM.SFO and choose View / edit PARAM.SFO to see TITLE, TITLE_ID, CATEGORY, and other fields. Right-click a games folder and choose PS3 file tools → Browse game metadata to see several titles in one list.'),
        ('Inspect', 'Inspect PKG header shows the Content ID and declared size. Preview image opens PNG and JPEG files, including ICON0.PNG.'),
        ('Edit', 'Edit config changes existing key/value entries. Edit boot plugins checks listed paths. The hex editor supports same-length changes, and the image preview can replace ICON0.PNG. Remote edits keep a backup in the same folder.'),
    )),
    ('Sync, schedules & workflows', (
        ('Sync preview', 'Compare the local and remote folders and review proposed copies before queueing them. Existing contents are kept by default. A same-size match is not a checksum match.'),
        ('Schedules and workflows', 'Use Schedules for recurring comparisons and Workflows for repeatable sequences of supported operations. Check their status in the lower panel.'),
        ('Cross-server', 'With two connections open, select a remote file and choose Copy to server to send it to the other connection.'),
    )),
    ('Hosting a server', (
        ('FTP/FTPS Server and SFTP Server', 'These pages make your PC the server. Set the shared folder, address, credentials, and access options before starting. Use Connection when you want to connect to your PS3 instead.'),
        ('Stop sharing', 'Stop the server from its page or the tray when you are done. Servers are disabled by default.'),
    )),
    ('Tray & Windows updates', (
        ('Background operation', 'Z FTP can keep transfers running in the tray. Use the tray menu to reopen the window or quit the app.'),
        ('Check for updates', 'Finish transfers and quit Z FTP, including its tray icon. Run Update-ZFTP.bat beside ZFTP.exe, or use Z FTP - Check for updates in the Start menu. The default install folder is %LOCALAPPDATA%\\Programs\\ZFTP.'),
    )),
)


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Z FTP Help')
        self.resize(800, 510)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search help, e.g. download destination or PARAM.SFO')
        self.search.setAccessibleName('Search help')
        layout.addWidget(self.search)
        content = QHBoxLayout()
        layout.addLayout(content, 1)
        self.topics = QListWidget()
        self.topics.setMinimumWidth(210)
        self.topics.setAccessibleName('Help topics')
        content.addWidget(self.topics)
        self.article = QTextBrowser()
        self.article.setAccessibleName('Help article')
        content.addWidget(self.article, 1)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.search.textChanged.connect(self.filter_topics)
        self.topics.currentItemChanged.connect(self.show_topic)
        self.filter_topics('')
        self.search.setFocus()

    def filter_topics(self, query):
        terms = query.casefold().split()
        self.topics.clear()
        for index, (title, sections) in enumerate(TOPICS):
            searchable = ' '.join([title, *(part for section in sections for part in section)]).casefold()
            if all(term in searchable for term in terms):
                self.topics.addItem(title)
                self.topics.item(self.topics.count() - 1).setData(Qt.UserRole, index)
        if self.topics.count():
            self.topics.setCurrentRow(0)
        else:
            self.article.setPlainText('No topics matched. Try “download”, “PS3”, or “updates”.')

    def show_topic(self, current, _previous):
        if current is None:
            return
        title, sections = TOPICS[current.data(Qt.UserRole)]
        self.article.setHtml(f'<h2>{escape(title)}</h2>' + ''.join(
            f'<h3>{escape(heading)}</h3><p>{escape(body)}</p>' for heading, body in sections
        ))
