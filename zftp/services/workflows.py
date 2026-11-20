"""SQLite-backed, manual-only sequential cross-server copy workflows.

No passwords stored. Running claims are paused after process restart; the user
must inspect both servers before resuming an interrupted step. Completed steps
are never replayed by the runner.
"""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

DEFAULT_DB = Path.home() / '.zftp' / 'workflows.sqlite3'


def _validate_step(step):
    if not isinstance(step, dict) or step.get('action') != 'copy_remote':
        raise ValueError('Only copy_remote workflow steps are currently supported')
    from zftp.transfers.cross_server import normalized_directory, safe_filename
    for key in ('source_endpoint', 'destination_endpoint'):
        ident = step.get(key)
        if not isinstance(ident, list) or len(ident) != 4 or any(not isinstance(v, str) or not v.strip() for v in ident):
            raise ValueError('Server identity must be [protocol,host,port,username] strings')
        if ident[0] not in ('FTP', 'FTPS', 'SFTP') or not ident[2].isdigit() or not 1 <= int(ident[2]) <= 65535:
            raise ValueError('Invalid server identity')
    if step['source_endpoint'] == step['destination_endpoint']:
        raise ValueError('Source and destination identities must be different')
    normalized_directory(step.get('source_dir'))
    normalized_directory(step.get('destination_dir'))
    safe_filename(step.get('source_name'))
    safe_filename(step.get('destination_name'))
    return {key: step[key] for key in ('action', 'source_endpoint', 'destination_endpoint',
             'source_dir', 'destination_dir', 'source_name', 'destination_name')}


class WorkflowStore:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, steps TEXT NOT NULL,
                cursor INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
                last_error TEXT NOT NULL DEFAULT '')''')
            db.execute("UPDATE workflows SET state='paused', last_error='Interrupted step; inspect destination before resuming' WHERE state='running'")

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add(self, name, steps):
        if not isinstance(name, str) or not name.strip() or len(name) > 140:
            raise ValueError('A workflow needs a name (max 140 characters)')
        if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
            raise ValueError('Workflow must contain 1 to 100 steps')
        clean_steps = [_validate_step(step) for step in steps]
        ident = uuid4().hex
        with self._db() as db:
            db.execute('INSERT INTO workflows VALUES (?, ?, ?, 0, ?, ?)',
                       (ident, name.strip(), json.dumps(clean_steps), 'ready', ''))
        return ident

    def get(self, ident):
        with self._db() as db:
            db.row_factory = sqlite3.Row
            item = db.execute('SELECT * FROM workflows WHERE id=?', (ident,)).fetchone()
            if item is None: return None
            result = dict(item)
            result['steps'] = json.loads(result['steps'])
            return result

    def all(self):
        with self._db() as db:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute('SELECT * FROM workflows ORDER BY name COLLATE NOCASE')]
        for row in rows: row['steps'] = json.loads(row['steps'])
        return rows

    def claim(self, ident):
        with self._db() as db:
            return db.execute("UPDATE workflows SET state='running', last_error='' WHERE id=? AND state='ready'", (ident,)).rowcount == 1

    def advance(self, ident):
        with self._db() as db:
            row = db.execute("SELECT cursor, steps FROM workflows WHERE id=? AND state='running'", (ident,)).fetchone()
            if not row: return False
            index, raw = row
            last = index + 1 >= len(json.loads(raw))
            return db.execute('UPDATE workflows SET cursor=?, state=? WHERE id=? AND state=\'running\'',
                              (index + 1, 'completed' if last else 'ready', ident)).rowcount == 1

    def pause(self, ident, reason):
        with self._db() as db:
            return db.execute("UPDATE workflows SET state='paused', last_error=? WHERE id=? AND state IN ('running', 'ready')",
                              (str(reason)[:500], ident)).rowcount == 1

    def resume(self, ident):
        # Manual-only. Never automatically assume an interrupted step had no side effects.
        with self._db() as db:
            return db.execute("UPDATE workflows SET state='ready', last_error='' WHERE id=? AND state='paused'", (ident,)).rowcount == 1

    def delete(self, ident):
        with self._db() as db:
            return db.execute("DELETE FROM workflows WHERE id=? AND state!='running'", (ident,)).rowcount == 1

    def rename(self, ident, name):
        """Rename without changing completed work or any server-side file."""
        if not isinstance(name, str) or not name.strip() or len(name) > 140:
            raise ValueError('A workflow needs a name (max 140 characters)')
        with self._db() as db:
            return db.execute("UPDATE workflows SET name=? WHERE id=? AND state!='running'",
                              (name.strip(), ident)).rowcount == 1

    def replace_steps(self, ident, steps):
        """Edit only a workflow that has never executed a step.

        Even a paused workflow at cursor=0 can have a partially published
        operation. Only 'ready' workflows can be edited to avoid replay risks.
        """
        if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
            raise ValueError('Workflow must contain 1 to 100 steps')
        clean = [_validate_step(step) for step in steps]
        with self._db() as db:
            return db.execute("UPDATE workflows SET steps=? WHERE id=? AND state='ready' AND cursor=0",
                              (json.dumps(clean), ident)).rowcount == 1

    def duplicate(self, ident, name=None):
        """Copy a definition as a fresh workflow without cloning execution state."""
        item = self.get(ident)
        if not item:
            raise ValueError('Workflow not found')
        return self.add(name if name is not None else item['name'] + ' (copy)', item['steps'])
