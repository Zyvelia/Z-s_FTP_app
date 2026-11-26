"""Non-secret connection profiles and OS-keychain-backed optional passwords."""
from __future__ import annotations
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from zftp.services.storage import portable_data

APP_SERVICE = 'ZFTP.connections.v1'


def default_storage() -> Path:
    portable = portable_data()
    if portable is not None:
        return portable / 'profiles.json'
    root = Path(os.environ.get('APPDATA') or (Path.home() / '.config')) / 'ZFTP'
    return root / 'profiles.json'


@dataclass
class Profile:
    id: str
    name: str
    host: str
    port: int
    protocol: str
    username: str
    favorite: bool = False
    key_path: str = ''
    save_password: bool = False
    bandwidth_kib: int = 0
    remote_path: str = ''
    local_path: str = ''

    @classmethod
    def create(cls, name, host, port, protocol, username, favorite=False, key_path='', save_password=False,
               *, bandwidth_kib=0, remote_path='', local_path=''):
        profile = cls(str(uuid.uuid4()), name.strip(), host.strip(), int(port), protocol, username.strip(),
                      bool(favorite), key_path.strip(), bool(save_password), int(bandwidth_kib),
                      remote_path.strip(), local_path.strip())
        profile.validate()
        return profile

    def validate(self):
        if not self.id or not self.name or not self.host:
            raise ValueError('Name and host are required')
        if self.protocol not in ('FTP', 'FTPS', 'SFTP'):
            raise ValueError('Unsupported protocol')
        if not 1 <= self.port <= 65535:
            raise ValueError('Port must be 1–65535')
        if not 0 <= self.bandwidth_kib <= 1048576:
            raise ValueError('Connection limit must be between 0 and 1,048,576 KiB/s')
        if self.remote_path and (not self.remote_path.startswith('/') or any(ord(c) < 32 for c in self.remote_path)):
            raise ValueError('Remote starting folder must be an absolute server path')
        if self.key_path and self.protocol != 'SFTP':
            raise ValueError('SSH keys only apply to SFTP')


class ProfileStore:
    def __init__(self, path: Path | None = None, keyring_module=None):
        self.path = path or default_storage()
        self._keyring = keyring_module

    def _keys(self):
        if self._keyring is None:
            import keyring
            return keyring
        return self._keyring

    def list(self) -> list[Profile]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict) or payload.get('version') != 1 or not isinstance(payload.get('profiles'), list):
            raise ValueError('Unsupported profiles database format')
        profiles = [Profile(**item) for item in payload['profiles']]
        for p in profiles: p.validate()
        return sorted(profiles, key=lambda p: (not p.favorite, p.name.casefold()))

    def _write(self, profiles: list[Profile]):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps({'version': 1, 'profiles': [asdict(p) for p in profiles]}, indent=2)
        fd, filename = tempfile.mkstemp(prefix='.profiles-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(filename, self.path)
            if os.name != 'nt': os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(filename): os.unlink(filename)

    def put(self, profile: Profile, password: str | None = None):
        profile.validate()
        existing = self.list()
        old = next((p for p in existing if p.id == profile.id), None)
        # Credential changes happen before changing the profile file: no silent plaintext fallback.
        if profile.save_password:
            if portable_data() is not None:
                raise ValueError('Portable profiles cannot save passwords in the system credential store.')
            if password is not None:
                self._keys().set_password(APP_SERVICE, profile.id, password)
            elif not old or not old.save_password:
                raise ValueError('Enter a password before enabling password storage')
        elif old and old.save_password and portable_data() is None:
            self._keys().delete_password(APP_SERVICE, profile.id)
        self._write([p for p in existing if p.id != profile.id] + [profile])

    def add_imported(self, profiles):
        existing = self.list()
        keys = {(p.name.casefold(), p.protocol, p.host.casefold(), p.port, p.username) for p in existing}
        added = 0
        for profile in profiles:
            profile.validate()
            key = profile.name.casefold(), profile.protocol, profile.host.casefold(), profile.port, profile.username
            if key not in keys:
                existing.append(profile)
                keys.add(key)
                added += 1
        if added:
            self._write(existing)
        return added

    def delete(self, profile_id: str):
        existing = self.list()
        old = next((p for p in existing if p.id == profile_id), None)
        if old and old.save_password and portable_data() is None:
            self._keys().delete_password(APP_SERVICE, profile_id)
        self._write([p for p in existing if p.id != profile_id])

    def get_password(self, profile: Profile) -> str:
        if not profile.save_password or portable_data() is not None:
            return ''
        return self._keys().get_password(APP_SERVICE, profile.id) or ''
