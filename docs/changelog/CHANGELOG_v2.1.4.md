# Z FTP v2.1.4 — System Tray Controls

- Hide the application to the system tray while continuing connections, scheduled jobs, and transfers.
- Tray commands: Show/Hide, Open Transfers, Open Activity, confirm before cancelling queued or active jobs, and Quit.
- Opt-in **Close button hides to tray**: disabled by default to preserve v2.1.3 automatic disconnect on window close.
- Notifications setting synchronized between the main window and tray.
- Tray tooltip summarizes connected servers, running and queued jobs, and throughput.
- No silent cancellation or automatic reconnection; tray Quit still invokes the graceful shutdown logic.
- When the system tray is unavailable, normal closing behavior is retained.

**Windows UI and tray integration still require validation on a real desktop.**
