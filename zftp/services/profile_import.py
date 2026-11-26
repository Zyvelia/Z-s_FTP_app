"""Import connection metadata from FileZilla XML and OpenSSH configuration."""

from fnmatch import fnmatchcase
from pathlib import Path
import shlex
import xml.etree.ElementTree as ET

from zftp.services.profiles import Profile

IMPORT_LIMIT = 4 * 1024 * 1024


def filezilla_path(value):
    if not value or value.startswith('/'):
        return value
    header = value.split(' ', 2)
    if len(header) < 2 or header[:2] != ['1', '0']:
        return ''
    remaining = header[2] if len(header) == 3 else ''
    parts = []
    while remaining:
        length, sep, data = remaining.partition(' ')
        if not sep or not length.isdecimal() or len(length) > 8:
            return ''
        count = int(length)
        part, tail = data[:count], data[count:]
        if len(part) != count or '/' in part or (tail and not tail.startswith(' ')):
            return ''
        parts.append(part)
        remaining = tail[1:]
    return '/' + '/'.join(parts)


def filezilla_profiles(text):
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('FileZilla export must not contain XML entities or a document type.')
    root = ET.fromstring(text)
    if root.tag != 'FileZilla3':
        raise ValueError('Select a FileZilla site export XML file.')
    protocols = {'0': 'FTP', '1': 'SFTP', '4': 'FTPS', '6': 'FTP'}
    for site in root.findall('.//Server'):
        protocol = protocols.get(site.findtext('Protocol', '0'))
        if protocol is None:
            continue
        host = site.findtext('Host', '').strip()
        try:
            yield Profile.create(site.findtext('Name', '').strip() or host, host,
                                 int(site.findtext('Port', '') or (22 if protocol == 'SFTP' else 21)),
                                 protocol, site.findtext('User', ''),
                                 remote_path=filezilla_path(site.findtext('RemoteDir', '')),
                                 local_path=site.findtext('LocalDir', ''))
        except ValueError:
            continue


def openssh_profiles(text):
    sections = [(['*'], {})]
    aliases = {}
    active = sections[0][1]
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        key, separator, value = parts[0].partition('=')
        rest = parts[1] if len(parts) > 1 else ''
        value = ' '.join((value, rest)) if separator else rest
        key = key.casefold()
        if key not in ('host', 'match', 'hostname', 'port', 'user', 'identityfile'):
            continue
        try:
            values = shlex.split(value.strip().lstrip('='), comments=True, posix=True)
        except ValueError:
            continue
        if key == 'match':
            active = None
        elif key == 'host':
            active = {}
            sections.append((values, active))
            for value in values:
                if value and not any(character in value for character in ('*', '?', '!', '[')):
                    aliases.setdefault(value, None)
        elif active is not None and values:
            active.setdefault(key, values[0])
    for alias in aliases:
        options = {}
        for patterns, section in sections:
            if not any(fnmatchcase(alias.casefold(), p[1:].casefold()) for p in patterns if p.startswith('!')) and any(
                    fnmatchcase(alias.casefold(), p.casefold()) for p in patterns if not p.startswith('!')):
                for key, value in section.items():
                    options.setdefault(key, value)
        host = options.get('hostname', alias).replace('%h', alias)
        username = options.get('user', '')
        if '%' in host or '%' in username:
            continue
        key = options.get('identityfile', '').replace('%d', str(Path.home())).replace('%h', host).replace('%r', username)
        if key == 'none' or '%' in key:
            key = ''
        try:
            yield Profile.create(alias, host, int(options.get('port', 22)), 'SFTP', username,
                                 key_path=str(Path(key).expanduser()) if key else '')
        except (ValueError, RuntimeError):
            continue


def load_profiles(path):
    with Path(path).open('rb') as stream:
        data = stream.read(IMPORT_LIMIT + 1)
    if len(data) > IMPORT_LIMIT:
        raise ValueError('The import file exceeds 4 MiB.')
    text = data.decode('utf-8-sig')
    parser = filezilla_profiles if text.lstrip().startswith('<') else openssh_profiles
    profiles = list(parser(text))
    if not profiles:
        raise ValueError('No supported connections were found in this file.')
    return profiles
