# Z FTP v2.0 — Release acceptance checklist

## Verified in the provided test environment

- [x] Source ZIP contains all v1.5 components, including both hosting backends and optional ZMT adapter.
- [x] Offline regression suite, including simulated FTP, SFTP-path mocked cases and persistence tests.
- [x] Syntax parsing and compilation.
- [x] Installer metadata is set to v2.0.0 and build scripts perform output existence checks.
- [x] Source ZIP integrity checked.

## Windows acceptance tests still required (not performed in this environment)

- [ ] Install Python 3.11+ and dependencies on Windows 11; launch `start_windows.bat`.
- [ ] Verify GUI startup, tabs, focus order, keyboard shortcuts, file list responsiveness and notifications.
- [ ] Build `dist/ZFTP/ZFTP.exe` with `build_exe.bat`; run from a clean Windows profile.
- [ ] Run `build_installer.bat` with Inno Setup 6, install/uninstall as a non-admin user.
- [ ] Confirm no passwords or host private keys are included in logs, installer or ZIP.
- [ ] Verify actual FTP/FTPS and SFTP connections with a test server, including invalid certificates and host keys.
- [ ] Run FTP/FTPS server on loopback and validate TLS failures, permissions, and stop/restart.
- [ ] Run SFTP server on loopback with Paramiko and test authentication, read-only accounts, traversal and shutdown.
- [ ] Test 1 GiB+ upload/download, interruption/resume, disk-full, disconnected network, filename collisions and hash mismatch.
- [ ] Test cross-server transfers and workflow manual recovery after forced termination.
- [ ] Test scheduled jobs with server disconnect and process crash.
- [ ] Verify ZMT adapter launch separately against the current ZMT PluginManager; no integration installed.

**Release posture:** v2.0 is a source release candidate; do not classify as fully production-verified until the above Windows/live-server checks have passed.

## v2.0.7 PS3 Wi-Fi verification (Windows; not yet completed)
- [ ] Connect to mmCM with plain FTP, port 21, confirm Latin-1 fallback still handles remote folders.
- [ ] Upload the same representative file at 64, 256, and 512 KiB, compare sustained MiB/s and failures.
- [ ] Check the Wi-Fi preset does not change current username, credentials, or SHA-256 setting.
- [ ] Run the confirmed 8 MiB benchmark and verify the disposable file has been deleted on the PS3.
- [ ] Simulate Wi-Fi drop during upload and verify `.zftp.part` can resume without corruption.
- [ ] Verify drag-and-drop, server hosting, EXE and installer on Windows.
