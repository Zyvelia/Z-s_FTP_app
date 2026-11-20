"""Pure planning and guarded inventory discovery for non-destructive folder synchronization.

No tree traversal happens in the GUI thread. Remote backend is exclusively owned
by the calling worker. No overwrite or delete operations are produced.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import posixpath
import stat

MAX_ENTRIES = 100_000
MAX_DEPTH = 64


class ScanLimitError(ValueError):
    pass


def segment(name: str) -> str:
    if (not name or name in ('.', '..') or '/' in name or '\\' in name or
            '\r' in name or '\n' in name or '\x00' in name):
        raise ValueError(f'Unsafe file name: {name!r}')
    return name


def relpath(relative: str) -> str:
    parts = relative.split('/')
    if not parts or any(not p for p in parts):
        raise ValueError('Empty relative path segment')
    return '/'.join(segment(p) for p in parts)


@dataclass(frozen=True)
class Inventory:
    files: dict[str, int | None]
    folders: frozenset[str]


@dataclass(frozen=True)
class Change:
    kind: str  # upload, download, conflict, same_size, type_conflict, mkdir_remote, mkdir_local
    path: str
    local_size: int | None = None
    remote_size: int | None = None


def inventory_local(root: Path, cancelled=lambda: False, *, selected=None) -> Inventory:
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError('Root must be a non-symlink directory')
    files: dict[str, int | None] = {}
    folders: set[str] = set()
    stack = [(root, '', 0)]
    seen = 0
    while stack:
        if cancelled(): raise InterruptedError('Scan cancelled')
        folder, prefix, depth = stack.pop()
        if depth > MAX_DEPTH: raise ScanLimitError('Local tree exceeds maximum depth')
        for child in folder.iterdir():
            if cancelled(): raise InterruptedError('Scan cancelled')
            if child.is_symlink() or (depth == 0 and selected is not None and child.name not in selected): continue
            name = segment(child.name)
            rel = f'{prefix}/{name}' if prefix else name
            seen += 1
            if seen > MAX_ENTRIES: raise ScanLimitError('Local tree exceeds file count limit')
            if child.is_dir():
                folders.add(rel)
                stack.append((child, rel, depth + 1))
            elif child.is_file():
                files[rel] = child.stat().st_size
    return Inventory(files, frozenset(folders))


def inventory_remote(backend, root: str, cancelled=lambda: False, *, selected=None) -> Inventory:
    """Walk only within root; restore original cwd even if a scan fails."""
    root = posixpath.normpath(root)
    if not root.startswith('/'):
        raise ValueError('Remote root must be an absolute path')
    original = backend.pwd()
    files: dict[str, int | None] = {}
    folders: set[str] = set()
    stack = [(root, '', 0)]
    seen = 0
    try:
        while stack:
            if cancelled(): raise InterruptedError('Scan cancelled')
            dirname, prefix, depth = stack.pop()
            if depth > MAX_DEPTH: raise ScanLimitError('Remote tree exceeds maximum depth')
            backend.cwd(dirname)
            for entry in backend.list_dir():
                if cancelled(): raise InterruptedError('Scan cancelled')
                if getattr(entry, 'is_link', False) or (depth == 0 and selected is not None and entry.name not in selected): continue
                name = segment(entry.name)
                rel = f'{prefix}/{name}' if prefix else name
                seen += 1
                if seen > MAX_ENTRIES: raise ScanLimitError('Remote tree exceeds file count limit')
                if entry.is_dir:
                    folders.add(rel)
                    stack.append((posixpath.join(dirname, name), rel, depth + 1))
                else:
                    files[rel] = entry.size
        return Inventory(files, frozenset(folders))
    finally:
        backend.cwd(original)


def plan(local: Inventory, remote: Inventory, mode: str) -> list[Change]:
    """Upload-only, download-only or two-way additions; existing differing data stays intact.

    Same-size matches are NOT verified duplicates. Never overwrite or delete.
    """
    if mode not in ('upload', 'download', 'two-way'):
        raise ValueError('Invalid sync mode')
    changes: list[Change] = []
    dirs = sorted(local.folders | remote.folders, key=lambda p: (p.count('/'), p))
    for path in dirs:
        relpath(path)
        if path in local.files or path in remote.files:
            changes.append(Change('type_conflict', path))
        elif path in local.folders and path not in remote.folders and mode in ('upload', 'two-way'):
            changes.append(Change('mkdir_remote', path))
        elif path in remote.folders and path not in local.folders and mode in ('download', 'two-way'):
            changes.append(Change('mkdir_local', path))
    for path in sorted(local.files.keys() | remote.files.keys()):
        relpath(path)
        ls = local.files.get(path)
        rs = remote.files.get(path)
        in_local = path in local.files
        in_remote = path in remote.files
        if path in local.folders or path in remote.folders:
            # Already reported as a folder/file type conflict above.
            continue
        elif in_local and in_remote:
            changes.append(Change('same_size' if ls is not None and rs is not None and ls == rs else 'conflict', path, ls, rs))
        elif in_local and mode in ('upload', 'two-way'):
            changes.append(Change('upload', path, ls, None))
        elif in_remote and mode in ('download', 'two-way'):
            changes.append(Change('download', path, None, rs))
    # A folder/file type collision suppresses all descendants, not just the entry itself.
    blocked = tuple(c.path + '/' for c in changes if c.kind == 'type_conflict')
    return [c for c in changes if not c.path.startswith(blocked)]


def safe_local_target(root: Path, relative: str) -> Path:
    root = Path(root)
    if root.is_symlink(): raise ValueError('Local root is a symlink')
    relative = relpath(relative)
    target = root.joinpath(*relative.split('/'))
    # No symlink traversal into directories, including intermediate ancestors.
    ancestor = target.parent
    while ancestor != root and ancestor != ancestor.parent:
        if ancestor.is_symlink(): raise ValueError(f'Symlink in destination path: {ancestor}')
        ancestor = ancestor.parent
    if target.is_symlink(): raise ValueError('Destination is a symlink')
    return target


def remote_folder(root: str, relative: str) -> str:
    relative = relpath(relative)
    return posixpath.join(posixpath.normpath(root), posixpath.dirname(relative))


def unique_name(original: str, occupied: set[str], suffix=' (Z FTP copy)') -> str:
    segment(original)
    p = Path(original)
    stem, ext = p.stem, p.suffix
    for index in range(1, 10001):
        candidate = f'{stem}{suffix}{" " + str(index) if index > 1 else ""}{ext}'
        if candidate not in occupied:
            occupied.add(candidate)
            return candidate
    raise ValueError('Too many conflicting names')
