"""Parsers and bounded read-only operations for PS3 file tools."""
from ftplib import Error as FTPError
import posixpath
import re
from struct import unpack_from

from zftp.services.param_sfo import ParamSfo
from zftp.services.remote_management import validate_name

CONFIG_LINE = re.compile(r'^([ \t]*[^\s#;=][^=\r\n]*?[ \t]*=[ \t]*)([^\r\n]*)(\r?\n|\r)?$')
MAX_SCAN = 200


def inspect_pkg(header, listing_size):
    if len(header) < 128 or header[:4] != b'\x7fPKG':
        raise ValueError('Expected a PS3 PKG header.')
    declared = unpack_from('>Q', header, 24)[0]
    if declared < 128:
        raise ValueError('Invalid package size in header.')
    content = header[48:96].split(b'\0', 1)[0]
    if not re.fullmatch(rb'[A-Za-z0-9_-]+', content):
        raise ValueError('Invalid package Content ID.')
    return [('Content ID', content.decode('ascii')), ('Declared bytes', str(declared)),
            ('Remote bytes', 'Unknown' if listing_size is None else str(listing_size)),
            ('Size comparison', 'Unavailable' if listing_size is None else
             'Matches' if declared == listing_size else 'Mismatch')]


def plugin_paths(text):
    paths = []
    for line in text.splitlines():
        path = line.strip()
        if not path or path.startswith(('#', ';')):
            continue
        if not path.startswith('/') or posixpath.normpath(path) != path or not path.lower().endswith('.sprx'):
            raise ValueError(f'Expected an absolute .sprx path: {path}')
        for part in path[1:].split('/'):
            validate_name(part)
        if path in paths:
            raise ValueError(f'Duplicate plugin: {path}')
        paths.append(path)
    return paths


def missing_plugins(backend, paths):
    initial = backend.pwd()
    missing = []
    try:
        for path in paths:
            folder, name = posixpath.split(path)
            try:
                backend.cwd(folder)
                if any(entry.name == name and not entry.is_dir and not entry.is_link for entry in backend.list_dir()):
                    continue
            except (OSError, FTPError):
                pass
            missing.append(path)
    finally:
        backend.cwd(initial)
    return missing


def scan_game_metadata(backend, folder):
    initial = backend.pwd()
    rows = []
    try:
        backend.cwd(folder)
        root = backend.pwd()
        directories = [entry.name for entry in backend.list_dir() if entry.is_dir and not entry.is_link]
        for name in directories[:MAX_SCAN]:
            validate_name(name)
            try:
                backend.cwd(posixpath.join(root, name))
                entry = next((item for item in backend.list_dir()
                              if item.name.upper() == 'PARAM.SFO' and not item.is_dir and not item.is_link), None)
                if entry is None:
                    continue
                doc = ParamSfo(backend.read_bytes(validate_name(entry.name), 1024 * 1024))
                fields = {field.name: field.value for field in doc.fields}
                rows.append((name, fields.get('TITLE', ''), fields.get('TITLE_ID', ''),
                             fields.get('CATEGORY', ''), fields.get('APP_VER') or fields.get('VERSION', ''), ''))
            except Exception as exc:
                rows.append((name, '', '', '', '', str(exc)))
        return rows, len(directories) > MAX_SCAN
    finally:
        backend.cwd(initial)
