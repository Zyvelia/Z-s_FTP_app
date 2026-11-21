"""Static regression checks for the v2.0.4 space-saving browser layout."""
from ui_source_support import ui_text

import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
MAIN = ui_text(ROOT)

class RemotePanelLayoutTests(unittest.TestCase):
    def test_remote_table_is_primary_space_consumer(self):
        self.assertIn('self.table.setMinimumHeight(220)', MAIN)
        self.assertIn('layout.addWidget(self.table, 1)', MAIN)
        self.assertIn('self.tabs.setMinimumHeight(280)', MAIN)
        self.assertIn('layout.addWidget(splitter, 8)', MAIN)
    def test_status_and_progress_share_one_row(self):
        self.assertIn('progress_row.addWidget(self.status)', MAIN)
        self.assertIn('progress_row.addWidget(self.progress, 1)', MAIN)
        self.assertIn('progress_row.addWidget(self.cancel)', MAIN)
        self.assertNotIn('layout.addWidget(self.status)', MAIN.split('class MainWindow')[0])
    def test_activity_opens_in_a_resizable_dialog(self):
        self.assertIn('self.transfer_details = QDialog(self)', MAIN)
        self.assertIn('details_layout.addWidget(self.bottom_tabs)', MAIN)
        for name in ('Activity', 'History', 'Workflows'):
            self.assertIn("'"+name+"'", MAIN)
    def test_ps3_listing_fix_preserved(self):
        engine=(ROOT/'zftp/protocols/ftp_engine.py').read_text(encoding='utf-8')
        self.assertIn('latin-1',engine)
        self.assertTrue((ROOT/'tests'/'test_ps3_listing_fallback.py').exists())

if __name__ == '__main__': unittest.main()
