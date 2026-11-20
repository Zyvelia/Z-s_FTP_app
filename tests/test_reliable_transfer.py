"""Offline simulations of resumed upload/download and transient failures."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from zftp.transfers.reliable_transfer import (RateLimiter, TransferSettings, UnsafeResumeError,
                               TransferCancelled, download, upload, with_retries)

class FakeRemoteFile:
    def __init__(self, fs, name, mode):
        import io
        self.fs, self.name, self.mode = fs, name, mode
        if mode == 'x' and name in fs: raise FileExistsError(name)
        if mode in ('rb', 'r+b') and name not in fs: raise FileNotFoundError(name)
        self.buffer = io.BytesIO(fs.get(name, b''))
        if mode == 'x': self.buffer = io.BytesIO()
    def __enter__(self): return self
    def __exit__(self, *args):
        if self.mode != 'rb': self.fs[self.name] = self.buffer.getvalue()
    def read(self, n): return self.buffer.read(n)
    def write(self, data): return self.buffer.write(data)
    def seek(self, offset): return self.buffer.seek(offset)

class FakeSFTP:
    def __init__(self, fs): self.fs = fs
    def stat(self, name):
        if name not in self.fs: raise FileNotFoundError(name)
        return type('Info', (), {'st_size': len(self.fs[name])})()
    def open(self, name, mode): return FakeRemoteFile(self.fs, name, mode)
    def rename(self, src, dst):
        if dst in self.fs: raise FileExistsError(dst)
        self.fs[dst] = self.fs.pop(src)

class FakeBackend:
    def __init__(self, fs): self.conn = FakeSFTP(fs)
    def _sftp(self): return self.conn
    def _path(self, name): return '/home/' + name

class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fs = {}
        self.backend = FakeBackend(self.fs)
        self.settings = TransferSettings()
        self.progress = lambda *_: None
        self.active = lambda: False

    def test_download_from_scratch(self):
        self.fs['/home/a'] = b'abc' * 100
        dest = self.root / 'a'
        self.assertEqual(download(self.backend, 'a', dest, self.progress, self.active, self.settings), 300)
        self.assertEqual(dest.read_bytes(), self.fs['/home/a'])
        self.assertFalse((self.root/'a.zftp.part').exists())

    def test_download_resume_verified(self):
        data = b'abc' * 100
        self.fs['/home/a'] = data
        (self.root/'a.zftp.part').write_bytes(data[:101])
        download(self.backend, 'a', self.root/'a', self.progress, self.active, self.settings)
        self.assertEqual((self.root/'a').read_bytes(), data)

    def test_download_resume_mismatch_fails_without_deleting_partial(self):
        self.fs['/home/a'] = b'abcdefgh'
        (self.root/'a.zftp.part').write_bytes(b'ABC')
        with self.assertRaises(UnsafeResumeError):
            download(self.backend, 'a', self.root/'a', self.progress, self.active, self.settings)
        self.assertEqual((self.root/'a.zftp.part').read_bytes(), b'ABC')
        self.assertFalse((self.root/'a').exists())

    def test_download_existing_final_refused(self):
        self.fs['/home/a'] = b'new'
        (self.root/'a').write_bytes(b'old')
        with self.assertRaises(FileExistsError):
            download(self.backend, 'a', self.root/'a', self.progress, self.active, self.settings)
        self.assertEqual((self.root/'a').read_bytes(), b'old')

    def test_cancel_keeps_partial(self):
        self.fs['/home/a'] = b'x'*250000
        switches = {'cancel': False}
        def progress(count, total):
            switches['cancel'] = count >= 65536
        with self.assertRaises(TransferCancelled):
            download(self.backend,'a',self.root/'a',progress,lambda:switches['cancel'],self.settings)
        self.assertTrue((self.root/'a.zftp.part').exists())
        download(self.backend,'a',self.root/'a',self.progress,self.active,self.settings)
        self.assertEqual((self.root/'a').stat().st_size,250000)

    def test_upload_from_scratch(self):
        src = self.root/'a'; src.write_bytes(b'abcd' * 40)
        upload(self.backend,src,'a',self.progress,self.active,self.settings)
        self.assertEqual(self.fs['/home/a'],src.read_bytes())
        self.assertNotIn('/home/a.zftp.part',self.fs)

    def test_upload_resume_verified(self):
        src = self.root/'a'; src.write_bytes(b'abcd' * 40)
        self.fs['/home/a.zftp.part'] = src.read_bytes()[:17]
        upload(self.backend,src,'a',self.progress,self.active,self.settings)
        self.assertEqual(self.fs['/home/a'],src.read_bytes())

    def test_upload_mismatch_refused(self):
        src = self.root/'a'; src.write_bytes(b'abcd'*40)
        self.fs['/home/a.zftp.part'] = b'XXXXX'
        with self.assertRaises(UnsafeResumeError):
            upload(self.backend,src,'a',self.progress,self.active,self.settings)
        self.assertNotIn('/home/a',self.fs)

    def test_upload_existing_final_refused(self):
        src=self.root/'a'; src.write_bytes(b'new')
        self.fs['/home/a']=b'old'
        with self.assertRaises(FileExistsError):
            upload(self.backend,src,'a',self.progress,self.active,self.settings)
        self.assertEqual(self.fs['/home/a'],b'old')

    def test_retry_transient_with_reconnect(self):
        tries=[0]; reconnects=[0]; delays=[]
        def action():
            tries[0]+=1
            if tries[0] < 3: raise ConnectionResetError('broken')
            return 'ok'
        def reconnect(): reconnects[0]+=1
        self.assertEqual(with_retries(action,reconnect,TransferSettings(retries=3,backoff=0.5),self.active,
                                      sleep=lambda secs:delays.append(secs)), 'ok')
        self.assertEqual((tries[0],reconnects[0]),(3,2))
        self.assertAlmostEqual(sum(delays),1.5)

    def test_retry_permanent_error_no_reconnect(self):
        callback=Mock()
        def failure(): raise UnsafeResumeError('mismatch')
        with self.assertRaises(UnsafeResumeError):
            with_retries(failure,callback,TransferSettings(),self.active)
        callback.assert_not_called()

    def test_retry_exhausted(self):
        tries=[0]
        def fail():
            tries[0]+=1; raise EOFError('offline')
        with self.assertRaises(EOFError):
            with_retries(fail,lambda:None,TransferSettings(retries=2,backoff=0),self.active)
        self.assertEqual(tries[0],3)

    def test_limits_invalid(self):
        for invalid in ({'retries':11},{'bandwidth_kib':-1},{'backoff':-1}):
            with self.assertRaises(ValueError): TransferSettings(**invalid)

    def test_rate_limiter(self):
        now=[0.]
        def sleep(seconds): now[0]+=seconds
        limiter=RateLimiter(1,clock=lambda:now[0],sleep=sleep)
        limiter.consume(1024,self.active)
        self.assertAlmostEqual(now[0],1.)

if __name__ == '__main__': unittest.main()
