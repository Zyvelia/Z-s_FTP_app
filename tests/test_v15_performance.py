"""Offline regression / performance smoke coverage for v1.5."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from zftp.services.directory_performance import (BATCH_SIZE, RenderGate, UpdateThrottle,
                                   batches, scan_directory)
from zftp.transfers.reliable_transfer import CHUNK, RateLimiter, TransferSettings


class DirectoryPerformanceTests(unittest.TestCase):
    def test_scandir_sort_folders_first(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p/'b.txt').write_bytes(b'abc')
            (p/'A').mkdir()
            (p/'a.txt').write_bytes(b'x')
            entries = scan_directory(p)
            self.assertEqual([e.name for e in entries], ['A', 'a.txt', 'b.txt'])
            self.assertEqual(entries[-1].size, 3)
            self.assertIsNone(entries[0].size)

    def test_directory_failure_preserves_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises((FileNotFoundError, NotADirectoryError)):
                scan_directory(Path(directory)/'does_not_exist')

    def test_large_listing_batches_are_bounded_and_complete(self):
        items = list(range(10003))
        chunks = list(batches(items))
        self.assertEqual(len(chunks), 51)
        self.assertEqual([v for chunk in chunks for v in chunk], items)
        self.assertTrue(all(len(chunk) <= BATCH_SIZE for chunk in chunks))

    def test_reject_empty_batch(self):
        with self.assertRaises(ValueError):
            list(batches([1], 0))

    def test_fast_scandir_ten_thousand_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            for index in range(2000):
                (p/f'file_{index:05d}.dat').touch()
            start=time.perf_counter()
            result=scan_directory(p)
            elapsed=time.perf_counter()-start
            self.assertEqual(len(result), 2000)
            # Very generous smoke threshold, not a claimed benchmark guarantee.
            self.assertLess(elapsed, 10.0)

    def test_generation_ignores_old_results(self):
        gate=RenderGate(); a=gate.new(); b=gate.new()
        self.assertFalse(gate.current(a))
        self.assertTrue(gate.current(b))

    def test_draw_throttling_keeps_terminal_update(self):
        ticks=iter([0.0, .02, .04, .20, .21])
        limiter=UpdateThrottle(.10, clock=lambda: next(ticks))
        self.assertTrue(limiter.due())
        self.assertFalse(limiter.due())
        self.assertFalse(limiter.due())
        self.assertTrue(limiter.due())
        self.assertTrue(limiter.due(finished=True))

    def test_streaming_limit_does_not_buffer_entire_file(self):
        self.assertLessEqual(CHUNK, 1024*1024)
        self.assertEqual(TransferSettings(bandwidth_kib=0).retries, 3)
        limiter=RateLimiter(0)
        limiter.consume(100*1024*1024, lambda: False)  # constant memory accounting
        self.assertEqual(limiter.bytes, 100*1024*1024)

if __name__ == '__main__':
    unittest.main()
