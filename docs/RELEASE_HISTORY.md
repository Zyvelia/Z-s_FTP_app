# Z FTP v2.0.7 — PS3/mmCM Wi-Fi Upload Optimization

## v2.0.7 — PS3/mmCM Wi-Fi performance controls

This source update preserves **all v2.0.4 functions**, including the working PS3 FTP legacy-encoding listing fallback, server hosting, client tabs, transfer queue, and resumable `.zftp.part` uploads.

- Open **Client → Transfers → Settings**, then choose **PS3 Wi-Fi preset** for **plain FTP / port 21**, **256 KiB transfer blocks** and **no software bandwidth cap**. Existing username/password are untouched. SHA-256 post-transfer verification stays at its current setting (on by default). Connecting to an existing server tab is not automatically altered; the changed chunk setting applies to newly queued jobs when they begin.
- Block-size selector supports **64 / 128 / 256 / 512 / 1024 KiB**. Larger sizes are experimental, not guaranteed to improve PS3 Wi-Fi throughput. The old 64 KiB setting remains the standard default.
- The reliable uploader/downloader now uses the configured block size, while preserving staged files, full-prefix resume comparison, collision protection, cancellation, limited retries and independent server sessions. No parallel uploads to a single FTP control connection are introduced.
- **Benchmark PS3…** requires an *already connected, idle plain-FTP tab*, no queued jobs on that tab, and an explicit confirmation. It uploads 8 MiB of cryptographically random disposable data to the current folder, measures upload data-phase throughput, and attempts to delete both its own uniquely named final and staging file. If cleanup fails or the transfer is interrupted, the remote `zftp_benchmark_*.bin` or `.zftp.part` may remain and should be removed manually. Benchmark data never contains user files or credentials.
- This is a **real FTP data upload test**, not a local disk benchmark. It **does not measure PS3 Wi-Fi radio link speed separately** from filesystem and FTP server performance. It does not test true application throughput under SHA-256 reread or resume, and it does not automatically disable verification. Windows client and actual mmCM performance remain **unverified here**.

**Performance expectations:** A PS3 connected by Wi-Fi may remain near 2 MiB/s regardless of Python tuning. For the largest practical improvement, use Ethernet or improve signal and congestion. For large ISOs, disabling post-transfer SHA-256 (only after weighing integrity tradeoffs) can avoid a second full file read over the network. FTP transfers are not encrypted; use only on a trusted LAN. Do not interrupt active ISO transfers just to apply a preset.

**Offline validation:** `python verify_release.py` runs AST syntax checks, packaging layout checks, and regression tests. New tests simulate FTP uploads at selected block sizes, successful benchmark cleanup, cleanup failures, and transfer interruption. Integration tests for actual mmCM Wi-Fi throughput, live SSH/FTP hosting, Qt UI and Windows EXE/installer are still required.

Standalone Windows-first Python/PySide6 file transfer client and opt-in FTP/FTPS/SFTP server. This release builds on the **complete v1.5 source** and retains the existing client, profiles, multi-tab sessions, transfer queue, sync engine, remote editor, FTP/FTPS and SFTP hosting, dark UI, and Windows build scripts. A standalone, optional launcher adapter is included for future ZMT integration; it is not installed or registered.

## v2.0.4 changes

- **Fixes the PS3 error after FTP login:** some homebrew servers accept login in UTF-8 but return 8-bit directory listing bytes. Z FTP now catches UnicodeDecodeError from `MLSD`/`LIST`, closes the potentially desynchronized FTP session, reconnects with Latin-1, restores the last known working directory, and retries the listing.
- Uses the cached successful `PWD` value, rather than sending commands on a corrupted connection.
- Scope limited to FTP/FTPS sessions. SFTP SSH host-key and encryption behavior is unchanged. Authentication errors are not retried as encoding issues.
- Adds four targeted tests for initial listing fallback, folder restoration, authentication failures, and failed-restoration cleanup. No live PS3 test has been performed.

## v2.0.2 changes

- **Legacy PS3 FTP support:** the FTP/FTPS control channel initially uses UTF-8; if a server reply cannot be decoded, Z FTP closes the attempted connection and retries using Latin-1. This supports servers emitting non-UTF-8 8-bit responses without dropping filename bytes. Authentication (e.g. `530 Not logged in`) and TLS errors do not trigger fallback. FTP remains unencrypted; use only on a trusted LAN. PS3 typically uses plain FTP, port 21, with credentials determined by the installed FTP server. Live PS3 compatibility remains unverified.

