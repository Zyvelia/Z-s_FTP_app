"""Qt-free regression checks for the v2.1.6 UI extraction and composition."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "zftp" / "ui"
EXTRACTED = {
    "tray_controls": ("TrayControlsMixin", "setup_tray_controls", "quit_from_tray"),
    "file_browser": ("FileBrowserMixin", "set_local_path", "run_session_job", "remote_context_menu"),
    "transfers_view": ("TransfersViewMixin", "render_queue", "pump_queue", "on_transfer_progress"),
    "workflow_views": ("WorkflowViewsMixin", "refresh_workflows", "start_workflow_step", "refresh_history"),
    "shutdown_controls": ("ShutdownControlsMixin", "closeEvent", "_advance_shutdown"),
    "profile_controls": ("ProfileControlsMixin", "refresh_profiles", "manage_profiles"),
}


def classes(path):
    return {node.name: node for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, ast.ClassDef)}


class ModularUITests(unittest.TestCase):
    def test_main_window_is_composed_without_duplicating_state(self):
        path = UI / "main_window.py"
        main = classes(path)["MainWindow"]
        bases = [base.id for base in main.bases if isinstance(base, ast.Name)]
        self.assertEqual(bases, [spec[0] for spec in EXTRACTED.values()] + ["QMainWindow"])
        self.assertLess(len(path.read_text().splitlines()), 950)
        names = [node.name for node in main.body if isinstance(node, ast.FunctionDef)]
        self.assertEqual(names.count("__init__"), 1)
        source = path.read_text()
        for snippet in ("self.sessions =", "self.queue =", "self.store ="):
            # Annotation exists for sessions, simple assignment for queue/store.
            if snippet != "self.sessions =":
                self.assertIn(snippet, source)
        self.assertIn("self.sessions: dict[RemoteTab, ConnectionSession]", source)
        for module in EXTRACTED:
            self.assertIn(f"from zftp.ui.{module} import", source)

    def test_features_still_live_in_one_inherited_window(self):
        for module, (class_name, *methods) in EXTRACTED.items():
            nodes = classes(UI / f"{module}.py")
            self.assertIn(class_name, nodes)
            names = {n.name for n in nodes[class_name].body if isinstance(n, ast.FunctionDef)}
            for method in methods:
                self.assertIn(method, names)
            self.assertNotIn("__init__", names, "Mixins must share the original state")

    def test_tab_independent_from_main_window(self):
        source = (UI / "remote_tab.py").read_text(encoding="utf-8")
        self.assertIn('create_file_table("remote")', source)
        self.assertNotIn('from zftp.ui.main_window import', source)
        self.assertNotIn('MainWindow.make_table(', source)
        self.assertIn("class RemoteTab(QWidget)", source)

    def test_no_duplicate_method_names_between_modules(self):
        seen = {}
        for module, (class_name, *_) in EXTRACTED.items():
            for node in classes(UI / f"{module}.py")[class_name].body:
                if isinstance(node, ast.FunctionDef):
                    self.assertNotIn(node.name, seen, f"Duplicate {node.name} in {module} and {seen.get(node.name)}")
                    seen[node.name] = module
        for node in classes(UI / "main_window.py")["MainWindow"].body:
            if isinstance(node, ast.FunctionDef):
                self.assertNotIn(node.name, seen)

    def test_worker_still_reports_success_progress_and_failures(self):
        source = (UI / "ui_workers.py").read_text(encoding="utf-8")
        for fragment in ('class Events(QObject)', 'class Job(QRunnable)',
                         'self.events.done.emit', 'self.events.failed.emit',
                         'self.events.progress.emit'):
            self.assertIn(fragment, source)

    def test_preserved_build_metadata(self):
        self.assertIn('AppVersion "2.1.12"', (ROOT / 'packaging' / 'ZFTP.iss').read_text())
        self.assertIn('ZFTP_Setup_v2.1.12', (ROOT / 'build_installer.bat').read_text())
        self.assertTrue((ROOT / 'optional_zmt_adapter' / 'launcher.py').exists())
        self.assertTrue((ROOT / 'docs/changelog/CHANGELOG_v2.1.6.md').exists())


if __name__ == "__main__":
    unittest.main()
