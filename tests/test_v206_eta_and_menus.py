from ui_source_support import ui_text
import unittest
from pathlib import Path
from zftp.transfers.eta_estimator import ETAEstimator, format_eta

ROOT = Path(__file__).resolve().parents[1]

class ETATests(unittest.TestCase):
    def test_stable_speed(self):
        e = ETAEstimator()
        self.assertEqual(e.update(0, 1000, 0), (0.0, None))
        speed, eta = e.update(400, 1000, 4)
        self.assertAlmostEqual(speed, 100)
        self.assertAlmostEqual(eta, 6)

    def test_stall_then_recovery(self):
        e = ETAEstimator()
        e.update(0, 1000, 0)
        e.update(400, 1000, 4)
        speed, eta = e.update(400, 1000, 13)
        self.assertEqual(speed, 0)
        self.assertIsNone(eta)
        _, recovered = e.update(500, 1000, 14)
        self.assertIsNotNone(recovered)

    def test_resume_resets_old_speed(self):
        e = ETAEstimator()
        e.update(0, 1000, 0)
        e.update(500, 1000, 5)
        self.assertEqual(e.update(100, 1000, 6), (0., None))
        self.assertIsNotNone(e.update(300, 1000, 9)[1])

    def test_unknown_and_completed(self):
        e = ETAEstimator()
        self.assertEqual(e.update(20, None, 0), (0., None))
        self.assertEqual(e.update(100, 100, 1), (0., 0.))

    def test_eta_format(self):
        self.assertEqual(format_eta(None), 'Calculating…')
        self.assertEqual(format_eta(60), '01:00')
        self.assertEqual(format_eta(3661), '1:01:01')
        self.assertEqual(format_eta(90000), '1d 01h')

    def test_menus_and_safe_delete_source(self):
        main = ui_text(ROOT)
        for fragment in ('def local_context_menu(', 'def remote_context_menu(',
                         'QFile.moveToTrash', 'def delete_remote_entry(',
                         'delete_remote_tree', 'ETAEstimator()'):
            self.assertIn(fragment, main)
        for file in ('zftp/protocols/ftp_engine.py', 'zftp/protocols/sftp_engine.py'):
            self.assertIn('def remove_empty_dir(', (ROOT/file).read_text(encoding='utf-8'))

if __name__ == '__main__': unittest.main()