## v2.0.1 release changes and verification

**Status: Source release candidate, not a Windows-tested production release.** Read `docs/RELEASE_CHECKLIST.md` and `docs/SECURITY.md` before exposing services on the network.

- **SQLite resource reliability:** history and scheduler operations now commit/rollback **and explicitly close connections**, preventing the database-connection resource warnings previously observed. WorkflowStore already used explicit connection closure and is preserved.
- **Partial-file defense:** resumable local downloads reject pre-existing symlinks, directories, and multiply linked partial files. They re-check the size on opening and use no-follow file opens where the OS supports it. This reduces accidental damage; filesystem races with hostile local processes are still possible.
- **Protocol name validation:** FTP/SFTP single-segment file names reject ASCII control characters and non-string inputs, reducing unsafe filenames passed to protocol commands.
- **SFTP hosting correction:** writable SFTP handles now use file modes consistent with descriptor permissions, fixing write-only opens. An actual Paramiko server/client round trip remains required to verify on Windows.
- **Shared folder validation:** FTP server setup rejects Windows junction roots as well as symlink roots; existing SFTP path checks remain in place.
- **Packaging checks:** Inno Setup metadata is v2.0.0; batch build scripts now verify that the expected EXE and installer were produced. No binaries are included.
- **Testing:** extended offline checks exercise simulated FTP upload, download, staged resume, collision and mismatch scenarios; database cleanup; partial file path safety; control character validation; and packaging metadata. Run `python verify_release.py` for a repeatable offline preflight.
- **Preservation:** all client backends, FTP/FTPS and SFTP servers, tabs, workflows, schedules, remote-to-remote copies, transfer history, encryption features and optional separate ZMT launcher adapter remain available.

This release does **not** claim that live network interoperability, real TLS/SSH negotiation, Windows GUI, compiled EXE, installer, or actual ZMT registration has been validated. Those are documented in `RELEASE_CHECKLIST.md`.

## Run from source on Windows

1. Install Python 3.11+ and extract `Z_FTP_v2.0_Source.zip`.
2. Open `Z_FTP_v2.0.7` and run `start_windows.bat`. The launcher creates a virtual environment and installs dependencies listed in `requirements.txt`.
3. Use **Client** to connect, **History** to inspect completed/failed/cancelled file jobs, and **Schedules** to add time-based operations for your connected server.

Or manually:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

## New in v1.5 — Workflow Studio and optional ZMT adapter

- **Structured workflow editor:** Create workflows with named copy steps using form fields for source server, destination server, directories, and filenames. No raw JSON editing is required. Only the `copy_remote` action is supported, as in v1.3.
- **Step management:** Add, edit, remove, and reorder steps before first execution. Validate server identities, safe paths, and file names prior to saving.
- **Duplicate workflows:** Copy a workflow's definition into a new `ready` workflow with progress reset to step zero; no previous execution state is replayed.
- **Edit safety:** An already-started, paused, or completed workflow cannot have its steps changed, even if the cursor is zero. Duplicate instead. Pausing an active step only stops subsequent steps; the active operation must finish first.
- **Preserved persistence and recovery:** SQLite workflow definitions, saved progress, paused-on-interruption behavior, manual resume after destination review, and no overwrite behavior remain in force.
- **Optional ZMT adapter:** `optional_zmt_adapter/launcher.py` provides `launch_zftp()` that starts a separate Z FTP process via a validated executable/script path with `shell=False`. A standalone example and integration notes are provided. **No ZMT imports, no marketplace registration, no ZMT filesystem changes, and no ZMT installation** are performed. Actual compatibility with the user's ZMT PluginManager and PageBridge cannot be confirmed without its current source. This adapter launches the standalone window; it is not a Qt page embedded into ZMT.
- **Database resource handling:** WorkflowStore now explicitly closes SQLite connections after each transaction.

### Workflow Studio usage

1. Connect to two distinct servers in the **Client** section.
2. Open **Workflows** > **Create workflow…**, enter a name, then **Add step**.
3. Choose source/destination connections, directories, and filenames, and save the step.
4. Add more steps; select a step to edit, remove, or move it up/down.
5. Save workflow, select it, and choose **Run / continue**. For paused workflows, inspect the destination and confirm before restarting.
6. Use **Duplicate** to create a fresh editable copy of a workflow that has already run.

### Optional integration example

