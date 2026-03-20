"""Pattern Sequencer window extracted from the legacy monolith."""

from __future__ import annotations

import json
import logging
import tkinter as tk
import traceback
import urllib.error
import urllib.request
import wave
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from dr_sidekick import APP_VERSION
from dr_sidekick.engine import (
    DEFAULT_PATTERN_LENGTH_BARS,
    GrooveLibrary,
    INTERNAL_PPQN,
    MAX_PATTERN_LENGTH_BARS,
    PatternModel,
    SLOT_COUNT,
    apply_groove_to_card,
    load_midi_notes,
    load_midi_notes_by_channel,
)
from dr_sidekick.ui.constants import (
    COLOR_PALETTES,
    COLORS,
    GRID_SNAPS,
    PAD_NAMES,
    PAD_ORDER,
)
from dr_sidekick.ui.dialogs import show_text_dialog
from dr_sidekick.ui.piano_roll import PianoRollCanvas

if TYPE_CHECKING:
    from dr_sidekick.app_state import AppState
    from dr_sidekick.ui.library_window import SmartMediaLibraryWindow

log = logging.getLogger("dr_sidekick")

class PatternSequencerWindow:
    """Main application window"""

    def __init__(self, root, state: 'AppState', lib_win: SmartMediaLibraryWindow, debug_mode: bool = False):
        self.root = root
        self.state = state
        self.lib_win = lib_win
        self.debug_mode = debug_mode
        self.root.title("Dr. Sidekick — Pattern Sequencer")
        self.root.configure(bg="#000000")
        self.root.protocol("WM_DELETE_WINDOW", self.on_hide)

        # Calculate window height: toolbar (~40) + ruler (25) + 32 lanes + statusbar (~25) + padding
        window_height = 40 + 25 + (32 * 25) + 25 + 30  # ~920 pixels
        self.root.geometry(f"1200x{window_height}")

        # Model
        self.model = PatternModel()
        self.groove_library = GrooveLibrary()
        self.current_palette = "High Contrast (White on Black)"
        self.slot_combo: Optional[ttk.Combobox] = None
        self.active_workflow = "Patterns"
        self.loaded_card_context = "Not loaded"

        # Recent files (max 5)
        self.recent_files: List[Path] = []
        self.max_recent_files = 5
        self.load_recent_files()

        # Build UI
        self._create_menu()
        self._create_toolbar()
        self._create_main_area()
        self._create_status_bar()

        # Keyboard shortcuts
        self.root.bind("<Control-n>", lambda e: self.on_new())
        self.root.bind("<Control-o>", lambda e: self.on_open())
        self.root.bind("<Control-s>", lambda e: self.on_save())
        self.root.bind("<Control-Shift-S>", lambda e: self.on_save_as())
        self.root.bind("<Control-z>", lambda e: self.on_undo())
        self.root.bind("<Control-Shift-Z>", lambda e: self.on_redo())
        self.root.bind("<Control-a>", lambda e: self.on_select_all())
        self.root.bind("<Control-x>", lambda e: self.on_cut_slot())
        self.root.bind("<Control-c>", lambda e: self.on_copy_slot())
        self.root.bind("<Control-v>", lambda e: self.on_paste_slot())
        self.root.bind("<Control-q>", lambda e: self.on_exit())
        self.root.bind("<Escape>", lambda e: self.on_clear_selection())
        self.root.bind("<d>", self.on_delete_key_root)
        self.root.bind("<D>", self.on_delete_key_root)
        self.root.bind("<Delete>", self.on_delete_key_root)
        self.root.bind("<BackSpace>", self.on_delete_key_root)
        self.root.bind("<bracketleft>", self.on_velocity_decrease_root)
        self.root.bind("<bracketright>", self.on_velocity_increase_root)

        # Zoom shortcuts
        self.root.bind("<Control-plus>", lambda e: self.on_zoom_in())
        self.root.bind("<Control-equal>", lambda e: self.on_zoom_in())
        self.root.bind("<Control-minus>", lambda e: self.on_zoom_out())
        self.root.bind("<Control-0>", lambda e: self.on_zoom_reset())

        # Pattern navigation shortcuts (Ctrl+arrows only to avoid conflicts with spinbox/entry cursor movement)
        self.root.bind("<Control-Left>", lambda e: self.on_slot_previous())
        self.root.bind("<Control-Right>", lambda e: self.on_slot_next())

        # Start with new pattern
        self.on_new()
        self.refresh_slot_labels()


    def on_hide(self):
        """Hide the Pattern Sequencer and return focus to the Library window."""
        self.root.withdraw()
        self.lib_win.root.deiconify()
        self.lib_win.root.lift()

    # ── Menu / toolbar / UI ──────────────────────────────────────────────

    def _create_menu(self):
        """Create menu bar"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # File menu
        self.file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=self.file_menu)
        self.file_menu.add_command(label="New", command=self.on_new, accelerator="Ctrl+N")
        self.file_menu.add_command(label="Open...", command=self.on_open, accelerator="Ctrl+O")

        self.recent_files_menu = tk.Menu(self.file_menu, tearoff=0)
        self.file_menu.add_cascade(label="Open Recent", menu=self.recent_files_menu)
        self.update_recent_files_menu()

        self.file_menu.add_separator()
        self.file_menu.add_command(label="Save", command=self.on_save, accelerator="Ctrl+S")
        self.file_menu.add_command(label="Save As...", command=self.on_save_as, accelerator="Ctrl+Shift+S")
        self.file_menu.add_separator()
        import_menu = tk.Menu(self.file_menu, tearoff=0)
        import_menu.add_command(label="MIDI File...", command=self.on_import_midi)
        import_menu.add_command(label="MIDI Files (Batch)...", command=self.on_import_multiple_midi)
        self.file_menu.add_cascade(label="Import", menu=import_menu)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Properties...", command=self.on_pattern_info)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Close", command=self.on_exit, accelerator="Ctrl+Q")

        # Edit menu
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        edit_menu.add_command(label="Undo", command=self.on_undo, accelerator="Ctrl+Z")
        edit_menu.add_command(label="Redo", command=self.on_redo, accelerator="Ctrl+Shift+Z")
        edit_menu.add_separator()
        edit_menu.add_command(label="Cut", command=self.on_cut_slot, accelerator="Ctrl+X")
        edit_menu.add_command(label="Copy", command=self.on_copy_slot, accelerator="Ctrl+C")
        edit_menu.add_command(label="Paste", command=self.on_paste_slot, accelerator="Ctrl+V")
        edit_menu.add_command(label="Delete", command=self.on_delete, accelerator="Del")
        edit_menu.add_separator()
        edit_menu.add_command(label="Select All", command=self.on_select_all, accelerator="Ctrl+A")
        edit_menu.add_separator()
        edit_menu.add_command(label="Clear Pattern", command=self.on_clear_slot)
        edit_menu.add_separator()
        transform_menu = tk.Menu(edit_menu, tearoff=0)
        transform_menu.add_command(label="Quantize...", command=self.on_quantize_selected)
        transform_menu.add_command(label="Apply Groove...", command=self.on_apply_groove)
        transform_menu.add_command(label="Set Velocity...", command=self.on_set_velocity)
        transform_menu.add_command(label="Reassign Pad...", command=self.on_reassign_pad)
        edit_menu.add_cascade(label="Transform", menu=transform_menu)
        edit_menu.add_command(label="Stamp Pattern...", command=self.on_stamp_pattern)
        edit_menu.add_command(label="Exchange Patterns...", command=self.on_exchange_slots)
        if self.debug_mode:
            edit_menu.add_separator()
            edit_menu.add_command(label="Generate Test Data...", command=self.on_generate_test_data)

        # View menu
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Zoom In", command=self.on_zoom_in, accelerator="Ctrl++")
        view_menu.add_command(label="Zoom Out", command=self.on_zoom_out, accelerator="Ctrl+-")
        view_menu.add_command(label="Reset Zoom", command=self.on_zoom_reset, accelerator="Ctrl+0")
        view_menu.add_separator()

        # Color palette submenu
        palette_menu = tk.Menu(view_menu, tearoff=0)
        view_menu.add_cascade(label="Color Palette", menu=palette_menu)
        self.palette_var = tk.StringVar(value=self.current_palette)
        for palette_name in COLOR_PALETTES.keys():
            palette_menu.add_radiobutton(
                label=palette_name,
                variable=self.palette_var,
                value=palette_name,
                command=lambda name=palette_name: self.on_palette_changed(name)
            )

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Keyboard Shortcuts", command=self.on_show_shortcuts)
        help_menu.add_command(label="Grooves & Patterns Help", command=self.on_show_groove_help)
        help_menu.add_separator()
        help_menu.add_command(label="About", command=self.on_about)


    def on_show_shortcuts(self):
        """Show keyboard shortcuts"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Keyboard Shortcuts")
        dialog.geometry("520x620")
        dialog.resizable(True, True)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#000000")

        canvas = tk.Canvas(dialog, bg="#000000", highlightthickness=0)
        scrollbar = ttk.Scrollbar(dialog, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        outer = tk.Frame(canvas, bg="#000000", padx=16, pady=12)
        canvas_window = canvas.create_window((0, 0), window=outer, anchor=tk.NW)

        def on_frame_configure(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)
        outer.bind("<Configure>", on_frame_configure)
        canvas.bind("<Configure>", on_canvas_configure)

        sections = [
            ("FILE", [
                ("Ctrl+N",        "New"),
                ("Ctrl+O",        "Open"),
                ("Ctrl+S",        "Save"),
                ("Ctrl+Shift+S",  "Save As"),
                ("Ctrl+Q",        "Close"),
            ]),
            ("EDIT", [
                ("Ctrl+Z",        "Undo"),
                ("Ctrl+Shift+Z",  "Redo"),
                ("Ctrl+X",        "Cut Pattern"),
                ("Ctrl+C",        "Copy Pattern"),
                ("Ctrl+V",        "Paste Pattern"),
                ("Ctrl+A",        "Select All"),
                ("Esc",           "Clear row/event selection"),
                ("D / Del / Bksp","Delete Selected"),
                ("Right-Click",   "Delete Event"),
                ("[",             "Decrease Velocity"),
                ("]",             "Increase Velocity"),
            ]),
            ("VIEW", [
                ("Ctrl++",        "Zoom In"),
                ("Ctrl+-",        "Zoom Out"),
                ("Ctrl+0",        "Reset Zoom"),
            ]),
            ("NAVIGATION", [
                ("Ctrl+Left",     "Previous Pattern"),
                ("Ctrl+Right",    "Next Pattern"),
            ]),
            ("EDIT MODES", [
                ("Draw",          "Click to add notes, drag to move, right-click to delete"),
                ("Select",        "Click to select, drag to create selection rectangle"),
                ("Erase",         "Click to delete notes"),
            ]),
            ("PAD ROWS", [
                ("Click Row Header", "Select all events on a pad row such as C1"),
                ("Click Row Again",  "Deselect the highlighted row"),
                ("Double-Click Row", "Open Reassign Pad for that source row"),
                ("Edit > Transform > Reassign Pad...", "Move the selected row or single-pad selection to another pad"),
            ]),
        ]

        row = 0
        for section_name, items in sections:
            tk.Label(
                outer, text=section_name,
                font=("", 9, "bold"), bg="#000000", fg="#888888", anchor="w",
            ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10 if row > 0 else 0, 3))
            row += 1
            for key, desc in items:
                tk.Label(
                    outer, text=key,
                    font=("TkFixedFont", 10), bg="#000000", fg="#ffffff", anchor="w", width=17,
                ).grid(row=row, column=0, sticky="w", padx=(10, 0))
                tk.Label(
                    outer, text=desc,
                    font=("TkFixedFont", 10), bg="#000000", fg="#cccccc", anchor="w",
                ).grid(row=row, column=1, sticky="w")
                row += 1

    def on_show_groove_help(self):
        """Show the Grooves & Patterns help page."""
        # Build machine/groove summary from the loaded library
        lines = []
        grid_total = 0
        compound_total = 0
        for m in self.groove_library.machines:
            grooves = self.groove_library.get_grooves(m)
            g = sum(1 for x in grooves if x.groove_type == "grid")
            c = sum(1 for x in grooves if x.groove_type == "compound")
            grid_total += g
            compound_total += c
            parts = []
            if g:
                parts.append(f"{g} grooves")
            if c:
                parts.append(f"{c} patterns")
            lines.append(f"  {m:12s}  {', '.join(parts)}")

        attr = self.groove_library.get_attribution(
            self.groove_library.machines[0]
        ) if self.groove_library.machines else {}
        attr_line = (
            f"{attr.get('author', '')} — {attr.get('license', '')}"
            if attr else "Unknown"
        )

        content = f"""\
GROOVES, PATTERNS & PAD ROWS
============================

Dr. Sidekick includes a library of timing templates captured from
classic drum machines. These let you apply the feel of vintage
hardware to your SP-303 patterns.

  Groove library:  {grid_total} grooves, {compound_total} patterns
  Attribution:     {attr_line}


PAD ROW SELECTION  (Lane Header Click)
--------------------------------------

Click a row header on the left, such as C1 or D4, to select all
events on that pad lane.

  How to use:
    1. Click a lane label such as C1
    2. The full row highlights and its events become selected
    3. Click the same row again, click the ruler spacer, or press Esc
       to clear the selection

  Double-clicking a row header opens Reassign Pad for that row.


REASSIGN PAD  (Edit > Transform > Reassign Pad...)
--------------------------------------

Moves notes from one pad to another without changing timing or
velocity.

  How to use:
    1. Click a row header such as C1
    2. Choose Edit > Transform > Reassign Pad...
    3. Pick a destination pad such as C2
    4. Click Apply

  Notes:
    - If you already selected events on exactly one pad, Reassign Pad
      can use that selection as the source.
    - The operation is undoable with Ctrl+Z.
    - Destination pads may already contain notes; the moved notes are
      merged onto that pad.


APPLY GROOVE  (Edit > Transform > Apply Groove...)
-----------------------------------

Shifts the timing of selected events to match a machine's swing
or humanised feel. The original notes stay on the same pads —
only their timing changes.

  How to use:
    1. Select the events you want to affect (Ctrl+A for all)
    2. Edit > Transform > Apply Groove...
    3. Choose a machine, then a groove
    4. Click Apply (or double-click the groove)

  The groove quantises each event to the groove's grid (e.g. 16th
  notes) and then shifts it by the machine's recorded offset.
  This is undoable (Ctrl+Z).

  Grid labels:
    16   = 16th notes      (24 ticks)
    16T  = 16th triplets   (16 ticks)
    8    = 8th notes        (48 ticks)
    8T   = 8th triplets    (32 ticks)
    4T   = quarter triplets (64 ticks)

  Tip: Apply Groove works best on events that are already close to
  a grid. If your notes are freeform, quantise them first
  (Edit > Transform > Quantize...).


STAMP PATTERN  (Edit > Stamp Pattern...)
--------------------------------------

Adds a rhythmic pattern to the current slot on a chosen pad.
Unlike Apply Groove, this creates new events — it does not move
existing ones.

  How to use:
    1. Edit > Stamp Pattern...
    2. Choose a machine and a pattern (Grid or Compound)
    3. Pick a target pad (e.g. A1)
    4. Click Stamp

  - Compound Patterns are raw musical performances (e.g. flams, rolls).
  - Grid Grooves create a straight rhythmic grid using that machine's
    timing feel (e.g. a steady 16th note hi-hat).
  - SP-303 capacity note: a pattern slot has a nominal 112-event cap,
    but dense timing can fit fewer once encoded for the hardware.
  - If Stamp reports "too dense," shorten the pattern or reduce hits.

  This is undoable (Ctrl+Z).


AVAILABLE MACHINES
------------------

{chr(10).join(lines)}
"""
        show_text_dialog(self.root, "Grooves & Patterns", content, "580x720")

    def on_about(self):
        """Show about dialog."""
        about = tk.Toplevel(self.root)
        about.title("About Dr. Sidekick")
        about.geometry("620x340")
        about.resizable(False, False)
        about.transient(self.root)
        about.grab_set()
        about.configure(bg="#000000")

        container = tk.Frame(about, bg="#000000", padx=16, pady=16)
        container.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            container,
            text=f"Dr. Sidekick v{APP_VERSION}",
            font=("", 14, "bold"),
            bg="#000000",
            fg="#ffffff",
            anchor="w",
            justify=tk.LEFT,
        ).pack(anchor=tk.W)

        tk.Label(
            container,
            text="Standalone graphical pattern editor and SmartMedia librarian for the BOSS Dr. Sample SP-303",
            wraplength=580,
            justify=tk.LEFT,
            bg="#000000",
            fg="#ffffff",
            anchor="w",
        ).pack(anchor=tk.W, pady=(8, 10))

        contacts = (
            "Author: One Coin One Play\n\n"
            "github.com/OneCoinOnePlay\n"
            "soundcloud.com/one_coin_one_play\n"
            "instagram.com/one_coin_one_play\n"
            "linkedin.com/in/onecoinoneplay\n"
            "x.com/OneCoinOnePlay\n"
            "youtube.com/@1coin1play"
        )
        tk.Label(
            container,
            text=contacts,
            justify=tk.LEFT,
            bg="#000000",
            fg="#ffffff",
            anchor="w",
        ).pack(anchor=tk.W, pady=(0, 14))

        tk.Label(
            container,
            text="Disclaimer: Dr. Sidekick is an independent community project and is not affiliated with, endorsed by, or supported by Roland Corporation or BOSS.",
            wraplength=580,
            justify=tk.LEFT,
            bg="#000000",
            fg="#cccccc",
            anchor="w",
        ).pack(anchor=tk.W)

    def _create_toolbar(self):
        """Create toolbar"""
        toolbar = ttk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        dropdown_width = 4

        # Slot selector with bank labels
        ttk.Label(toolbar, text="PATTERN", style="Toolbar.TLabel").pack(side=tk.LEFT, padx=(0, 5))

        # Previous slot button
        ttk.Button(toolbar, text="◀", width=3, command=self.on_slot_previous, style="Toolbar.TButton").pack(side=tk.LEFT, padx=(0, 2))

        # Slot dropdown (Bank C 1-8, Bank D 1-8)
        self.slot_var = tk.StringVar(value="C1")
        self.slot_labels = [f"C{i+1}" for i in range(8)] + [f"D{i+1}" for i in range(8)]
        slot_combo = ttk.Combobox(
            toolbar,
            textvariable=self.slot_var,
            values=self.slot_labels,
            width=dropdown_width,
            state="readonly",
            style="Toolbar.TCombobox"
        )
        slot_combo.pack(side=tk.LEFT, padx=(0, 2))
        slot_combo.bind("<<ComboboxSelected>>", self.on_slot_changed)
        self.slot_combo = slot_combo

        # Next slot button
        ttk.Button(toolbar, text="▶", width=3, command=self.on_slot_next, style="Toolbar.TButton").pack(side=tk.LEFT, padx=(0, 15))

        # Pattern Length (per-slot, hardware max 99 bars)
        ttk.Label(toolbar, text="LENGTH", style="Toolbar.TLabel").pack(side=tk.LEFT, padx=(0, 5))
        self.pattern_length_var = tk.IntVar(value=DEFAULT_PATTERN_LENGTH_BARS)
        length_spin = ttk.Spinbox(
            toolbar,
            from_=1,
            to=MAX_PATTERN_LENGTH_BARS,
            textvariable=self.pattern_length_var,
            width=3,
            command=self.on_pattern_length_changed,
            style="Toolbar.TSpinbox"
        )
        length_spin.pack(side=tk.LEFT, padx=(0, 5))
        length_spin.bind("<Return>", self.on_pattern_length_changed)
        # ttk.Label(toolbar, text="bars (1-99)").pack(side=tk.LEFT, padx=(0, 15))

        # Grid snap (Quantise)
        ttk.Label(toolbar, text="QUANTIZE", style="Toolbar.TLabel").pack(side=tk.LEFT, padx=(0, 5))
        self.grid_var = tk.StringVar(value="16")
        grid_combo = ttk.Combobox(
            toolbar,
            textvariable=self.grid_var,
            values=list(GRID_SNAPS.keys()),
            width=dropdown_width,
            state="readonly",
            style="Toolbar.TCombobox"
        )
        grid_combo.pack(side=tk.LEFT, padx=(0, 15))
        grid_combo.bind("<<ComboboxSelected>>", self.on_grid_changed)

        # Zoom controls
        # ttk.Label(toolbar, text="Zoom:").pack(side=tk.LEFT, padx=(0, 5))
        # ttk.Button(toolbar, text="-", width=3, command=self.on_zoom_out).pack(side=tk.LEFT, padx=(0, 2))
        # ttk.Button(toolbar, text="0", width=3, command=self.on_zoom_reset).pack(side=tk.LEFT, padx=(0, 2))
        # ttk.Button(toolbar, text="+", width=3, command=self.on_zoom_in).pack(side=tk.LEFT, padx=(0, 15))

        # Edit mode
        self.mode_var = tk.StringVar(value="Draw")
        for mode in ["Draw", "Select", "Erase"]:
            rb = ttk.Radiobutton(
                toolbar,
                text=mode,
                variable=self.mode_var,
                value=mode,
                command=self.on_mode_changed,
                style="Toolbar.TRadiobutton"
            )
            rb.pack(side=tk.LEFT, padx=(0, 5))

    def _create_main_area(self):
        """Create main editing area"""
        main_frame = ttk.Frame(self.root)
        main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Lane labels (left side) - aligned with canvas lanes
        lane_frame = tk.Frame(main_frame, width=60, bg=COLORS["lane_label_bg"])
        lane_frame.pack(side=tk.LEFT, fill=tk.Y)
        lane_frame.pack_propagate(False)

        # Canvas to draw aligned lane labels
        self.lane_canvas = tk.Canvas(
            lane_frame,
            width=60,
            bg=COLORS["lane_label_bg"],
            highlightthickness=0
        )
        self.lane_canvas.pack(fill=tk.BOTH, expand=True)
        self.lane_canvas.bind("<Button-1>", self.on_lane_click)
        self.lane_canvas.bind("<Double-Button-1>", self.on_lane_double_click)

        # Piano roll canvas with scrollbars
        canvas_frame = ttk.Frame(main_frame)
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Create scrollbars
        h_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL)
        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)

        v_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL)
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Create canvas
        self.canvas = PianoRollCanvas(
            canvas_frame,
            self.model,
            width=800,
            height=640,
            xscrollcommand=h_scrollbar.set,
            yscrollcommand=v_scrollbar.set
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.on_view_changed = self.update_lane_labels

        # Configure scrollbars
        h_scrollbar.config(command=self.canvas.xview)
        v_scrollbar.config(command=self.canvas.yview)

        # Bind resize event
        self.canvas.bind("<Configure>", lambda e: self.on_canvas_configure())

        # Initial lane label draw
        self.root.after(100, self.update_lane_labels)

    def _create_status_bar(self):
        """Create status bar"""
        status_frame = tk.Frame(self.root, bg="#000000")
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.status_bar = ttk.Label(
            status_frame,
            text="Ready",
            relief=tk.SUNKEN,
            anchor=tk.W
        )
        self.status_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(
            status_frame,
            text=self.state.config.get("device", "BOSS Dr. Sample SP-303"),
            relief=tk.SUNKEN,
            anchor=tk.E,
        ).pack(side=tk.RIGHT)

    def set_active_workflow(self, workflow: str):
        self.active_workflow = workflow
        self.refresh_context_bar()

    def set_loaded_card_context(self, loaded_card: str):
        self.loaded_card_context = loaded_card
        self.refresh_context_bar()

    def _build_context_text(self) -> str:
        return f"Workflow: {self.active_workflow} | Card Setup: {self.loaded_card_context}"

    def refresh_context_bar(self):
        if hasattr(self, "context_bar"):
            self.context_bar.config(text=self._build_context_text())



    def update_status(self, message: str):
        """Update status bar"""
        self.status_bar.config(text=message)

    def update_status_with_pattern_info(self):
        """Update status bar with pattern info (per-slot)"""
        slot_label = self.slot_var.get()
        slot_index = self.model.current_slot
        event_count = len(self.model.events)
        mapping_index = self.model.get_mapping_index(slot_index)
        mapping_text = ""
        if mapping_index is not None:
            mapped_slot = mapping_index - 1
            mapped_label = self.slot_index_to_label(mapped_slot)
            mapping_text = f" | Map {mapping_index:02d}->{mapped_label}"

        # Get per-slot pattern length from model
        pattern_length_bars = self.model.get_pattern_length_bars()

        if event_count == 0:
            status_text = f"Pattern: {slot_label} - Empty ({pattern_length_bars} bars){mapping_text}"
        else:
            # Pads used
            pads_used = len(set(e.pad for e in self.model.events))

            # Event span can be shorter than the loop length when a groove
            # intentionally leaves space before the bar wraps.
            last_tick = max(e.tick for e in self.model.events)
            event_span_bars = (last_tick + 1) / (4 * INTERNAL_PPQN)

            status_text = (
                f"Pattern: {slot_label} - {event_count} events, "
                f"{pattern_length_bars} bar loop, {event_span_bars:.1f} bar event span, "
                f"{pads_used} pads{mapping_text}"
            )

        self.status_bar.config(text=status_text)
        self.refresh_slot_labels()

    def refresh_slot_labels(self):
        """Refresh slot labels to mark slots with events"""
        self.slot_labels = [f"C{i+1}" for i in range(8)] + [f"D{i+1}" for i in range(8)]
        if self.model.ptndata is None:
            if self.slot_combo is not None:
                self.slot_combo.config(values=self.slot_labels)
            return

        labels = []
        label_by_slot = {}
        for slot in range(16):
            label = self.slot_labels[slot]
            if self.model.slot_has_pattern(slot):
                label = f"*{label}"
            labels.append(label)
            label_by_slot[slot] = label

        if self.slot_combo is not None:
            self.slot_combo.config(values=labels)
            current_slot = self.model.current_slot
            if 0 <= current_slot < 16:
                self.slot_var.set(label_by_slot[current_slot])

    def update_lane_labels(self):
        """Update lane labels to align with canvas"""
        self.lane_canvas.delete("all")
        zoom_y = self.canvas.zoom_y
        offset_y = self.canvas.offset_y
        ruler_height = 25

        # Update background color
        colors = self.canvas.colors
        self.lane_canvas.config(bg=colors["lane_label_bg"])

        # Draw spacer for ruler
        self.lane_canvas.create_rectangle(
            0, 0, 60, ruler_height,
            fill=colors["ruler_bg"],
            outline="",
            tags="ruler_spacer"
        )

        for i, pad in enumerate(PAD_ORDER):
            y = i * zoom_y - offset_y + ruler_height
            row_fill = colors["lane_label_bg"]
            if self.canvas.selected_pad_row == pad:
                row_fill = self.canvas._tk_fill_style(colors["selection_fill"])[0]
            self.lane_canvas.create_rectangle(
                0,
                y,
                60,
                y + zoom_y,
                fill=row_fill,
                outline="",
                tags=("lane_row", f"lane_pad_{pad:02x}"),
            )
            # Draw label centered in lane
            self.lane_canvas.create_text(
                30, y + zoom_y // 2,
                text=PAD_NAMES[pad],
                fill=colors["lane_label_text"],
                font=("Courier", 10, "bold"),
                tags=("lane_label", f"lane_pad_{pad:02x}"),
            )

    def on_canvas_configure(self):
        """Handle canvas resize"""
        self.canvas.redraw()
        self.update_lane_labels()

    def _pad_from_lane_y(self, y: int) -> Optional[int]:
        """Resolve a lane label click to the visible pad row."""
        ruler_height = 25
        if y < ruler_height:
            return None
        lane_index = int((y + self.canvas.offset_y - ruler_height) / self.canvas.zoom_y)
        if 0 <= lane_index < len(PAD_ORDER):
            return PAD_ORDER[lane_index]
        return None

    def _select_pad_row(self, pad: Optional[int]):
        """Select all events on a pad row and refresh both canvases."""
        self.canvas.select_pad_row(pad)
        self.update_lane_labels()
        if pad is None:
            self.update_status_with_pattern_info()
            return
        count = len(self.canvas.selected_events)
        self.update_status(f"Selected row {PAD_NAMES[pad]} ({count} event(s))")

    def on_lane_click(self, event):
        """Select a full pad row from the lane header."""
        pad = self._pad_from_lane_y(event.y)
        if pad is None:
            self._select_pad_row(None)
            return
        if self.canvas.selected_pad_row == pad:
            self._select_pad_row(None)
            return
        self._select_pad_row(pad)

    def on_lane_double_click(self, event):
        """Open pad reassignment from the clicked row."""
        pad = self._pad_from_lane_y(event.y)
        if pad is None:
            return
        self._select_pad_row(pad)
        self.on_reassign_pad()

    def slot_index_to_label(self, index: int) -> str:
        """Convert slot index (0-15) to label (C1-C8, D1-D8)"""
        if 0 <= index < len(self.slot_labels):
            return self.slot_labels[index]
        return "C1"

    def slot_label_to_index(self, label: str) -> int:
        """Convert label (C1-C8, D1-D8) to slot index (0-15)"""
        if label.startswith("*"):
            label = label[1:]
        try:
            return self.slot_labels.index(label)
        except ValueError:
            return 0

    def on_new(self):
        """Create new pattern"""
        if self.model.dirty:
            if not messagebox.askyesno("Unsaved Changes", "Discard unsaved changes?"):
                return

        self.model.new_pattern()
        self.slot_var.set("C1")

        # Reset pattern length to default
        self.pattern_length_var.set(DEFAULT_PATTERN_LENGTH_BARS)
        self.canvas.set_pattern_length(DEFAULT_PATTERN_LENGTH_BARS)

        self.canvas.selected_events.clear()
        self.canvas.selected_pad_row = None
        self.canvas.redraw()
        self.update_lane_labels()
        self.update_status_with_pattern_info()
        self.refresh_slot_labels()

    def on_open(self):
        """Open pattern files"""
        if self.model.dirty:
            if not messagebox.askyesno("Unsaved Changes", "Discard unsaved changes?"):
                return

        # Ask for PTNINFO file
        ptninfo_path = filedialog.askopenfilename(
            title="Open PTNINFO0.SP0",
            initialdir=str(self.state.default_pattern_open_dir()),
            filetypes=[("PTNINFO Files", "PTNINFO0.SP0")]
        )

        if not ptninfo_path:
            return

        ptninfo_path = Path(ptninfo_path)
        if ptninfo_path.name != "PTNINFO0.SP0":
            messagebox.showerror("Invalid File", "Please select PTNINFO0.SP0.")
            return

        # Infer PTNDATA path (same directory)
        ptndata_path = ptninfo_path.parent / "PTNDATA0.SP0"

        if not ptndata_path.exists():
            # Ask for PTNDATA file
            ptndata_path = filedialog.askopenfilename(
                title="Open PTNDATA0.SP0",
                initialdir=ptninfo_path.parent,
                filetypes=[("SP-303 Pattern Data", "PTNDATA0.SP0"), ("All Files", "*.*")]
            )
            if not ptndata_path:
                return
            ptndata_path = Path(ptndata_path)

        try:
            self.model.load_pattern(ptninfo_path, ptndata_path)
            self.slot_var.set("C1")

            # Update pattern length from loaded events
            calculated_length = self.model.get_pattern_length_bars()
            self.pattern_length_var.set(calculated_length)
            self.canvas.set_pattern_length(calculated_length)
            self.grid_var.set(self.model.get_ptninfo_quantize_display(self.model.current_slot))

            self.canvas.selected_events.clear()
            self.canvas.selected_pad_row = None
            self.canvas.redraw()
            self.update_lane_labels()

            self.update_status(f"Loaded: {ptninfo_path.parent}")
            self.refresh_slot_labels()

            # Add to recent files
            self.add_recent_file(ptninfo_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load pattern: {e}")

    def on_save(self):
        """Save pattern files"""
        if self.model.ptninfo_path is None:
            self.on_save_as()
            return

        try:
            self.model.save_pattern()
            self.canvas.redraw()
            self.update_status(f"Saved: {self.model.ptninfo_path.parent}")
            self.refresh_slot_labels()
            if self.model.last_save_warning:
                messagebox.showwarning("Pattern Save Warning", self.model.last_save_warning)
                self.model.last_save_warning = None
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save pattern: {e}")

    def on_save_as(self):
        """Save pattern files as"""
        # Ask for directory
        directory = filedialog.askdirectory(
            title="Select Output Directory",
            initialdir=str(self.state.default_pattern_save_dir())
        )
        if not directory:
            return

        directory = Path(directory)
        ptninfo_path = directory / "PTNINFO0.SP0"
        ptndata_path = directory / "PTNDATA0.SP0"

        try:
            self.model.save_pattern(ptninfo_path, ptndata_path)
            self.canvas.redraw()
            self.update_status(f"Saved: {directory}")
            self.refresh_slot_labels()
            if self.model.last_save_warning:
                messagebox.showwarning("Pattern Save Warning", self.model.last_save_warning)
                self.model.last_save_warning = None
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save pattern: {e}")

    def on_exit(self):
        """Close the Pattern Sequencer and return focus to the Library."""
        if self.model.dirty:
            if not messagebox.askyesno("Unsaved Changes", "Close without saving?"):
                return
        self.on_hide()

    def on_undo(self):
        """Undo last operation"""
        if self.model.undo():
            self.canvas.selected_events.clear()
            self.canvas.selected_pad_row = None
            self.canvas.redraw()
            self.update_lane_labels()
            self.update_status("Undo")

    def on_redo(self):
        """Redo last undone operation"""
        if self.model.redo():
            self.canvas.selected_events.clear()
            self.canvas.selected_pad_row = None
            self.canvas.redraw()
            self.update_lane_labels()
            self.update_status("Redo")

    def on_delete(self):
        """Delete selected events"""
        if self.canvas.selected_events:
            count = len(self.canvas.selected_events)
            self.model.remove_events(list(self.canvas.selected_events))
            self.canvas.selected_events.clear()
            self.canvas.selected_pad_row = None
            self.canvas.redraw()
            self.update_lane_labels()
            self.update_status(f"Deleted {count} events")
            # Update to show pattern info after a moment
            self.root.after(1500, self.update_status_with_pattern_info)

    def on_delete_key_root(self, event=None):
        """Root-level delete shortcut handler."""
        self.on_delete()
        return "break"

    def on_clear_selection(self):
        """Clear any row or event selection."""
        if not self.canvas.selected_events and self.canvas.selected_pad_row is None:
            return "break"
        self.canvas.selected_events.clear()
        self.canvas.selected_pad_row = None
        self.canvas.redraw()
        self.update_lane_labels()
        self.update_status_with_pattern_info()
        return "break"

    def on_velocity_decrease_root(self, event=None):
        """Root-level velocity decrease shortcut handler."""
        self.canvas.on_velocity_decrease(event)
        return "break"

    def on_velocity_increase_root(self, event=None):
        """Root-level velocity increase shortcut handler."""
        self.canvas.on_velocity_increase(event)
        return "break"

    def on_select_all(self):
        """Select all events"""
        self.canvas.selected_events = list(self.model.events)
        self.canvas.selected_pad_row = None
        self.canvas.redraw()
        self.update_lane_labels()
        self.update_status(f"Selected {len(self.model.events)} events")

    def on_clear_slot(self):
        """Clear current slot"""
        pattern_label = self.slot_index_to_label(self.model.current_slot)
        if messagebox.askyesno("Clear Pattern", f"Clear all events in pattern {pattern_label}?"):
            self.model.clear_slot()
            self.canvas.selected_events.clear()
            self.canvas.selected_pad_row = None
            self.canvas.redraw()
            self.update_lane_labels()
            self.update_status_with_pattern_info()
            self.refresh_slot_labels()

    def on_slot_changed(self, event=None):
        """Handle slot selection change"""
        slot_label = self.slot_var.get()
        slot_index = self.slot_label_to_index(slot_label)
        try:
            self.model.load_slot(slot_index)
        except ValueError as exc:
            # Keep UI stable when the current pattern cannot be serialized/saved.
            self.slot_var.set(self.slot_index_to_label(self.model.current_slot))
            messagebox.showerror(
                "Cannot Switch Pattern",
                f"Failed to save current pattern before switching:\n{exc}\n\n"
                "Reduce event density or pattern complexity, then try again.",
            )
            return
        if self.model.last_save_warning:
            messagebox.showwarning("CAPACITY EXCEEDED", self.model.last_save_warning)
            self.model.last_save_warning = None

        # Update pattern length from events
        calculated_length = self.model.get_pattern_length_bars()
        self.pattern_length_var.set(calculated_length)
        self.canvas.set_pattern_length(calculated_length)
        self.grid_var.set(self.model.get_ptninfo_quantize_display(slot_index))

        self.canvas.selected_events.clear()
        self.canvas.selected_pad_row = None
        self.canvas.redraw()
        self.update_lane_labels()
        self.update_status_with_pattern_info()

    def on_slot_previous(self):
        """Navigate to previous pattern slot"""
        current_label = self.slot_var.get()
        current_index = self.slot_label_to_index(current_label)
        new_index = (current_index - 1) % 16  # Wrap around
        new_label = self.slot_index_to_label(new_index)
        self.slot_var.set(new_label)
        self.on_slot_changed()

    def on_slot_next(self):
        """Navigate to next pattern slot"""
        current_label = self.slot_var.get()
        current_index = self.slot_label_to_index(current_label)
        new_index = (current_index + 1) % 16  # Wrap around
        new_label = self.slot_index_to_label(new_index)
        self.slot_var.set(new_label)
        self.on_slot_changed()

    def _focus_first_event(self):
        """Scroll to the first event so imports are immediately visible."""
        if not self.model.events:
            return
        first_event = min(self.model.events, key=lambda e: e.tick)
        first_tick = first_event.tick
        max_ticks = max(1, self.canvas.pattern_length_bars * 4 * INTERNAL_PPQN)
        lead_ticks = INTERNAL_PPQN  # Keep ~1 beat of context before first event
        target_tick = max(0, first_tick - lead_ticks)
        fraction = max(0.0, min(1.0, target_tick / max_ticks))
        self.canvas.xview_moveto(fraction)
        if first_event.pad in PAD_ORDER:
            lane_index = PAD_ORDER.index(first_event.pad)
            ruler_height = 25
            lane_context = 2
            target_y = max(0, (lane_index - lane_context) * self.canvas.zoom_y + ruler_height)
            total_height = max(1, ruler_height + (len(PAD_ORDER) * self.canvas.zoom_y) + 50)
            y_fraction = max(0.0, min(1.0, target_y / total_height))
            self.canvas.yview_moveto(y_fraction)
        self.update_lane_labels()

    def on_grid_changed(self, event=None):
        """Handle grid snap change"""
        snap_name = self.grid_var.get()
        self.canvas.set_grid_snap(snap_name)
        self.update_status(f"Quantise: {snap_name}")

    def on_pattern_length_changed(self, event=None):
        """Handle pattern length change"""
        try:
            bars = self.pattern_length_var.get()
            self.canvas.set_pattern_length(bars)
            self.model.set_current_slot_length_bars(bars)
            self.update_status(f"Pattern Length: {bars} bars")
        except tk.TclError:
            pass  # Invalid input, ignore

    def on_zoom_in(self):
        """Handle zoom in"""
        self.canvas.zoom_in()
        self.update_lane_labels()
        self.update_status(f"Zoom: {self.canvas.zoom_x:.2f}x (H) {self.canvas.zoom_y:.0f}px (V)")

    def on_zoom_out(self):
        """Handle zoom out"""
        self.canvas.zoom_out()
        self.update_lane_labels()
        self.update_status(f"Zoom: {self.canvas.zoom_x:.2f}x (H) {self.canvas.zoom_y:.0f}px (V)")

    def on_zoom_reset(self):
        """Handle zoom reset"""
        self.canvas.zoom_reset()
        self.update_lane_labels()
        self.update_status("Zoom: Reset to default")

    def on_palette_changed(self, palette_name: str):
        """Handle color palette change"""
        self.current_palette = palette_name
        self.canvas.set_color_palette(palette_name)
        self.update_lane_labels()
        self.update_status(f"Color Palette: {palette_name}")

    def on_mode_changed(self):
        """Handle edit mode change"""
        mode = self.mode_var.get()
        self.canvas.set_edit_mode(mode)
        self.update_status(f"Mode: {mode}")

    def _ask_out_of_range_action(self, out_of_range_count: int, total_count: int) -> Optional[str]:
        """Ask user how to handle out-of-range MIDI notes.

        Returns 'transpose', 'skip', or None (user cancelled).
        """
        result = messagebox.askyesnocancel(
            "Out-of-Range MIDI Notes",
            f"{out_of_range_count} of {total_count} note(s) fall outside the SP-303 range (MIDI 60–75).\n\n"
            "Yes    = Transpose  (shift all notes by best octave fit)\n"
            "No     = Skip  (drop out-of-range notes)\n"
            "Cancel = Abort import",
        )
        if result is True:
            return "transpose"
        elif result is False:
            return "skip"
        return None

    def on_import_midi(self):
        """Import MIDI file to current slot"""
        # Ask for MIDI file
        midi_path = filedialog.askopenfilename(
            title="Import MIDI File",
            filetypes=[("MIDI Files", "*.mid"), ("All Files", "*.*")]
        )

        if not midi_path:
            return

        midi_path = Path(midi_path)

        try:
            channel_notes, ppqn = load_midi_notes_by_channel(str(midi_path))
            active_channels = [ch for ch in range(1, 17) if channel_notes[ch]]
            if not active_channels:
                messagebox.showerror("MIDI Import Error", "No note events found in this MIDI file.")
                return

            channel_counts = ", ".join(f"Ch {ch}: {len(channel_notes[ch])}" for ch in active_channels)
            channel_choice = simpledialog.askstring(
                "MIDI Channel",
                "Select channel to import.\n"
                "Enter 1-16 or ALL.\n\n"
                f"Detected channels: {channel_counts}",
                initialvalue="1",
            )
            if not channel_choice:
                return
            channel_choice = channel_choice.strip().upper()

            if channel_choice == "ALL":
                start_slot = self.model.current_slot
                available_slots = SLOT_COUNT - start_slot
                channels_to_import = active_channels[:available_slots]
                if not channels_to_import:
                    messagebox.showerror("MIDI Import Error", "No available pattern slots for channel import.")
                    return

                if len(active_channels) > available_slots:
                    messagebox.showwarning(
                        "MIDI Import",
                        f"Detected {len(active_channels)} channels, but only {available_slots} pattern slots are available "
                        f"from {self.slot_index_to_label(start_slot)} to D8.\n"
                        f"Importing first {available_slots} channel(s)."
                    )

                end_slot = start_slot + len(channels_to_import) - 1
                mapping_preview = []
                for i, ch in enumerate(channels_to_import[:8]):
                    mapping_preview.append(f"{self.slot_index_to_label(start_slot + i)} <- Ch {ch}")
                if len(channels_to_import) > 8:
                    mapping_preview.append("...")
                proceed_all = messagebox.askyesno(
                    "Import All Channels",
                    "ALL will populate multiple patterns, not just the current one.\n\n"
                    f"Range: {self.slot_index_to_label(start_slot)} to {self.slot_index_to_label(end_slot)}\n"
                    f"Channels to import: {len(channels_to_import)}\n\n"
                    "Preview:\n"
                    + "\n".join(mapping_preview)
                    + "\n\nContinue?",
                )
                if not proceed_all:
                    return

                # Check for out-of-range notes across all channels being imported
                all_notes_flat = [n for ch in channels_to_import for _, n, _ in channel_notes[ch]]
                oor_count = sum(1 for n in all_notes_flat if not (60 <= n <= 75))
                all_out_of_range_action = "skip"
                if oor_count > 0:
                    all_out_of_range_action = self._ask_out_of_range_action(oor_count, len(all_notes_flat))
                    if all_out_of_range_action is None:
                        return

                import_results = []
                for i, ch in enumerate(channels_to_import):
                    target_slot = start_slot + i
                    target_label = self.slot_index_to_label(target_slot)
                    self.model.load_slot(target_slot)
                    import_meta = self.model.import_midi(
                        midi_path,
                        replace=True,
                        notes_override=channel_notes[ch],
                        ppqn_override=ppqn,
                        out_of_range=all_out_of_range_action,
                    )
                    self.model.save_slot()
                    count = import_meta["imported_events"]
                    import_results.append(
                        f"{target_label}: Ch {ch} -> {count} events, {import_meta['imported_length_bars']} bars"
                    )

                # Reload first imported pattern for immediate review.
                self.model.load_slot(start_slot)
                self.slot_var.set(self.slot_index_to_label(start_slot))
                calculated_length = self.model.get_pattern_length_bars()
                self.pattern_length_var.set(calculated_length)
                self.canvas.set_pattern_length(calculated_length)
                self.grid_var.set(self.model.get_ptninfo_quantize_display(self.model.current_slot))
                self.canvas.selected_events.clear()
                self.canvas.selected_pad_row = None
                self.canvas.redraw()
                self.update_lane_labels()
                self._focus_first_event()
                self.refresh_slot_labels()
                self.update_status(
                    f"MIDI Import: imported {len(channels_to_import)} channel(s) across patterns "
                    f"from {self.slot_index_to_label(start_slot)}"
                )
                messagebox.showinfo(
                    "MIDI Import (All Channels)",
                    f"Imported {len(channels_to_import)} channel(s) from {midi_path.name}:\n\n"
                    + "\n".join(import_results),
                )
                return

            # Single-channel import path.
            if channel_choice.isdigit() and 1 <= int(channel_choice) <= 16:
                selected_channel = int(channel_choice)
            else:
                messagebox.showerror("MIDI Channel", "Enter a channel number 1-16 or ALL.")
                return

            if not channel_notes[selected_channel]:
                messagebox.showwarning("MIDI Import", f"Channel {selected_channel} has no note events.")
                return

            # Check for out-of-range notes before asking replace/append
            ch_notes = channel_notes[selected_channel]
            oor_count = sum(1 for _, n, _ in ch_notes if not (60 <= n <= 75))
            single_out_of_range_action = "skip"
            if oor_count > 0:
                single_out_of_range_action = self._ask_out_of_range_action(oor_count, len(ch_notes))
                if single_out_of_range_action is None:
                    return

            replace = messagebox.askyesno(
                "Import MIDI",
                f"Replace current pattern events with Channel {selected_channel}?\n\n"
                "Yes = Replace all events\n"
                "No = Append to existing events"
            )

            import_meta = self.model.import_midi(
                midi_path,
                replace=replace,
                notes_override=channel_notes[selected_channel],
                ppqn_override=ppqn,
                out_of_range=single_out_of_range_action,
            )
            count = import_meta["imported_events"]

            # Update pattern length from imported events
            calculated_length = self.model.get_pattern_length_bars()
            self.pattern_length_var.set(calculated_length)
            self.canvas.set_pattern_length(calculated_length)

            self.canvas.selected_events.clear()
            self.canvas.selected_pad_row = None
            self.canvas.redraw()
            self.update_lane_labels()
            self._focus_first_event()
            action = "Replaced" if replace else "Added"
            status_msg = f"MIDI Import: {action} {count} events from Ch {selected_channel}"
            if import_meta["truncated_events"] > 0:
                status_msg += f" (Time Truncated: {import_meta['truncated_events']} notes)"
            if import_meta["density_truncated_events"] > 0:
                status_msg += f" (CAPACITY EXCEEDED: {import_meta['density_truncated_events']} notes dropped)"
            
            self.update_status(status_msg)
            self.refresh_slot_labels()

            truncation_note = ""
            if import_meta["truncated_events"] > 0:
                truncation_note = (
                    f"\n\nMIDI length limit applied ({import_meta['max_bars']} bars max):\n"
                    f"  Removed {import_meta['truncated_events']} event(s)\n"
                    f"  Dropped approximately {import_meta['truncated_bars']:.1f} bar(s) from source"
                )
            density_note = ""
            if import_meta["density_truncated_events"] > 0:
                capped_source_bars = min(import_meta["max_bars"], import_meta.get("source_bars", import_meta["max_bars"]))
                discarded_capacity_bars = max(0.0, capped_source_bars - float(import_meta["imported_length_bars"]))
                if import_meta["truncated_events"] > 0:
                    density_note = (
                        "\n\nThen device capacity limit applied:\n"
                        f"  Kept approximately {float(import_meta['imported_length_bars']):.1f} bar(s) "
                        f"from the {import_meta['max_bars']}-bar capped import\n"
                        f"  Discarded approximately {discarded_capacity_bars:.1f} additional bar(s)\n"
                        f"  Removed {import_meta['density_truncated_events']} trailing event(s)"
                    )
                else:
                    density_note = (
                        "\n\nDevice capacity limit applied:\n"
                        f"  Loaded approximately {float(import_meta['imported_length_bars']):.1f} bar(s)\n"
                        f"  Discarded approximately {discarded_capacity_bars:.1f} bar(s)\n"
                        f"  Removed {import_meta['density_truncated_events']} trailing event(s)"
                    )

            oor_note = ""
            if import_meta["skipped_out_of_range"] > 0:
                if import_meta["transpose_shift"] != 0:
                    semitones = import_meta["transpose_shift"]
                    direction = "up" if semitones > 0 else "down"
                    oor_note = (
                        f"\n\nOut-of-range handling (transposed {abs(semitones)} semitones {direction}):\n"
                        f"  Skipped {import_meta['skipped_out_of_range']} note(s) still outside range after shift"
                    )
                else:
                    oor_note = f"\n\nSkipped {import_meta['skipped_out_of_range']} out-of-range note(s)"
            elif import_meta["transpose_shift"] != 0:
                semitones = import_meta["transpose_shift"]
                direction = "up" if semitones > 0 else "down"
                oor_note = f"\n\nTransposed {abs(semitones)} semitones {direction} (best octave fit)"

            messagebox.showinfo(
                "MIDI Import",
                f"Successfully imported {count} events from {midi_path.name} (Channel {selected_channel})\n\n"
                "Note mapping:\n"
                "  MIDI 60-75 -> SP-303 Pads C1-D8\n\n"
                f"Pattern length: {calculated_length} bars"
                f"{oor_note}"
                f"{truncation_note}"
                f"{density_note}"
            )
        except Exception as e:
            messagebox.showerror("MIDI Import Error", f"Failed to import MIDI file:\n{e}")

    def on_import_multiple_midi(self):
        """Import multiple MIDI files to consecutive patterns."""
        # Ask for multiple MIDI files
        midi_paths = filedialog.askopenfilenames(
            title="Import Multiple MIDI Files (up to 16)",
            filetypes=[("MIDI Files", "*.mid"), ("All Files", "*.*")]
        )

        if not midi_paths:
            return

        midi_paths = [Path(p) for p in midi_paths]

        # Limit to 16 files
        if len(midi_paths) > 16:
            messagebox.showwarning(
                "Too Many Files",
                f"Selected {len(midi_paths)} files, but only 16 patterns (C1-D8) are available.\n"
                "Only the first 16 files will be imported."
            )
            midi_paths = midi_paths[:16]

        # Ask for starting pattern
        start_pattern = simpledialog.askstring(
            "Starting Pattern",
            f"Import {len(midi_paths)} file(s) starting at which pattern?\n\n"
            "Use C1-C8 or D1-D8 (example: C1).",
            initialvalue="C1",
        )

        if not start_pattern:
            return
        start_pattern = start_pattern.strip().upper()
        valid_patterns = [f"C{i+1}" for i in range(8)] + [f"D{i+1}" for i in range(8)]
        if start_pattern not in valid_patterns:
            messagebox.showerror("Invalid Pattern", "Please enter a valid pattern label (C1-D8).")
            return
        start_slot = valid_patterns.index(start_pattern)

        # Check if we have enough patterns
        if start_slot + len(midi_paths) > 16:
            available_patterns = 16 - start_slot
            messagebox.showerror(
                "Not Enough Patterns",
                f"Cannot import {len(midi_paths)} files starting at {start_pattern}.\n"
                f"Only {available_patterns} pattern(s) are available from {start_pattern} to D8.\n\n"
                "Choose an earlier starting pattern or fewer files."
            )
            return

        end_slot = start_slot + len(midi_paths) - 1
        proceed_batch = messagebox.askyesno(
            "Confirm Batch Import",
            "Batch import will populate multiple patterns.\n\n"
            f"Range: {self.slot_index_to_label(start_slot)} to {self.slot_index_to_label(end_slot)}\n"
            f"Files: {len(midi_paths)}\n\n"
            "Each file replaces the target pattern.\n\n"
            "Continue?",
        )
        if not proceed_batch:
            return

        # Pre-scan all files for out-of-range notes and ask once
        batch_oor_count = 0
        batch_total_count = 0
        for p in midi_paths:
            try:
                scan_notes, _ = load_midi_notes(str(p))
                batch_total_count += len(scan_notes)
                batch_oor_count += sum(1 for _, n, _ in scan_notes if not (60 <= n <= 75))
            except Exception:
                pass
        batch_out_of_range_action = "skip"
        if batch_oor_count > 0:
            batch_out_of_range_action = self._ask_out_of_range_action(batch_oor_count, batch_total_count)
            if batch_out_of_range_action is None:
                return

        # Import each file
        import_results = []
        failed_imports = []

        for i, midi_path in enumerate(midi_paths):
            target_slot = start_slot + i
            try:
                # Switch to target slot
                self.model.load_slot(target_slot)

                # Import MIDI (replace mode)
                import_meta = self.model.import_midi(midi_path, replace=True, out_of_range=batch_out_of_range_action)
                self.model.save_slot()
                count = import_meta["imported_events"]

                target_label = self.slot_index_to_label(target_slot)
                if import_meta["truncated_events"] > 0:
                    entry = (
                        f"Pattern {target_label}: {midi_path.name} "
                        f"({count} events; 99-bar cap removed ~{import_meta['truncated_bars']:.1f} bars)"
                    )
                    if import_meta["density_truncated_events"] > 0:
                        capped_source_bars = min(
                            import_meta["max_bars"],
                            import_meta.get("source_bars", import_meta["max_bars"])
                        )
                        discarded_capacity_bars = max(
                            0.0,
                            capped_source_bars - float(import_meta["imported_length_bars"])
                        )
                        entry += (
                            f" -> then capacity kept ~{float(import_meta['imported_length_bars']):.1f} bars, "
                            f"discarded ~{discarded_capacity_bars:.1f} bars"
                        )
                    import_results.append(entry)
                elif import_meta["density_truncated_events"] > 0:
                    capped_source_bars = min(import_meta["max_bars"], import_meta.get("source_bars", import_meta["max_bars"]))
                    discarded_capacity_bars = max(0.0, capped_source_bars - float(import_meta["imported_length_bars"]))
                    import_results.append(
                        f"Pattern {target_label}: {midi_path.name} "
                        f"({count} events, loaded ~{float(import_meta['imported_length_bars']):.1f} bars, "
                        f"discarded ~{discarded_capacity_bars:.1f} bars)"
                    )
                else:
                    import_results.append(f"Pattern {target_label}: {midi_path.name} ({count} events)")
            except Exception as e:
                target_label = self.slot_index_to_label(target_slot)
                failed_imports.append(f"Pattern {target_label}: {midi_path.name} - {str(e)}")

        # Ensure the last imported pattern is committed before reloading target.
        self.model.save_slot()

        # Switch to first imported slot
        self.model.load_slot(start_slot)
        self.slot_var.set(self.slot_index_to_label(start_slot))

        # Update pattern length from imported events
        calculated_length = self.model.get_pattern_length_bars()
        self.pattern_length_var.set(calculated_length)
        self.canvas.set_pattern_length(calculated_length)

        self.canvas.selected_events.clear()
        self.canvas.selected_pad_row = None
        self.canvas.redraw()
        self.update_lane_labels()
        self._focus_first_event()
        self.update_status_with_pattern_info()

        # Show results
        result_text = f"Successfully imported {len(import_results)} file(s):\n\n"
        result_text += "\n".join(import_results)

        if failed_imports:
            result_text += f"\n\nFailed to import {len(failed_imports)} file(s):\n\n"
            result_text += "\n".join(failed_imports)

        result_text += "\n\nNote mapping:\n"
        result_text += "  MIDI 60-75 -> SP-303 Pads C1-D8"
        if batch_oor_count > 0:
            if batch_out_of_range_action == "transpose":
                result_text += "\n  Out-of-range notes: transposed by best octave fit"
            else:
                result_text += "\n  Out-of-range notes: skipped"

        messagebox.showinfo("Multiple MIDI Import", result_text)

    def on_generate_test_data(self):
        """Generate test data across all slots"""
        if self.model.ptninfo is None or self.model.ptndata is None:
            create_new = messagebox.askyesno(
                "Generate Test Data",
                "No pattern files loaded.\n\nCreate new pattern files and generate test data?"
            )
            if not create_new:
                return
            self.model.new_pattern()

        if not messagebox.askyesno(
            "Generate Test Data",
            "This will overwrite all 16 patterns (C1-D8) with generated test data.\n\nContinue?"
        ):
            return

        try:
            self.model.generate_test_data()
            self.slot_var.set("C1")

            calculated_length = self.model.get_pattern_length_bars()
            self.pattern_length_var.set(calculated_length)
            self.canvas.set_pattern_length(calculated_length)

            self.canvas.selected_events.clear()
            self.canvas.selected_pad_row = None
            self.canvas.redraw()
            self.update_lane_labels()
            self.update_status("Generated test data (Bank C + D: asc/desc A1-D8, 1-8 bars, quantize cycle)")
            self.refresh_slot_labels()

            messagebox.showinfo(
                "Generate Test Data",
                "Bank C + D\n"
                "Bank C: ascending or descending across A1–D8\n"
                "Bank D: ascending then reversing within the same pattern\n"
                "Bar lengths from 1 to 8\n"
                "Quantize cycles through Off, 1/4, 1/8, 1/16, 1/8T, and 1/16T"
            )
        except Exception as e:
            messagebox.showerror("Generate Test Data", f"Failed to generate test data:\n{e}")

    def on_quantize_selected(self):
        """Quantize selected events"""
        if not self.canvas.selected_events:
            messagebox.showwarning("Quantize", "No events selected")
            return

        # Create dialog
        dialog = tk.Toplevel(self.root)
        dialog.title("Quantize Selected Events")
        dialog.geometry("320x240")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#000000")

        # Quantize options
        ttk.Label(dialog, text="Quantize to:").pack(pady=(10, 6))

        quantize_var = tk.StringVar(value="16")
        quantize_options_frame = ttk.Frame(dialog)
        quantize_options_frame.pack(fill=tk.X, padx=20)

        def apply_quantize(snap_name: str, close_after: bool = True):
            quantize_ticks = GRID_SNAPS[snap_name]
            if quantize_ticks == 0:
                messagebox.showwarning("Quantize", "Cannot quantize to 'Off'")
                if close_after:
                    dialog.destroy()
                return

            before_ticks = {id(evt): evt.tick for evt in self.canvas.selected_events}
            self.model.quantize_events(list(self.canvas.selected_events), quantize_ticks)
            moved_count = sum(
                1 for evt in self.canvas.selected_events
                if before_ticks.get(id(evt), evt.tick) != evt.tick
            )
            self.canvas.redraw()
            if moved_count == 0:
                self.update_status(
                    f"Quantize: no change ({len(self.canvas.selected_events)} event(s) already on {snap_name} grid)"
                )
            else:
                self.update_status(
                    f"Quantized {len(self.canvas.selected_events)} event(s) to {snap_name} ({moved_count} moved)"
                )
            if close_after:
                dialog.destroy()

        for snap_name in GRID_SNAPS.keys():
            rb = ttk.Radiobutton(
                quantize_options_frame,
                text=snap_name,
                variable=quantize_var,
                value=snap_name,
                command=lambda: apply_quantize(quantize_var.get(), close_after=True)
            )
            rb.pack(anchor=tk.W)

        # Buttons
        button_frame = ttk.Frame(dialog)
        button_frame.pack(pady=(10, 10))
        ttk.Button(button_frame, text="Apply", command=lambda: apply_quantize(quantize_var.get(), close_after=True)).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

        # Center dialog
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

    def on_apply_groove(self):
        """Apply a groove template to selected events."""
        if not self.canvas.selected_events:
            messagebox.showwarning("Apply Groove", "No events selected")
            return

        if not self.groove_library.machines:
            messagebox.showwarning("Apply Groove", "No groove files found in grooves/ folder")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Apply Groove")
        dialog.geometry("420x460")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#000000")

        # Attribution label (updated when machine changes)
        attr_var = tk.StringVar(value="")
        attr_label = ttk.Label(dialog, textvariable=attr_var, wraplength=380,
                               font=("TkDefaultFont", 9, "italic"))
        attr_label.pack(side=tk.BOTTOM, pady=(0, 8), padx=10)

        # Machine picker
        ttk.Label(dialog, text="Machine:").pack(pady=(10, 4))
        machine_var = tk.StringVar()
        machine_combo = ttk.Combobox(dialog, textvariable=machine_var,
                                     values=self.groove_library.machines,
                                     state="readonly", width=30)
        machine_combo.pack(padx=20)

        # Groove listbox
        ttk.Label(dialog, text="Groove:").pack(pady=(10, 4))
        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 6))
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        groove_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set,
                                    bg="#1a1a1a", fg="#cccccc",
                                    selectbackground="#335533",
                                    selectforeground="#ffffff",
                                    font=("TkFixedFont", 11))
        groove_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=groove_listbox.yview)

        # Track the currently loaded groove list
        current_grooves: list = []

        def on_machine_change(_event=None):
            current_grooves.clear()
            groove_listbox.delete(0, tk.END)
            machine = machine_var.get()
            grooves = [g for g in self.groove_library.get_grooves(machine)
                       if g.groove_type == "grid"]
            current_grooves.extend(grooves)
            for g in grooves:
                groove_listbox.insert(tk.END, f"{g.name}  [{g.grid_label}]")
            # Update attribution
            attr = self.groove_library.get_attribution(machine)
            if attr:
                attr_var.set(f"Grooves by {attr.get('author', '?')}  •  {attr.get('license', '')}")
            else:
                attr_var.set("")

        machine_combo.bind("<<ComboboxSelected>>", on_machine_change)

        def apply_selected():
            sel = groove_listbox.curselection()
            if not sel:
                messagebox.showwarning("Apply Groove", "Select a groove first")
                return
            groove = current_grooves[sel[0]]
            events = list(self.canvas.selected_events)
            moved = self.model.apply_groove(events, groove)
            self.canvas.redraw()
            self.update_status(
                f"Groove '{groove.name}' applied to {len(events)} event(s) ({moved} moved)"
            )
            dialog.destroy()

        # Buttons
        button_frame = ttk.Frame(dialog)
        button_frame.pack(pady=(6, 10))
        ttk.Button(button_frame, text="Apply", command=apply_selected).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

        # Double-click to apply
        groove_listbox.bind("<Double-1>", lambda _e: apply_selected())

        # Select first machine
        if self.groove_library.machines:
            machine_combo.current(0)
            on_machine_change()

        # Center dialog
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

    def on_stamp_pattern(self):
        """Stamp a groove or rhythm pattern into the current slot on a chosen pad."""
        if not self.groove_library.machines:
            messagebox.showwarning("Stamp Pattern", "No groove files found in grooves/ folder")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Stamp Pattern")
        dialog.geometry("420x520")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#000000")

        # Attribution
        attr_var = tk.StringVar(value="")
        attr_label = ttk.Label(dialog, textvariable=attr_var, wraplength=380,
                               font=("TkDefaultFont", 9, "italic"))
        attr_label.pack(side=tk.BOTTOM, pady=(0, 8), padx=10)

        # Machine picker
        ttk.Label(dialog, text="Machine:").pack(pady=(10, 4))
        machine_var = tk.StringVar()
        machine_combo = ttk.Combobox(dialog, textvariable=machine_var,
                                     values=self.groove_library.machines,
                                     state="readonly", width=30)
        machine_combo.pack(padx=20)

        # Pattern listbox
        ttk.Label(dialog, text="Groove/Pattern:").pack(pady=(10, 4))
        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 6))
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        pattern_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set,
                                     bg="#1a1a1a", fg="#cccccc",
                                     selectbackground="#335533",
                                     selectforeground="#ffffff",
                                     exportselection=False,
                                     font=("TkFixedFont", 11))
        pattern_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=pattern_listbox.yview)

        # Pad picker
        pad_frame = ttk.Frame(dialog)
        pad_frame.pack(fill=tk.X, padx=20, pady=(6, 0))
        ttk.Label(pad_frame, text="Target pad:").pack(side=tk.LEFT)
        pad_var = tk.StringVar(value="A1")
        pad_values = [PAD_NAMES[p] for p in PAD_ORDER]
        pad_combo = ttk.Combobox(pad_frame, textvariable=pad_var,
                                 values=pad_values, state="readonly", width=6)
        pad_combo.pack(side=tk.LEFT, padx=(8, 0))

        current_patterns: list = []

        def on_machine_change(_event=None):
            current_patterns.clear()
            pattern_listbox.delete(0, tk.END)
            machine = machine_var.get()
            patterns = self.groove_library.get_grooves(machine)
            current_patterns.extend(patterns)
            for g in patterns:
                if g.groove_type == "compound":
                    label = f"{g.name}  ({len(g.ticks)} hits)"
                else:
                    label = f"{g.name}  [{g.grid_label}]"
                pattern_listbox.insert(tk.END, label)
            
            # Select first item by default when machine changes
            if pattern_listbox.size() > 0:
                pattern_listbox.selection_set(0)
                pattern_listbox.activate(0)

            attr = self.groove_library.get_attribution(machine)
            if attr:
                attr_var.set(f"Grooves by {attr.get('author', '?')}  •  {attr.get('license', '')}")
            else:
                attr_var.set("")

        machine_combo.bind("<<ComboboxSelected>>", on_machine_change)

        def apply_selected():
            sel = pattern_listbox.curselection()
            if not sel:
                messagebox.showwarning("Stamp Pattern", "Select a pattern first")
                return
            groove = current_patterns[sel[0]]
            # Resolve pad name to pad number
            pad_name = pad_var.get()
            pad_num = next((p for p, n in PAD_NAMES.items() if n == pad_name), 0x00)
            added = self.model.stamp_pattern(groove, pad_num)
            self.canvas.redraw()
            self.update_status(
                f"Stamped '{groove.name}' on pad {pad_name} ({added} events added)"
            )
            if self.model.last_stamp_warning:
                messagebox.showwarning("Stamp Pattern", self.model.last_stamp_warning)
                self.model.last_stamp_warning = None
            dialog.destroy()

        # Buttons
        button_frame = ttk.Frame(dialog)
        button_frame.pack(pady=(6, 10))
        ttk.Button(button_frame, text="Stamp", command=apply_selected).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

        pattern_listbox.bind("<Double-1>", lambda _e: apply_selected())

        # Select first machine by default
        if self.groove_library.machines:
            machine_combo.current(0)
            on_machine_change()

        # Center dialog
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

    def on_set_velocity(self):
        """Set velocity for selected events"""
        if not self.canvas.selected_events:
            messagebox.showwarning("Set Velocity", "No events selected")
            return

        # Get current average velocity
        avg_vel = sum(e.velocity for e in self.canvas.selected_events) // len(self.canvas.selected_events)

        # Ask for new velocity
        new_vel = simpledialog.askinteger(
            "Set Velocity",
            f"Enter velocity (0-127):\n\nCurrent average: {avg_vel}",
            initialvalue=avg_vel,
            minvalue=0,
            maxvalue=127
        )

        if new_vel is None:
            return

        # Apply to all selected events as a single undoable operation
        self.model.push_undo_state()
        for evt in self.canvas.selected_events:
            evt.velocity = max(0, min(127, new_vel))
        self.model.dirty = True

        self.canvas.redraw()
        self.update_lane_labels()
        self.update_status(f"Set velocity to {new_vel} for {len(self.canvas.selected_events)} events")

    def on_reassign_pad(self):
        """Reassign events from one pad row to another."""
        source_pad = self.canvas.selected_pad_row
        candidate_events = None
        if source_pad is None:
            if not self.canvas.selected_events:
                messagebox.showwarning(
                    "Reassign Pad",
                    "Select a row header such as C1, or select events on a single pad first.",
                )
                return
            source_pads = {event.pad for event in self.canvas.selected_events}
            if len(source_pads) != 1:
                messagebox.showwarning(
                    "Reassign Pad",
                    "Selected events span multiple pads. Click a single row header to choose the source pad.",
                )
                return
            source_pad = next(iter(source_pads))
            candidate_events = list(self.canvas.selected_events)

        source_label = PAD_NAMES.get(source_pad, f"0x{source_pad:02X}")
        target_values = [PAD_NAMES[pad] for pad in PAD_ORDER if pad != source_pad]

        dialog = tk.Toplevel(self.root)
        dialog.title("Reassign Pad")
        dialog.geometry("320x180")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#000000")

        ttk.Label(dialog, text=f"Move events from {source_label} to:").pack(pady=(14, 8))
        target_var = tk.StringVar(value=target_values[0])
        target_combo = ttk.Combobox(
            dialog,
            textvariable=target_var,
            values=target_values,
            state="readonly",
            width=10,
        )
        target_combo.pack(pady=(0, 12))

        scope_text = (
            f"Scope: selected events on {source_label}"
            if candidate_events is not None
            else f"Scope: all events on row {source_label}"
        )
        ttk.Label(dialog, text=scope_text).pack(pady=(0, 12))

        def apply_reassign():
            target_label = target_var.get()
            target_pad = next((pad for pad, name in PAD_NAMES.items() if name == target_label), None)
            if target_pad is None:
                messagebox.showwarning("Reassign Pad", "Choose a target pad.", parent=dialog)
                return
            moved = self.model.reassign_pad(source_pad, target_pad, events=candidate_events)
            if moved == 0:
                messagebox.showwarning(
                    "Reassign Pad",
                    f"No events found on {source_label} to move.",
                    parent=dialog,
                )
                return
            self.canvas.selected_pad_row = target_pad
            self.canvas.selected_events = [event for event in self.model.events if event.pad == target_pad]
            self.canvas.redraw()
            self.update_lane_labels()
            self.update_status(f"Reassigned {moved} event(s) from {source_label} to {target_label}")
            dialog.destroy()

        button_frame = ttk.Frame(dialog)
        button_frame.pack(pady=(0, 10))
        ttk.Button(button_frame, text="Apply", command=apply_reassign).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

    def on_copy_slot(self):
        """Copy current slot"""
        self.model.copy_slot()
        event_count = len(self.model.slot_clipboard) if self.model.slot_clipboard else 0
        pattern_label = self.slot_index_to_label(self.model.current_slot)
        self.update_status(f"Copied pattern {pattern_label} ({event_count} events)")

    def on_cut_slot(self):
        """Cut current slot to clipboard."""
        self.model.copy_slot()
        event_count = len(self.model.slot_clipboard) if self.model.slot_clipboard else 0
        pattern_label = self.slot_index_to_label(self.model.current_slot)
        self.model.clear_slot()
        self.canvas.selected_events.clear()
        self.canvas.selected_pad_row = None
        self.canvas.redraw()
        self.update_lane_labels()
        self.refresh_slot_labels()
        self.update_status(f"Cut pattern {pattern_label} ({event_count} events)")

    def on_paste_slot(self):
        """Paste to current slot"""
        if self.model.slot_clipboard is None:
            messagebox.showwarning("Paste Pattern", "No pattern in clipboard")
            return

        self.model.paste_slot()
        self.canvas.selected_events.clear()
        self.canvas.selected_pad_row = None
        self.canvas.redraw()
        self.update_lane_labels()
        pattern_label = self.slot_index_to_label(self.model.current_slot)
        self.update_status(f"Pasted {len(self.model.events)} events to pattern {pattern_label}")
        self.refresh_slot_labels()

    def on_pattern_info(self):
        """Show pattern info"""
        if not self.model.events:
            messagebox.showinfo("Properties", "Current pattern is empty")
            return

        # Calculate pattern info
        event_count = len(self.model.events)
        first_tick = min(e.tick for e in self.model.events)
        last_tick = max(e.tick for e in self.model.events)
        event_span_ticks = (last_tick - first_tick) + 1
        event_span_bars = event_span_ticks / (4 * INTERNAL_PPQN)
        loop_length_bars = self.model.get_pattern_length_bars()
        loop_length_ticks = loop_length_bars * 4 * INTERNAL_PPQN

        # Pads used
        pads_used = set(e.pad for e in self.model.events)
        pad_names = [PAD_NAMES.get(p, f"0x{p:02X}") for p in sorted(pads_used)]

        # Velocity range
        min_vel = min(e.velocity for e in self.model.events)
        max_vel = max(e.velocity for e in self.model.events)
        avg_vel = sum(e.velocity for e in self.model.events) // event_count

        # Build info text
        slot_label = self.slot_var.get()
        info_text = f"""Pattern {slot_label} Properties:

Events: {event_count}
Loop Length: {loop_length_bars:.2f} bars ({loop_length_ticks} ticks)
Event Span: {event_span_bars:.2f} bars ({event_span_ticks} ticks)
First Event: Tick {first_tick}
Last Event: Tick {last_tick}

Pads Used ({len(pads_used)}):
  {', '.join(pad_names)}

Velocity:
  Min: {min_vel}
  Max: {max_vel}
  Average: {avg_vel}
"""

        messagebox.showinfo("Properties", info_text)

    def on_exchange_slots(self):
        """Exchange PTNINFO slot mappings."""
        if self.model.ptninfo is None or self.model.ptndata is None:
            messagebox.showwarning("No Pattern Files", "Load or create pattern files first.")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Exchange Patterns")
        dialog.geometry("330x165")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#000000")

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Swap pattern mappings between two patterns.").pack(anchor=tk.W, pady=(0, 10))

        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=(0, 12))

        slot_labels = [f"C{i+1}" for i in range(8)] + [f"D{i+1}" for i in range(8)]
        current_label = self.slot_index_to_label(self.model.current_slot)
        current_index = self.slot_label_to_index(current_label)
        default_to_index = current_index + 1 if current_index < 15 else max(0, current_index - 1)

        from_var = tk.StringVar(value=current_label)
        to_var = tk.StringVar(value=slot_labels[default_to_index])

        ttk.Label(row, text="From").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Combobox(row, textvariable=from_var, values=slot_labels, state="readonly", width=8).grid(
            row=0, column=1, sticky="w", padx=(0, 12)
        )
        ttk.Label(row, text="To").grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Combobox(row, textvariable=to_var, values=slot_labels, state="readonly", width=8).grid(
            row=0, column=3, sticky="w"
        )

        def do_exchange():
            from_label = from_var.get().strip().upper()
            to_label = to_var.get().strip().upper()
            from_slot = self.slot_label_to_index(from_label)
            to_slot = self.slot_label_to_index(to_label)
            if from_slot == to_slot:
                messagebox.showwarning("Exchange Patterns", "Choose two different patterns.", parent=dialog)
                return

            self.model.swap_ptninfo_entries(from_slot, to_slot)
            self.model.load_slot(self.model.current_slot)
            pattern_length = self.model.get_pattern_length_bars()
            self.pattern_length_var.set(pattern_length)
            self.canvas.set_pattern_length(pattern_length)
            self.grid_var.set(self.model.get_ptninfo_quantize_display(self.model.current_slot))
            self.canvas.selected_events.clear()
            self.canvas.selected_pad_row = None
            self.canvas.redraw()
            self.update_lane_labels()
            self.refresh_slot_labels()
            self.update_status_with_pattern_info()
            self.update_status(f"Exchanged patterns {from_label} and {to_label}")
            dialog.destroy()

        button_row = ttk.Frame(frame)
        button_row.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(button_row, text="Exchange", command=do_exchange).pack(side=tk.RIGHT)
        ttk.Button(button_row, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(0, 8))

    def on_add_groove_pattern_card(self):
        groove_dir = self.state.get_library_paths()["incoming"]
        groove_file = filedialog.askopenfilename(
            title="Select Groove MIDI",
            initialdir=str(groove_dir),
            filetypes=[("MIDI Files", "*.mid *.MID"), ("All Files", "*.*")],
        )
        if not groove_file:
            return

        card_dir = filedialog.askdirectory(title="Select Card Directory", initialdir=str(self.state.default_card_mount_dir()))
        if not card_dir:
            return

        pattern_label = simpledialog.askstring("Pattern", "Pattern (C1-D8):", initialvalue="C1")
        if not pattern_label:
            return
        pattern_label = pattern_label.strip().upper()
        valid_patterns = [f"C{i+1}" for i in range(8)] + [f"D{i+1}" for i in range(8)]
        if pattern_label not in valid_patterns:
            messagebox.showerror("Invalid Pattern", "Please enter a valid pattern label (C1-D8).")
            return
        pattern_slot = valid_patterns.index(pattern_label)
        target_pad = simpledialog.askinteger("Target Pad", "Target pad (0-15):", initialvalue=pattern_slot, minvalue=0, maxvalue=15)
        if target_pad is None:
            return

        try:
            apply_groove_to_card(Path(card_dir), Path(groove_file), pattern_slot, target_pad)
            self.update_status(f"Groove applied to pattern {pattern_label}, pad {target_pad}")
            messagebox.showinfo("Add Groove Pattern", "Groove applied successfully.")
        except Exception as exc:
            messagebox.showerror("Add Groove Pattern", str(exc))

    def load_recent_files(self):
        """Load recent files from config."""
        for line in self.state.config.get("recent_files", []):
            path = Path(line)
            if path.exists():
                self.recent_files.append(path)

    def save_recent_files(self):
        """Save recent files to config."""
        self.state.config["recent_files"] = [str(p) for p in self.recent_files]
        self.state.save_config()

    def add_recent_file(self, ptninfo_path: Path):
        """Add file to recent files list"""
        # Remove if already in list
        if ptninfo_path in self.recent_files:
            self.recent_files.remove(ptninfo_path)

        # Add to front
        self.recent_files.insert(0, ptninfo_path)

        # Limit to max recent files
        if len(self.recent_files) > self.max_recent_files:
            self.recent_files = self.recent_files[:self.max_recent_files]

        # Save to config
        self.save_recent_files()

        # Update menu
        self.update_recent_files_menu()

    def update_recent_files_menu(self):
        """Update recent files submenu."""
        self.recent_files_menu.delete(0, tk.END)

        if not self.recent_files:
            self.recent_files_menu.add_command(label="No Recent Files", state=tk.DISABLED)
            return

        for i, path in enumerate(self.recent_files):
            display_name = f"{path.parent.name}/{path.name}"
            self.recent_files_menu.add_command(
                label=f"{i + 1}. {display_name}",
                command=lambda p=path: self.open_recent_file(p),
            )

    def open_recent_file(self, ptninfo_path: Path):
        """Open a recent file"""
        if self.model.dirty:
            if not messagebox.askyesno("Unsaved Changes", "Discard unsaved changes?"):
                return

        # Check if file still exists
        if not ptninfo_path.exists():
            messagebox.showerror("Error", f"File not found: {ptninfo_path}")
            # Remove from recent files
            if ptninfo_path in self.recent_files:
                self.recent_files.remove(ptninfo_path)
                self.save_recent_files()
                self.update_recent_files_menu()
            return

        # Infer PTNDATA path
        ptndata_path = ptninfo_path.parent / "PTNDATA0.SP0"

        if not ptndata_path.exists():
            messagebox.showerror("Error", f"PTNDATA0.SP0 not found in: {ptninfo_path.parent}")
            return

        try:
            self.model.load_pattern(ptninfo_path, ptndata_path)
            self.slot_var.set("C1")

            # Update pattern length from loaded events
            calculated_length = self.model.get_pattern_length_bars()
            self.pattern_length_var.set(calculated_length)
            self.canvas.set_pattern_length(calculated_length)

            self.canvas.selected_events.clear()
            self.canvas.selected_pad_row = None
            self.canvas.redraw()
            self.update_lane_labels()
            self.update_status(f"Loaded: {ptninfo_path.parent}")

            # Move to front of recent files
            self.add_recent_file(ptninfo_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load pattern: {e}")
