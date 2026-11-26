"""Z FTP entry point. Implementation lives in the zftp package."""
from pathlib import Path
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from zftp.ui.main_window import MainWindow
from zftp.services.storage import portable_data


APP_NAME = "Z FTP"
WINDOWS_APP_ID = "Zyvelia.ZFTP"
WINDOWS_APP_KEY = f"Software\\Classes\\AppUserModelId\\{WINDOWS_APP_ID}"


def register_windows_notification_identity(icon: Path):
    if sys.platform != "win32":
        return
    import ctypes
    import winreg

    try:
        # Source and portable builds may run before an installer has created the entry.
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, WINDOWS_APP_KEY, 0,
                                winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            try:
                winreg.QueryValueEx(key, "DisplayName")
            except FileNotFoundError:
                winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon))
    except OSError:
        pass

    # Windows associates the tray window with the sender before Qt creates it.
    set_app_id = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID
    set_app_id.argtypes = (ctypes.c_wchar_p,)
    set_app_id.restype = ctypes.c_long
    set_app_id(WINDOWS_APP_ID)


def main():
    icon = Path(__file__).resolve().parent / "assets" / "zftp_icon.png"
    if portable_data() is None:
        register_windows_notification_identity(icon)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setWindowIcon(QIcon(str(icon)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
