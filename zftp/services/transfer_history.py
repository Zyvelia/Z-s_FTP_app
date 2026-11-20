"""Bounded, SQLite-backed transfer audit history; no credentials or file contents."""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

DEFAULT_DB = Path.home() / '.zftp' / 'transfer_history.sqlite3'

class TransferHistory:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS transfers (
                job_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                server TEXT NOT NULL, protocol TEXT NOT NULL,
                direction TEXT NOT NULL, source TEXT NOT NULL,
                destination TEXT NOT NULL, status TEXT NOT NULL,
                bytes_done INTEGER NOT NULL, attempts INTEGER NOT NULL,
                sha256 TEXT, error TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS transfer_timestamp ON transfers(timestamp DESC)')

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def record(self, job, server='', protocol='', sha256=None):
        if job.status not in ('Completed', 'Failed', 'Cancelled'):
            raise ValueError('Cannot record nonterminal transfer')
        with self._db() as db:
            db.execute('''INSERT OR REPLACE INTO transfers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (
                job.id, datetime.now(timezone.utc).isoformat(timespec='seconds'),
                str(server)[:255], str(protocol)[:16], job.direction,
                str(job.source)[:4096], str(job.destination)[:4096], job.status,
                max(0, job.count), max(0, job.attempts), sha256,
                str(job.error)[:1000]))

    def recent(self, limit=200):
        if not 1 <= limit <= 10000: raise ValueError('Invalid limit')
        with self._db() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('''SELECT * FROM transfers
                ORDER BY timestamp DESC, rowid DESC LIMIT ?''', (limit,))]

    def export_csv(self, target: Path):
        import csv
        target = Path(target)
        with target.open('x', newline='', encoding='utf-8-sig') as fp:
            writer = csv.DictWriter(fp, fieldnames=['timestamp', 'server', 'protocol',
                'direction', 'source', 'destination', 'status', 'bytes_done',
                'attempts', 'sha256', 'error', 'job_id'])
            writer.writeheader()
            for row in self.recent(10000):
                # Prevent spreadsheet formula execution when CSV is opened in Excel.
                safe = {key: ("'" + value if isinstance(value, str) and
                        value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value)
                        for key, value in row.items()}
                writer.writerow(safe)
        return target
