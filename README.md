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

The release workflow builds a Windows installer when a matching version tag is pushed, then attaches it to a stable GitHub Release. Update the version in the app, installer, build script, and version assertions before tagging. The installer filename must match the tag (for example, `ZFTP_Setup_v2.1.11.exe` for `v2.1.11`).

See the [v2.1.11 changelog](docs/changelog/CHANGELOG_v2.1.11.md), [release history](docs/RELEASE_HISTORY.md), and [security notes](docs/SECURITY.md).

### View or edit PS3 PARAM.SFO files

In the remote browser, open the game’s folder, right-click `PARAM.SFO`, and choose **View / edit PARAM.SFO**. You can check fields such as `TITLE`, `TITLE_ID`, and `CATEGORY` without saving. The editor supports UTF-8 text and unsigned integers in their existing field space and shows raw fields as read-only. **Save with backup** checks whether the file changed since opening and keeps the original in the same remote folder.

### PS3 file tools

Right-click a remote file or folder and open **PS3 file tools**.

- **Edit config** updates existing key/value entries in UTF-8 `.ini`, `.cfg`, and `.conf` files. The confirmation shows a diff.
- **Edit boot plugins** reorders or removes lines in `boot_plugins.txt`. The editor checks each configured path before saving.
- **View / edit hex** displays files up to 64 KiB and previews same-length edits by byte offset.
- **Inspect PKG header** reads the first 128 bytes and compares the declared size with the directory listing.
- **Browse game metadata** reads titles, title IDs, categories, and versions from up to 200 immediate child folders. Right-click a games folder or the empty space inside it.
- **Preview image** opens PNG or JPEG files up to 1 MiB. For `ICON0.PNG`, you can select a replacement PNG up to 320×176.

Remote writes check that the original file is still current and keep a backup beside it.

To choose where a remote download goes, use **Download** or right-click and choose **Download folder**, then select the destination in the picker. Dragging onto the local pane uses the folder shown in that pane. Folder downloads appear as one queue job with a single completion notification.

Open **Help** in Z FTP or press **F1** for a searchable guide to downloads, connections, transfer settings, PS3 file tools, and other features.
