"""In-app audio and video playback for local or bounded remote files."""

from pathlib import Path

from PySide6.QtCore import QDir, QTemporaryFile, Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout


REMOTE_PLAYBACK_LIMIT = 64 * 1024 * 1024
SEEK_STEPS = 1000


def check_media_header(name, data):
    suffix = Path(name).suffix.lower()
    if suffix == '.wav':
        recognized = data[:4] in (b'RIFF', b'RF64', b'RIFX', b'BW64') and data[8:12] == b'WAVE'
    elif suffix == '.ogg':
        recognized = data.startswith(b'OggS')
    else:
        recognized = data[4:8] in (b'ftyp', b'moov', b'mdat', b'wide', b'free', b'skip')
    if not recognized:
        raise ValueError(f'Could not recognize a {suffix[1:].upper()} media header.')


def clock_text(milliseconds):
    hours, seconds = divmod(milliseconds // 1000, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours}:{minutes:02}:{seconds:02}' if hours else f'{minutes}:{seconds:02}'


class MediaPreview(QDialog):
    def __init__(self, parent, name, path, data):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(name)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.temporary = None

        layout = QVBoxLayout(self)
        if Path(name).suffix.lower() == '.mp4':
            self.resize(780, 520)
            video = QVideoWidget()
            layout.addWidget(video, 1)
            self.player.setVideoOutput(video)
        else:
            self.resize(540, 130)

        self.status = QLabel('Loading media…')
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QSlider(Qt.Horizontal)
        self.progress.setRange(0, SEEK_STEPS)
        self.progress.setEnabled(False)
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.play = QPushButton('Play')
        self.play.setEnabled(False)
        self.time = QLabel('0:00 / 0:00')
        close = QPushButton('Close')
        buttons.addWidget(self.play)
        buttons.addWidget(self.time)
        buttons.addStretch()
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self.play.clicked.connect(self.toggle_playback)
        close.clicked.connect(self.close)
        self.progress.sliderMoved.connect(self.seek)
        self.progress.actionTriggered.connect(self.seek_action)
        self.player.positionChanged.connect(self.update_progress)
        self.player.durationChanged.connect(lambda _: self.update_progress(self.player.position()))
        self.player.seekableChanged.connect(self.progress.setEnabled)
        self.player.playbackStateChanged.connect(self.update_playback)
        self.player.mediaStatusChanged.connect(self.update_status)
        self.player.errorOccurred.connect(self.show_error)
        self.finished.connect(self.release_media)

        if data is not None:
            suffix = Path(name).suffix.lower()
            self.temporary = QTemporaryFile(QDir.temp().filePath(f'zftp-preview-XXXXXX{suffix}'), self)
            if not self.temporary.open() or self.temporary.write(data) != len(data) or not self.temporary.flush():
                self.temporary.remove()
                self.deleteLater()
                raise ValueError('Could not write the temporary media preview.')
            path = self.temporary.fileName()
            self.temporary.close()
        self.player.setSource(QUrl.fromLocalFile(str(path)))

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek(self, step):
        self.player.setPosition(round(self.player.duration() * step / SEEK_STEPS))

    def seek_action(self, action):
        # Mouse dragging also emits sliderMoved. Keyboard and track clicks use this signal.
        if action != QSlider.SliderMove:
            self.seek(self.progress.sliderPosition())

    def update_progress(self, position):
        duration = self.player.duration()
        if duration and not self.progress.isSliderDown():
            self.progress.setValue(round(position * SEEK_STEPS / duration))
        self.time.setText(f'{clock_text(position)} / {clock_text(duration)}')

    def update_playback(self, state):
        self.play.setText('Pause' if state == QMediaPlayer.PlayingState else 'Play')

    def update_status(self, status):
        if status == QMediaPlayer.LoadedMedia:
            self.status.clear()
            self.play.setEnabled(True)

    def show_error(self, _error, message):
        self.status.setText(message or 'This media could not be decoded.')
        self.play.setEnabled(False)

    def release_media(self, _result):
        self.player.stop()
        self.player.setSource(QUrl())
        if self.temporary is not None:
            self.temporary.remove()


def show_media(parent, name, *, path=None, data=None):
    if data is not None:
        check_media_header(name, data[:16])
    dialog = MediaPreview(parent, name, path, data)
    dialog.show()
    return dialog
