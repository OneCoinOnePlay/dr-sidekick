#!/usr/bin/env python3
"""
Dr. Sidekick - Pattern sequencer and SmartMedia librarian for the BOSS Dr. Sample SP-303
========================================================================================

Disclaimer: Dr. Sidekick is an independent community project and is not affiliated with, endorsed by, or supported by Roland Corporation or BOSS

Author: One Coin One Play
github.com/OneCoinOnePlay
"""

import tkinter as tk
from tkinter import messagebox
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import sys
import traceback

# ── Session logger ────────────────────────────────────────────────────────────
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_PATH = _LOG_DIR / "Dr_Sidekick.log"
_log_handler = RotatingFileHandler(
    _LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
)
_log_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
log = logging.getLogger("dr_sidekick")
log.setLevel(logging.DEBUG)
log.addHandler(_log_handler)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    TKDND_AVAILABLE = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    TKDND_AVAILABLE = False

from dr_sidekick import APP_VERSION
from dr_sidekick.app_state import AppState
from dr_sidekick.ui.app_controller import AppController

def main():
    """Main entry point"""
    # Catch unhandled exceptions
    def _excepthook(exc_type, exc_value, exc_tb):
        log.error("Unhandled exception:\n%s", "".join(traceback.format_exception(exc_type, exc_value, exc_tb)).rstrip())
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = _excepthook

    # Patch messagebox.showerror so all error dialogs are logged automatically
    _orig_showerror = messagebox.showerror
    def _showerror(title="Error", message="", **kwargs):
        log.error("[dialog] %s: %s", title, message)
        return _orig_showerror(title, message, **kwargs)
    messagebox.showerror = _showerror

    log.info("=" * 60)
    log.info("Dr. Sidekick %s started", APP_VERSION)

    root = TkinterDnD.Tk() if TKDND_AVAILABLE else tk.Tk()
    state = AppState()
    AppController(root, state)
    root.mainloop()
    log.info("Dr. Sidekick session ended")


if __name__ == '__main__':
    main()
