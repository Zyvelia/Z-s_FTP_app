"""Durable, conservative job scheduler. No automatic network login or secret storage.

Schedules run ONLY while Z FTP is open and the named session is connected.
Crash-interrupted claims are PAUSED for manual review; automatic replay could
repeat a transfer whose remote side effects happened before process exit.
"""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
import time
from pathlib import Path
from uuid import uuid4

DEFAULT_DB = Path.home() / '.zftp' / 'schedules.sqlite3'
ACTIONS = ('upload', 'download', 'sync_upload', 'sync_download', 'sync_two_way')

class ScheduleStore:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY, label TEXT NOT NULL, action TEXT NOT NULL,
                endpoint TEXT NOT NULL, local_path TEXT NOT NULL,
                remote_path TEXT NOT NULL, next_run REAL NOT NULL,
                repeat_seconds INTEGER NOT NULL, state TEXT NOT NULL,
                last_run REAL, last_error TEXT NOT NULL DEFAULT '')''')
            # The previous app process may have successfully transferred before
            # shutdown, so recovery requires an explicit human review.
            db.execute("UPDATE schedules SET state='paused', last_error='Interrupted dispatch; review before re-enabling' WHERE state='dispatching'")

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def endpoint(protocol, host, port, user):
        return json.dumps([protocol, host, int(port), user], ensure_ascii=False)

    def add(self, label, action, endpoint, local_path, remote_path, when, repeat_seconds=0):
        if action not in ACTIONS: raise ValueError('Unsupported schedule action')
        if not label.strip() or not local_path or not remote_path: raise ValueError('Missing schedule field')
        if not isinstance(when, (float, int)) or when < 0: raise ValueError('Invalid schedule time')
        if repeat_seconds not in (0,) and not 60 <= repeat_seconds <= 365 * 86400:
            raise ValueError('Repeat interval must be at least one minute')
        info = json.loads(endpoint)
        if not isinstance(info, list) or len(info) != 4 or not all(str(x) for x in info):
            raise ValueError('Invalid connection identity')
        ident = uuid4().hex
        with self._db() as db:
            db.execute('''INSERT INTO schedules (id,label,action,endpoint,local_path,remote_path,next_run,repeat_seconds,state)
                          VALUES (?,?,?,?,?,?,?,?,?)''', (ident, label.strip()[:140], action, endpoint,
                         str(local_path), str(remote_path), float(when), repeat_seconds, 'enabled'))
        return ident

    def all(self):
        with self._db() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM schedules ORDER BY next_run ASC')]

    def due(self, now=None):
        now = time.time() if now is None else now
        return [r for r in self.all() if r['state'] == 'enabled' and r['next_run'] <= now]

    def claim(self, ident, now=None):
        now = time.time() if now is None else now
        with self._db() as db:
            result = db.execute('''UPDATE schedules SET state='dispatching', last_run=?, last_error=''
                WHERE id=? AND state='enabled' AND next_run<=?''', (now, ident, now))
            return result.rowcount == 1

    def finish(self, ident, succeeded, error='', now=None):
        now = time.time() if now is None else now
        with self._db() as db:
            row = db.execute('SELECT repeat_seconds, next_run FROM schedules WHERE id=? AND state=\'dispatching\'', (ident,)).fetchone()
            if row is None: return False
            interval, prior = row
            # Skip missed occurrences to avoid a storm of replays after downtime.
            next_run = max(now, prior) + interval if interval else prior
            state = ('enabled' if interval else 'dispatched') if succeeded else 'paused'
            db.execute('UPDATE schedules SET state=?, next_run=?, last_error=? WHERE id=?',
                       (state, next_run, str(error)[:500], ident))
            return True

    def set_enabled(self, ident, enabled):
        with self._db() as db:
            return db.execute('UPDATE schedules SET state=?, last_error=\'\' WHERE id=? AND state!=\'dispatching\'',
                              ('enabled' if enabled else 'paused', ident)).rowcount == 1

    def delete(self, ident):
        with self._db() as db:
            return db.execute("DELETE FROM schedules WHERE id=? AND state!='dispatching'", (ident,)).rowcount == 1
