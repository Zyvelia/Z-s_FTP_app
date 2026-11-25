# Z FTP

Z FTP is a Windows file transfer client and optional server for FTP, FTPS, and SFTP. It can also host a writable NFSv3 share. Use it with a NAS, a Linux server, another computer, or a PS3 running an FTP server.

## Getting started

1. Download the Windows installer from [GitHub Releases](https://github.com/Zyvelia/Z-s_FTP_app/releases).
2. Open **Z FTP**, select the server protocol, and enter the host and login details.
3. Use the local and remote file panes to drag files into the transfer queue.

Saved connections, independent server tabs, resumable transfers, SHA-256 verification, sync previews, transfer history, and manual workflows are included.

## Share a Windows folder with a PS4 over NFS

1. In **NFS Server**, select a dedicated destination folder on a local drive and the PC address on the same LAN as the PS4. Click **Start writable share**.
2. Copy the `nfs://<PC-IP>/ps4` URL into the NFS share setting on the PS4. In [Itemzflow](https://github.com/LightningMods/Itemzflow#nfs-share-format), enter it under **Settings → Fuse NFS IP**, then set **Dumper Path** to `/hostapp`.
3. When the dump finishes, stop sharing from Z FTP.

On first use, Z FTP downloads and verifies [WinNFSd 2.4.0](https://github.com/winnfsd/winnfsd/releases/tag/2.4.0) and caches it for future starts. WinNFSd uses TCP and UDP ports 111, 1058, and 2049. If Windows Firewall blocks the connection, allow WinNFSd on the private network, scoped to your PS4 where possible. WinNFSd shares are writable and have no password.

## Updating an installed copy

Finish your transfers, then right-click the Z FTP tray icon and choose **Check for updates**. Z FTP will disconnect and close before the updater proceeds. If Z FTP is already closed, open **Z FTP - Check for updates** from the Windows Start menu, or run `Update-ZFTP.bat` from the install folder. Z FTP checks the latest stable GitHub Release and, when a newer Windows installer exists, downloads and verifies it before opening Setup. Setup asks you to confirm installation. For a check without downloading, run `Update-ZFTP.bat -CheckOnly`.

## Building from source

On Windows, install Python 3.11 or later and run `start_windows.bat`. Use `python verify_release.py` for the regression suite. Run `build_exe.bat` to build the app. To build the installer, install [Inno Setup 6](https://jrsoftware.org/isdl.php), extract the entire source ZIP, and run `build_all.bat` from the extracted folder. It shows the build result and leaves the window open so you can read any error. The installer goes in `installer_output`. For unattended builds, use `build_all.bat --no-pause`. If the compiler is in a custom location, set `ZFTP_ISCC` to the full path to `ISCC.exe`.

To build without setting up the Windows tools locally, run **Windows release** from GitHub Actions and download the installer artifact from the completed run. Pushing a matching version tag builds the installer and attaches it to a stable GitHub Release. Update the version in the app, installer, build script, and version assertions before tagging. The installer filename must match the tag (for example, `ZFTP_Setup_v2.1.12.exe` for `v2.1.12`).

See the [v2.1.12 changelog](docs/changelog/CHANGELOG_v2.1.12.md), [release history](docs/RELEASE_HISTORY.md), and [security notes](docs/SECURITY.md).

### Review transfers and run workflows

Use **Transfer settings → Parallel downloads** to run up to 32 background downloads at once while you browse and delete unrelated files. Additional downloads wait in the queue. Z FTP gives duplicate output filenames numbered copies. The queue protects active and pending transfer paths. A server that refuses an extra connection falls back to one download at a time.

Click **Activity** above the transfer queue for connection events, transfer updates, and alerts. The **History** button opens saved transfer outcomes and CSV export directly. Open **Workflows** in the same window to create or run a saved sequence of file copies.

Transfers skip the extra checksum pass by default. Enable **Full SHA-256 verification** in **Transfer settings** when you want an end-to-end checksum comparison. It reads the remote file again and can substantially increase transfer time.

### Read or edit logs, configuration files, and database files

Double-click a remote `.txt`, `.log`, `.logs`, `.ini`, `.db`, or `.dat` file to open its viewer. Local files with those extensions open the same way, with local `.txt` files using your default text app. Click **Edit**, then **Save with backup**. The editor recognizes text and SQLite by their contents and shows other binary data as hex. Text files are limited to 1 MiB, database and data files to 8 MiB, and binary edits to 64 KiB. Hex edits keep the same byte count. SQLite shows up to 100 rows per table and supports editing existing values in ordinary tables with row IDs.

To use Visual Studio Code for a remote text file, right-click it and choose **Edit in VS Code**. Save the temporary copy in VS Code, then return to Z FTP and click **Review changes** and **Upload reviewed changes**. Z FTP keeps the original as a backup and checks that the remote file still matches the copy you opened. Double-click opens the built-in viewer. External editing supports text files up to 1 MiB.

### Browse other file formats

Double-click **ZIP, 7z, RAR, TAR, and TAR.GZ/TGZ** archives to list entries and preview files up to 1 MiB. Large remote archives use FTP or SFTP ranges when supported. Compressed RAR entries use the Windows system tar utility, 7-Zip, or UnRAR. **DDS** and other raster images open in the image viewer. **WAV, OGG, and MP4** open in a media player with a 64 MiB limit for remote playback.

**SELF, SPRX, PRX, ELF, PKG, RIF, IMG/ISO**, static **.a** archives, and SQLite **DB-WAL, DB-SHM, and DB-JOURNAL** have read-only metadata previews. Binary **PLIST** files have a read-only value preview.

Text editing also covers NFO, XML PLIST, REG, EditorConfig, Git submodule configuration, SRT, VTT, JSON, YAML, TOML, XML, CSV/TSV, Markdown, CFG/CONF, common scripts, and other plain-text formats. CSV and TSV have a table preview. Files such as `.backup`, `.idx`, `.nvs`, and `.rec` open according to their contents, with a hex fallback for unfamiliar binary data.

### View or edit PARAM.SFO files

Double-click `PARAM.SFO` in either file pane. In the remote pane, you can also right-click it and choose **View / edit PARAM.SFO**. You can check fields such as `TITLE`, `TITLE_ID`, and `CATEGORY` without saving. The editor supports UTF-8 text and unsigned integers in their existing field space and shows raw fields as read-only. **Save with backup** checks whether the file changed since opening and keeps the original in the same folder.

### File tools

Right-click a remote file and open **File tools**.

- **Edit config** updates existing key/value entries in UTF-8 `.ini`, `.cfg`, and `.conf` files. The confirmation shows a diff.
- **Edit boot plugins** reorders or removes lines in `boot_plugins.txt`. The editor checks each configured path before saving.
- **View / edit hex** displays files up to 64 KiB and previews same-length edits by byte offset.
- **Inspect PKG header** shows PS3 or PS4 package metadata and compares the declared size with the directory listing.
- **Preview image** opens PNG or JPEG files up to 1 MiB. For `ICON0.PNG`, you can select a replacement PNG up to 320×176.

Remote writes check that the original file is still current and keep a backup beside it.

To choose where a remote download goes, use **Download** or right-click and choose **Download folder**, then select the destination in the picker. Dragging onto the local pane uses the folder shown in that pane. Folder downloads appear as one queue job with a single completion notification.

Open **Help** in Z FTP or press **F1** for a searchable guide to downloads, connections, transfer settings, file tools, and other features.
