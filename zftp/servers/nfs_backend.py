"""Pin the WinNFSd executable Z FTP obtains from the upstream release."""

from hashlib import sha256
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile, QStandardPaths


VERSION = '2.4.0'
BACKEND_URL = f'https://github.com/winnfsd/winnfsd/releases/download/{VERSION}/WinNFSd.exe'
BACKEND_SIZE = 274944
BACKEND_SHA256 = '35cb5a74b2bf2aaa94302f5c9ed70786b61f245b96dc1f20214fe337c24af6b2'


def backend_path():
    local_data = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
    if not local_data:
        raise OSError('Windows local application data folder is unavailable')
    return Path(local_data) / 'ZFTP' / 'nfs' / VERSION / 'WinNFSd.exe'


def matches_backend(data):
    return len(data) == BACKEND_SIZE and sha256(data).hexdigest() == BACKEND_SHA256


def has_verified_backend(path):
    try:
        with path.open('rb') as file:
            return matches_backend(file.read(BACKEND_SIZE + 1))
    except FileNotFoundError:
        return False


def save_backend(path, data):
    if not matches_backend(data):
        raise ValueError('The NFS backend download did not match its expected checksum')
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish the executable atomically after verification, including on retry.
    target = QSaveFile(str(path))
    if not target.open(QIODevice.WriteOnly):
        raise OSError(target.errorString())
    if target.write(data) != len(data):
        target.cancelWriting()
        raise OSError(target.errorString())
    if not target.commit():
        raise OSError(target.errorString())
