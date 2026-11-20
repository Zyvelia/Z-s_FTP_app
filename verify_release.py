"""Offline release preflight. Does not claim Windows or live-server validation."""
from __future__ import annotations
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def check_layout():
    required = ('main.py', 'zftp/ui/main_window.py', 'zftp/protocols/ftp_engine.py', 'zftp/protocols/sftp_engine.py', 'zftp/servers/server_core.py',
                'zftp/servers/sftp_server_core.py', 'zftp/transfers/reliable_transfer.py', 'zftp/transfers/cross_server.py',
                'zftp/services/workflows.py', 'zftp/services/scheduler.py', 'zftp/services/transfer_history.py',
                'optional_zmt_adapter/launcher.py', 'build_exe.bat',
                'build_installer.bat', 'packaging/ZFTP.iss', 'requirements.txt')
    missing = [item for item in required if not (ROOT/item).is_file()]
    if missing:
        raise RuntimeError('Missing release files: ' + ', '.join(missing))
    installer = (ROOT/'packaging/ZFTP.iss').read_text(encoding='utf-8')
    assert '#define AppVersion "2.1.6"' in installer
    assert 'OutputBaseFilename=ZFTP_Setup_v2.1.6' in installer
    assert 'PrivilegesRequired=lowest' in installer
    assert 'Source: "..\\dist\\ZFTP\\*"' in installer
    assert 'ZFTP.exe' in (ROOT/'build_exe.bat').read_text()
    assert 'ZFTP_Setup_v2.1.6.exe' in (ROOT/'build_installer.bat').read_text()
    print('Release file layout and installer metadata: PASS')


def check_syntax():
    paths = list(ROOT.glob('*.py')) + list((ROOT/'zftp').rglob('*.py')) + list((ROOT/'tests').glob('test_*.py')) + list((ROOT/'tools').glob('*.py'))
    paths += list((ROOT/'optional_zmt_adapter').glob('*.py'))
    for path in paths:
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    print(f'Python syntax (AST): PASS ({len(paths)} files)')


def run_tests():
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'], cwd=ROOT, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)
    print('Offline regression suite: PASS (see skipped dependency-dependent tests above)')


if __name__ == '__main__':
    check_layout()
    check_syntax()
    run_tests()
    print('NOT VERIFIED: Windows EXE/installer execution or real network interoperability.')
