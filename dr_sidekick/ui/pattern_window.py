"""Pattern Manager window extracted from the legacy monolith."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import traceback
import urllib.error
import urllib.request
import wave
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from dr_sidekick.engine import (
    AssignmentSession,
    DEFAULT_PATTERN_LENGTH_BARS,
    INTERNAL_PPQN,
    MAX_PATTERN_LENGTH_BARS,
    PatternModel,
    SLOT_COUNT,
    SMPINFO,
    SP303CardPrep,
    SourceType,
    VirtualCard,
    apply_groove_to_card,
    find_wav_files,
    load_midi_notes,
    load_midi_notes_by_channel,
    parse_mpc1000_pgm,
    quick_import,
    sp303_decode_sp0,
    sp303_write_wav,
)
from dr_sidekick.ui.constants import (
    COLOR_PALETTES,
    GRID_SNAPS,
    PAD_NAMES,
    PAD_ORDER,
)
from dr_sidekick.ui.dialogs import show_text_dialog
from dr_sidekick.ui.piano_roll import PianoRollCanvas

try:
    from tkinterdnd2 import DND_FILES
    TKDND_AVAILABLE = True
except ImportError:
    DND_FILES = None
    TKDND_AVAILABLE = False

if TYPE_CHECKING:
    from dr_sidekick.app_state import AppState
    from dr_sidekick.ui.library_window import SmartMediaLibraryWindow

log = logging.getLogger("dr_sidekick")

class PatternManagerWindow:
    """Main application window"""

    def __init__(self, root, state: 'AppState', lib_win: SmartMediaLibraryWindow, debug_mode: bool = False):
        self.root = root
        self.state = state
        self.lib_win = lib_win
        self.debug_mode = debug_mode
        self.root.title("Dr. Sidekick — Pattern Manager")
        self.root.configure(bg="#000000")
        self.root.protocol("WM_DELETE_WINDOW", self.on_hide)

        # Calculate window height: toolbar (~40) + ruler (25) + 32 lanes + statusbar (~25) + padding
        window_height = 40 + 25 + (32 * 25) + 25 + 30  # ~920 pixels
        self.root.geometry(f"1200x{window_height}")

        # Model
        self.model = PatternModel()
        self.current_palette = "Apple Green"
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
        self.root.bind("<Control-Shift-C>", lambda e: self.on_copy_slot())
        self.root.bind("<Control-Shift-V>", lambda e: self.on_paste_slot())
        self.root.bind("<Control-q>", lambda e: self.on_exit())
        self.root.bind("<Control-Shift-L>", lambda e: self.on_smartmedia_library())
        self.root.bind("<Control-Shift-l>", lambda e: self.on_smartmedia_library())
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
        """Hide the Pattern Manager and return focus to the Library window."""
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
        self.file_menu.add_command(label="New Pattern Files", command=self.on_new, accelerator="Ctrl+N")
        self.file_menu.add_command(label="Open Pattern Files...", command=self.on_open, accelerator="Ctrl+O")
        self.file_menu.add_separator()

        # Recent files section (will be populated by update_recent_files_menu)
        self.recent_files_menu_start_index = self.file_menu.index("end") + 1

        self.file_menu.add_separator()
        self.file_menu.add_command(
            label="SmartMedia Library...",
            command=self.on_smartmedia_library,
            accelerator="Ctrl+Shift+L",
        )
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Save", command=self.on_save, accelerator="Ctrl+S")
        self.file_menu.add_command(label="Save As...", command=self.on_save_as, accelerator="Ctrl+Shift+S")
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self.on_exit, accelerator="Ctrl+Q")

        # Initialize recent files menu
        self.update_recent_files_menu()

        # Edit menu
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        edit_menu.add_command(label="Undo", command=self.on_undo, accelerator="Ctrl+Z")
        edit_menu.add_command(label="Redo", command=self.on_redo, accelerator="Ctrl+Shift+Z")
        edit_menu.add_separator()
        edit_menu.add_command(label="Delete", command=self.on_delete, accelerator="Del")
        edit_menu.add_command(label="Select All", command=self.on_select_all, accelerator="Ctrl+A")
        edit_menu.add_separator()
        edit_menu.add_command(label="Copy Pattern", command=self.on_copy_slot, accelerator="Ctrl+Shift+C")
        edit_menu.add_command(label="Paste Pattern", command=self.on_paste_slot, accelerator="Ctrl+Shift+V")
        edit_menu.add_separator()
        edit_menu.add_command(label="Quantize Selected...", command=self.on_quantize_selected)
        edit_menu.add_command(label="Set Velocity...", command=self.on_set_velocity)
        edit_menu.add_separator()
        edit_menu.add_command(label="Clear Pattern", command=self.on_clear_slot)

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

        # Pattern menu (NEW!)
        pattern_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Patterns", menu=pattern_menu)
        pattern_menu.add_command(label="Import MIDI File...", command=self.on_import_midi)
        pattern_menu.add_command(label="Import MIDI Files (Batch)...", command=self.on_import_multiple_midi)
        if self.debug_mode:
            pattern_menu.add_separator()
            pattern_menu.add_command(label="Generate Test Data...", command=self.on_generate_test_data)
        pattern_menu.add_separator()
        pattern_menu.add_command(label="Exchange Patterns...", command=self.on_exchange_slots)
        pattern_menu.add_separator()
        pattern_menu.add_command(label="Add Groove Pattern...", command=self.on_add_groove_pattern_card)
        pattern_menu.add_separator()
        pattern_menu.add_command(label="Pattern Info", command=self.on_pattern_info)

        # Card menu (migrated from CLI)
        card_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Samples", menu=card_menu)
        card_menu.add_command(label="Quick Import WAV Folder...", command=self.on_quick_import_card)
        card_menu.add_command(label="Sample Manager...", command=self.on_sample_manager)
        card_menu.add_command(label="SmartMedia Library...", command=self.on_smartmedia_library)
        card_menu.add_separator()
        card_menu.add_command(label="Convert MPC1000 Program (.pgm)...", command=self.on_import_mpc1000)
        self.pattern_menu = pattern_menu
        self.samples_menu = card_menu

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Keyboard Shortcuts", command=self.on_show_shortcuts)


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
                ("Ctrl+N",        "New Pattern"),
                ("Ctrl+O",        "Open Pattern"),
                ("Ctrl+S",        "Save"),
                ("Ctrl+Shift+S",  "Save As"),
                ("Ctrl+Shift+L",  "SmartMedia Library"),
                ("Ctrl+Q",        "Exit"),
            ]),
            ("EDIT", [
                ("Ctrl+Z",        "Undo"),
                ("Ctrl+Shift+Z",  "Redo"),
                ("Ctrl+A",        "Select All"),
                ("D / Del / Bksp","Delete Selected"),
                ("Right-Click",   "Delete Event"),
                ("[",             "Decrease Velocity"),
                ("]",             "Increase Velocity"),
                ("Ctrl+Shift+C",  "Copy Pattern"),
                ("Ctrl+Shift+V",  "Paste Pattern"),
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

            # Calculate actual length from events
            last_tick = max(e.tick for e in self.model.events)
            actual_length = last_tick / (4 * INTERNAL_PPQN)

            status_text = f"Pattern: {slot_label} - {event_count} events, {actual_length:.1f} bars, {pads_used} pads{mapping_text}"

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
            mapping_index = self.model.get_mapping_index(slot)
            events = []
            if mapping_index is not None and 1 <= mapping_index <= 16:
                mapped_slot = mapping_index - 1
                events = self.model.ptndata.decode_events(mapped_slot)
            else:
                events = self.model.ptndata.decode_events(slot)
            if events:
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
            # Draw label centered in lane
            self.lane_canvas.create_text(
                30, y + zoom_y // 2,
                text=PAD_NAMES[pad],
                fill=colors["lane_label_text"],
                font=("Courier", 10, "bold"),
                tags="lane_label"
            )

    def on_canvas_configure(self):
        """Handle canvas resize"""
        self.canvas.redraw()
        self.update_lane_labels()

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
        self.canvas.redraw()
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
            self.canvas.redraw()

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
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save pattern: {e}")

    def on_exit(self):
        """Exit application"""
        if self.model.dirty:
            if not messagebox.askyesno("Unsaved Changes", "Exit without saving?"):
                return
        self.lib_win.root.destroy()

    def on_undo(self):
        """Undo last operation"""
        if self.model.undo():
            self.canvas.selected_events.clear()
            self.canvas.redraw()
            self.update_status("Undo")

    def on_redo(self):
        """Redo last undone operation"""
        if self.model.redo():
            self.canvas.selected_events.clear()
            self.canvas.redraw()
            self.update_status("Redo")

    def on_delete(self):
        """Delete selected events"""
        if self.canvas.selected_events:
            count = len(self.canvas.selected_events)
            self.model.remove_events(list(self.canvas.selected_events))
            self.canvas.selected_events.clear()
            self.canvas.redraw()
            self.update_status(f"Deleted {count} events")
            # Update to show pattern info after a moment
            self.root.after(1500, self.update_status_with_pattern_info)

    def on_delete_key_root(self, event=None):
        """Root-level delete shortcut handler."""
        self.on_delete()
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
        self.canvas.redraw()
        self.update_status(f"Selected {len(self.model.events)} events")

    def on_clear_slot(self):
        """Clear current slot"""
        pattern_label = self.slot_index_to_label(self.model.current_slot)
        if messagebox.askyesno("Clear Pattern", f"Clear all events in pattern {pattern_label}?"):
            self.model.clear_slot()
            self.canvas.selected_events.clear()
            self.canvas.redraw()
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
            messagebox.showwarning("Pattern Truncated", self.model.last_save_warning)
            self.model.last_save_warning = None

        # Update pattern length from events
        calculated_length = self.model.get_pattern_length_bars()
        self.pattern_length_var.set(calculated_length)
        self.canvas.set_pattern_length(calculated_length)
        self.grid_var.set(self.model.get_ptninfo_quantize_display(slot_index))

        self.canvas.selected_events.clear()
        self.canvas.redraw()
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
                self.canvas.redraw()
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
            self.canvas.redraw()
            self._focus_first_event()
            action = "Replaced" if replace else "Added"
            if import_meta["truncated_events"] > 0:
                self.update_status(
                    f"MIDI Import: {action} {count} events from Ch {selected_channel} ({midi_path.name}) "
                    f"(truncated +{import_meta['truncated_bars']:.1f} bar(s))"
                )
            elif import_meta["density_truncated_events"] > 0:
                capped_source_bars = min(import_meta["max_bars"], import_meta.get("source_bars", import_meta["max_bars"]))
                discarded_capacity_bars = max(0.0, capped_source_bars - float(import_meta["imported_length_bars"]))
                self.update_status(
                    f"MIDI Import: {action} {count} events from Ch {selected_channel} ({midi_path.name}) "
                    f"(loaded ~{float(import_meta['imported_length_bars']):.1f} bars, discarded ~{discarded_capacity_bars:.1f} bars)"
                )
            else:
                self.update_status(
                    f"MIDI Import: {action} {count} events from Ch {selected_channel} ({midi_path.name})"
                )
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
        self.canvas.redraw()
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
            self.canvas.redraw()
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
        self.update_status(f"Set velocity to {new_vel} for {len(self.canvas.selected_events)} events")

    def on_copy_slot(self):
        """Copy current slot"""
        self.model.copy_slot()
        event_count = len(self.model.slot_clipboard) if self.model.slot_clipboard else 0
        pattern_label = self.slot_index_to_label(self.model.current_slot)
        self.update_status(f"Copied pattern {pattern_label} ({event_count} events)")

    def on_paste_slot(self):
        """Paste to current slot"""
        if self.model.slot_clipboard is None:
            messagebox.showwarning("Paste Pattern", "No pattern in clipboard")
            return

        self.model.paste_slot()
        self.canvas.selected_events.clear()
        self.canvas.redraw()
        pattern_label = self.slot_index_to_label(self.model.current_slot)
        self.update_status(f"Pasted {len(self.model.events)} events to pattern {pattern_label}")
        self.refresh_slot_labels()

    def on_pattern_info(self):
        """Show pattern info"""
        if not self.model.events:
            messagebox.showinfo("Pattern Info", "Current pattern is empty")
            return

        # Calculate pattern info
        event_count = len(self.model.events)
        first_tick = min(e.tick for e in self.model.events)
        last_tick = max(e.tick for e in self.model.events)
        pattern_length_ticks = last_tick - first_tick
        pattern_length_bars = pattern_length_ticks / (4 * INTERNAL_PPQN)

        # Pads used
        pads_used = set(e.pad for e in self.model.events)
        pad_names = [PAD_NAMES.get(p, f"0x{p:02X}") for p in sorted(pads_used)]

        # Velocity range
        min_vel = min(e.velocity for e in self.model.events)
        max_vel = max(e.velocity for e in self.model.events)
        avg_vel = sum(e.velocity for e in self.model.events) // event_count

        # Build info text
        slot_label = self.slot_var.get()
        info_text = f"""Pattern {slot_label} Info:

