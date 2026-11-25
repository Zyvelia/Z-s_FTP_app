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

    def test_different_folders_can_share_a_download_destination(self):
        queue = TransferQueue()
        jobs = []
        for folder in ('base-game', 'patch'):
            child = Transfer('one', 'download', 'content.pkg', f'backup/{folder}/content.pkg',
                             f'/{folder}', f'backup/{folder}')
            batch = Transfer('one', 'download_folder', folder, 'backup', '/', 'backup',
                             children=(child,), total=1)
            jobs.append(queue.add(batch, keep_both=True))
        self.assertIs(queue.next_for('one', download_limit=4), jobs[0])
        self.assertIs(queue.next_for('one', download_limit=4), jobs[1])
        self.assertNotEqual(jobs[0].children[0].destination, jobs[1].children[0].destination)
