"""Optional ZMT integration: launch standalone Z FTP without importing ZMT.

Call launch_zftp() from a ZMT module or another program. No hardcoded ZMT
folders, no modifications to the user's existing marketplace registry.
"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys


def executable_candidates(root=None):
    """Look for the packaged EXE or source entry point beside this adapter."""
    project = Path(root).resolve() if root is not None else Path(__file__).resolve().parent.parent
    return [project/'dist'/'ZFTP'/'ZFTP.exe', project/'ZFTP.exe', project/'main.py']


def launch_zftp(root=None, *, popen=subprocess.Popen):
    project = Path(root).resolve() if root is not None else Path(__file__).resolve().parent.parent
    for path in executable_candidates(project):
        if not path.is_file():
            continue
        if path.suffix.lower() == '.exe':
            command = [str(path)]
        else:
            command = [sys.executable, str(path)]
        # cwd is the project root, without user-supplied shell fragments.
        return popen(command, cwd=str(project), shell=False)
    raise FileNotFoundError('Z FTP executable or main.py not found; build/install Z FTP first')
