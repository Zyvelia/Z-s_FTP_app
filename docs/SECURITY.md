# Z FTP v2.0 — Security and operational limitations

- Prefer SFTP or verified FTPS. Plain FTP transmits credentials/data without encryption, and is permitted for localhost server hosting only.
- SSH unknown host keys require explicit approval. Changing a previously accepted key is rejected. Confirm fingerprints through an independent trusted channel.
- FTPS uses system certificate verification on the client. Server operation requires a user-provided certificate/key; remote access must be explicitly enabled.
- FTP/FTPS and SFTP servers are disabled by default. Do not expose them publicly before live integration tests, firewall review and account security checks.
- SFTP hosting is password-authenticated in this release; server-side authorized public keys are not implemented.
- Shared directories reject known traversal, symlinks and junctions. Highly privileged or hostile concurrent local filesystem modifications may still cause race conditions; use a dedicated restricted OS account and isolated share.
- Local `.zftp.part` resume files are verified before reuse, and pre-existing symlinks/hardlinks are rejected. Keep local transfer directories trusted; POSIX `O_NOFOLLOW` provides additional protection where available, not a fully race-free cross-platform guarantee.
- Remote rename semantics differ by server. FTP and SFTP servers may not guarantee atomic no-replace operations against independent concurrent writers. Keep independent backups.
- Cross-server staging is temporary, private where supported, and not encrypted at rest. Ensure enough local disk space and use encrypted volumes for sensitive content.
- Transfer histories, schedules, and workflows retain hostnames and paths (but not user passwords); handle SQLite files as sensitive metadata.
- A scheduled dispatch is not a successful transfer. Check history and manual recovery state after failures.
- Installer and EXE scripts are source packaging instructions, not evidence of a successful Windows build. No code signing is configured.

## v2.0.7 transfer and deletion security notes
The optional PS3 benchmark writes 8 MiB random data to the selected remote folder, never real user content. It attempts to delete its unique test artifacts. If the session disconnects or deletion is denied, test files may remain on the PS3; inspect and remove only matching `zftp_benchmark_` names created by this app. The preset preserves checksum verification and does not change credentials. Plain FTP is unencrypted and should be restricted to a trusted local network.
