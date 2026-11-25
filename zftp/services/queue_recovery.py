"""Persist pending transfer plans without recording authentication secrets."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from zftp.services.remote_management import validate_name
from zftp.transfers.transfer_queue import Transfer

DIRECTIONS = {'download', 'download_folder', 'upload', 'mkdir_local', 'mkdir_remote'}


def endpoint_key(state):
    return state.protocol, state.host.strip().casefold(), state.port, state.username


def transfer_plan(job):
    remaining = job.children[job.count:] if job.children else ()
    if job.children and not remaining:
        return None
    return {
        'id': job.id,
        'direction': job.direction,
        'source': job.source,
        'destination': job.destination,
        'remote_dir': job.remote_dir,
        'local_dir': job.local_dir,
        'total': None if job.children else job.total,
        'skipped': job.skipped,
        'verify_sha256': job.verify_sha256,
        'children': [transfer_plan(child) for child in remaining],
    }


def restore_transfer(plan, session_id, child=False):
    direction = plan['direction']
    source, destination, remote_dir, local_dir = (plan[key] for key in
                                                  ('source', 'destination', 'remote_dir', 'local_dir'))
    if direction not in DIRECTIONS or not remote_dir.startswith('/') or not Path(local_dir).is_absolute():
        raise ValueError('Invalid saved transfer')
    if direction in ('download', 'download_folder'):
        validate_name(source)
    elif direction in ('upload', 'mkdir_remote'):
        validate_name(destination)
    if direction in ('download', 'download_folder', 'mkdir_local') and not Path(destination).is_absolute():
        raise ValueError('Invalid local destination')
    if direction == 'upload' and not Path(source).is_absolute():
        raise ValueError('Invalid local source')
    if direction == 'download_folder':
        if child or not plan['children']:
            raise ValueError('Invalid saved folder transfer')
        children = tuple(restore_transfer(part, session_id, True) for part in plan['children'])
        if any(part.direction not in ('download', 'mkdir_local') for part in children):
            raise ValueError('Invalid saved folder member')
    else:
        children = ()
    return Transfer(session_id, direction, source, destination, remote_dir, local_dir, id=plan['id'],
                    total=len(children) if children else plan['total'], children=children,
                    skipped=plan['skipped'], verify_sha256=plan.get('verify_sha256', False), recovered=True)


class QueueRecovery:
    def __init__(self, path, disabled=""):
        self.path = path
        self.waiting = []
        self.last = []
        self.load_error = disabled
        if disabled:
            return
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding='utf-8'))
                if data['version'] != 1 or not isinstance(data['jobs'], list):
                    raise ValueError('Unsupported transfer recovery format')
                seen = set()
                for record in data['jobs']:
                    key = record['endpoint']
                    if (len(key) != 4 or key[0] not in ('FTP', 'FTPS', 'SFTP') or not isinstance(key[2], int)
                            or not all(isinstance(key[i], str) for i in (0, 1, 3))):
                        raise ValueError('Invalid saved endpoint')
                    job = restore_transfer(record['plan'], '')
                    if job.id in seen:
                        raise ValueError('Duplicate saved transfer ID')
                    seen.add(job.id)
                self.last = self.waiting = data['jobs']
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as error:
            self.load_error = str(error)

    def save(self, jobs, states):
        records = self.waiting.copy()
        for job in jobs:
            if job.status not in ('Queued', 'Running') or job.session_id not in states:
                continue
            plan = transfer_plan(job)
            if plan:
                records.append({'endpoint': list(endpoint_key(states[job.session_id])), 'plan': plan})
        if records == self.last or self.load_error:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent, delete=False) as stream:
            staged = Path(stream.name)
            try:
                json.dump({'version': 1, 'jobs': records}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                stream.close()
                staged.unlink(missing_ok=True)
                raise
        try:
            os.replace(staged, self.path)
        finally:
            staged.unlink(missing_ok=True)
        self.last = records

    def defer_pending(self, queue, session_id, state):
        remaining = []
        for job in queue.jobs:
            if job.session_id == session_id and job.status == 'Queued':
                self.waiting.append({'endpoint': list(endpoint_key(state)), 'plan': transfer_plan(job)})
            else:
                remaining.append(job)
        queue.jobs[:] = remaining

    def restore(self, queue, session_id, state):
        endpoint = list(endpoint_key(state))
        remaining, restored, blocked = [], 0, 0
        for record in self.waiting:
            if record['endpoint'] != endpoint:
                remaining.append(record)
                continue
            try:
                queue.add(restore_transfer(record['plan'], session_id))
            except (FileExistsError, ValueError):
                remaining.append(record)
                blocked += 1
            else:
                restored += 1
        self.waiting = remaining
        return restored, blocked