Events: {event_count}
Pattern Length: {pattern_length_bars:.2f} bars ({pattern_length_ticks} ticks)
  Note: Each pattern has its own length
First Event: Tick {first_tick}
Last Event: Tick {last_tick}

Pads Used ({len(pads_used)}):
  {', '.join(pad_names)}

Velocity:
  Min: {min_vel}
  Max: {max_vel}
  Average: {avg_vel}
"""

        messagebox.showinfo("Pattern Info", info_text)

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
            self.canvas.redraw()
            self.refresh_slot_labels()
            self.update_status_with_pattern_info()
            self.update_status(f"Exchanged patterns {from_label} and {to_label}")
            dialog.destroy()

        button_row = ttk.Frame(frame)
        button_row.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(button_row, text="Exchange", command=do_exchange).pack(side=tk.RIGHT)
        ttk.Button(button_row, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(0, 8))

    def ask_output_directory(self, initialdir: Optional[Path] = None) -> Optional[Path]:
        kwargs = {"title": "Select Output Directory"}
        if initialdir is not None:
            kwargs["initialdir"] = str(initialdir)
        output_dir = filedialog.askdirectory(**kwargs)
        return Path(output_dir) if output_dir else None

    def show_prepare_results(
        self,
        results: dict,
        output_dir: Path,
        title: str = "Card Preparation Complete",
        extra_lines: Optional[List[str]] = None,
        include_counts: bool = True,
    ):
        lines = [f"Output directory: {output_dir}"]
        if include_counts:
            if results.get("wav_prepared"):
                lines.append(f"WAV files prepared: {len(results['wav_prepared'])}")
            if results.get("archived_sp0_copied"):
                lines.append(f".SP0 files copied: {len(results['archived_sp0_copied'])}")
            if results.get("smpinfo_created"):
                lines.append("SMPINFO0.SP0 created")
        if extra_lines:
            lines.extend(extra_lines)
        show_text_dialog(self.root, title, "\n".join(lines), geometry="1024x640")

    def archive_existing_outgoing_wavs(self, output_dir: Path) -> Optional[Path]:
        wav_files = sorted(
            [
                path
                for path in output_dir.rglob("*")
                if path.is_file()
                and path.suffix.lower() == ".wav"
                and not any(part.startswith("wav_archive_") for part in path.parts)
            ]
        )
        if not wav_files:
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_dir = output_dir / f"wav_archive_{timestamp}"
        archive_dir.mkdir(parents=True, exist_ok=True)

        for wav_file in wav_files:
            relative_parent = wav_file.parent.relative_to(output_dir)
            target_parent = archive_dir / relative_parent
            target_parent.mkdir(parents=True, exist_ok=True)
            target = target_parent / wav_file.name
            suffix = 1
            while target.exists():
                target = target_parent / f"{wav_file.stem}_{suffix}{wav_file.suffix}"
                suffix += 1
            wav_file.rename(target)

        return archive_dir

    def on_quick_import_card(self):
        wav_file = filedialog.askopenfilename(
            title="Select Any WAV In The Target Folder (Cancel To Pick Folder)",
            filetypes=[("WAV Files", "*.wav *.WAV"), ("All Files", "*.*")],
        )
        if wav_file:
            wav_dir_path = Path(wav_file).parent
        else:
            wav_dir = filedialog.askdirectory(title="Select WAV Folder")
            if not wav_dir:
                return
            wav_dir_path = Path(wav_dir)

        output_dir = self.state.get_library_paths()["outgoing"]
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            archive_dir = self.archive_existing_outgoing_wavs(output_dir)
            payload = quick_import(wav_dir_path, output_dir, None)
            summary_lines = [
                f"WAV files processed: {payload['imported_count']}",
            ]
            if archive_dir is not None:
                summary_lines.append(
                    f"Archived existing WAV files in BOSS DATA_OUTGOING to Cards/BOSS DATA_OUTGOING/{archive_dir.name}"
                )
            if payload.get("batch_count", 1) > 1:
                summary_lines.append(
                    f"Prepared {payload['batch_count']} bank-load folders: {', '.join(payload.get('batch_dirs', []))}"
                )
                summary_lines.append(
                    "Import one folder at a time: copy its SMPL0001.WAV-SMPL0008.WAV to card, import to chosen bank, then repeat with the next folder."
                )
            conversion_lines = [
                item["conversion_summary"]
                for item in payload["results"].get("wav_prepared", [])
                if item.get("conversion_summary")
            ]
            if conversion_lines:
                summary_lines.extend(conversion_lines)
            else:
                for item in payload["results"].get("wav_prepared", []):
                    source_name = item.get("source_file")
                    target_name = item.get("file")
                    if source_name and target_name:
                        summary_lines.append(f"Converted {source_name} -> {target_name}")
            self.show_prepare_results(
                payload["results"],
                output_dir,
                "Quick Import Complete",
                summary_lines,
                include_counts=False,
            )
            self.update_status(
                f"Quick import complete: processed {payload['imported_count']} of {payload['total_found']} WAV files"
            )
        except Exception as exc:
            messagebox.showerror("Quick Import Error", str(exc))

    def on_import_mpc1000(self):
        """Convert MPC1000 .pgm program + WAV folder into a SmartMedia Library card."""
        pgm_file = filedialog.askopenfilename(
            title="Select MPC1000 Program (.pgm)",
            filetypes=[("MPC1000 Program", "*.pgm *.PGM"), ("All Files", "*.*")],
        )
        if not pgm_file:
            return
        pgm_path = Path(pgm_file)

        wav_dir_path = pgm_path.parent
        if not find_wav_files(wav_dir_path, recursive=True):
            wav_dir = filedialog.askdirectory(
                title="No WAVs found next to .pgm — Select WAV Folder",
                initialdir=str(pgm_path.parent),
            )
            if not wav_dir:
                return
            wav_dir_path = Path(wav_dir)

        try:
            pads = parse_mpc1000_pgm(pgm_path)
        except ValueError as exc:
            messagebox.showerror("MPC1000 Import Error", str(exc))
            return

        wav_files_in_dir = find_wav_files(wav_dir_path, recursive=True)

        def find_wav_for_name(sample_name):
            for f in wav_files_in_dir:
                if f.stem == sample_name:
                    return f
            for f in wav_files_in_dir:
                if f.stem.lower() == sample_name.lower():
                    return f
            for f in wav_files_in_dir:
                if sample_name.lower() in f.stem.lower():
                    return f
            return None

        self.state.smartmedia_lib.ensure_dirs()
        card_name = pgm_path.stem
        card_dir = self.state.smartmedia_lib.cards_dir / card_name
        if card_dir.exists():
            shutil.rmtree(card_dir)
        card_dir.mkdir(parents=True, exist_ok=True)
        card = VirtualCard(name=card_name, tags=["mpc1000"])
        self.state.smartmedia_lib.create_card(card)

        prep = SP303CardPrep()
        summary_lines = [f"Program: {pgm_path.name}", f"WAV folder: {wav_dir_path.name}", ""]
        total_written = 0
        not_found = []

        for bank_idx, bank_name in enumerate("ABCDEFGH"):
            bank_dir = card_dir / f"BANK_LOAD_{bank_idx + 1:02d}"
            bank_lines = []
            bank_has_samples = False

            for slot in range(8):
                pad_index = bank_idx * 8 + slot
                sample_name = pads.get(pad_index)
                smpl_name = f"SMPL{slot + 1:04d}.WAV"

                if not sample_name:
                    bank_lines.append(f"  {smpl_name}: (empty)")
                    continue

                wav = find_wav_for_name(sample_name)
                if not wav:
                    not_found.append(f"Bank {bank_name} pad {slot + 1}: {sample_name}")
                    bank_lines.append(f"  {smpl_name}: NOT FOUND ({sample_name})")
                    continue

                bank_dir.mkdir(parents=True, exist_ok=True)
                target = bank_dir / smpl_name
                actions = prep._prepare_wav(wav, target)
                action_str = f" [{', '.join(actions)}]" if actions else ""
                bank_lines.append(f"  {smpl_name}: {wav.name}{action_str}")
                total_written += 1
                bank_has_samples = True

            if bank_has_samples:
                summary_lines.append(f"Bank {bank_name} (BANK_LOAD_{bank_idx + 1:02d}):")
                summary_lines.extend(bank_lines)
                summary_lines.append("")

        summary_lines.append(f"Total samples written: {total_written}")
        if not_found:
            summary_lines.append(f"\nNot found ({len(not_found)}):")
            summary_lines.extend(f"  {s}" for s in not_found)
        summary_lines.append(f"\nSaved to: {card_dir}")
        summary_lines.append("Load one BANK_LOAD folder at a time on the SP-303.")

        show_text_dialog(self.root, "MPC1000 Import Complete", "\n".join(summary_lines))
        self.update_status(
            f"MPC1000 import complete: {total_written} samples written to Cards/{card_name}"
        )
        log.info("MPC1000 import: %s -> Cards/%s (%d samples)", pgm_path.name, card_name, total_written)

    def on_smartmedia_library(self):
        """Bring the SmartMedia Library window to the front."""
        self.lib_win.root.deiconify()
        self.lib_win.root.lift()

    def on_sample_manager(self, smpinfo_path: Optional[Path] = None):
        session = AssignmentSession()
        dialog = tk.Toplevel(self.root)
        card_label = smpinfo_path.parent.name if smpinfo_path else "No card loaded"
        dialog.title(f"Sample Manager — {card_label}" if smpinfo_path else "Sample Manager")
        dialog.geometry("1180x680")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#000000")

        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Sample Manager", font=("Courier", 13, "bold")).pack(anchor=tk.W, pady=(0, 2))
        ttk.Label(frame, text="Helps you manage the samples on your SmartMedia card. You can also reorganise the order by dragging them into new positions.").pack(anchor=tk.W, pady=(0, 4))
        ttk.Label(
            frame,
            text="Coming Soon: Long/Lo-Fi and DSP Effect editing.",
        ).pack(anchor=tk.W, pady=(0, 4))
        dialog_context = ttk.Label(frame, text="Card Setup: Not loaded | Pending pad changes: 0")
        dialog_context.pack(anchor=tk.W, pady=(0, 8))

        style = ttk.Style(dialog)
        style.configure(
            "CustomPad.Treeview",
            background="#000000",
            fieldbackground="#000000",
            foreground="#ffffff",
            rowheight=24,
        )
        style.map(
            "CustomPad.Treeview",
            background=[("selected", "#2a7fff")],
            foreground=[("selected", "#ffffff")],
        )

        columns = ("bank_pad", "source", "file", "long_lofi", "stereo", "length", "duration", "gate", "loop", "reverse")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=14, style="CustomPad.Treeview")
        tree.heading("bank_pad", text="Pad")
        tree.heading("source", text="Source")
        tree.heading("file", text="File")
        tree.heading("long_lofi", text="Long/Lo-Fi")
        tree.heading("stereo", text="Stereo")
        tree.heading("length", text="File Length")
        tree.heading("duration", text="Duration")
        tree.heading("gate", text="Gate")
        tree.heading("loop", text="Loop")
        tree.heading("reverse", text="Reverse")
        tree.column("bank_pad", width=58, anchor=tk.CENTER, stretch=False)
        tree.column("source", width=82, anchor=tk.CENTER, stretch=False)
        tree.column("file", width=215, stretch=True)
        tree.column("long_lofi", width=80, anchor=tk.CENTER, stretch=False)
        tree.column("stereo", width=66, anchor=tk.CENTER, stretch=False)
        tree.column("length", width=88, anchor=tk.E, stretch=False)
        tree.column("duration", width=86, anchor=tk.E, stretch=False)
        tree.column("gate", width=64, anchor=tk.CENTER, stretch=False)
        tree.column("loop", width=96, anchor=tk.CENTER, stretch=False)
        tree.column("reverse", width=74, anchor=tk.CENTER, stretch=False)
        tree.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        tree.tag_configure("drop_target", background="#1f3f66", foreground="#ffffff")
        tree.tag_configure("rearrange_target", background="#5a2a8a", foreground="#ffffff")

        ttk.Label(
            frame,
            text="Tip: Load SMPINFO0.SP0 to view slot metadata. Select a row to assign WAV/SP0 and re-map pads. Click Gate, Loop or Reverse to toggle.",
        ).pack(anchor=tk.W, pady=(8, 8))

        control_panel = tk.Frame(frame, bg="#050505", highlightbackground="#2a2a2a", highlightthickness=1, bd=0)
        control_panel.pack(fill=tk.X, pady=(2, 0))

        setup_section = tk.Frame(control_panel, bg="#050505", highlightbackground="#262626", highlightthickness=1, bd=0)
        setup_section.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)
        tk.Label(setup_section, text="CARD SETUP", bg="#050505", fg="#f0f0f0", font=("Courier", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 4))
        setup_row = ttk.Frame(setup_section)
        setup_row.pack(fill=tk.X, padx=6, pady=(0, 6))

        route_section = tk.Frame(control_panel, bg="#050505", highlightbackground="#262626", highlightthickness=1, bd=0)
        route_section.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)
        tk.Label(route_section, text="PAD ROUTING", bg="#050505", fg="#f0f0f0", font=("Courier", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 4))
        route_row = ttk.Frame(route_section)
        route_row.pack(fill=tk.X, padx=6, pady=(0, 6))

        write_section = tk.Frame(control_panel, bg="#050505", highlightbackground="#262626", highlightthickness=1, bd=0)
        write_section.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)
        tk.Label(write_section, text="WRITE", bg="#050505", fg="#f0f0f0", font=("Courier", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 4))
        write_row = ttk.Frame(write_section)
        write_row.pack(fill=tk.X, padx=6, pady=(0, 6))
        drop_target_iid: Optional[str] = None
        rearrange_target_iid: Optional[str] = None
        rearrange_source_iid: Optional[str] = None
        slot_metadata: Dict[int, Dict[str, str]] = {}
        gate_state: Dict[int, bool] = {}
        baseline_gate_state: Dict[int, bool] = {}
        loop_state: Dict[int, bool] = {}
        baseline_loop_state: Dict[int, bool] = {}
        reverse_state: Dict[int, bool] = {}
        baseline_reverse_state: Dict[int, bool] = {}
        loaded_smpinfo_bytes: Optional[bytes] = None
        loaded_smpinfo_path: Optional[Path] = None
        baseline_assignments: Dict[int, str] = {}
        _playback: List = [None, None]  # [subprocess.Popen, tmp_wav_path]

        def slot_to_label(slot: int) -> str:
            bank = "C" if slot < 8 else "D"
            pad = (slot % 8) + 1
            return f"{bank}{pad}"

        def current_assignment_snapshot() -> Dict[int, str]:
            snapshot: Dict[int, str] = {}
            for slot, source in enumerate(session.prep.sources):
                if source.source_path is None:
                    snapshot[slot] = "-"
                else:
                    snapshot[slot] = source.source_path.name
            return snapshot

        def build_change_lines() -> List[str]:
            current = current_assignment_snapshot()
            lines: List[str] = []
            for slot in range(SLOT_COUNT):
                before = baseline_assignments.get(slot, "-")
                after = current.get(slot, "-")
                if before != after:
                    lines.append(f"{slot_to_label(slot)}: {before} -> {after}")
            for slot in range(SLOT_COUNT):
                if slot not in baseline_gate_state:
                    continue
                before_gate = baseline_gate_state[slot]
                after_gate = gate_state.get(slot, before_gate)
                if before_gate != after_gate:
                    lines.append(f"{slot_to_label(slot)}: Gate -> {'On' if after_gate else 'Off'}")
            for slot in range(SLOT_COUNT):
                if slot not in baseline_loop_state:
                    continue
                before_loop = baseline_loop_state[slot]
                after_loop = loop_state.get(slot, before_loop)
                if before_loop != after_loop:
                    lines.append(f"{slot_to_label(slot)}: Loop -> {'On' if after_loop else 'Off'}")
            for slot in range(SLOT_COUNT):
                if slot not in baseline_reverse_state:
                    continue
                before_rev = baseline_reverse_state[slot]
                after_rev = reverse_state.get(slot, before_rev)
                if before_rev != after_rev:
                    lines.append(f"{slot_to_label(slot)}: Reverse -> {'On' if after_rev else 'Off'}")
            return lines

        def refresh_dialog_context():
            loaded = str(loaded_smpinfo_path) if loaded_smpinfo_path is not None else "Not loaded"
            changes = len(build_change_lines()) if baseline_assignments else 0
            dialog_context.config(text=f"Card Setup: {loaded} | Pending pad changes: {changes}")

        def selected_slot() -> Optional[int]:
            selected = tree.selection()
            if not selected:
                messagebox.showinfo("Sample Manager", "Select a pad first.", parent=dialog)
                return None
            return int(selected[0])

        def refresh_tree():
            nonlocal drop_target_iid, rearrange_target_iid
            for item in tree.get_children():
                tree.delete(item)
            drop_target_iid = None
            rearrange_target_iid = None
            for slot, source in enumerate(session.prep.sources):
                bank = "C" if slot < 8 else "D"
                pad = (slot % 8) + 1
                if source.source_type.value == "archived":
                    source_name = "SP-303"
                else:
                    source_name = source.source_type.value

                if source.source_path:
                    filename = source.source_path.name
                    if source.source_type.value == "archived" and source.is_stereo:
                        right_name = source.source_path.name.replace("L.SP0", "R.SP0")
                        if right_name != source.source_path.name:
                            filename = f"{source.source_path.name} + {right_name}"
                else:
                    filename = "-"
                meta = slot_metadata.get(slot, {})
                tree.insert(
                    "",
                    tk.END,
                    iid=str(slot),
                    values=(
                        f"{bank}{pad}",
                        source_name,
                        filename,
                        meta.get("long_lofi", "-"),
                        meta.get("stereo", "-"),
                        meta.get("length", "-"),
                        meta.get("duration", "-"),
                        meta.get("gate", "-"),
                        meta.get("loop", "-"),
                        meta.get("reverse", "-"),
                    ),
                )
            refresh_dialog_context()

        def set_drop_target(iid: Optional[str]):
            nonlocal drop_target_iid
            if drop_target_iid and tree.exists(drop_target_iid):
                tree.item(drop_target_iid, tags=())
            drop_target_iid = iid
            if drop_target_iid and tree.exists(drop_target_iid):
                tree.item(drop_target_iid, tags=("drop_target",))

        def set_rearrange_target(iid: Optional[str]):
            nonlocal rearrange_target_iid
            if rearrange_target_iid and tree.exists(rearrange_target_iid):
                tree.item(rearrange_target_iid, tags=())
            rearrange_target_iid = iid
            if rearrange_target_iid and tree.exists(rearrange_target_iid):
                tree.item(rearrange_target_iid, tags=("rearrange_target",))

        def assign_wav():
            slot = selected_slot()
            if slot is None:
                return
            wav_file = filedialog.askopenfilename(
                parent=dialog,
                title="Select WAV File",
                filetypes=[("WAV Files", "*.wav *.WAV"), ("All Files", "*.*")],
            )
            if not wav_file:
                return
            try:
                assign_wav_to_slot(slot, Path(wav_file))
                self.update_status(f"Assigned WAV to {slot_to_label(slot)}")
            except Exception as exc:
                messagebox.showerror("Assign WAV", str(exc), parent=dialog)

        def assign_sp0():
            slot = selected_slot()
            if slot is None:
                return
            sp0_file = filedialog.askopenfilename(
                parent=dialog,
                title="Select SP0 File",
                filetypes=[("SP0 Files", "*.SP0"), ("All Files", "*.*")],
            )
            if not sp0_file:
                return
            try:
                selected_path = Path(sp0_file)
                name_upper = selected_path.name.upper()

                if name_upper.endswith("R.SP0"):
                    left_candidate = selected_path.with_name(selected_path.name[:-5] + "L.SP0")
                    if not left_candidate.exists():
                        raise ValueError(f"Matching left file not found: {left_candidate.name}")
                    selected_path = left_candidate
                    name_upper = selected_path.name.upper()

                if name_upper.endswith("L.SP0"):
                    right_candidate = selected_path.with_name(selected_path.name[:-5] + "R.SP0")
                    stereo = right_candidate.exists()
                else:
                    stereo = False

                session.assign_archived_sp0(slot, selected_path, stereo)
                refresh_tree()
                self.update_status(f"Assigned SP0 to {slot_to_label(slot)}")
            except Exception as exc:
                messagebox.showerror("Assign SP0", str(exc), parent=dialog)

        def load_smpinfo_from_path(path: Path):
            nonlocal loaded_smpinfo_bytes, loaded_smpinfo_path, gate_state, loop_state, reverse_state
            try:
                if path.name.upper() != "SMPINFO0.SP0":
                    raise ValueError("Please select SMPINFO0.SP0")

                loaded_smpinfo_bytes = path.read_bytes()
                loaded_smpinfo_path = path
                smpinfo = SMPINFO.from_file(path)
                source_dir = path.parent

                for slot in range(SLOT_COUNT):
                    session.clear_slot(slot)
                slot_metadata.clear()
                gate_state.clear()
                loop_state.clear()
                reverse_state.clear()
                missing_files: List[str] = []

                for slot in range(SLOT_COUNT):
                    slot_record = smpinfo.slots[slot]
                    if slot_record.is_empty:
                        continue
                    
                    # Duration from SMPINFO: sample_length_bytes / 33075 Hz (= 44100 × ¾)
                    seconds = slot_record.sample_length_bytes / 33075.0
                    duration_text = f"{seconds:.2f}s" if seconds >= 1.0 else f"{seconds * 1000.0:.1f}ms"
                    gate_state[slot] = slot_record.is_gate
                    loop_state[slot] = slot_record.is_loop
                    reverse_state[slot] = slot_record.is_reverse
                    slot_metadata[slot] = {
                        "long_lofi": "-",
                        "stereo": "Stereo" if slot_record.is_stereo else "Mono",
                        "length": f"{slot_record.sample_length_bytes:,} B",
                        "duration": duration_text,
                        "loop": "Loop" if slot_record.is_loop else "Off",
                        "reverse": "Reverse" if slot_record.is_reverse else "Off",
                        "gate": "Gate" if slot_record.is_gate else "Off",
                    }

                    left_file = source_dir / f"SMP{slot:04X}L.SP0"
                    right_file = source_dir / f"SMP{slot:04X}R.SP0"
                    if not left_file.exists():
                        missing_files.append(left_file.name)
                        continue
                    if slot_record.is_stereo and not right_file.exists():
                        missing_files.append(right_file.name)
                        continue
                    session.assign_archived_sp0(slot, left_file, slot_record.is_stereo)

                baseline_assignments.clear()
                baseline_assignments.update(current_assignment_snapshot())
                baseline_gate_state.clear()
                baseline_gate_state.update(gate_state)
                baseline_loop_state.clear()
                baseline_loop_state.update(loop_state)
                baseline_reverse_state.clear()
                baseline_reverse_state.update(reverse_state)
                refresh_tree()
                self.set_loaded_card_context(str(source_dir))
                self.update_status(f"Loaded card setup: {path}")
                dialog.title(f"Sample Manager — {source_dir.name}")
                log.info("Card opened: %s (%d slots populated)", path, sum(1 for s in smpinfo.slots if not s.is_empty))
                if missing_files:
                    messagebox.showwarning(
                        "Load SMPINFO0.SP0",
                        f"Metadata loaded. Missing sample files: {', '.join(sorted(set(missing_files)))}",
                        parent=dialog,
                    )
            except Exception as exc:
                messagebox.showerror("Load SMPINFO0.SP0", str(exc), parent=dialog)

        def load_smpinfo_metadata():
            smpinfo_file = filedialog.askopenfilename(
                parent=dialog,
                title="Select SMPINFO0.SP0",
                initialdir=str(self.state.default_card_mount_dir()),
                filetypes=[("SMPINFO0.SP0", "SMPINFO0.SP0"), ("SP0 Files", "*.SP0"), ("All Files", "*.*")],
            )
            if not smpinfo_file:
                return
            load_smpinfo_from_path(Path(smpinfo_file))

        def clear_pad():
            slot = selected_slot()
            if slot is None:
                return
            session.clear_slot(slot)
            refresh_tree()
            self.update_status(f"Cleared {slot_to_label(slot)}")

        def assign_wav_to_slot(slot: int, wav_path: Path):
            session.assign_wav(slot, wav_path)
            refresh_tree()
            tree.selection_set(str(slot))
            tree.focus(str(slot))

        def prepare_card_now():
            nonlocal loaded_smpinfo_bytes
            preferred_output = Path("/Volumes/BOSS DATA")
            if not preferred_output.exists():
                preferred_output = self.state.get_library_paths()["outgoing"]
            output_dir = self.ask_output_directory(preferred_output)
            if output_dir is None:
                return
            change_lines = build_change_lines()

            summary_lines = [
                f"Target card/output: {output_dir}",
                f"Pending pad changes: {len(change_lines)}",
            ]
            if change_lines:
                summary_lines.extend(change_lines[:24])
                if len(change_lines) > 24:
                    summary_lines.append(f"... and {len(change_lines) - 24} more")

            confirm_dialog = tk.Toplevel(dialog)
            confirm_dialog.title("Confirm Write Changes to Card")
            confirm_dialog.geometry("720x520")
            confirm_dialog.transient(dialog)
            confirm_dialog.grab_set()
            confirm_dialog.configure(bg="#000000")

            confirm_frame = ttk.Frame(confirm_dialog, padding=10)
            confirm_frame.pack(fill=tk.BOTH, expand=True)
            confirm_text = tk.Text(
                confirm_frame,
                wrap=tk.NONE,
                bg="#000000",
                fg="#ffffff",
                insertbackground="#ffffff",
                relief=tk.FLAT,
                highlightthickness=0,
            )
            confirm_text.insert("1.0", "\n".join(summary_lines))
            confirm_text.configure(state=tk.DISABLED)
            confirm_text.pack(fill=tk.BOTH, expand=True)
            action_row = ttk.Frame(confirm_frame)
            action_row.pack(fill=tk.X, pady=(8, 0))

            def do_write():
                for widget in action_row.winfo_children():
                    widget.destroy()
                confirm_text.configure(state=tk.NORMAL)
                confirm_text.delete("1.0", tk.END)
                confirm_text.insert("1.0", "Writing...")
                confirm_text.configure(state=tk.DISABLED)
                confirm_dialog.update()
                log.info("Write to card started: %s", output_dir)
                try:

                    results = session.prepare_card(output_dir)
                    smpinfo_out = output_dir / "SMPINFO0.SP0"
                    if loaded_smpinfo_bytes is not None and smpinfo_out.exists():
                        # Preserving the full extended tail can override pad reassignments.
                        # Only preserve it when archived SP0 assignments still match native slot filenames.
                        has_reassignment = False
                        for slot, source in enumerate(session.prep.sources):
                            if source.source_type.value != "archived" or source.source_path is None:
                                continue
                            expected_name = f"SMP{slot:04X}L.SP0"
                            if source.source_path.name.upper() != expected_name:
                                has_reassignment = True
                                break

                        generated = smpinfo_out.read_bytes()
                        if len(generated) == len(loaded_smpinfo_bytes) and len(generated) >= 0x400:
                            merged = bytearray(generated)
                            if not has_reassignment:
                                merged[0x400:] = loaded_smpinfo_bytes[0x400:]
                            else:
                                results.setdefault("warnings", []).append(
                                    "Skipped SMPINFO tail preservation because pad reassignment was detected."
                                )
                            smpinfo_out.write_bytes(merged)
                    # Patch gate into ALL slot-record blocks AFTER the tail merge.
                    # The SP-303 reads from the LAST written block in the write log
                    # (0x0400, 0x0800, …), not from 0x0000, so we must update every block.
                    if gate_state and smpinfo_out.exists():
                        patched = bytearray(smpinfo_out.read_bytes())
                        block_size = 0x400
                        num_blocks = len(patched) // block_size
                        for slot, is_gate in gate_state.items():
                            gate_byte = 0x01 if is_gate else 0x00
                            for blk in range(num_blocks):
                                blk_start = blk * block_size
                                # Stop at first unwritten (0xFF-filled) block
                                if patched[blk_start:blk_start + 4] == b'\xff\xff\xff\xff':
                                    break
                                byte_off = blk_start + slot * 48 + 37
                                if byte_off < len(patched):
                                    patched[byte_off] = gate_byte
                        smpinfo_out.write_bytes(patched)
                    # Patch loop flag (byte 0x26, record offset 38) in all written blocks.
                    if loop_state and smpinfo_out.exists():
                        patched = bytearray(smpinfo_out.read_bytes())
                        block_size = 0x400
                        num_blocks = len(patched) // block_size
                        for slot, is_loop in loop_state.items():
                            loop_byte = 0x01 if is_loop else 0x00
                            for blk in range(num_blocks):
                                blk_start = blk * block_size
                                if patched[blk_start:blk_start + 4] == b'\xff\xff\xff\xff':
                                    break
                                byte_off = blk_start + slot * 48 + 38
                                if byte_off < len(patched):
                                    patched[byte_off] = loop_byte
                        smpinfo_out.write_bytes(patched)
                    # Patch reverse flag (byte 0x27, record offset 39) in all written blocks.
                    if reverse_state and smpinfo_out.exists():
                        patched = bytearray(smpinfo_out.read_bytes())
                        block_size = 0x400
                        num_blocks = len(patched) // block_size
                        for slot, is_reverse in reverse_state.items():
                            rev_byte = 0x01 if is_reverse else 0x00
                            for blk in range(num_blocks):
                                blk_start = blk * block_size
                                if patched[blk_start:blk_start + 4] == b'\xff\xff\xff\xff':
                                    break
                                byte_off = blk_start + slot * 48 + 39
                                if byte_off < len(patched):
                                    patched[byte_off] = rev_byte
                        smpinfo_out.write_bytes(patched)

                    result_lines = [f"Output directory: {output_dir}"]
                    if results.get("wav_prepared"):
                        result_lines.append(f"WAV files prepared: {len(results['wav_prepared'])}")
                    if results.get("archived_sp0_copied"):
                        result_lines.append(f".SP0 files copied: {len(results['archived_sp0_copied'])}")
                    if results.get("smpinfo_created"):
                        result_lines.append("SMPINFO0.SP0 created.")
                    if results.get("warnings"):
                        result_lines.extend(results["warnings"])
                    confirm_text.configure(state=tk.NORMAL)
                    confirm_text.delete("1.0", tk.END)
                    confirm_text.insert("1.0", "\n".join(result_lines))
                    confirm_text.configure(state=tk.DISABLED)
                    confirm_dialog.title("Write Complete")
                    self.update_status("Custom pad assignment complete")
                    log.info("Write to card complete: %s", output_dir)
                    baseline_assignments.clear()
                    baseline_assignments.update(current_assignment_snapshot())
                    baseline_gate_state.clear()
                    baseline_gate_state.update(gate_state)
                    baseline_loop_state.clear()
                    baseline_loop_state.update(loop_state)
                    baseline_reverse_state.clear()
                    baseline_reverse_state.update(reverse_state)
                    refresh_dialog_context()
                except Exception as exc:
                    log.error("Write to card failed: %s", exc, exc_info=True)
                    confirm_text.configure(state=tk.NORMAL)
                    confirm_text.delete("1.0", tk.END)
                    confirm_text.insert("1.0", f"Error: {exc}")
                    confirm_text.configure(state=tk.DISABLED)
                    confirm_dialog.title("Write Failed")
                ttk.Button(action_row, text="Close", command=confirm_dialog.destroy).pack(side=tk.RIGHT)

            ttk.Button(action_row, text="Cancel", command=confirm_dialog.destroy).pack(side=tk.RIGHT)
            ttk.Button(action_row, text="Write Changes", command=do_write).pack(side=tk.RIGHT, padx=(0, 6))

        def stop_playback():
            proc, tmp = _playback[0], _playback[1]
            _playback[0] = None
            _playback[1] = None
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass

        def _launch_playback(file_path: str, tmp_path: Optional[str] = None):
            if sys.platform == 'darwin':
                cmd = ['afplay', file_path]
            elif sys.platform.startswith('linux'):
                cmd = ['aplay', file_path]
            else:
                messagebox.showinfo("Preview", "Audio preview is not supported on this platform.", parent=dialog)
                return
            try:
                proc = subprocess.Popen(cmd)
            except Exception as exc:
                messagebox.showerror("Preview", str(exc), parent=dialog)
                return
            _playback[0] = proc
            _playback[1] = tmp_path
            def _cleanup(p, tmp):
                try:
                    p.wait(timeout=120)
                except Exception:
                    pass
                if _playback[0] is p:
                    _playback[0] = None
                if tmp:
                    if _playback[1] == tmp:
                        _playback[1] = None
                    try:
                        os.unlink(tmp)
                    except Exception:
                        pass
            threading.Thread(target=_cleanup, args=(proc, tmp_path), daemon=True).start()

        def _decode_sp0_to_pcm(l_path: Path, is_stereo: bool):
            """Decode SP0 (mono or stereo pair) to (pcm_list, num_samples, num_channels)."""
            samples_l = sp303_decode_sp0(str(l_path))
            if is_stereo:
                r_path = l_path.with_name(l_path.name[:-5] + "R.SP0")
                if r_path.exists():
                    samples_r = sp303_decode_sp0(str(r_path))
                    n = max(len(samples_l), len(samples_r))
                    samples_l += [0] * (n - len(samples_l))
                    samples_r += [0] * (n - len(samples_r))
                    pcm = [v for pair in zip(samples_l, samples_r) for v in pair]
                    return pcm, n, 2
            return samples_l, len(samples_l), 1

        def preview_pad():
            slot = selected_slot()
            if slot is None:
                return
            source = session.prep.sources[slot]
            if source.source_type == SourceType.EMPTY or source.source_path is None:
                messagebox.showinfo("Preview", "No sample assigned to this pad.", parent=dialog)
                return
            stop_playback()
            try:
                if source.source_type == SourceType.ARCHIVED_SP0:
                    pcm, n_samples, channels = _decode_sp0_to_pcm(source.source_path, source.is_stereo)
                    fd, tmp_path = tempfile.mkstemp(suffix='.wav')
                    with os.fdopen(fd, 'wb') as f:
                        sp303_write_wav(f, n_samples, 32000, channels)
                        f.write(struct.pack(f'<{len(pcm)}h', *pcm))
                    _launch_playback(tmp_path, tmp_path)
                else:
                    _launch_playback(str(source.source_path))
            except Exception as exc:
                messagebox.showerror("Preview", str(exc), parent=dialog)

        def convert_sp0_to_wav():
            l_path: Optional[Path] = None
            is_stereo = False
            sel = tree.selection()
            if sel:
                source = session.prep.sources[int(sel[0])]
                if source.source_type == SourceType.ARCHIVED_SP0 and source.source_path is not None:
                    l_path = source.source_path
                    is_stereo = source.is_stereo
            if l_path is None:
                sp0_file = filedialog.askopenfilename(
                    parent=dialog,
                    title="Select SP0 File to Convert",
                    filetypes=[("SP0 Files", "*.SP0 *.sp0"), ("All Files", "*.*")],
                )
                if not sp0_file:
                    return
                l_path = Path(sp0_file)
                if l_path.name.upper().endswith('L.SP0'):
                    r_path = l_path.with_name(l_path.name[:-5] + "R.SP0")
                    is_stereo = r_path.exists()
            stem = l_path.stem[:-1] if l_path.stem.upper().endswith('L') else l_path.stem
            out_file = filedialog.asksaveasfilename(
                parent=dialog,
                title="Save WAV As",
                initialfile=stem + '.wav',
                defaultextension='.wav',
                filetypes=[("WAV Files", "*.wav"), ("All Files", "*.*")],
            )
            if not out_file:
                return
            try:
                pcm, n_samples, channels = _decode_sp0_to_pcm(l_path, is_stereo)
                with open(out_file, 'wb') as f:
                    sp303_write_wav(f, n_samples, 32000, channels)
                    f.write(struct.pack(f'<{len(pcm)}h', *pcm))
                duration = n_samples / 32000.0
                messagebox.showinfo(
                    "Convert SP0 to WAV",
                    f"Saved: {out_file}\n{n_samples:,} samples  {duration:.2f}s  "
                    f"{'Stereo' if channels == 2 else 'Mono'}  32 kHz",
                    parent=dialog,
                )
                log.info("SP0 → WAV: %s → %s (%.2fs, %s)", l_path.name, out_file, duration,
                         'stereo' if channels == 2 else 'mono')
            except Exception as exc:
                messagebox.showerror("Convert SP0 to WAV", str(exc), parent=dialog)

        def on_dialog_close():
            stop_playback()
            dialog.destroy()

        ttk.Button(setup_row, text="Load Card Setup", command=load_smpinfo_metadata).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(route_row, text="Assign WAV", command=assign_wav).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(route_row, text="Assign SP0", command=assign_sp0).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(route_row, text="Clear Pad", command=clear_pad).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(route_row, text="Refresh", command=refresh_tree).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(route_row, text="Preview", command=preview_pad).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(write_row, text="Write Changes to Card", command=prepare_card_now).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(write_row, text="Close", command=on_dialog_close).pack(side=tk.RIGHT)

        sm_menubar = tk.Menu(dialog, tearoff=0)
        sm_tools = tk.Menu(sm_menubar, tearoff=0)
        sm_menubar.add_cascade(label="Tools", menu=sm_tools)
        sm_tools.add_command(label="Preview Selected Pad", command=preview_pad, accelerator="Space")
        sm_tools.add_command(label="Convert SP0 to WAV...", command=convert_sp0_to_wav)
        dialog.configure(menu=sm_menubar)
        tree.bind("<space>", lambda e: preview_pad())
        dialog.protocol("WM_DELETE_WINDOW", on_dialog_close)

        if TKDND_AVAILABLE and hasattr(tree, "drop_target_register"):
            def on_tree_drop_position(event):
                row_id = tree.identify_row(event.y)
                col_id = tree.identify_column(event.x)
                if row_id and col_id == "#3":
                    set_drop_target(row_id)
                else:
                    set_drop_target(None)
                return "break"

            def on_tree_drop_leave(_event):
                set_drop_target(None)
                return "break"

            def on_tree_drop(event):
                row_id = tree.identify_row(event.y)
                col_id = tree.identify_column(event.x)
                if not row_id or col_id != "#3":
                    set_drop_target(None)
                    return "break"

                try:
                    dropped_paths = list(dialog.tk.splitlist(event.data))
                except Exception:
                    dropped_paths = [event.data]

                wav_path = None
                for raw_path in dropped_paths:
                    candidate = Path(str(raw_path).strip().strip("{}"))
                    if candidate.suffix.lower() == ".wav":
                        wav_path = candidate
                        break

                if wav_path is None:
                    messagebox.showerror(
                        "Assign WAV",
                        "Drop a .wav file onto the File column for the target pad.",
                        parent=dialog,
                    )
                    return "break"

                if not wav_path.exists():
                    messagebox.showerror("Assign WAV", f"File not found: {wav_path}", parent=dialog)
                    return "break"

                try:
                    assign_wav_to_slot(int(row_id), wav_path)
                except Exception as exc:
                    messagebox.showerror("Assign WAV", str(exc), parent=dialog)
                set_drop_target(None)
                return "break"

            tree.drop_target_register(DND_FILES)
            tree.dnd_bind("<<DropPosition>>", on_tree_drop_position)
            tree.dnd_bind("<<DropLeave>>", on_tree_drop_leave)
            tree.dnd_bind("<<Drop>>", on_tree_drop)

        def on_tree_double_click(event):
            row_id = tree.identify_row(event.y)
            col_id = tree.identify_column(event.x)
            if not row_id or col_id != "#3":
                return

            wav_file = filedialog.askopenfilename(
                parent=dialog,
                title="Select WAV File",
                filetypes=[("WAV Files", "*.wav *.WAV"), ("All Files", "*.*")],
            )
            if not wav_file:
                return

            try:
                assign_wav_to_slot(int(row_id), Path(wav_file))
            except Exception as exc:
                messagebox.showerror("Assign WAV", str(exc), parent=dialog)

        def swap_assignments(slot_a: int, slot_b: int):
            if slot_a == slot_b:
                return
            session.prep.sources[slot_a], session.prep.sources[slot_b] = (
                session.prep.sources[slot_b],
                session.prep.sources[slot_a],
            )
            session.prep.sources[slot_a].slot_index = slot_a
            session.prep.sources[slot_b].slot_index = slot_b
            if slot_a in slot_metadata or slot_b in slot_metadata:
                meta_a = slot_metadata.get(slot_a)
                meta_b = slot_metadata.get(slot_b)
                if meta_b is None:
                    slot_metadata.pop(slot_a, None)
                else:
                    slot_metadata[slot_a] = meta_b
                if meta_a is None:
                    slot_metadata.pop(slot_b, None)
                else:
                    slot_metadata[slot_b] = meta_a
            gate_a = gate_state.pop(slot_a, False)
            gate_b = gate_state.pop(slot_b, False)
            if gate_b:
                gate_state[slot_a] = gate_b
            if gate_a:
                gate_state[slot_b] = gate_a
            loop_a = loop_state.pop(slot_a, False)
            loop_b = loop_state.pop(slot_b, False)
            if loop_b:
                loop_state[slot_a] = loop_b
            if loop_a:
                loop_state[slot_b] = loop_a
            rev_a = reverse_state.pop(slot_a, False)
            rev_b = reverse_state.pop(slot_b, False)
            if rev_b:
                reverse_state[slot_a] = rev_b
            if rev_a:
                reverse_state[slot_b] = rev_a
            refresh_tree()
            tree.selection_set(str(slot_b))
            tree.focus(str(slot_b))

        def on_tree_press(event):
            nonlocal rearrange_source_iid
            row_id = tree.identify_row(event.y)
            rearrange_source_iid = row_id if row_id else None

        def on_tree_drag(event):
            if not rearrange_source_iid:
                return
            row_id = tree.identify_row(event.y)
            if not row_id or row_id == rearrange_source_iid:
                set_rearrange_target(None)
                return
            set_rearrange_target(row_id)

        # Column indices (1-based, show="headings"):
        # #1=bank_pad #2=source #3=file #4=long_lofi #5=stereo #6=length #7=duration #8=gate #9=loop #10=reverse
        TOGGLE_COLS = {"#8": "gate", "#9": "loop", "#10": "reverse"}

        def handle_cell_toggle(slot: int, col_id: str):
            field = TOGGLE_COLS.get(col_id)
            if not field:
                return
            if loaded_smpinfo_path is None or slot not in slot_metadata:
                return
            if field == "gate":
                new_val = not gate_state.get(slot, False)
                gate_state[slot] = new_val
                slot_metadata[slot]["gate"] = "Gate" if new_val else "Off"
                refresh_tree()
                self.update_status(f"{slot_to_label(slot)} Gate: {'On' if new_val else 'Off'}")
            elif field == "loop":
                new_val = not loop_state.get(slot, False)
                loop_state[slot] = new_val
                slot_metadata[slot]["loop"] = "Loop" if new_val else "Off"
                refresh_tree()
                self.update_status(f"{slot_to_label(slot)} Loop: {'On' if new_val else 'Off'}")
            elif field == "reverse":
                new_val = not reverse_state.get(slot, False)
                reverse_state[slot] = new_val
                slot_metadata[slot]["reverse"] = "Reverse" if new_val else "Off"
                refresh_tree()
                self.update_status(f"{slot_to_label(slot)} Reverse: {'On' if new_val else 'Off'}")

        def on_tree_release(event):
            nonlocal rearrange_source_iid
            if rearrange_source_iid and rearrange_target_iid:
                try:
                    swap_assignments(int(rearrange_source_iid), int(rearrange_target_iid))
                except Exception as exc:
                    messagebox.showerror("Rearrange Pads", str(exc), parent=dialog)
            elif rearrange_source_iid:
                row_id = tree.identify_row(event.y)
                col_id = tree.identify_column(event.x)
                if row_id and row_id == rearrange_source_iid and col_id in TOGGLE_COLS:
                    handle_cell_toggle(int(row_id), col_id)
            rearrange_source_iid = None
            set_rearrange_target(None)

        tree.bind("<Double-1>", on_tree_double_click)
        tree.bind("<ButtonPress-1>", on_tree_press, add="+")
        tree.bind("<B1-Motion>", on_tree_drag, add="+")
        tree.bind("<ButtonRelease-1>", on_tree_release, add="+")

        refresh_tree()
        tree.selection_set("0")
        tree.focus("0")
        if smpinfo_path is not None:
            dialog.after(50, lambda: load_smpinfo_from_path(smpinfo_path))

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
        """Update recent files in File menu"""
        # Remove existing recent file entries
        # Delete items between the "Open..." command and the next separator
        try:
            # Count items to delete (from start index to next separator)
            delete_count = 0
            for i in range(self.recent_files_menu_start_index, self.file_menu.index("end") + 1):
                try:
                    if self.file_menu.type(i) == "separator":
                        break
                    delete_count += 1
                except tk.TclError:
                    break

            # Delete the items
            for _ in range(delete_count):
                self.file_menu.delete(self.recent_files_menu_start_index)
        except tk.TclError:
            pass  # Menu might not be fully initialized yet

        # Add recent files if any exist
        if self.recent_files:
            for i, path in enumerate(self.recent_files):
                # Show shortened path (parent directory name + filename)
                display_name = f"{path.parent.name}/{path.name}"
                self.file_menu.insert_command(
                    self.recent_files_menu_start_index + i,
                    label=f"{i + 1}. {display_name}",
                    command=lambda p=path: self.open_recent_file(p)
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
            self.canvas.redraw()
            self.update_status(f"Loaded: {ptninfo_path.parent}")

            # Move to front of recent files
            self.add_recent_file(ptninfo_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load pattern: {e}")

