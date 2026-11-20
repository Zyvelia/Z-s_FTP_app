import unittest

from zftp.transfers.transfer_queue import Transfer, TransferQueue


class FolderDownloadQueueTests(unittest.TestCase):
    def test_folder_is_one_cancellable_job_and_reserves_its_files(self):
        queue = TransferQueue()
        file = Transfer('one', 'download', 'EBOOT.BIN', 'backup/game/EBOOT.BIN', '/game', 'backup/game')
        folder = Transfer('one', 'download_folder', 'game', 'backup', '/', 'backup',
                          children=(file,), total=1)
        queue.add(folder)
        self.assertEqual(queue.jobs, [folder])
        with self.assertRaises(FileExistsError):
            queue.add(Transfer('two', 'download', 'EBOOT.BIN', file.destination, '/game', 'backup/game'))
        self.assertIs(queue.next_for('one'), folder)
        self.assertIsNone(queue.next_for('one'))
        queue.progress(folder, 1, 1)
        queue.finish(folder)
        self.assertEqual(folder.status, 'Completed')
        self.assertEqual(folder.count, 1)
