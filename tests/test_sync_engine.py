import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from zftp.services.sync_engine import (Inventory, ScanLimitError, inventory_local, inventory_remote,
                         plan, safe_local_target, relpath, unique_name)

class FakeRemote:
    def __init__(self, paths):
        self.current = '/root'
        self.paths = paths
    def pwd(self): return self.current
    def cwd(self, p):
        if p not in self.paths: raise FileNotFoundError(p)
        self.current = p
    def list_dir(self): return self.paths[self.current]

class SyncTests(unittest.TestCase):
    def test_local_recursion_and_empty_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'a'/'b').mkdir(parents=True)
            (root/'empty').mkdir()
            (root/'a'/'b'/'file.txt').write_bytes(b'abc')
            inv=inventory_local(root)
            self.assertEqual(inv.files, {'a/b/file.txt':3})
            self.assertEqual(inv.folders, frozenset({'a','a/b','empty'}))
    def test_local_symlink_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'normal').mkdir()
            try: (root/'link').symlink_to(root/'normal', target_is_directory=True)
            except OSError: self.skipTest('Cannot create symlink')
            self.assertNotIn('link', inventory_local(root).folders)
            with self.assertRaises(ValueError): safe_local_target(root, 'link/a.txt')
    def test_remote_recursion_and_restore(self):
        def e(name, is_dir=False, size=None, is_link=False):
            return SimpleNamespace(name=name,is_dir=is_dir,size=size,is_link=is_link)
        b=FakeRemote({'/root':[e('nested',True),e('top',False,3),e('link',True,None,True)],
                      '/root/nested':[e('file',False,2),e('empty',True)],
                      '/root/nested/empty':[]})
        inv=inventory_remote(b,'/root')
        self.assertEqual(b.pwd(), '/root')
        self.assertEqual(inv.files,{'top':3,'nested/file':2})
        self.assertEqual(inv.folders,frozenset({'nested','nested/empty'}))
    def test_remote_restore_on_error(self):
        b=FakeRemote({'/root':[SimpleNamespace(name='nested',is_dir=True,is_link=False)],
                      '/root/nested':[SimpleNamespace(name='../bad',is_dir=False,is_link=False)]})
        with self.assertRaises(ValueError): inventory_remote(b,'/root')
        self.assertEqual(b.pwd(), '/root')
    def test_direction_and_conflict(self):
        a=Inventory({'only_local':1,'same':2,'different':3},frozenset({'a'}))
        b=Inventory({'only_remote':1,'same':2,'different':4},frozenset({'b'}))
        result=plan(a,b,'two-way')
        self.assertEqual({(c.kind,c.path) for c in result}, {
            ('upload','only_local'),('download','only_remote'),('same_size','same'),
            ('conflict','different'),('mkdir_remote','a'),('mkdir_local','b')})
        self.assertFalse(any(c.kind=='download' for c in plan(a,b,'upload')))
        self.assertFalse(any(c.kind=='upload' for c in plan(a,b,'download')))
    def test_type_conflict_blocks_child(self):
        a=Inventory({'folder/child':3},frozenset({'folder'}))
        b=Inventory({'folder':5},frozenset())
        actions=plan(a,b,'two-way')
        self.assertEqual([(c.kind,c.path) for c in actions], [('type_conflict','folder')])
    def test_invalid_paths(self):
        for p in ['../attack','/abs','folder//file','x\\y','x\ny']:
            with self.subTest(p=p), self.assertRaises(ValueError): relpath(p)
    def test_unique_rename(self):
        names={'test.txt','test (Z FTP copy).txt'}
        self.assertEqual(unique_name('test.txt',names),'test (Z FTP copy) 2.txt')
    def test_existing_symlink_target(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'file').write_bytes(b'')
            self.assertEqual(safe_local_target(root,'file'),root/'file')
    def test_cancelled_scan(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(InterruptedError): inventory_local(Path(d),lambda:True)

if __name__=='__main__': unittest.main()
