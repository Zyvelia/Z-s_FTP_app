"""PS3/mmCM FTP performance presets and *explicitly approved* transfer benchmark.

No network activity occurs by importing this module or applying a preset.
A benchmark transfers a random temporary local payload using the normal safe
resumable uploader, then removes only its own unique remote test file.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tempfile
import time
import uuid

from zftp.transfers.reliable_transfer import TransferSettings, upload as resilient_upload

PRESETS = {"Standard": 64, "PS3 Wi-Fi (balanced)": 256, "PS3 Wi-Fi (large blocks)": 512}


@dataclass(frozen=True)
class BenchmarkResult:
    byte_count: int
    elapsed_seconds: float
    mib_per_second: float
    remote_cleanup: bool
    remote_name: str


def benchmark_ftp_upload(backend, *, size_mib=8, settings=None, cancelled=lambda: False,
                         clock=time.monotonic, progress=lambda done, total: None):
    """Uploads non-sensitive random bytes, then best-effort removes its own test file.

    The FTP connection must already be authenticated, FTP (not FTPS/SFTP),
    and exclusively owned by the calling transfer worker. The UI must obtain
    confirmation before invoking this function. No remote existing file is
    intentionally touched. Cleanup failures are clearly reported.
    """
    if size_mib not in (4, 8, 16, 32):
        raise ValueError("Benchmark size must be 4, 8, 16, or 32 MiB")
    settings = settings or TransferSettings(chunk_kib=256)
    marker = uuid.uuid4().hex
    name = f"zftp_benchmark_{marker}.bin"
    started = False
    finished = False
    cleanup = True
    elapsed = 0.0
    with tempfile.TemporaryDirectory(prefix="zftp_benchmark_") as folder:
        source = Path(folder) / name
        # Generate payload in bounded chunks; no RAM-sized 32 MiB allocation.
        with source.open('xb') as handle:
            for _ in range(size_mib * 4):
                handle.write(os.urandom(256 * 1024))
        try:
            if cancelled():
                raise InterruptedError('Benchmark cancelled')
            started = True
            t0 = clock()
            resilient_upload(backend, source, name, progress, cancelled, settings)
            elapsed = max(clock() - t0, 1e-9)
            finished = True
        finally:
            if started:
                # Remove only the unique test name. If the safe upload failed
                # its partial stage may remain; this is a test artifact owned
                # by this invocation, not an arbitrary existing remote file.
                for target in (name, name + '.zftp.part'):
                    try:
                        backend.delete_file(target)
                    except Exception:
                        # No test path should be considered clean unless both
                        # calls finish. FTP servers may use 550 for absent files.
                        from ftplib import error_perm
                        try:
                            present = backend._ftp().size(target)
                        except error_perm as exc:
                            if str(exc).startswith('550'):
                                continue
                            cleanup = False
                        except Exception:
                            cleanup = False
                        else:
                            if present is not None:
                                cleanup = False
    if not finished:
        raise RuntimeError('Benchmark did not finish')
    return BenchmarkResult(size_mib * 1024 * 1024, elapsed,
                           size_mib / elapsed, cleanup, name)
