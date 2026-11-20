# Z FTP v2.1.1 — Label Background Fix

- Removed opaque dark rectangles behind headings, file-panel path labels, and section captions by giving ordinary `QLabel` instances transparent backgrounds.
- Retained deliberate backgrounds on status badges, transfer-stage ribbons, and footer metrics.
- Matched the table corner button and header background to the violet table header.
- Preserved the v2.1.0 layout, FTP/FTPS/SFTP and PS3/mmCM compatibility logic, transfer queue, packaging assets, and Windows build scripts.
- Added static style-regression tests.

Live Windows rendering still needs verification.
