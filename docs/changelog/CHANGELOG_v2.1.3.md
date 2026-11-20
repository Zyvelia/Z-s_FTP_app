# Z FTP v2.1.3 — Compact Transfers & Auto Disconnect

- Transfer settings use a consistent two-row grid, reducing clipping at narrower window widths.
- Workflow toolbar wraps actions into two rows, avoiding horizontal overflow.
- Transfer queue defaults to a smaller footprint; activity panel remains collapsible.
- Closing Z FTP automatically disconnects idle FTP/FTPS/SFTP client sessions using background workers.
- When operations are running, closing asks for confirmation before requesting cancellation and waits for workers to finish safely.
- The built-in FTP/FTPS and SFTP hosting services retain their explicit stop-before-exit requirement.
- Existing PS3/mmCM encoding and safe resume behavior is unchanged.

Windows GUI behavior and live network shutdown must still be verified on Windows.