```python
from optional_zmt_adapter.launcher import launch_zftp
launch_zftp()  # separate process; no ZMT required
```

For an installed app, point the adapter at a project root containing `ZFTP.exe` or `dist/ZFTP/ZFTP.exe`. The launcher also supports source execution using `main.py`. To register inside ZMT, adapt the example to its **current** module API; do not copy plugin metadata from an unrelated version.

## Existing v1.3 — Remote-to-remote copies

- Connect **two different server tabs** (FTP, FTPS, or SFTP) and select **one regular remote file** on the source tab. Select **Copy to server…**, choose the destination tab and destination filename, then confirm. Transfers are always **downloads to private temporary local storage followed by uploads**, never direct FTP FXP/server-to-server sockets.
- A cross-server copy reserves **both connection sessions** for its duration, preventing control channel races with normal queued transfers. Independent third-party tabs continue functioning. Per-transfer throttling uses the existing bandwidth control; the cross-server copy does not automatically retry a failed two-server operation.
- The source is downloaded to a temporary directory with private directory permissions where supported, compared with a fresh remote SHA-256 read, uploaded using the existing staged-file/resume engine, then verified again against the destination via SHA-256. Temp storage is deleted on success and failure. This requires local free space for the complete file, and verification reads the source and destination again over the network.
- The destination is checked before upload and **never intentionally overwritten**. If a remote upload is interrupted, the destination's `.zftp.part` file can remain and may require manual inspection. Some servers do not offer atomic no-replace rename semantics; concurrent external writers are not fully protected. Local temporary files are not encrypted at rest (they are short-lived and private).
- Source folders, identifiable symlinks, and unsafe remote names are rejected. v1.3 supports **single-file cross-server copies**, not recursive cross-server folder trees. Transfer outcomes, statuses, and checksums appear under the new **Cross-server** section and in persistent **History**.
- Cancellation is available through either participating tab's **Cancel transfer** button. After a failed/cancelled operation both connections are conservatively marked disconnected for manual review. Close and reconnect those tabs to resume normal use. Transfers in the existing ordinary queue are not replayed automatically.

### Multi-step workflows

- The new **Workflows** section lets you save **1–100 ordered `copy_remote` steps** in `%USERPROFILE%\.zftp\workflows.sqlite3`. To create one, select a source file with another server tab connected, click **Create workflow…** and use the prefilled JSON step as a template. Duplicate/edit steps to create a multi-step sequence, then save. The editor is JSON-based, not yet a drag-and-drop workflow builder.
- The workflow step schema requires `action`, `source_endpoint`, `destination_endpoint`, `source_dir`, `source_name`, `destination_dir`, and `destination_name`. Endpoint identities contain `[protocol, hostname, port-as-string, username]` only, never credentials. The destination identity must differ from the source identity.
- **Run / continue** executes steps sequentially while the app stays open and exactly one connected server tab matches each identity. After each successful copy, the cursor is saved. On failure, future steps stop, and the workflow pauses. The program never automatically logs into a server or resumes work after reboot.
- **Crash-safe recovery:** If the program closed while a workflow step was active, startup marks the workflow paused for manual review. The pending step is not automatically retried because its destination may already exist. **Pause** lets an active step finish but stops later steps. Completed workflows cannot be replayed; create a new workflow if needed. **Delete** removes only the workflow definition, never remote files.
- v1.3 workflow steps are **sequential cross-server file copies only**; arbitrary scripting, shell execution, deletions, and unattended service/background scheduling are intentionally not supported.

### Security caveats

The source and destination connection objects are existing trusted sessions. Files pass over the protocols selected for each tab: **plain FTP remains unencrypted** and should be avoided for sensitive data. Use FTPS or SFTP when possible. Workflow DBs store server addresses and paths but no passwords, although those paths can themselves be sensitive. Without a transactional remote filesystem, recovery is conservative rather than a guarantee of atomic writes.

## Inherited from v1.2

### SHA-256 post-transfer verification

- Enabled by default for **individual upload/download jobs**; uncheck **Verify transfers with SHA-256** if you do not want the extra remote read and network traffic.
- Once a file is transferred and published, Z FTP computes SHA-256 independently for the local file and the remote file. It reads the **entire remote file** over FTP/FTPS `RETR` or SFTP read: the protocols have no standardized portable remote SHA-256 operation.
- Comparison happens in the connection's background worker, never simultaneously with another operation on the same session. An SSH/FTP read error or mismatch marks the job **Failed**, records the error and stops subsequent jobs for that server until the connection is reviewed. Unrelated server tabs remain independent.
- A mismatch **does not automatically delete, replace, or overwrite** either side. Verification checks equality at the time files were read; without server-side locks, another writer may alter a file after verification. Because the transfer may already have been committed before a failure, manually investigate failed checks. Checksums are not used to claim that a preexisting same-size sync conflict is identical.

