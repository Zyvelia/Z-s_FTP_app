import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from zftp.transfers.cross_server import copy_file, normalized_directory, safe_filename
from zftp.services.workflows import WorkflowStore
from zftp.transfers.reliable_transfer import TransferCancelled


def step(src='one', dst='two', name='a.bin'):
    return {'action':'copy_remote', 'source_endpoint':['SFTP',src,'22','z'],
            'destination_endpoint':['SFTP',dst,'22','z'],
            'source_dir':'/incoming', 'source_name':name,
            'destination_dir':'/outgoing', 'destination_name':name}


class MemoryBackend:
    def __init__(self, files):
        self.files = files.copy()
        self.cwd_path = '/'
    def pwd(self): return self.cwd_path
    def cwd(self, path): self.cwd_path = path
    def list_dir(self):
        prefix = self.cwd_path.rstrip('/') + '/'
        return [SimpleNamespace(name=k[len(prefix):], is_dir=False, is_symlink=False)
                for k in self.files if k.startswith(prefix) and '/' not in k[len(prefix):]]


def fake_download(backend, name, dest, progress, cancelled, settings):
    if cancelled(): raise TransferCancelled('cancelled')
    data = backend.files[backend.pwd() + '/' + name]
    dest.write_bytes(data); progress(len(data), len(data)); return len(data)


def fake_upload(backend, src, name, progress, cancelled, settings):
    if cancelled(): raise TransferCancelled('cancelled')
    path = backend.pwd() + '/' + name
    if path in backend.files: raise FileExistsError(path)
    data = src.read_bytes(); backend.files[path] = data
    progress(len(data), len(data)); return len(data)


def fake_verify(backend, direction, staged, name, cancelled):
    content = backend.files[backend.pwd() + '/' + name]
    if staged.read_bytes() != content: raise ValueError('checksum mismatch')
    return hashlib.sha256(content).hexdigest()


class CrossCopyTests(unittest.TestCase):
    def test_copy_verification_private_staging_and_paths_restored(self):
        src = MemoryBackend({'/incoming/a.bin': b'abc'*100})
        dst = MemoryBackend({})
        src.cwd('/before'); dst.cwd('/elsewhere')
        phases = []
        with patch('zftp.transfers.cross_server.download', fake_download), patch('zftp.transfers.cross_server.upload', fake_upload), \
             patch('zftp.transfers.cross_server.verify_transfer', fake_verify):
            result = copy_file(src, dst, source_dir='/incoming', source_name='a.bin',
                destination_dir='/outgoing', progress=lambda *args:phases.append(args))
        self.assertEqual(dst.files['/outgoing/a.bin'], b'abc'*100)
        self.assertEqual(result['sha256'], hashlib.sha256(b'abc'*100).hexdigest())
        self.assertEqual(src.pwd(), '/before'); self.assertEqual(dst.pwd(), '/elsewhere')
        self.assertEqual([p[0] for p in phases], ['download', 'verify_source', 'upload', 'verify_destination'])

    def test_no_overwrite_and_no_failed_stage_leak(self):
        src = MemoryBackend({'/incoming/a.bin': b'source'})
        dst = MemoryBackend({'/outgoing/a.bin': b'existing'})
        with patch('zftp.transfers.cross_server.download', fake_download), patch('zftp.transfers.cross_server.upload', fake_upload):
            with self.assertRaises(FileExistsError):
                copy_file(src, dst, source_dir='/incoming', source_name='a.bin', destination_dir='/outgoing')
        self.assertEqual(dst.files['/outgoing/a.bin'], b'existing')

    def test_failed_upload_restores_directories(self):
        src = MemoryBackend({'/incoming/a.bin': b'data'})
        dst = MemoryBackend({})
        def fail(*args): raise ConnectionError('network failed')
        with patch('zftp.transfers.cross_server.download', fake_download), patch('zftp.transfers.cross_server.upload', fail), \
             patch('zftp.transfers.cross_server.verify_transfer', fake_verify):
            with self.assertRaises(ConnectionError):
                copy_file(src,dst,source_dir='/incoming',source_name='a.bin',destination_dir='/outgoing')
        self.assertEqual(src.pwd(), '/');self.assertEqual(dst.pwd(), '/')

    def test_cancel_and_unsafe_paths(self):
        src = MemoryBackend({'/incoming/a.bin': b'data'}); dst = MemoryBackend({})
        with self.assertRaises(TransferCancelled):
            copy_file(src,dst,source_dir='/incoming',source_name='a.bin',
                      destination_dir='/outgoing',cancelled=lambda:True)
        for path in ('/a/../b','relative','/a/./b','/a\\b'):
            with self.assertRaises(ValueError): normalized_directory(path)
        for name in ('../x','a/b','a\\b','.','a.zftp.part',None):
            with self.assertRaises(ValueError): safe_filename(name)

    def test_reject_symlink_source(self):
        src = MemoryBackend({'/incoming/a.bin':b'data'});dst = MemoryBackend({})
        src.list_dir = lambda: [SimpleNamespace(name='a.bin', is_dir=False, is_symlink=True)]
        with self.assertRaises(ValueError):
            copy_file(src,dst,source_dir='/incoming',source_name='a.bin',destination_dir='/outgoing')

class WorkflowTests(unittest.TestCase):
    def test_persistence_sequential_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'workflow.sqlite'
            store = WorkflowStore(path)
            ident = store.add('Backup', [step(name='a'),step(name='b')])
            self.assertTrue(store.claim(ident))
            self.assertFalse(store.claim(ident))
            self.assertTrue(store.advance(ident))
            self.assertEqual(store.get(ident)['cursor'],1)
            self.assertTrue(store.claim(ident))
            self.assertTrue(store.advance(ident))
            self.assertEqual(WorkflowStore(path).get(ident)['state'],'completed')
            self.assertFalse(store.claim(ident))

    def test_interruption_paused_and_manual_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'workflow.sqlite'
            store = WorkflowStore(path)
            ident = store.add('Recovery', [step()])
            store.claim(ident)
            recovered = WorkflowStore(path)
            self.assertEqual(recovered.get(ident)['state'],'paused')
            self.assertEqual(recovered.get(ident)['cursor'],0)
            self.assertFalse(recovered.claim(ident))
            self.assertTrue(recovered.resume(ident))
            self.assertTrue(recovered.claim(ident))
            self.assertTrue(recovered.pause(ident,'source disconnected'))
            self.assertEqual(recovered.get(ident)['state'],'paused')

    def test_reject_bad_workflows(self):
        with tempfile.TemporaryDirectory() as folder:
            store = WorkflowStore(Path(folder)/'db')
            for steps in ([], [step(dst='one')], [dict(action='delete')], [step(name='../escape')]):
                with self.assertRaises((ValueError,KeyError)):
                    store.add('unsafe', steps)
            with self.assertRaises(ValueError): store.add('empty', [step()]*101)

if __name__ == '__main__': unittest.main()
