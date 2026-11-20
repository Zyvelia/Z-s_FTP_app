"""Qt-free regression checks for visual polish and accurate transfer stage labels."""
from ui_source_support import ui_text

import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MAIN=ui_text(ROOT)
CSS=(ROOT/'zftp/ui/ui_theme.py').read_text(encoding='utf-8')
class PolishTests(unittest.TestCase):
    def test_icons_use_scalable_native_assets(self):
        self.assertIn('self.style().standardIcon(nav_icons[key])',MAIN)
        self.assertIn('button.setIconSize(QSize(19, 19))',MAIN)
    def test_progress_bar_marks_bytes_not_final_completion(self):
        self.assertIn('self.queue_table.setCellWidget(row, 3, widget)',MAIN)
        self.assertIn('100% sent · {stage}',MAIN)
        self.assertIn("item.status == 'Completed'",MAIN)
    def test_footer_reflects_running_jobs(self):
        for name in ('refresh_live_footer','footer_connection','footer_speed','footer_jobs'):
            self.assertIn(name,MAIN)
        self.assertIn('for job in running if job.direction',MAIN)
    def test_style_and_build_version(self):
        for name in ('jobProgress','footerConnection','appSidebar'):
            self.assertIn(name,CSS)
        self.assertIn('2.1.10',(ROOT/'packaging/ZFTP.iss').read_text())
if __name__=='__main__': unittest.main()
