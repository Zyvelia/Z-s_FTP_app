from ui_source_support import ui_text
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from zftp.services.workflows import WorkflowStore
from optional_zmt_adapter.launcher import launch_zftp, executable_candidates


def step(src='a.txt', dst='b.txt'):
    return dict(action='copy_remote', source_endpoint=['SFTP', 'host-a', '22', 'alice'],
                destination_endpoint=['FTPS', 'host-b', '990', 'bob'],
                source_dir='/src', destination_dir='/dst',
                source_name=src, destination_name=dst)


class WorkflowEditingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = WorkflowStore(Path(self.temp.name)/'wf.db')

    def test_edit_unstarted_workflow(self):
        ident = self.db.add('Copy A', [step()])
        self.assertTrue(self.db.replace_steps(ident, [step('a2.txt', 'b2.txt'), step()]))
        self.assertTrue(self.db.rename(ident, 'Copy B'))
        current = self.db.get(ident)
        self.assertEqual(current['name'], 'Copy B')
        self.assertEqual(len(current['steps']), 2)
        self.assertEqual(current['cursor'], 0)

    def test_edit_rejects_paused_workflow_even_at_zero(self):
        ident = self.db.add('Copy A', [step()])
        self.assertTrue(self.db.claim(ident))
        self.assertTrue(self.db.pause(ident, 'review destination'))
        self.assertFalse(self.db.replace_steps(ident, [step('different.txt')]))

    def test_edit_rejects_finished_workflow(self):
        ident = self.db.add('Copy A', [step()])
        self.db.claim(ident)
        self.db.advance(ident)
        self.assertFalse(self.db.replace_steps(ident, [step('other.txt')]))

    def test_duplicate_resets_state_and_cursor(self):
        ident = self.db.add('Old', [step()])
        self.db.claim(ident)
        self.db.advance(ident)
        copy = self.db.duplicate(ident)
        item = self.db.get(copy)
        self.assertEqual(item['state'], 'ready')
        self.assertEqual(item['cursor'], 0)
        self.assertEqual(item['name'], 'Old (copy)')
        self.assertEqual(item['steps'], [step()])

    def test_invalid_edit_does_not_corrupt_existing_steps(self):
        ident = self.db.add('Old', [step()])
        with self.assertRaises(ValueError):
            self.db.replace_steps(ident, [dict(action='delete_remote')])
        self.assertEqual(self.db.get(ident)['steps'], [step()])

    def test_bad_name_rejected(self):
        ident = self.db.add('Old', [step()])
        with self.assertRaises(ValueError):
            self.db.rename(ident, '  ')


class OptionalAdapterTests(unittest.TestCase):
    def test_no_automatic_zmt_dependency(self):
        source = (ROOT/'optional_zmt_adapter'/'launcher.py').read_text()
        self.assertNotIn('import zmt', source.lower())
        self.assertNotIn('shell=True', source)

    def test_source_launch_without_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'main.py').write_text('pass')
            mock = Mock()
            launch_zftp(root, popen=mock)
            args, kwargs = mock.call_args
            self.assertEqual(args[0], [sys.executable, str((root/'main.py').resolve())])
            self.assertFalse(kwargs['shell'])
            self.assertEqual(kwargs['cwd'], str(root.resolve()))

    def test_prefer_exe_if_present(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'dist'/'ZFTP').mkdir(parents=True)
            (root/'dist'/'ZFTP'/'ZFTP.exe').touch()
            (root/'main.py').touch()
            mock = Mock()
            launch_zftp(root, popen=mock)
            self.assertEqual(mock.call_args.args[0], [str((root/'dist'/'ZFTP'/'ZFTP.exe').resolve())])

    def test_missing_source_returns_explicit_error(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                launch_zftp(folder)


class ProjectLayoutTests(unittest.TestCase):
    def test_workflow_editor_is_wired(self):
        source = ui_text(ROOT)
        for name in ('WorkflowDialog(', 'edit_selected_workflow', 'duplicate_selected_workflow',
                     'self.workflows.replace_steps(', 'self.workflows.duplicate('):
            self.assertIn(name, source)

    def test_installer_bumped(self):
        spec = (ROOT/'packaging'/'ZFTP.iss').read_text()
        self.assertIn('2.1.8', spec)
        self.assertIn('ZFTP_Setup_v2.1.8', spec)

if __name__ == '__main__':
    unittest.main()
