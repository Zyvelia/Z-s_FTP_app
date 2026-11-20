"""Qt-free transfer queue; one active job per server, independent servers concurrent."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

TERMINAL = frozenset({'Completed', 'Failed', 'Cancelled'})

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

    @property
    def name(self):
        return Path(self.source.replace('\\', '/')).name

class TransferQueue:
    def __init__(self):
        self.jobs: list[Transfer] = []

    def add(self, transfer: Transfer):
        if transfer.direction not in ('upload', 'download', 'mkdir_local', 'mkdir_remote'):
            raise ValueError('Invalid transfer direction')
        if not transfer.source or not transfer.destination:
            raise ValueError('Source and destination required')
        # Reject collisions amongst queued and running operations, including downloads to disk.
        if any(j.status not in TERMINAL and j.session_id == transfer.session_id
               and j.direction == transfer.direction and j.remote_dir == transfer.remote_dir
               and j.destination == transfer.destination for j in self.jobs):
            raise FileExistsError('Destination already reserved by another queued transfer')
        if transfer.direction == 'download' and any(
            j.status not in TERMINAL and j.direction == 'download'
            and Path(j.destination) == Path(transfer.destination) for j in self.jobs
        ):
            raise FileExistsError('Local download destination already reserved')
        self.jobs.append(transfer)
        return transfer

    def next_for(self, session_id: str):
        if any(j.session_id == session_id and j.status == 'Running' for j in self.jobs):
            return None
        for job in self.jobs:
            if job.session_id == session_id and job.status == 'Queued':
                job.status = 'Running'
                job.phase = 'Uploading' if job.direction == 'upload' else ('Downloading' if job.direction == 'download' else 'Processing')
                return job
        return None

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
