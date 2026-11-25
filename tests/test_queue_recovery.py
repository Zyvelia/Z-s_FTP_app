import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from zftp.services.queue_recovery import QueueRecovery
from zftp.services.sessions import ConnectionSession
from zftp.transfers.transfer_queue import Transfer, TransferQueue


class QueueRecoveryTests(unittest.TestCase):
    def test_restores_pending_folder_members_to_matching_login(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state = ConnectionSession(None, 'FTP', 'Example.net', 21, 'one')
            path = root / 'queue.json'
            children = tuple(Transfer('old', 'download', name, str(root / name), '/remote', directory, total=23)
                             for name in ('done.bin', 'pending.bin'))
            job = Transfer('old', 'download_folder', 'remote', directory, '/remote', directory,
                           status='Running', count=1, children=children, verify_sha256=True)
            finished = Transfer('old', 'download', 'old.bin', str(root / 'old.bin'), '/remote', directory,
                                status='Completed')
            QueueRecovery(path).save([job, finished], {'old': state})
            manifest = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(len(manifest['jobs']), 1)
            self.assertEqual(manifest['jobs'][0]['endpoint'], ['FTP', 'example.net', 21, 'one'])
            recovery = QueueRecovery(path)
            queue = TransferQueue()
            wrong = ConnectionSession(None, 'FTP', 'example.net', 21, 'other')
            self.assertEqual(recovery.restore(queue, 'new', wrong), (0, 0))
            self.assertEqual(recovery.restore(queue, 'new', state), (1, 0))
            restored, = queue.jobs
            self.assertEqual(restored.status, 'Queued')
            self.assertEqual(restored.session_id, 'new')
            self.assertTrue(restored.verify_sha256)
            self.assertEqual([child.destination for child in restored.children], [str(root / 'pending.bin')])
            queue.paused = True
            self.assertIsNone(queue.next_for('new'))
            queue.paused = False
            self.assertIs(queue.next_for('new'), restored)
            queue.finish(restored)
            recovery.save(queue.jobs, {'new': state})
            self.assertEqual(QueueRecovery(path).waiting, [])