### Persistent local transfer history

- SQLite audit database: `%USERPROFILE%\.zftp\transfer_history.sqlite3`.
- One terminal outcome per job ID with timestamp (UTC), server name, protocol, direction, paths, bytes, attempts, SHA-256 when verified, and error text. Includes completed and failed jobs and queued jobs cancelled by the user or connection closure. These records are not passwords, but hostnames and file paths may be sensitive.
- Open **History** to review the latest 200 outcomes; **Export CSV** writes up to 10,000 of the latest records to a *new* file (existing destination not overwritten). Potential spreadsheet formulas in exported text fields are neutralized.
- SQLite files live outside the source tree and therefore survive ordinary source upgrades. There is no automatic purge/retention policy yet. Back up or delete the SQLite files yourself if appropriate.

### Scheduled jobs and synchronization

- SQLite scheduler database: `%USERPROFILE%\.zftp\schedules.sqlite3`.
- Use **Schedules → Add schedule** while connected to a server. Choose one file upload, one file download, upload-missing sync, download-missing sync, or two-way missing-file sync; select a start delay in minutes and an optional repeat interval (minimum one minute).
- **Explicitly running app only:** the timer runs every 15 seconds while the GUI is open. The matching protocol/host/port/username session must be connected and not otherwise busy; scheduled jobs do **not** store passwords, automatically connect, execute as a background Windows service, or run after Z FTP closes.
- Upload/download jobs are submitted to the existing serial-per-server transfer queue. Scheduled directory sync scans both trees in a connection worker and submits only non-destructive planned changes. Existing paths are skipped, files are never deleted or overwritten, and any rejected paths pause the schedule for review. When creating a sync schedule, a confirmation explains this automation.
- Pause, enable, or remove schedules through the Schedules tab. One-shot schedules show **dispatched** once submitted to the queue (this **does not** mean the transfer successfully completed; check History). For repeating schedules, the next occurrence advances after dispatch rather than replaying every missed interval.
- **Crash recovery:** on restart, any schedule that was in the middle of dispatch is paused with an interruption message and requires manual review before re-enabling. In-memory transfer queues are **not** restored. This prioritizes avoiding accidental duplicate transfers over automatic completion.
- A repeated single-file upload to the same remote name will not overwrite it on the next run; it may pause due to the existing target. Use sync schedules for recurring non-destructive directory updates, or a different destination filename.
- Scheduled tasks are not guaranteed to run at an exact wall-clock time (e.g. system sleep, disconnected tab, or busy session delays them). This is an **in-app scheduler**, not Windows Task Scheduler.

## Preserved behavior

All v1.1 modules remain: FTP/FTPS/SFTP clients; encrypted credential storage for saved client profiles; independent connections and queued transfers; guarded resume and retries; speed/ETA/bandwidth limits; folder drag-and-drop and sync previews; remote rename/search/chmod/text editing; separately controlled FTP/FTPS and SSH/SFTP servers with user account access controls; charcoal/violet UI and notification history.

## Windows build

- `build_exe.bat` — build `ZFTP.exe` using PyInstaller.
- `build_installer.bat` — build Windows installer with Inno Setup 6. Output metadata is updated to **v2.0.0**.
- The ZIP is **source only**; no Windows EXE or installer is prebuilt here.

## Run tests

```powershell
py -m unittest discover -s tests -v
py -m compileall -q .
```

The regression suite includes mocked FTP/SFTP checksum tests, SQLite persistence, schedule claim/recovery, legacy client and server tests, and static GUI integration checks. Runtime GUI, network interoperability, packaging, and server TLS/SSH interactions require testing on a Windows machine with the dependencies installed. Do not expose servers on the public internet until those checks pass and the host firewall is configured.

## Historical v1.5 Verification

- `python -m unittest discover -s tests -q`: **128 total, 120 passed, 8 skipped**. Optional GUI/network dependency tests are skipped when their libraries are unavailable.
- Python compilation and ZIP integrity checked.
- Newly added tests cover workflow edit restrictions, rename, duplication, adapter command execution, and version metadata.
- **Not verified:** live Windows UI interactions, production cross-server transfers, actual Windows EXE/installer build, real ZMT PluginManager registration. No remote deployment or modification to the ZMT client was performed.

