"""v0.5 queue behavior: ordering, collision avoidance, isolation, cancellation."""
import unittest
from zftp.transfers.transfer_queue import Transfer, TransferQueue


def upload(session='a', filename='one.bin', remote_dir='/'):
    return Transfer(session, 'upload', '/tmp/' + filename, filename, remote_dir, '/tmp')


def download(session='a', filename='one.bin', local_dir='/tmp'):
    return Transfer(session, 'download', filename, local_dir + '/' + filename, '/', local_dir)


class TransferQueueTests(unittest.TestCase):
    def test_fifo_for_same_session(self):
        q = TransferQueue(); a = q.add(upload(filename='a')); b = q.add(upload(filename='b'))
        self.assertIs(q.next_for('a'), a)
        self.assertIsNone(q.next_for('a'))
        q.finish(a)
        self.assertIs(q.next_for('a'), b)
        q.finish(b)
        self.assertEqual([j.status for j in q.jobs], ['Completed', 'Completed'])

    def test_parallel_different_sessions(self):
        q = TransferQueue(); a = q.add(upload('a')); b = q.add(upload('b'))
        self.assertIs(q.next_for('a'), a)
        self.assertIs(q.next_for('b'), b)
        q.progress(a, 5, 20)
        self.assertEqual((a.count, a.total), (5, 20))
        self.assertEqual(b.count, 0)

    def test_remote_collision_rejected_until_finished(self):
        q = TransferQueue(); first = q.add(upload())
        with self.assertRaises(FileExistsError): q.add(upload())
        q.next_for('a'); q.finish(first)
        q.add(upload())

    def test_local_download_collision_across_servers(self):
        q = TransferQueue(); q.add(download('a'))
        with self.assertRaises(FileExistsError): q.add(download('b'))
        q.add(download('b', 'other.bin'))

    def test_cancel_queued_does_not_affect_other_session(self):
        q = TransferQueue(); a = q.add(upload('a')); b = q.add(upload('b'))
        self.assertIs(q.cancel(a.id), a)
        self.assertEqual(a.status, 'Cancelled')
        self.assertIs(q.next_for('b'), b)
        self.assertEqual(b.status, 'Running')

    def test_cancel_running_requires_worker_cooperation(self):
        q = TransferQueue(); a = q.add(upload())
        q.next_for('a')
        q.cancel(a.id)
        self.assertEqual(a.status, 'Running')
        q.finish(a, InterruptedError('Cancelled'))
        self.assertEqual(a.status, 'Cancelled')

    def test_failed_status_and_clear_finished(self):
        q = TransferQueue(); a = q.add(upload()); b = q.add(upload(filename='b'))
        q.next_for('a'); q.finish(a, ValueError('no permission'))
        self.assertEqual(a.status, 'Failed')
        self.assertIn('permission', a.error)
        q.clear_finished(); self.assertEqual(q.jobs, [b])

    def test_cancel_pending_only_one_connection(self):
        q = TransferQueue(); running = q.add(upload('a')); queued = q.add(upload('a', 'two'))
        other = q.add(upload('b'))
        q.next_for('a')
        q.cancel_pending_for('a')
        self.assertEqual(running.status, 'Running')
        self.assertEqual(queued.status, 'Cancelled')
        self.assertEqual(other.status, 'Queued')

    def test_validation(self):
        q = TransferQueue()
        with self.assertRaises(ValueError): q.add(Transfer('a', 'copy', 'src', 'dst', '/', '/tmp'))
        with self.assertRaises(ValueError): q.add(Transfer('a', 'upload', '', 'dst', '/', '/tmp'))


if __name__ == '__main__': unittest.main()
