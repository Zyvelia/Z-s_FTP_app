# Z FTP

Z FTP is a Windows file transfer client and optional server for FTP, FTPS, and SFTP. Use it with a NAS, a Linux server, another computer, or a PS3 running an FTP server.

## Getting started

1. Download the Windows installer from [GitHub Releases](https://github.com/Zyvelia/Z-s_FTP_app/releases).
2. Open **Z FTP**, select the server protocol, and enter the host and login details.
3. Use the local and remote file panes to drag files into the transfer queue.

Saved connections, independent server tabs, resumable transfers, SHA-256 verification, sync previews, history, schedules, and cross-server copy workflows are included. The **PS3 Wi-Fi preset** and **Benchmark PS3** are optional tools under **Client → Transfers → Settings**.

## Updating an installed copy

Finish your transfers and quit Z FTP, including its tray icon. Open **Z FTP - Check for updates** from the Windows Start menu, or run `Update-ZFTP.bat` from the install folder. Z FTP checks the latest stable GitHub Release and, when a newer Windows installer exists, downloads and verifies it before opening Setup. Setup asks you to confirm installation. For a check without downloading, run `Update-ZFTP.bat -CheckOnly`.

## Building from source

On Windows, install Python 3.11 or later and run `start_windows.bat`. Use `python verify_release.py` for the regression suite. Run `build_exe.bat` to build the app or `build_all.bat` to build the app and installer with Inno Setup 6.

The release workflow builds a Windows installer when a matching version tag is pushed, then attaches it to a stable GitHub Release. Update the version in the app, installer, build script, and version assertions before tagging. The installer filename must match the tag (for example, `ZFTP_Setup_v2.1.6.exe` for `v2.1.6`).

See the [v2.1.6 changelog](docs/changelog/CHANGELOG_v2.1.6.md), [release history](docs/RELEASE_HISTORY.md), and [security notes](docs/SECURITY.md).
