# Z FTP v2.0.7 — Transfer stages, faster navigation, refreshed UI

## Implemented
- Upload lifecycle is now Uploading → Verifying (if SHA-256 enabled) → Renaming → Finalizing → Completed. Verification is performed on the staged `.zftp.part` before remote promotion. Failure leaves an unverified staged name, not a final published file.
- Download lifecycle: Downloading → Verifying (optional) → Finalizing → Completed. For downloads, the local stage is promoted before checksum validation; a failed verification marks the job Failed.
- Queue status displays the phase and shows `100% sent` or `100% received` during final operations. Transfer ETA is labeled `Checking…`/`Finishing…` rather than inaccurate 00:00 during non-transfer phases.
- Per-server directory cache: 10-second TTL, maximum 24 folders, Back/Forward navigation, explicit Refresh, invalidation on regular remote mutations and completed uploads.
- FTP fallback remembers when MLSD is unsupported and uses LIST directly for later directories on that same connection. Existing PS3/mmCM Latin-1 compatibility remains.
- Connection setup collapses after connecting. Updated restrained charcoal/violet theme, softer panels, compact controls and clearer empty state.

## Preserved
- FTP, FTPS and SFTP clients, hosting servers, safe resume, checksum verification, saved connections, transfers, synchronization, context menus, scheduling, automation and optional ZMT launcher adapter.

## Tests
- `python -m unittest discover -s tests -q`: 174 passed, 8 skipped (requires optional GUI/network dependencies).
- `python verify_release.py`: passed.
- `python -m compileall -q .`: passed.
- ZIP archive verified.

## Remaining verification
- Windows PySide6 GUI visual behavior, PS3/mmCM upload and rename behavior, network speed improvement, and Windows EXE/installer are NOT verified here.
- When SHA-256 is enabled, an upload is read back from the PS3 while still named `.zftp.part`; some FTP servers may not allow reading temporary files, in which case the job fails closed with that staged file retained for recovery.
