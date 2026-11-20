"""Optional lightweight local scan benchmark. Never touches network or user files."""
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from zftp.services.directory_performance import scan_directory, batches

def main():
    with TemporaryDirectory() as root:
        path = Path(root)
        for i in range(3000):
            (path / f'file_{i:05d}.dat').touch()
        start = perf_counter()
        entries = scan_directory(path)
        scan_time = perf_counter() - start
        start = perf_counter()
        chunks = list(batches(entries))
        prep_time = perf_counter() - start
        print(f'Entries: {len(entries)}, scanning: {scan_time:.4f}s, '
              f'preparing {len(chunks)} bounded batches: {prep_time:.4f}s')
        print('These timings do not include Qt painting, disk/network latency, or remote directory listing.')

if __name__ == '__main__':
    main()
