"""Packaging and branding regressions for Z FTP v2.0.8."""
import unittest
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

class BrandingTests(unittest.TestCase):
    def test_assets_are_valid_images(self):
        for filename in ('zftp_icon.ico','zftp_icon.png','installer_sidebar.bmp','installer_small.bmp'):
            with Image.open(ROOT/'assets'/filename) as image:
                image.verify()

    def test_scripts_reference_assets(self):
        exe=(ROOT/'build_exe.bat').read_text()
        installer=(ROOT/'packaging/ZFTP.iss').read_text()
        self.assertIn('zftp_icon.ico',exe)
        self.assertIn('zftp_icon.png',exe)
        self.assertIn('WizardImageFile',installer)
        self.assertIn('WizardSmallImageFile',installer)
        self.assertIn('SetupIconFile',installer)
        self.assertIn('2.1.6',installer)

    def test_build_all_chains_both(self):
        s=(ROOT/'build_all.bat').read_text()
        self.assertIn('call build_exe.bat',s)
        self.assertIn('call build_installer.bat',s)

if __name__=='__main__': unittest.main()
