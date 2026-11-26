"""Per-install storage location for portable builds."""

from pathlib import Path
import sys


def portable_data():
    root = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parents[2])
    if (root / 'zftp-portable.ini').is_file():
        return root / 'data'
    return None
