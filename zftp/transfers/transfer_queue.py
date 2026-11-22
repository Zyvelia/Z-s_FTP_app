"""Qt-free transfer queue; bounded parallel downloads and ordered writes."""
from __future__ import annotations
from dataclasses import dataclass, field
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

    def add(self, transfer: Transfer):
        if transfer.direction not in ('upload', 'download', 'mkdir_local', 'mkdir_remote', 'download_folder'):
            raise ValueError('Invalid transfer direction')
        if not transfer.source or not transfer.destination:
            raise ValueError('Source and destination required')
        # Reject collisions amongst queued and running operations, including downloads to disk.
        if any(j.status not in TERMINAL and j.session_id == transfer.session_id
               and j.direction == transfer.direction and j.remote_dir == transfer.remote_dir
               and j.destination == transfer.destination for j in self.jobs):
            raise FileExistsError('Destination already reserved by another queued transfer')
        def download_targets(job):
            paths = job.children if job.direction == 'download_folder' else (job,)
            return {Path(part.destination) for part in paths if part.direction == 'download'}

        targets = download_targets(transfer)
        if any(targets & download_targets(job) for job in self.jobs if job.status not in TERMINAL):
            raise FileExistsError('Local download destination already reserved')
        self.jobs.append(transfer)
        return transfer

    def next_for(self, session_id: str, *, download_limit=1):
        running = [j for j in self.jobs if j.session_id == session_id and j.status == 'Running']
        pending = next((j for j in self.jobs if j.session_id == session_id and j.status == 'Queued'), None)
        if pending is None:
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
