"""Application-level window and dialog orchestration."""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from typing import Optional

from dr_sidekick.app_state import AppState
from dr_sidekick.ui.library_window import SmartMediaLibraryWindow
from dr_sidekick.ui.pattern_window import PatternSequencerWindow
from dr_sidekick.ui.sample_manager import SampleManagerHost, open_sample_manager


class AppController(SampleManagerHost):
    """Own top-level UI composition so launch paths are explicit from main()."""

    def __init__(self, root: tk.Misc, state: AppState):
        self.root = root
        self.state = state
        self.loaded_card_context = "Not loaded"
        self.library_window = SmartMediaLibraryWindow(
            root,
            state,
            on_open_sample_manager=self.open_sample_manager,
            on_open_pattern_sequencer=self.open_pattern_sequencer,
        )
        self._pattern_sequencer: Optional[PatternSequencerWindow] = None
        self._pattern_window: Optional[tk.Toplevel] = None

    def open_sample_manager(self, smpinfo_path: Optional[Path] = None) -> None:
        open_sample_manager(self, smpinfo_path=smpinfo_path)

    def open_pattern_sequencer(self) -> PatternSequencerWindow:
        if self._pattern_sequencer is None or self._pattern_window is None:
            self._pattern_window = tk.Toplevel(self.root)
            debug_mode = "--debug" in sys.argv[1:]
            self._pattern_sequencer = PatternSequencerWindow(
                self._pattern_window,
                self.state,
                self.library_window,
                debug_mode=debug_mode,
            )
        else:
            self._pattern_window.deiconify()
        self._pattern_window.lift()
        return self._pattern_sequencer

    def update_status(self, message: str) -> None:
        return None

    def set_loaded_card_context(self, loaded_card: str) -> None:
        self.loaded_card_context = loaded_card
