# Z FTP v2.1.6 — Modular UI Architecture

Refactored the original 2,580-line `zftp/ui/main_window.py` by extracting active UI behaviors into `tray_controls.py`, `file_browser.py`, `transfers_view.py`, `workflow_views.py`, `shutdown_controls.py`, and `profile_controls.py`, with separate `remote_tab.py` and `ui_workers.py` modules.

The `MainWindow` retains construction and its original widget and signal wiring. Extracted controllers are mixins on the *same* `MainWindow` instance: no duplicate transfer queues, hosts, settings, or background threads. RemoteTab no longer depends on MainWindow for table creation, avoiding a cyclic import.

No intended transfer-protocol or behavior changes. Existing Windows and packaging scripts and the optional ZMT launcher are retained.

Offline tests do not validate Windows rendering, live mmCM FTP, or Windows package builds.
