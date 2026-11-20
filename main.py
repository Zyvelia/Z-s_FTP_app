"""Z FTP entry point. Implementation lives in the zftp package."""
from pathlib import Path
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from zftp.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    icon = Path(__file__).resolve().parent / "assets" / "zftp_icon.png"
    app.setWindowIcon(QIcon(str(icon)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
