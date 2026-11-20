"""Structure and import regressions for the reorganized v2.1.6 source."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class OrganizationTests(unittest.TestCase):
    def test_small_launcher_and_build_scripts_preserved(self):
        entry = ROOT / 'main.py'
        self.assertTrue(entry.exists())
        self.assertLess(entry.stat().st_size, 2500)
        for script in ('start_windows.bat','build_exe.bat','build_installer.bat','build_all.bat'):
            self.assertTrue((ROOT/script).is_file(),script)

    def test_source_modules_grouped_into_packages(self):
        for group in ('protocols','transfers','servers','services','ui'):
            self.assertTrue((ROOT/'zftp'/group/'__init__.py').is_file(),group)
        self.assertTrue((ROOT/'zftp/ui/main_window.py').is_file())
        self.assertTrue((ROOT/'zftp/protocols/ftp_engine.py').is_file())
        self.assertFalse((ROOT/'ftp_engine.py').exists())

    def test_relative_package_imports_are_resolvable(self):
        modules = {'.'.join(p.relative_to(ROOT).with_suffix('').parts)
                   for p in (ROOT/'zftp').rglob('*.py')}
        for p in (ROOT/'zftp').rglob('*.py'):
            tree = ast.parse(p.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('zftp.'):
                    self.assertIn(node.module, modules, f'{p}: {node.module}')

    def test_packaging_artwork_and_docs_preserved(self):
        for item in ('assets/zftp_icon.ico','assets/installer_sidebar.bmp',
                     'packaging/ZFTP.iss','docs/SECURITY.md','docs/RELEASE_CHECKLIST.md',
                     'docs/changelog/CHANGELOG_v2.1.4.md',
                     'optional_zmt_adapter/launcher.py'):
            self.assertTrue((ROOT/item).is_file(),item)

    def test_installer_and_application_versions(self):
        self.assertIn('2.1.6', (ROOT/'packaging/ZFTP.iss').read_text())
        self.assertIn('v2.1.6', (ROOT/'zftp/ui/main_window.py').read_text())

if __name__ == '__main__':
    unittest.main()
