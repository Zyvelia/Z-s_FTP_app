"""FTP/FTPS backend. No persistent credential storage."""
from __future__ import annotations

import ftplib
import posixpath
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    is_dir: bool
    size: int | None = None
    is_link: bool = False


class FTPBackend:
    def __init__(self):
        self.conn: ftplib.FTP | ftplib.FTP_TLS | None = None
        self.control_encoding = None
        self._mlsd_supported = None
        self._last_remote_path = None
        # Connection-only settings; never written to disk or transfer history.
        self._reconnect_settings = None

    def connect(self, host: str, port: int, username: str, password: str, *, tls: bool = True, timeout: int = 15, encoding: str = "auto"):
        """Try UTF-8 first, then legacy FTP only for a decoding failure.

        Some homebrew FTP servers, including PS3 implementations, emit 8-bit
        control replies or directory names without advertising UTF-8. Reopen
        the connection on decode failure, since the socket/protocol state may
        already be unusable. Do not retry authentication errors or TLS errors.
        """
        if encoding not in ('auto', 'utf-8', 'latin-1'):
            raise ValueError('Unsupported FTP encoding')
        self.disconnect()
        candidates = ('utf-8', 'latin-1') if encoding == 'auto' else (encoding,)
        for candidate in candidates:
            ftp = (ftplib.FTP_TLS(context=ssl.create_default_context(), timeout=timeout)
                   if tls else ftplib.FTP(timeout=timeout))
            ftp.encoding = candidate
            try:
                ftp.connect(host, port)
                ftp.login(username or 'anonymous', password)
                if tls:
                    ftp.prot_p()
                ftp.set_pasv(True)
                pwd = ftp.pwd()
                self.conn = ftp
                self.control_encoding = candidate
                self._last_remote_path = pwd
                self._reconnect_settings = (host, port, username, password, tls, timeout)
                return pwd
            except UnicodeDecodeError:
                try:
                    ftp.close()
                except Exception:
                    pass
                if encoding == 'auto' and candidate == 'utf-8':
                    continue
                raise
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass
                raise
        raise RuntimeError('Unable to decode FTP server reply')

    def disconnect(self):
        old, self.conn = self.conn, None
        self._reconnect_settings = None
        self.control_encoding = None
        self._mlsd_supported = None
        self._last_remote_path = None
        if old:
            try:
                old.quit()
            except Exception:
                old.close()

    def _ftp(self):
        if self.conn is None:
            raise RuntimeError('Not connected')
        return self.conn

    def pwd(self):
        path = self._ftp().pwd()
        self._last_remote_path = path
        return path

    def cwd(self, path: str):
        result = self._ftp().cwd(path)
        self._last_remote_path = self._ftp().pwd()
        return result

    def list_dir(self) -> list[RemoteEntry]:
        """Read a directory, retrying legacy listings on a fresh connection.

        Some PS3 FTP servers send an ASCII login response but use 8-bit
        filenames in MLSD/LIST. ftplib.retrlines decodes the *data* socket,
        so a connect-only fallback does not cover this case. A failed data
        transfer can leave a pending control reply, so do not reuse the
        socket after UnicodeDecodeError.
        """
        try:
            return self._list_dir_once()
        except UnicodeDecodeError:
            if self.control_encoding != 'utf-8' or self._reconnect_settings is None:
                raise
            host, port, username, password, tls, timeout = self._reconnect_settings
            # The current directory may have changed since login.
            current_path = self._last_remote_path
            if current_path is None:
                raise RuntimeError('Cannot safely restore FTP directory after decoding failure')
            self.connect(host, port, username, password, tls=tls,
                         timeout=timeout, encoding='latin-1')
            try:
                self.cwd(current_path)
                return self._list_dir_once()
            except Exception:
                # Avoid retaining a misleading, partially restored session.
                self.disconnect()
                raise

    def _list_dir_once(self) -> list[RemoteEntry]:
        ftp = self._ftp()
        try:
            if self._mlsd_supported is False:
                raise ftplib.error_perm("MLSD unsupported on this server")
            entries = []
            for name, facts in ftp.mlsd():
                if name in ('.', '..'):
                    continue
                typ = facts.get('type', '')
                if typ in ('cdir', 'pdir'):
                    continue
                size = int(facts['size']) if facts.get('size', '').isdigit() else None
                entries.append(RemoteEntry(name, typ == 'dir', size, typ in ('OS.unix=slink', 'slink', 'link')))
            self._mlsd_supported = True
            return sorted(entries, key=lambda x: (not x.is_dir, x.name.casefold()))
        except (ftplib.error_perm, ftplib.error_reply, AttributeError):
            self._mlsd_supported = False
            lines = []
            ftp.retrlines('LIST', lines.append)
            entries = []
            for line in lines:
                parts = line.split(maxsplit=8)
                if len(parts) >= 9 and parts[0][0] in ('d', '-', 'l'):
                    name = parts[8]
                    if name not in ('.', '..'):
                        size = int(parts[4]) if parts[4].isdigit() else None
                        entries.append(RemoteEntry(name, parts[0][0] == 'd', size, parts[0][0] == 'l'))
                # LIST syntax varies by platform; entries not safely parseable are skipped.
            return sorted(entries, key=lambda x: (not x.is_dir, x.name.casefold()))

    def mkdir(self, name: str):
        self._ftp().mkd(name)

    def delete_file(self, name: str):
        self._ftp().delete(name)

    def remove_empty_dir(self, name: str):
        self._ftp().rmd(name)

    def download(self, name: str, destination: Path, progress: Callable[[int, int | None], None], cancelled: Callable[[], bool]):
        ftp = self._ftp()
        try:
            size = ftp.size(name)
        except ftplib.all_errors:
            size = None
        transferred = 0
        temp = destination.with_name(destination.name + '.zftp.part')
        # Never overwrite a preexisting partial file from another job.
        try:
            with temp.open('xb') as out:
                def write(block: bytes):
                    nonlocal transferred
                    if cancelled():
                        raise InterruptedError('Download cancelled')
                    out.write(block)
                    transferred += len(block)
                    progress(transferred, size)
                ftp.retrbinary('RETR ' + name, write, blocksize=64 * 1024)
            # Fail closed rather than replace an existing local file.
            created_final = False
            try:
                with destination.open('xb') as final:
                    created_final = True
                    with temp.open('rb') as staged:
                        import shutil
                        shutil.copyfileobj(staged, final)
            except BaseException:
                if created_final:
                    destination.unlink(missing_ok=True)
                raise
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        temp.unlink(missing_ok=True)

    def upload(self, source: Path, remote_name: str, progress: Callable[[int, int | None], None], cancelled: Callable[[], bool]):
        ftp = self._ftp()
        size = source.stat().st_size
        transferred = 0
        with source.open('rb') as stream:
            def report(block: bytes):
                nonlocal transferred
                transferred += len(block)
                progress(transferred, size)
                if cancelled():
                    raise InterruptedError('Upload cancelled')
            ftp.storbinary('STOR ' + remote_name, stream, blocksize=64 * 1024, callback=report)



    def rename(self, old_name: str, new_name: str):
        self._ftp().rename(clean_remote_name(old_name), clean_remote_name(new_name))

    def chmod(self, name: str, mode: int):
        if not 0 <= mode <= 0o777:
            raise ValueError('Invalid permission mode')
        self._ftp().sendcmd(f'SITE CHMOD {mode:03o} {clean_remote_name(name)}')

    def read_bytes(self, name: str, max_bytes: int) -> bytes:
        name = clean_remote_name(name)
        chunks = []
        total = 0
        def take(chunk):
            nonlocal total
            total += len(chunk)
            if total > max_bytes:
                raise ValueError('Remote file is too large for the editor')
            chunks.append(chunk)
        self._ftp().retrbinary('RETR ' + name, take, blocksize=32768)
        return b''.join(chunks)

    def read_prefix(self, name: str, length: int) -> bytes:
        # An interrupted RETR can leave pending replies, so isolate it from browsing.
        if self._reconnect_settings is None:
            raise RuntimeError('No active FTP connection')
        host, port, user, password, tls, timeout = self._reconnect_settings
        isolated = FTPBackend()
        try:
            isolated.connect(host, port, user, password, tls=tls, timeout=timeout,
                             encoding=self.control_encoding)
            isolated.cwd(self.pwd())
            ftp = isolated._ftp()
            ftp.voidcmd('TYPE I')
            with ftp.transfercmd('RETR ' + clean_remote_name(name)) as stream:
                reader = stream.makefile('rb')
                with reader:
                    return reader.read(length)
        finally:
            if isolated.conn is not None:
                isolated.conn.close()

    def write_new_bytes(self, name: str, data: bytes):
        import io
        name = clean_remote_name(name)
        # A stage name is unpredictable, but FTP STOR is not universally exclusive.
        if any(e.name == name for e in self.list_dir()):
            raise FileExistsError(name)
        self._ftp().storbinary('STOR ' + name, io.BytesIO(data))

def clean_remote_name(name: str) -> str:
    """Only single remote path segments; avoid accidental path traversal."""
    if (not isinstance(name, str) or not name or name in ('.', '..')
            or '/' in name or '\\' in name or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError('Invalid remote file or folder name')
    return name


def parent_remote(path: str) -> str:
    return posixpath.dirname(path.rstrip('/')) or '/'