## v1.5 performance, responsiveness, accessibility and reliability

- **Local directory scanning:** uses `os.scandir()` with cached file-type data and performs the scan in the existing `QThreadPool` so enumerating a large directory does not block the event loop. Requests carry generation IDs; a late result from an old directory cannot replace a newer navigation result.
- **Bounded UI rendering:** local and remote file browsers fill at most 200 rows per event-loop turn. Row counts are allocated once rather than repeatedly inserting rows, reducing table layout/repaint work. Directory metadata and sorting remain folder-first, case-insensitive. A batch limit is a *rendering* bound, not a limit on the number of returned files; the complete listing remains in memory.
- **Transfer queue redraw:** high-frequency progress events update job state but throttle full queue-table redraws to roughly 8 per second. Terminal progress is displayed immediately; completion/failure handling still forces a full redraw. This does not change the network transfer rate or the configured bandwidth cap.
- **Keyboard navigation:** F5 refreshes the local directory, Ctrl+L opens a local directory entry dialog, and Ctrl+R refreshes the active remote connection where safe. Existing mouse/drag-drop actions are preserved. Keyboard focus and selection styling are retained from v1.0.
- **Large-file behavior:** the existing 64 KiB streaming transfer chunks, resumable partial-file staging, checksums, and limited simultaneous per-session operations are preserved; files are not loaded in full for transfer. No change to the on-wire protocol was necessary.
- **Safe cleanup:** worker jobs remain referenced until their completion/error signals run; late local scan results are discarded. Session closing rules (no closing busy sessions) and cross-server temporary-directory cleanup are unchanged.

### Limitations and verification

Run `python -m unittest discover -s tests -v`. New tests exercise 2,000-entry directory scans, 10,003-item rendering batches, out-of-order navigation generation checking, refresh throttling, and constant-memory rate accounting. These smoke thresholds are intentionally generous and are not an end-user speed guarantee. Actual GUI responsiveness, file server interoperability, Windows EXE/installer creation, and large live transfers need tests on Windows with optional dependencies installed. Existing FTP/FTPS/SFTP client and hosting functionality is maintained, not reimplemented. Optional ZMT adapter stays separate and does not register itself.

Run from Windows via `start_windows.bat`, build EXE with `build_exe.bat`, or build the installer with `build_installer.bat` after installing the required packaging tools. ZIP contains source and scripts, not a compiled executable.


## v2.0.1 UI organization
The connection form uses a compact grid, saved connections have their own row, SFTP key fields appear only when SFTP is selected, remote file management actions are under **File actions**, and the sync preview is under the local **More** menu. Transfer retry/bandwidth options and SHA-256 verification are in the collapsible **Settings** area. All existing operations remain available.


## v2.0.4 — Remote panel sizing patch

- Expands the default local/remote browser area and assigns more width to the remote panel.
- Sets a minimum file-list height, with slightly taller, more readable file rows.
- Combines per-server status, progress bar and Cancel into a single compact row.
- Collapses Activity/Notifications/History/Schedules/Workflows/Cross-server by default; the **Show activity** toggle reopens all of them without removing features.
- Reduces the transfer queue's fixed vertical footprint and keeps advanced transfer settings collapsed.
- Preserves the v2.0.3 PS3/mmCM legacy-encoding listing fallback.
- No FTP server behavior, credentials, transfer algorithms, or session management logic changed.

The GUI sizing needs a Windows visual check because PySide6 is not installed in the offline test environment.


## v2.0.7 — ETA and file context menus

- ETA now uses a rolling 18-second measurement window, includes transfer stalls, resets after retry/resume byte count regressions, and shows **Calculating…** until a representative sample is available. It is an estimate, not a guaranteed completion time. Post-upload checksum verification can take additional time after bytes reach 100%.
- Right-click **local files** to open, upload, rename, copy the path, or move to the Windows Recycle Bin (no permanent-delete fallback); right-click empty space to refresh or make a new folder.
- Right-click **remote files** to download, rename, edit text, change permissions (where supported), or permanently delete a file after confirmation. Right-click empty space to refresh or make a remote folder. No remote recursive delete is exposed.
- FTP/mmCM encoding fallback, partial-file verification, server tools, drag-and-drop and queued transfers remain unchanged.

