"""Qt-free transfer queue; bounded parallel downloads and ordered writes."""
from __future__ import annotations
from dataclasses import dataclass, field
from itertools import count
import posixpath
from pathlib import Path, PurePosixPath
from uuid import uuid4

DOWNLOADS = frozenset({'download', 'download_folder'})
TERMINAL = frozenset({'Completed', 'Failed', 'Cancelled'})

@dataclass(frozen=True)
class FolderProgress:
    items_done: int
    bytes_done: int
    bytes_total: int | None
    status: str


@dataclass
class Transfer:
    session_id: str
    direction: str
    source: str
    destination: str
    remote_dir: str
    local_dir: str
    id: str = field(default_factory=lambda: uuid4().hex)
    status: str = 'Queued'
    phase: str = 'Queued'
    count: int = 0
    total: int | None = None
    error: str = ''
    attempts: int = 0
    speed_bps: float = 0.0
    eta_seconds: float | None = None
    children: tuple[Transfer, ...] = ()
    skipped: int = 0
    verify_sha256: bool = False
    folder_progress: FolderProgress | None = None

    @property
    def name(self):
        return Path(self.source.replace('\\', '/')).name

class TransferQueue:
    def __init__(self):
        self.jobs: list[Transfer] = []

    @staticmethod
    def _download_paths(path):
        path = path.parent.resolve() / path.name
        return {path, path.with_name(path.name + '.zftp.part')}

    def add(self, transfer: Transfer, *, keep_both=False):
        if transfer.direction not in ('upload', 'download', 'mkdir_local', 'mkdir_remote', 'download_folder'):
            raise ValueError('Invalid transfer direction')
        if not transfer.source or not transfer.destination:
            raise ValueError('Source and destination required')
        if transfer.direction not in DOWNLOADS and any(
                job.status not in TERMINAL and job.session_id == transfer.session_id
                and job.direction == transfer.direction and job.remote_dir == transfer.remote_dir
                and job.destination == transfer.destination for job in self.jobs):
            raise FileExistsError('Destination already reserved by another queued transfer')

        planned = transfer.children if transfer.direction == 'download_folder' else (transfer,)
        downloads = [part for part in planned if part.direction == 'download']
        if not downloads:
            self.jobs.append(transfer)
            return transfer

        # Folder jobs share a destination directory, so compare their files.
        reserved = set()
        for part in self.active_parts():
            if part.direction == 'download':
                reserved.update(self._download_paths(Path(part.destination)))
            elif part.direction == 'mkdir_local':
                reserved.add(Path(part.destination).resolve())
        if keep_both:
            # Directories created by this batch are also unavailable as filenames.
            reserved.update(Path(part.destination).resolve() for part in planned if part.direction == 'mkdir_local')
        renames = []
        for part in downloads:
            target = Path(part.destination)
            paths = self._download_paths(target)
            if keep_both and (reserved & paths or target.exists() or target.is_symlink()):
                for index in count(2):
                    renamed = target.with_name(f'{target.stem} ({index}){target.suffix}')
                    paths = self._download_paths(renamed)
                    if not reserved & paths and not any(path.exists() or path.is_symlink() for path in paths):
                        target = renamed
                        renames.append((part, target))
                        break
            elif reserved & paths:
                raise FileExistsError('Local download destination already reserved')
            reserved.update(paths)

        for part, target in renames:
            part.destination = str(target)
        self.jobs.append(transfer)
        return transfer

    def next_for(self, session_id: str, *, download_limit=1):
        running = [j for j in self.jobs if j.session_id == session_id and j.status == 'Running']
        pending = next((j for j in self.jobs if j.session_id == session_id and j.status == 'Queued'), None)
        if pending is None:
            return None
        if pending.direction in DOWNLOADS and download_limit == 0:
            return None
        if running and (len(running) >= download_limit or pending.direction not in DOWNLOADS
                        or any(j.direction not in DOWNLOADS for j in running)):
            return None
        pending.status = 'Running'
        pending.phase = ('Uploading' if pending.direction == 'upload' else
                         'Downloading' if pending.direction in DOWNLOADS else 'Processing')
        return pending

    def active_parts(self):
        for job in self.jobs:
            if job.status not in TERMINAL:
                yield job
                yield from job.children

    def protects_remote_path(self, session_ids, directory, name):
        def remote_path(folder, basename):
            path = posixpath.normpath(posixpath.join(folder, basename)).casefold()
            return PurePosixPath('/' + path.lstrip('/'))

        selected = remote_path(directory, name)
        for item in self.active_parts():
            if item.session_id not in session_ids:
                continue
            if item.direction in DOWNLOADS or item.direction in ('upload', 'mkdir_remote'):
                filename = item.source if item.direction in DOWNLOADS else item.destination
                target = remote_path(item.remote_dir, filename)
                if target.is_relative_to(selected):
                    return True
                if item.direction == 'upload' and target.with_name(target.name + '.zftp.part') == selected:
                    return True
        return False

    def protects_local_path(self, path):
        selected = path.resolve()
        for item in self.active_parts():
            if item.direction == 'upload':
                target = Path(item.source).resolve()
            elif item.direction in DOWNLOADS or item.direction == 'mkdir_local':
                target = Path(item.destination).resolve()
                if item.direction == 'download' and target.with_name(target.name + '.zftp.part') == selected:
                    return True
            else:
                continue
            if target.is_relative_to(selected):
                return True
        return False

    def cancel(self, transfer_id: str):
        job = next((j for j in self.jobs if j.id == transfer_id), None)
        if job is None or job.status in TERMINAL:
            return None
        if job.status == 'Queued':
            job.status = 'Cancelled'
            job.phase = 'Cancelled'
        return job

    def progress(self, job: Transfer, count: int, total: int | None):
        if job.status == 'Running':
            job.count, job.total = count, total

    def finish(self, job: Transfer, error: Exception | None = None):
        if job.status != 'Running':
            raise RuntimeError('Only running transfers can finish')
        if error is None:
            job.status = 'Completed'
            job.phase = 'Completed'
        elif isinstance(error, InterruptedError):
            job.status = 'Cancelled'
            job.phase = 'Cancelled'
        else:
            job.status = 'Failed'
            job.phase = 'Failed'
            job.error = str(error)

    def cancel_pending_for(self, session_id: str):
        for j in self.jobs:
            if j.session_id == session_id and j.status == 'Queued':
                j.status = 'Cancelled'
                j.phase = 'Cancelled'

    def clear_finished(self):
        self.jobs[:] = [j for j in self.jobs if j.status not in TERMINAL]
