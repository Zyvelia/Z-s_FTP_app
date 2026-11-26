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

For a portable copy, choose **Portable folder** in the Inno Setup installer and select a writable location. Settings, queue recovery, and history stay in the folder beside the executable. Enter passwords when connecting from portable mode.

## Building from source

On Windows, install Python 3.11 or later and run `start_windows.bat`. Use `python verify_release.py` for the regression suite. Run `build_exe.bat` to build the app. To build the installer, install [Inno Setup 6](https://jrsoftware.org/isdl.php), extract the entire source ZIP, and run `build_all.bat` from the extracted folder. It shows the build result and leaves the window open so you can read any error. The installer goes in `installer_output`. For unattended builds, use `build_all.bat --no-pause`. If the compiler is in a custom location, set `ZFTP_ISCC` to the full path to `ISCC.exe`.

To build without setting up the Windows tools locally, run **Windows release** from GitHub Actions and download the installer artifact from the completed run. Pushing a matching version tag builds the installer and attaches it to a stable GitHub Release. Update the version in the app, installer, build script, and version assertions before tagging. The installer filename must match the tag (for example, `ZFTP_Setup_v2.1.12.exe` for `v2.1.12`).

See the [v2.1.12 changelog](docs/changelog/CHANGELOG_v2.1.12.md), [release history](docs/RELEASE_HISTORY.md), and [security notes](docs/SECURITY.md).

### Review transfers and run workflows

Use **Transfer settings → Parallel downloads** to run up to 32 background downloads at once while you browse and delete unrelated files. Additional downloads wait in the queue. Z FTP gives duplicate output filenames numbered copies. The queue protects active and pending transfer paths. A server that refuses an extra connection falls back to one download at a time.

Click **Activity** above the transfer queue for connection events, transfer updates, and alerts. The **History** button opens saved transfer outcomes and CSV export directly. Open **Workflows** in the same window to create or run a saved sequence of file copies.

Queued and interrupted jobs are saved for recovery. Reconnect to the same protocol, host, port, and username to bring them back. Z FTP checks the existing partial file before resuming and protects completed destination files. Use **Pause queue** in the transfer header or tray menu to stop starting new jobs while active transfers finish.

In **Activity**, choose **Open download folder** to open a completed download’s destination. Use **Toast settings** in the status bar to choose completion, failure, connection, and other desktop alerts. Clear **Desktop notifications** to turn all toasts off.

Turn on **Streamer mode** in the status bar or press **Ctrl+Shift+H** to mask typed connection details and hide connection identifiers in client views and alerts. Server setup and workflow views are temporarily hidden while it’s on.

Transfers skip the extra checksum pass by default. Enable **Full SHA-256 verification** in **Transfer settings** when you want an end-to-end checksum comparison. It reads the remote file again and can substantially increase transfer time.

### Queue and connection controls

Folder downloads preserve the exact base-game, patch, and DLC folder names. If the same exact destination name is already taken, Z FTP creates a numbered folder copy. Folder requests made while a connection is busy wait for a scan slot.

The queue shows the active file beneath its folder and summarizes active, queued, and failed jobs with combined speed. Use **Compact queue** for combined speed and ETA, and **Decimal units (MB)** to switch from binary units. Name a connection and enter its combined speed limit in Quick Connect or a saved profile. The **Browsing reserve** leaves a percentage of the configured bandwidth available for other traffic.

Open **Manage → Import** under Saved Connections to import FileZilla XML sites or OpenSSH host aliases. Z FTP imports supported connection settings and starting folders from FileZilla. OpenSSH imports supported host, port, user, and key-path settings and does not evaluate external commands or includes.

### Phone Drop and hosted clients

Open **Phone Drop**, choose a local Wi-Fi address and a destination folder, then start the receiver. Scan its QR code on a phone on the same network. After choosing a file on the phone, select the pending request in Z FTP and click **Approve selected upload**. Only approved requests can send files. Phone Drop uses HTTP on the selected LAN address, and the link expires when you stop the receiver.

FTP/FTPS and SFTP server pages have a **Connected clients** view with account, session age, and current transfers. FTP errors include the server reply, failed command, and remote path.

### Read or edit logs, configuration files, and database files

Double-click a remote `.txt`, `.log`, `.logs`, `.ini`, `.db`, or `.dat` file to open its viewer. Local files with those extensions open the same way, with local `.txt` files using your default text app. Click **Edit**, then **Save with backup**. The editor recognizes text and SQLite by their contents and shows other binary data as hex. Text files are limited to 1 MiB, database and data files to 8 MiB, and binary edits to 64 KiB. Hex edits keep the same byte count. SQLite shows up to 100 rows per table and supports editing existing values in ordinary tables with row IDs.

The built-in text viewer has syntax highlighting for JSON, INI, XML, and common scripts. Click **Go to line** or press **Ctrl+G** to jump to a line. Use the encoding menu to reopen the original bytes in UTF-8, UTF-16, Windows-1252, or another supported encoding. Saving uses the selected encoding.

Use **File actions → Search** on a remote tab to combine filename wildcards, extensions, byte ranges, and modification dates. Date filters use UTC day boundaries. FTP servers that only return Unix-style listings may report times in the server’s local zone. Results omit entries whose required metadata is unavailable.

To use Visual Studio Code for a remote text file, right-click it and choose **Edit in VS Code**. Save the temporary copy in VS Code, then return to Z FTP and click **Review changes** and **Upload reviewed changes**. Z FTP keeps the original as a backup and checks that the remote file still matches the copy you opened. Double-click opens the built-in viewer. External editing supports text files up to 1 MiB.

In the built-in editor, use **Ctrl+F** to find and highlight matches, or **Ctrl+H** for replacement. Options include **Match case**, **Whole words**, and **Regex**.

### Browse other file formats

Double-click **ZIP, 7z, RAR, TAR, and TAR.GZ/TGZ** archives to list entries and preview files up to 1 MiB. Large remote archives use FTP or SFTP ranges when supported. Compressed RAR entries use the Windows system tar utility, 7-Zip, or UnRAR. **DDS** and other raster images open in the image viewer. **WAV, OGG, and MP4** open in a media player with a 64 MiB limit for remote playback.

**SELF, SPRX, PRX, ELF, PKG, RIF, IMG/ISO**, static **.a** archives, and SQLite **DB-WAL, DB-SHM, and DB-JOURNAL** have read-only metadata previews. Binary **PLIST** files have a read-only value preview.

Text editing also covers NFO, XML PLIST, REG, EditorConfig, Git submodule configuration, SRT, VTT, JSON, YAML, TOML, XML, CSV/TSV, Markdown, CFG/CONF, common scripts, and other plain-text formats. CSV and TSV have a table preview. Files such as `.backup`, `.idx`, `.nvs`, and `.rec` open according to their contents, with a hex fallback for unfamiliar binary data.

Password prompts for encrypted ZIP and 7z previews reuse the password for the current archive window. Password-protected RAR entry previews require a compatible local extraction tool such as UnRAR or 7-Zip.

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

The Local pane opens in your system **Downloads** folder. **Download**, the remote right-click menu, and drag-and-drop all save into the folder currently shown in the Local pane. To change the destination, use **Browse** or navigate in the Local pane before queuing the download. Folder downloads show a preview and appear as one queue job with a single completion notification.

Open **Help** in Z FTP or press **F1** for a searchable guide to downloads, connections, transfer settings, file tools, and other features.