- Empty remote folders can be deleted through the context menu after explicit confirmation; nonempty folders are rejected by the backend. Remote deletion never recursively deletes.
- ETA estimates exclude any subsequent SHA-256 verification phase and may remain calculating during sustained stalls.


## v2.0.7 — Stage-aware transfers and cleaner file browsing

- An upload now displays **Uploading → Verifying (when SHA-256 enabled) → Renaming → Finalizing → Completed**. The `.zftp.part` file is verified *before* it is renamed to the final filename. If verification fails, the incomplete/unverified staged filename remains and is not published. A transfer at 100% sent is not reported as completed until the final operation succeeds.
- Downloads display **Downloading → Verifying (optional) → Finalizing → Completed**. Note that download staging is promoted before the post-transfer hash check, so an integrity failure should not be treated as a verified download.
- Per-transfer status and ETA show phase-specific text rather than `Running / 00:00` while verification is ongoing. Verification time cannot currently be estimated precisely.
- Remote folders visited recently are cached per connection for **10 seconds**, with a bounded 24-directory LRU cache. Back/forward buttons, refresh and mutation invalidation are included. Press **Refresh** when another FTP client has modified the server. FTP servers that reject MLSD are no longer probed repeatedly within the same connection; legacy LIST and Latin-1 fallback remain available.
- A less cluttered, softer violet design increases browser priority, rounds panels and controls, and collapses connection controls automatically after successful connection.
- All existing client, hosting, transfer, resumption, checksum, scheduling, workflow, and optional ZMT adapter code remains in the source.

**Not yet verified:** visual Qt rendering on Windows, actual mmCM throughput, and live PS3 rename/verification. Do not cancel a transfer simply because the byte counter has reached 100%.


## v2.1.6 — Windows branding and packaging

- `assets/zftp_icon.ico`: branded Windows EXE, shortcuts, installer, and taskbar icon.
- `assets/zftp_icon.png`: Qt window/application icon.
- `assets/installer_sidebar.bmp`, `assets/installer_small.bmp`: custom Inno Setup wizard graphics.
- `assets/design_concept.png`: optional UI *concept image*; not implemented as a replacement interface.
- `build_exe.bat`: creates `dist/ZFTP/ZFTP.exe` and bundled dependencies (requires Windows and Python).
- `build_installer.bat`: creates `installer_output/ZFTP_Setup_v2.1.6.exe` (requires Inno Setup 6).
- `build_all.bat`: runs the EXE builder and then the installer builder.
- Installer offers Start Menu shortcut, optional desktop shortcut, and post-install launch.
- No new file associations are registered.

These scripts have been inspected and the source compiled, but the actual Windows EXE and installer builds have **not** been executed here. Run and verify on Windows before distributing.


## v2.1.6 Visual Polish
See `docs/changelog/CHANGELOG_v2.1.6.md` for this release and the organized source layout.


## v2.1.6 — Tray controls
Right-click the Z FTP notification-area icon for Show/Hide, Transfers, Activity, cancellations, desktop notifications and Quit. Double-click to show/hide. Use *Close button hides to tray* to opt into X-as-hide; unchecked (default), X quits and automatically disconnects as in v2.1.3. Use Quit from the tray to exit when hidden.


## v2.1.6 — Source directory organization

The application is now organized by responsibility instead of keeping its modules
at the root. `main.py`, `start_windows.bat`, `build_exe.bat`,
`build_installer.bat`, and `build_all.bat` stay at the top level.

```text
Z_FTP/
  main.py                 # minimal Windows entry point
  zftp/
    protocols/            # FTP, FTPS and SFTP clients
    transfers/            # queue, resume, verification, cross-server copy
    servers/              # optional FTP/FTPS and SFTP hosting
    services/             # profiles, sync, history, schedules, workflows
    ui/                   # PySide6 window, dialogs, themes and tables
  assets/                 # app icon and Inno artwork
  packaging/              # Inno Setup definition
  docs/changelog/         # release histories
  tools/                  # local benchmarking utility
  tests/                  # regression suite
  optional_zmt_adapter/   # standalone launcher bridge
```

**No on-disk user files or FTP remote folders are moved by this update.**
Existing settings and data under `~/.zftp` continue to be used.


## v2.1.6 — Modular UI architecture
The large `zftp/ui/main_window.py` has been decomposed into dedicated UI mixins, remote-tab and worker modules without changing the main application entry point. See `docs/changelog/CHANGELOG_v2.1.6.md`.
