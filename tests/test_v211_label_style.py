"""Regression: card labels should never paint opaque black rectangles."""
import unittest
from zftp.ui.ui_theme import APP_STYLESHEET

class TransparentLabelTests(unittest.TestCase):
    def test_plain_labels_do_not_paint_opaque_background(self):
        self.assertIn('QLabel { background-color: transparent; border: none; }', APP_STYLESHEET)
    def test_card_headings_are_transparent(self):
        self.assertIn('QLabel#sectionHeading, QLabel#cardTitle', APP_STYLESHEET)
        self.assertIn('background-color: transparent; border: none;', APP_STYLESHEET)
    def test_corner_matches_table_header(self):
        self.assertIn('QTableCornerButton::section', APP_STYLESHEET)
    def test_special_badges_still_styled(self):
        for selector in ('QLabel#statusBadge', 'QLabel#stageRibbon', 'QLabel#footerConnection'):
            self.assertIn(selector, APP_STYLESHEET)

if __name__=='__main__': unittest.main()
