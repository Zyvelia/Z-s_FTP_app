# Optional ZMT adapter (not installed)

This directory contains an opt-in launcher integration only. It does not load,
modify, or depend on ZMT, and the standalone Z FTP executable/source works without it.

To integrate into ZMT later, place a small module in the ZMT Utilities category
that imports `launch_zftp` from this directory and invokes it from the module's
launch action. A minimal example is in `example_zmt_module.py`.

**Note:** ZMT plugin metadata and `PageBridge`/PluginManager signatures must be
verified against the user's actual ZMT source before registration. This adapter
is not automatically installed or registered and is not an in-process Qt page.
