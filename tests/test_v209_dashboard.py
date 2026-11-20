"""Static regression coverage for v2.1.6 dashboard wiring, without Qt dependency."""
from ui_source_support import ui_text

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ui_text(ROOT)
CSS = (ROOT / 'zftp/ui/ui_theme.py').read_text(encoding='utf-8')

class DashboardTests(unittest.TestCase):
    def test_sidebar_controls_reach_existing_views(self):
        for text in ('appSidebar', 'navigate_sidebar', 'ftp_server', 'sftp_server',
                     'schedules', 'workflows', 'browser', 'transfers', 'settings'):
            self.assertIn(text, MAIN)
        self.assertIn('self.main_sections.setCurrentIndex(0)', MAIN)
        self.assertIn('self.main_sections.tabBar().setVisible(index != 0)', MAIN)

    def test_browser_transfer_buttons_use_existing_callbacks(self):
        self.assertIn('self.center_upload.clicked.connect(self.upload)', MAIN)
        self.assertIn('self.center_download.clicked.connect(self.download_active)', MAIN)
        self.assertIn('self.download(tab)', MAIN)
        self.assertIn('self.file_browser_splitter = splitter', MAIN)

    def test_transfer_stages_are_wired_to_job_state(self):
        for stage in ('Uploading', 'Verifying', 'Renaming', 'Completed'):
            self.assertIn(stage, MAIN)
        self.assertIn('self.stage_ribbon.setText', MAIN)
        self.assertIn("job.status == 'Running'", MAIN)

    def test_style_and_build_scripts(self):
        for selector in ('appSidebar', 'browserCard', 'transferCard', 'stageRibbon'):
            self.assertIn(selector, CSS)
        self.assertIn('2.1.10', (ROOT / 'packaging' / 'ZFTP.iss').read_text())
        self.assertTrue((ROOT / 'build_all.bat').is_file())
        self.assertTrue((ROOT / 'assets' / 'zftp_icon.ico').is_file())

if __name__ == '__main__':
    unittest.main()
