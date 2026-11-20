"""Static layout invariants, run even when Qt is unavailable."""
from ui_source_support import ui_text

from pathlib import Path
import unittest
MAIN = ui_text(Path(__file__).resolve().parents[1])

class LayoutTests(unittest.TestCase):
    def test_menus_keep_toolbar_compact(self):
        self.assertIn("self.more_actions.setMenu(self.manage_menu)", MAIN)
        self.assertIn("self.local_tools.setMenu(self.local_menu)", MAIN)
        self.assertNotIn("layout.addLayout(manage_actions)", MAIN)

    def test_advanced_settings_hidden_initially(self):
        self.assertIn("self.transfer_options.setVisible(False)", MAIN)
        self.assertIn("advanced.addWidget(self.verify_checksums)", MAIN)
        self.assertIn("self.transfer_options_toggle.toggled.connect(self.transfer_options.setVisible)", MAIN)

    def test_connection_fields_do_not_stretch_connect_button(self):
        self.assertIn("self.connect_button.setMinimumWidth(115)", MAIN)
        self.assertIn("conn.addWidget(self.connect_button, 0, 5)", MAIN)
        self.assertIn("self.ssh_row.setVisible(protocol == 'SFTP')", MAIN)

    def test_remote_callbacks_remain(self):
        for identifier in ('self.rename_btn', 'self.edit_btn', 'self.search_btn', 'self.permissions_btn', 'self.copy_to_server'):
            self.assertIn(identifier, MAIN)
