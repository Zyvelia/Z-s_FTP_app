"""Read Z FTP UI implementation across its modular v2.1.6 modules."""
from pathlib import Path
UI_PARTS = (
    "main_window.py", "remote_tab.py", "tray_controls.py", "file_browser.py",
    "transfers_view.py", "workflow_views.py", "shutdown_controls.py", "profile_controls.py",
)

def ui_text(root):
    return "\n".join((Path(root) / "zftp" / "ui" / name).read_text(encoding="utf-8") for name in UI_PARTS)
