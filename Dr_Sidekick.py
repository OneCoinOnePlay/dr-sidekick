#!/usr/bin/env python3
"""
Dr. Sidekick - Standalone graphical pattern editor and SmartMedia librarian for the BOSS Dr. Sample SP-303
==========================================================================================================

Disclaimer: Dr. Sidekick is an independent community project and is not affiliated with, endorsed by, or supported by Roland Corporation or BOSS

Author: One Coin One Play
github.com/OneCoinOnePlay
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import asdict, dataclass, field
from copy import deepcopy
from datetime import datetime
from enum import Enum
import logging
from logging.handlers import RotatingFileHandler
import math
import sys
import random
import struct
import json
import shutil
import textwrap
import traceback
import urllib.error
import urllib.request
import threading
import wave
import os
import subprocess
import tempfile

# ── Session logger ────────────────────────────────────────────────────────────
_LOG_PATH = Path(__file__).parent / "Dr_Sidekick.log"
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

# Inlined pattern engine
# SP-303 Constants
from dr_sidekick.engine import *  # noqa: F401,F403
from dr_sidekick.app_state import AppState
from dr_sidekick.ui.constants import *  # noqa: F401,F403
from dr_sidekick.ui.dialogs import show_text_dialog


class PianoRollCanvas(tk.Canvas):
    """
    Visual editor - piano roll with event blocks

    Coordinate system:
    - X axis: time in ticks (horizontal)
    - Y axis: pad lanes (vertical, 32 lanes for Banks A, B, C & D)
    """

    def __init__(self, parent, model: PatternModel, **kwargs):
        super().__init__(parent, bg=COLORS["background"], highlightthickness=0, **kwargs)

        self.model = model
        self.zoom_x = 1.0  # pixels per tick (reduced from 4.0 for better overview)
        self.zoom_y = 25   # pixels per lane (reduced from 40 for better overview)
        self.offset_x = 0  # cached scroll offset (for lane labels/status)
        self.offset_y = 0
        self.grid_snap = GRID_SNAPS["16"]  # Default snap (16th notes)
        self.pattern_length_bars = DEFAULT_PATTERN_LENGTH_BARS
        self.colors = COLORS  # Current color palette

        # UI state (use list instead of set since Event is not hashable)
        self.selected_events: List[Event] = []
        self.dragging_event: Optional[Event] = None
        self.drag_start_pos: Optional[Tuple[int, int]] = None
        self.drag_offset: Optional[Tuple[int, int]] = None
        self.selection_rect_start: Optional[Tuple[int, int]] = None
        self.edit_mode = "Draw"  # Draw, Select, Erase

        # Bind mouse events
        self.bind("<Button-1>", self.on_mouse_down)
        self.bind("<B1-Motion>", self.on_mouse_drag)
        self.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.bind("<Motion>", self.on_mouse_move)

        # Right-click to delete events
        self.bind("<Button-2>", self.on_right_click)  # macOS (Button-2 is right-click)
        self.bind("<Button-3>", self.on_right_click)  # Linux/Windows (Button-3 is right-click)

        # Bind keyboard
        self.bind("<Delete>", self.on_delete_key)
        self.bind("<BackSpace>", self.on_delete_key)
        self.bind("<d>", self.on_delete_key)
        self.bind("<D>", self.on_delete_key)

        # Bind velocity shortcuts
        self.bind("<bracketleft>", self.on_velocity_decrease)  # [
        self.bind("<bracketright>", self.on_velocity_increase)  # ]

        # Bind zoom shortcuts
        self.bind("<plus>", lambda e: self.zoom_in())
        self.bind("<equal>", lambda e: self.zoom_in())  # For keyboards without numpad
        self.bind("<minus>", lambda e: self.zoom_out())
        self.bind("<0>", lambda e: self.zoom_reset())

    def set_grid_snap(self, snap_name: str):
        """Set grid snap value"""
        self.grid_snap = GRID_SNAPS.get(snap_name, 0)
        self.redraw()

    def set_pattern_length(self, bars: int):
        """Set pattern length in bars"""
        self.pattern_length_bars = max(1, min(MAX_PATTERN_LENGTH_BARS, bars))
        self.redraw()

    def zoom_in(self):
        """Zoom in (increase zoom factors)"""
        self.zoom_x = min(self.zoom_x * 1.5, 10.0)  # Max 10 pixels per tick
        self.zoom_y = min(self.zoom_y * 1.2, 60)    # Max 60 pixels per lane
        self.redraw()

    def zoom_out(self):
        """Zoom out (decrease zoom factors)"""
        self.zoom_x = max(self.zoom_x / 1.5, 0.25)  # Min 0.25 pixels per tick
        self.zoom_y = max(self.zoom_y / 1.2, 15)    # Min 15 pixels per lane
        self.redraw()

    def zoom_reset(self):
        """Reset zoom to default"""
        self.zoom_x = 1.0
        self.zoom_y = 25
        self.redraw()

    def set_color_palette(self, palette_name: str):
        """Set color palette"""
        if palette_name in COLOR_PALETTES:
            self.colors = COLOR_PALETTES[palette_name]
            self.config(bg=self.colors["background"])
            self.redraw()

    def set_edit_mode(self, mode: str):
        """Set edit mode"""
        self.edit_mode = mode
        self.selected_events.clear()
        self.redraw()

    def redraw(self):
        """Redraw entire canvas"""
        self.delete("all")
        self._refresh_view_offsets()

        # Get canvas dimensions
        width = self.winfo_width()
        height = self.winfo_height()

        if width <= 1 or height <= 1:
            return

        # Update scroll region based on zoom level
        self._update_scroll_region()

        # Draw ruler at top
        self._draw_ruler(width)

        # Draw grid
        self._draw_grid(width, height)

        # Draw lane separators
        self._draw_lane_separators(width, height)

        # Draw pattern end marker
        self._draw_pattern_end_marker(height)

        # Draw events
        self._draw_events()

        # Draw selection rectangle
        if self.selection_rect_start is not None:
            x0, y0 = self.selection_rect_start
            x1 = self.canvasx(self.winfo_pointerx() - self.winfo_rootx())
            y1 = self.canvasy(self.winfo_pointery() - self.winfo_rooty())
            fill_color, fill_stipple = self._tk_fill_style(self.colors["selection_fill"])
            self.create_rectangle(
                x0, y0, x1, y1,
                outline=self.colors["selection_rect"],
                fill=fill_color,
                stipple=fill_stipple,
                width=2,
                tags="selection_rect"
            )

    def _tk_fill_style(self, color: str) -> Tuple[str, str]:
        """Convert RGBA-like hex to Tk-compatible fill + stipple."""
        # Tk accepts #RRGGBB, not #RRGGBBAA.
        if isinstance(color, str) and color.startswith("#") and len(color) == 9:
            return color[:7], "gray25"
        return color, ""

    def _update_scroll_region(self):
        """Update scrollable region based on zoom and pattern length"""
        # Calculate total width needed (pattern length in pixels)
        max_ticks = self.pattern_length_bars * 4 * INTERNAL_PPQN
        total_width = int(max_ticks * self.zoom_x) + 100  # Add padding

        # Calculate total height needed (32 lanes + ruler)
        ruler_height = 25
        total_height = ruler_height + (len(PAD_ORDER) * self.zoom_y) + 50  # Add padding

        # Set scroll region
        self.config(scrollregion=(0, 0, total_width, total_height))
    
    def _refresh_view_offsets(self):
        """Sync cached view offsets with current canvas scroll position."""
        self.offset_x = self.canvasx(0)
        self.offset_y = self.canvasy(0)

    def xview(self, *args):
        """Track horizontal scroll so hit-testing uses the visible viewport."""
        result = super().xview(*args)
        self._refresh_view_offsets()
        self.redraw()
        return result

    def yview(self, *args):
        """Track vertical scroll so lane hit-testing remains accurate."""
        result = super().yview(*args)
        self._refresh_view_offsets()
        self.redraw()
        return result

    def _draw_ruler(self, width: int):
        """Draw ruler at top showing bar numbers"""
        ruler_height = 25
        left_px = self.canvasx(0)
        right_px = self.canvasx(width)
        left_tick = int(left_px / self.zoom_x)
        right_tick = int(right_px / self.zoom_x)

        # Draw ruler background
        self.create_rectangle(
            0, 0, width, ruler_height,
            fill=self.colors["ruler_bg"],
            outline="",
            tags="ruler_bg"
        )

        # Draw bar markers
        bar_ticks = 4 * INTERNAL_PPQN  # 4 beats per bar

        for bar in range(left_tick // bar_ticks, (right_tick // bar_ticks) + 2):
            tick = bar * bar_ticks
            x = self.tick_to_x(tick)
            if left_px - 50 <= x <= right_px + 50:
                # Draw bar number
                self.create_text(
                    x + 3, 12,
                    text=f"{bar + 1}",
                    fill=self.colors["ruler_text"],
                    font=("Arial", 9),
                    anchor="w",
                    tags="ruler_text"
                )
                # Draw bar line
                self.create_line(
                    x, ruler_height - 5, x, ruler_height,
                    fill=self.colors["ruler_text"],
                    width=1,
                    tags="ruler_line"
                )

    def _draw_grid(self, width: int, height: int):
        """Draw grid lines"""
        ruler_height = 25
        left_px = self.canvasx(0)
        right_px = self.canvasx(width)
        top_py = self.canvasy(0)
        bottom_py = self.canvasy(height)
        left_tick = int(left_px / self.zoom_x)
        right_tick = int(right_px / self.zoom_x)

        # Vertical grid lines (time)
        # Draw grid based on snap setting, or every quarter note if snap is off
        grid_interval = self.grid_snap if self.grid_snap > 0 else 96

        for tick in range(left_tick - (left_tick % grid_interval), right_tick + 1, grid_interval):
            x = self.tick_to_x(tick)
            # Major beat line every 96 ticks (quarter note)
            is_major = (tick % 96) == 0
            color = self.colors["grid_major"] if is_major else self.colors["grid_minor"]
            width_val = 2 if is_major else 1
            self.create_line(
                x, top_py + ruler_height, x, bottom_py,
                fill=color, width=width_val, tags="grid"
            )

    def _draw_lane_separators(self, width: int, height: int):
        """Draw horizontal lane separators"""
        ruler_height = 25
        top_y = self.canvasy(0)
        bottom_y = self.canvasy(height)
        left_x = self.canvasx(0)
        right_x = self.canvasx(width)
        for i in range(len(PAD_ORDER) + 1):
            y = i * self.zoom_y + ruler_height
            if top_y <= y <= bottom_y:
                self.create_line(left_x, y, right_x, y, fill=self.colors["lane_separator"], tags="lane_sep")

    def _draw_pattern_end_marker(self, height: int):
        """Draw vertical line showing pattern end"""
        top_py = self.canvasy(0)
        bottom_py = self.canvasy(height)
        max_ticks = self.pattern_length_bars * 4 * INTERNAL_PPQN
        x = self.tick_to_x(max_ticks)

        # Draw pattern end line
        self.create_line(
            x, top_py, x, bottom_py,
            fill=self.colors["pattern_end"],
            width=3,
            dash=(8, 4),
            tags="pattern_end"
        )

        # Draw label
        self.create_text(
            x + 5, top_py + 35,
            text=f"End (Bar {self.pattern_length_bars})",
            fill=self.colors["pattern_end"],
            font=("Arial", 9, "bold"),
            anchor="w",
            tags="pattern_end_label"
        )

    def _draw_events(self):
        """Draw event blocks"""
        for event in self.model.events:
            self._draw_event(event, event in self.selected_events)

    def _draw_event(self, event: Event, selected: bool):
        """Draw single event block"""
        ruler_height = 25

        # Get coordinates
        x = self.tick_to_x(event.tick)
        lane_index = PAD_ORDER.index(event.pad) if event.pad in PAD_ORDER else 0
        y = lane_index * self.zoom_y + ruler_height

        # Event width (minimum 8 pixels)
        event_width = max(8, self.zoom_x * 12)  # ~12 ticks wide

        # Get color based on pad
        color = self._get_event_color(event.pad, event.velocity)

        # Draw rectangle
        outline_color = "#ffffff" if selected else color
        outline_width = 2 if selected else 1

        self.create_rectangle(
            x, y + 2, x + event_width, y + self.zoom_y - 2,
            fill=color,
            outline=outline_color,
            width=outline_width,
            tags=("event", f"event_{id(event)}")
        )

    def _get_event_color(self, pad: int, velocity: int) -> str:
        """Get color for event based on pad and velocity"""
        # Determine bank and pad index
        if 0x00 <= pad <= 0x07:
            # Bank A
            pad_index = pad - 0x00
            base_color = self.colors["pad_a"][pad_index]
        elif 0x08 <= pad <= 0x0F:
            # Bank B
            pad_index = pad - 0x08
            base_color = self.colors["pad_b"][pad_index]
        elif 0x10 <= pad <= 0x17:
            # Bank C
            pad_index = pad - 0x10
            base_color = self.colors["pad_c"][pad_index]
        elif 0x18 <= pad <= 0x1F:
            # Bank D
            pad_index = pad - 0x18
            base_color = self.colors["pad_d"][pad_index]
        else:
            return "#888888"

        # Adjust brightness by velocity (default velocity is 127/0x7F - maximum)
        # Use a range that keeps max velocity bright
        clamped_velocity = max(0, min(127, int(velocity)))
        brightness_factor = 0.5 + (clamped_velocity / 127.0) * 0.5

        # Parse hex color
        r = int(base_color[1:3], 16)
        g = int(base_color[3:5], 16)
        b = int(base_color[5:7], 16)

        # Apply brightness
        r = max(0, min(255, int(r * brightness_factor)))
        g = max(0, min(255, int(g * brightness_factor)))
        b = max(0, min(255, int(b * brightness_factor)))

        return f"#{r:02x}{g:02x}{b:02x}"

    def tick_to_x(self, tick: int) -> int:
        """Convert tick to X pixel coordinate"""
        return int(tick * self.zoom_x)

    def x_to_tick(self, x: int) -> int:
        """Convert X pixel to tick (with snap)"""
        tick = int(self.canvasx(x) / self.zoom_x)
        if self.grid_snap > 0:
            tick = round(tick / self.grid_snap) * self.grid_snap
        max_ticks = self.pattern_length_bars * 4 * INTERNAL_PPQN  # bars * 4 beats * 96 PPQN
        return max(0, min(max_ticks - 1, tick))

    def y_to_pad(self, y: int) -> int:
        """Convert Y pixel to pad"""
        ruler_height = 25
        lane_index = int((self.canvasy(y) - ruler_height) / self.zoom_y)
        if 0 <= lane_index < len(PAD_ORDER):
            return PAD_ORDER[lane_index]
        return PAD_ORDER[0]

    def find_event_at(self, x: int, y: int) -> Optional[Event]:
        """Find event at pixel coordinates"""
        ruler_height = 25

        # Ignore clicks in ruler area
        if y < ruler_height:
            return None

        # Convert to actual pixel tick (no snapping for hit detection)
        tick_raw = int(self.canvasx(x) / self.zoom_x)
        pad = self.y_to_pad(y)

        # Match hit-test width to rendered event width.
        # Rendered width is max(8px, zoom_x*12px in tick-space), so convert to ticks.
        event_width_ticks = max(12.0, 8.0 / max(self.zoom_x, 1e-6))
        for event in self.model.events:
            if event.pad == pad:
                # Check if click is within the visual event block
                if event.tick <= tick_raw <= (event.tick + event_width_ticks):
                    return event
        return None

    def on_mouse_down(self, event):
        """Handle mouse down"""
        self.focus_set()
        # Find event at position
        clicked_event = self.find_event_at(event.x, event.y)

        if self.edit_mode == "Draw":
            if clicked_event:
                # Select and prepare to drag
                self.selected_events.clear()
                self.selected_events.append(clicked_event)
                self.dragging_event = clicked_event
                self.drag_start_pos = (event.x, event.y)
                self.drag_offset = (0, 0)
            else:
                # Add new event
                tick = self.x_to_tick(event.x)
                pad = self.y_to_pad(event.y)
                self.model.add_event(tick, pad)
                self.redraw()

        elif self.edit_mode == "Select":
            if clicked_event:
                # Toggle selection
                if clicked_event in self.selected_events:
                    self.selected_events.remove(clicked_event)
                else:
                    self.selected_events.append(clicked_event)
                self.dragging_event = clicked_event
                self.drag_start_pos = (event.x, event.y)
            else:
                # Start selection rectangle
                self.selection_rect_start = (self.canvasx(event.x), self.canvasy(event.y))
                self.selected_events.clear()
            self.redraw()

        elif self.edit_mode == "Erase":
            if clicked_event:
                self.model.remove_event(clicked_event)
                if clicked_event in self.selected_events:
                    self.selected_events.remove(clicked_event)
                self.redraw()

    def on_mouse_drag(self, event):
        """Handle mouse drag"""
        if self.edit_mode == "Draw" and self.dragging_event:
            # Move event
            new_tick = self.x_to_tick(event.x)
            new_pad = self.y_to_pad(event.y)
            self.model.move_event(self.dragging_event, new_tick, new_pad)
            self.redraw()

        elif self.edit_mode == "Select" and self.selection_rect_start:
            # Update selection rectangle
            self.redraw()

    def on_mouse_up(self, event):
        """Handle mouse up"""
        if self.edit_mode == "Select" and self.selection_rect_start:
            # Complete selection rectangle
            x0, y0 = self.selection_rect_start
            x1, y1 = self.canvasx(event.x), self.canvasy(event.y)

            # Normalize rectangle
            left = min(x0, x1)
            right = max(x0, x1)
            top = min(y0, y1)
            bottom = max(y0, y1)

            # Select events in rectangle
            self.selected_events.clear()
            for evt in self.model.events:
                evt_x = self.tick_to_x(evt.tick)
                lane_index = PAD_ORDER.index(evt.pad) if evt.pad in PAD_ORDER else 0
                evt_y = lane_index * self.zoom_y + 25

                if left <= evt_x <= right and top <= evt_y <= bottom:
                    self.selected_events.append(evt)

            self.selection_rect_start = None
            self.redraw()

        self.dragging_event = None
        self.drag_start_pos = None

    def on_mouse_move(self, event):
        """Handle mouse move (for cursor changes)"""
        pass

    def on_delete_key(self, event):
        """Handle delete key"""
        if self.selected_events:
            self.model.remove_events(list(self.selected_events))
            self.selected_events.clear()
            self.redraw()

    def on_velocity_decrease(self, event):
        """Decrease velocity of selected events"""
        if self.selected_events:
            self.model.push_undo_state()
            for evt in self.selected_events:
                evt.velocity = max(0, min(127, evt.velocity - 10))
            self.model.dirty = True
            self.redraw()
            return "break"

    def on_velocity_increase(self, event):
        """Increase velocity of selected events"""
        if self.selected_events:
            self.model.push_undo_state()
            for evt in self.selected_events:
                evt.velocity = max(0, min(127, evt.velocity + 10))
            self.model.dirty = True
            self.redraw()
            return "break"

    def on_right_click(self, event):
        """Handle right-click to delete event"""
        clicked_event = self.find_event_at(event.x, event.y)
        if clicked_event:
            self.model.remove_event(clicked_event)
            if clicked_event in self.selected_events:
                self.selected_events.remove(clicked_event)
            self.redraw()


class SmartMediaLibraryWindow:
    """SmartMedia Library — the application's true root window."""

    def __init__(self, root, state: 'AppState'):
        self.root = root
        self.state = state
        self.root.title("Dr. Sidekick — SmartMedia Library")
        self.root.geometry("1200x720")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._setup_styles()

        self._editor: Optional['PatternManagerWindow'] = None
        self._editor_win: Optional[tk.Toplevel] = None

        self._build_ui()

    # ── Styles ───────────────────────────────────────────────────────────

    def _setup_styles(self):
        """Configure global ttk dark-theme styles."""
        style = ttk.Style()
        style.theme_use('clam')
        self.root.configure(bg="#000000")
        style.configure(".", background="#000000", foreground="#ffffff")
        style.configure("TFrame", background="#000000")
        style.configure("TLabel", background="#000000", foreground="#ffffff")
        style.configure("TButton", background="#111111", foreground="#ffffff", borderwidth=1)
        style.map(
            "TButton",
            background=[("active", "#1a1a1a"), ("pressed", "#222222")],
            foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
        )
        style.configure(
            "TCombobox",
            fieldbackground="#000000",
            background="#111111",
            foreground="#ffffff",
            arrowcolor="#ffffff",
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#000000")],
            selectbackground=[("readonly", "#000000")],
            selectforeground=[("readonly", "#ffffff")],
            foreground=[("readonly", "#ffffff")],
        )
        style.configure(
            "Treeview",
            background="#000000",
            fieldbackground="#000000",
            foreground="#ffffff",
            bordercolor="#222222",
            lightcolor="#222222",
            darkcolor="#222222",
        )
        style.map("Treeview", background=[("selected", "#1f1f1f")], foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background="#111111", foreground="#ffffff")
        style.map(
            "Treeview.Heading",
            background=[("active", "#111111"), ("pressed", "#111111")],
            foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
        )
        style.configure("TRadiobutton", background="#000000", foreground="#ffffff", indicatorcolor="#111111")
        style.map(
            "TRadiobutton",
            background=[("active", "#000000"), ("focus", "#000000"), ("selected", "#000000")],
            foreground=[("active", "#ffffff"), ("focus", "#ffffff"), ("selected", "#ffffff")],
            indicatorcolor=[("selected", "#00aa55"), ("active", "#111111"), ("!selected", "#111111")],
        )
        style.configure("TCheckbutton", background="#000000", foreground="#ffffff")
        style.configure("TEntry", fieldbackground="#000000", foreground="#ffffff")
        style.configure("TSpinbox", fieldbackground="#000000", foreground="#ffffff")
        style.configure("Toolbar.TLabel", background="#000000", foreground="#ffffff", font=("", 10, "bold"))
        style.configure("Toolbar.TButton", background="#111111", foreground="#ffffff", font=("", 10, "bold"))
        style.configure(
            "Toolbar.TRadiobutton",
            background="#000000",
            foreground="#ffffff",
            font=("", 10, "bold"),
            indicatorcolor="#111111",
        )
        style.map(
            "Toolbar.TRadiobutton",
            background=[("active", "#000000"), ("focus", "#000000"), ("selected", "#000000")],
            foreground=[("active", "#ffffff"), ("focus", "#ffffff"), ("selected", "#ffffff")],
            indicatorcolor=[("selected", "#00aa55"), ("active", "#111111"), ("!selected", "#111111")],
        )
        style.configure(
            "Toolbar.TCombobox",
            fieldbackground="#000000",
            background="#111111",
            foreground="#ffffff",
            arrowcolor="#ffffff",
            font=("", 10, "bold"),
        )
        style.configure("Toolbar.TSpinbox", fieldbackground="#000000", foreground="#ffffff", font=("", 10, "bold"))


    # ── Library menu bar ─────────────────────────────────────────────────

    def _create_menu(self, *, open_card, backup_card, new_card, delete_card,
                     save_current_card, restore_to_card, open_in_manager,
                     create_virtual_card_from_physical):
        """Build and attach the library window menu bar."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # File
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Open Pattern Manager",
                              command=self.open_pattern_manager, accelerator="Ctrl+Shift+L")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)

        # Card
        card_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Card", menu=card_menu)
        card_menu.add_command(label="Open Card...", command=open_card)
        card_menu.add_command(label="Backup Card", command=backup_card)
        card_menu.add_separator()
        card_menu.add_command(label="New Card...", command=new_card)
        card_menu.add_command(label="Delete Card", command=delete_card)
        card_menu.add_separator()
        card_menu.add_command(label="Save Card Changes", command=save_current_card)
        card_menu.add_command(label="Restore to Card", command=restore_to_card)
        card_menu.add_command(label="Open in Sample Manager", command=open_in_manager)
        card_menu.add_separator()
        card_menu.add_command(label="Create Virtual Card from Physical",
                              command=create_virtual_card_from_physical)

        # Help
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Quick Start", command=self.on_help_quick_start)
        help_menu.add_command(label="Workflow Examples", command=self.on_help_workflow_examples)
        help_menu.add_command(label="FAQ / Troubleshooting", command=self.on_help_faq)
        help_menu.add_separator()
        help_menu.add_command(label="Check for Update...", command=self.on_check_for_update)
        help_menu.add_command(label="About", command=self.on_about)
        help_menu.add_separator()
        help_menu.add_command(label="View Session Log...", command=self.on_view_log)

        # Keyboard shortcuts
        self.root.bind("<Control-Shift-L>", lambda e: self.open_pattern_manager())
        self.root.bind("<Control-Shift-l>", lambda e: self.open_pattern_manager())

    # ── Pattern Manager ──────────────────────────────────────────────────

    def _open_sample_manager(self, smpinfo_path=None):
        """Open the Sample Manager dialog parented to the Library window."""
        state = self.state
        root = self.root

        class _Adapter:
            def update_status(self, msg): pass
            def set_loaded_card_context(self, ctx): pass
            def ask_output_directory(self, initialdir=None):
                kwargs = {"title": "Select Output Directory"}
                if initialdir is not None:
                    kwargs["initialdir"] = str(initialdir)
                result = filedialog.askdirectory(**kwargs)
                return Path(result) if result else None

        adapter = _Adapter()
        adapter.root = root
        adapter.state = state
        PatternManagerWindow.on_sample_manager(adapter, smpinfo_path=smpinfo_path)

    def open_pattern_manager(self) -> 'PatternManagerWindow':
        """Show the Pattern Manager window, creating it if needed."""
        if self._editor is None:
            self._editor_win = tk.Toplevel(self.root)
            debug_mode = "--debug" in sys.argv[1:]
            self._editor = PatternManagerWindow(self._editor_win, self.state, self, debug_mode=debug_mode)
        else:
            self._editor_win.deiconify()
        self._editor_win.lift()
        return self._editor

    def _on_close(self):
        """Quit the application."""
        self.root.destroy()

    # ── Library UI ───────────────────────────────────────────────────────

    def on_help_quick_start(self):
        """Show quick-start guide for beta users."""
        quick_start = """Dr. Sidekick — Quick Start

Welcome! Here's everything you need to get going.


THE SMARTMEDIA LIBRARY — Your starting point
─────────────────────────────────────────────
Dr. Sidekick opens with the SmartMedia Library. This is your personal library
of virtual SP-303 cards — one card per project, kit, or physical card.

Virtual cards are the heart of the workflow. They let you:

  • Back up your physical card — Card -> Backup Card preserves everything
    (patterns + samples) before you make any changes.

  • Build a project library — create as many virtual cards as you like, each
    with its own name, samples and patterns. Your work is never stuck on one
    physical card.

  • Restore to your physical card — when a virtual card is ready to perform,
    Card -> Restore to Card writes it straight back to the SP-303.

  • Import a physical card — Card -> Create Virtual Card from Physical reads
    a mounted SP-303 card and brings it into the library.

From the library you can branch into two main areas:


WORK ON PATTERNS
─────────────────────────────────────────────
Open the Pattern Manager: File -> Open Pattern Manager (or Ctrl+Shift+L).

  • Select a pattern slot (C1–D8), switch to Draw mode, and click the pad
    rows to place hits. Drag to move them, right-click to delete.
    Set bar length with the Pattern Length spinner.
    Adjust velocity by selecting notes and using the [ / ] keys.
    Save with Ctrl+S.

  • Import a MIDI file: Patterns -> Import MIDI File...

  • Apply a groove: Patterns -> Add Groove Pattern...

  • Copy or exchange patterns between slots:
    Edit -> Copy Pattern / Paste Pattern, or Patterns -> Exchange Patterns...


WORK ON SAMPLES
─────────────────────────────────────────────
All sample tools are in the Pattern Manager under the Samples menu.

  • Quick Import WAV Folder — point at a folder of WAVs. Dr. Sidekick
    converts and prepares everything in BOSS DATA_OUTGOING, ready to copy
    to the SP-303. More than 8 files? They split into BANK_LOAD_01,
    BANK_LOAD_02 etc. — load one bank at a time on the device.

  • Sample Manager — view and reassign which sample lives on which pad.
    Load a card setup, edit the table, then Write Changes to Card.

  • Convert MPC1000 Program (.pgm) — select a .pgm file and Dr. Sidekick
    maps all 64 pads to SP-303 banks, creates a new virtual card named
    after the program, and prepares BANK_LOAD folders for the device.


First time? Back up before you touch anything.
   Card -> Backup Card, then explore. If anything goes wrong,
   Card -> Restore to Card gets you back to where you started.
"""
        show_text_dialog(self.root, "Quick Start", quick_start, geometry="980x700")

    def on_help_workflow_examples(self):
        """Show real-world workflow examples."""
        examples = """WORKFLOW EXAMPLES


─────────────────────────────────────────────────────────────
Example 1: Load a Kit and Program a Pattern from Scratch
─────────────────────────────────────────────────────────────

Goal: Get your own samples onto the SP-303 and program a beat
      ready to play back on the hardware.

Step 1 — Open the Pattern Manager.
  In the SmartMedia Library window: File -> Open Pattern Manager (Ctrl+Shift+L).

Step 2 — Load your samples onto the card.
  Samples -> Quick Import WAV Folder -> select your kit folder.
  Files are prepared in SmartMedia-Library/Cards/BOSS DATA_OUTGOING.
  If more than 8 WAVs, load BANK_LOAD_01 first, then BANK_LOAD_02
  on the device. Samples land on pads A1–D8 in file order.

Step 3 — Program the pattern.
  Select a pattern slot (C1–D8). Switch to Draw mode.
  Click pad rows to place hits. Drag to move. Right-click to delete.
  Set bar length with the Pattern Length spinner.
  Adjust velocity by selecting notes and using [ / ] keys.

  Alternatively, import your own MIDI file:
  Patterns -> Import MIDI File... -> select your file.
  Review events in the editor and adjust as needed.

  Optionally apply a groove:
  Patterns -> Add Groove Pattern... -> select your groove file.

Step 4 — Save and load onto the SP-303.
  File -> Save (Ctrl+S).
  Copy the PTNINFO0.SP0 and PTNDATA0.SP0 files to your card.
  Eject safely, insert into SP-303, and play.

Note: A library of example MIDI patterns and grooves is planned for a future release.


─────────────────────────────────────────────────────────────
Example 2: Convert an MPC1000 Kit to SP-303
─────────────────────────────────────────────────────────────

Goal: Bring an MPC1000 drum program straight onto the SP-303,
      preserving the original pad layout as closely as possible.

Step 1 — Open the Pattern Manager, then go to
  Samples -> Convert MPC1000 Program (.pgm).
  Select the .pgm file. If the WAV samples are in the same folder
  (or a subfolder), no further prompt appears.
  If WAVs live elsewhere, a folder picker opens.

Step 2 — Review the results dialog.
  Each bank (A–H) shows which WAV landed on which SMPL slot.
  NOT FOUND entries mean the .pgm referenced a sample name that
  wasn't matched in the WAV folder — check spelling or relocate.

Step 3 — Load onto the SP-303.
  Open SmartMedia-Library/Cards/<pgm name> in Finder.
  Copy BANK_LOAD_01 contents (SMPL0001–SMPL0008.WAV) to your card.
  On the SP-303 select the target bank and run Import.
  Repeat for each BANK_LOAD folder.

Note: Each .pgm gets its own card named after the program file.
  Re-running with the same .pgm overwrites that card only.


─────────────────────────────────────────────────────────────
Example 3: Reorganize a Card Without Losing Anything
─────────────────────────────────────────────────────────────

Goal: Safely reassign pads and shuffle patterns on an existing card.

Step 1 — Back up first.
  In the SmartMedia Library window: Card -> Backup Card.
  A backup is created in Backup/ next to SmartMedia-Library.

Step 2 — Load the current card setup.
  In the Pattern Manager: Samples -> Sample Manager -> Load Card Setup.
  All current pad assignments appear in the table.

Step 3 — Reassign pads.
  Select a pad row, then use Assign WAV/SP0 to swap samples.
  The status bar confirms every change.

Step 4 — Remap or exchange patterns.
  In the Pattern Manager use Edit -> Copy Pattern / Paste Pattern
  to move patterns between slots without re-programming.

Step 5 — Write changes.
  In Sample Manager: Write Changes to Card.
  Eject safely and verify on device.
  If anything is wrong: SmartMedia Library -> Card -> Restore to Card.


─────────────────────────────────────────────────────────────
Example 4: Build and Refine a Sample Kit
─────────────────────────────────────────────────────────────

Goal: Load a folder of WAVs onto the SP-303, then fine-tune
      which sample sits on which pad before committing to the card.

Step 1 — Back up your current card first.
  In the SmartMedia Library window: Card -> Backup Card.
  A backup is stored in Backup/ next to SmartMedia-Library.

Step 2 — Quick Import your WAVs.
  In the Pattern Manager: Samples -> Quick Import WAV Folder.
  Select your kit folder. Dr. Sidekick converts and prepares
  the files in SmartMedia-Library/Cards/BOSS DATA_OUTGOING.
  If there are more than 8 WAVs they split into BANK_LOAD_01,
  BANK_LOAD_02 etc. — load one bank at a time on the device.
  Samples are assigned to pads in file order (A1 upwards).

Step 3 — Review and reassign pads.
  Samples -> Sample Manager -> Load Card Setup.
  The table shows every pad and its current assignment.
  To move a sample: select its row, click Assign WAV/SP0,
  and pick the replacement file. Repeat for any pad you want
  to change. The status bar confirms each reassignment.

Step 4 — Write to card.
  Click Write Changes to Card in the Sample Manager.
  Copy the output files to your physical SP-303 card.
  Eject safely, insert into SP-303, and verify on device.

Step 5 — Iterate.
  Not happy with the layout? Go back to Step 3 — the virtual
  card in the library holds your work between sessions.
  When you're satisfied, Card -> Backup Card again to save
  the final state before loading it onto the hardware.
"""
        show_text_dialog(self.root, "Workflow Examples", examples, geometry="980x700")

    def on_help_faq(self):
        """Show FAQ and troubleshooting notes for beta users."""
        faq = """FAQ / Troubleshooting (Beta)

Q: Where do I start — the SmartMedia Library or the Pattern Manager?
A: The SmartMedia Library window opens first and is always present. Use it to
   manage your virtual cards. Open the Pattern Manager from File -> Open Pattern
   Manager (or Ctrl+Shift+L) when you need to edit patterns or work with samples.

Q: I selected a single WAV file in Quick Import. Is that valid?
A: Yes. The app uses that file's parent folder automatically.

Q: Why do I get BANK_LOAD_01 folders?
A: More than 8 WAV files were found. SP-303 loads one bank (8 samples) at a time.

Q: Where are Quick Import files written?
A: SmartMedia-Library/Cards/BOSS DATA_OUTGOING (or /Volumes/BOSS DATA if your
   physical card is mounted and write-to-card is enabled).

Q: Existing WAVs disappeared from BOSS DATA_OUTGOING.
A: They are archived into the subfolder wav_archive_YYYYMMDD_HHMMSS before
   each Quick Import run.

Q: Write Changes completed, but the device did not reflect changes.
A: Most common causes:
   - Card not ejected safely before inserting into SP-303.
   - Wrong output path: check SmartMedia-Library/Cards/BOSS DATA_OUTGOING or
     confirm write-to-card is enabled if targeting a mounted physical card.

Q: How do I back up and restore a card?
A: In the SmartMedia Library window, select the card and use Card -> Backup Card.
   Backups are stored in Backup/ next to SmartMedia-Library. To restore, use
   Card -> Restore to Card.

Q: I used Convert MPC1000 Program and some pads say NOT FOUND.
A: The .pgm stores sample names without file extensions, and matching is
   done by filename stem. Check that your WAV filenames match the names
   stored in the .pgm (case-insensitive partial matches are tried too).
   If WAVs are in a different folder, re-run and point to the correct folder
   when the folder picker appears.

Q: Convert MPC1000 Program only shows a few banks — where are the rest?
A: Only banks that contain at least one matched sample are written.
   Empty banks are skipped. Pads with no assignment or unresolved samples
   are listed per-slot in the results dialog.

Q: I ran Convert MPC1000 twice with different programs and both are there.
A: Correct — each .pgm gets its own card named after the program file.
   Re-running with the same .pgm overwrites that card only.

Q: How do I import my physical SP-303 card into the library?
A: In the SmartMedia Library window: Card -> Create Virtual Card from Physical.
   This copies the SP0 files from the mounted card into a new virtual card entry.

"""
        show_text_dialog(self.root, "FAQ / Troubleshooting", faq, geometry="1024x680")

    def on_check_for_update(self):
        """Check GitHub for the latest release."""
        api_url = "https://api.github.com/repos/OneCoinOnePlay/dr-sidekick/releases/latest"
        current_version = APP_VERSION

        def parse_version(raw: str) -> Tuple[int, ...]:
            raw = raw.strip().lstrip("vV")
            parts: List[int] = []
            for token in raw.split("."):
                digits = "".join(ch for ch in token if ch.isdigit())
                if not digits:
                    break
                parts.append(int(digits))
            return tuple(parts) if parts else (0,)

        def show_result(title: str, msg: str):
            self.root.after(0, lambda: messagebox.showinfo(title, msg))

        def do_check():
            try:
                req = urllib.request.Request(
                    api_url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "Dr-Sidekick-Update-Check",
                    },
                )
                with urllib.request.urlopen(req, timeout=6) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                latest_tag = str(payload.get("tag_name", "")).strip()
                latest_name = str(payload.get("name", "")).strip()
                latest_version_label = latest_tag or latest_name or "unknown"
                release_url = str(payload.get("html_url", "https://github.com/OneCoinOnePlay/dr-sidekick/releases"))

                if parse_version(latest_version_label) > parse_version(current_version):
                    show_result(
                        "Update Available",
                        f"Current version: {current_version}\n"
                        f"Latest version: {latest_version_label}\n\n"
                        f"Download:\n{release_url}",
                    )
                else:
                    show_result(
                        "Up To Date",
                        f"Dr. Sidekick is up to date.\n\nCurrent version: {current_version}",
                    )
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
                show_result(
                    "Check for Update",
                    "Unable to check updates right now.\n\n"
                    f"Current version: {current_version}\n"
                    "Manual check:\nhttps://github.com/OneCoinOnePlay/dr-sidekick/releases",
                )

        threading.Thread(target=do_check, daemon=True).start()

    def on_view_log(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Session Log")
        dialog.geometry("900x540")
        dialog.transient(self.root)
        dialog.configure(bg="#000000")

        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        text = tk.Text(
            frame, wrap=tk.NONE, bg="#000000", fg="#cccccc",
            insertbackground="#ffffff", relief=tk.FLAT, highlightthickness=0,
            font=("Courier", 10),
        )
        scroll_y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        scroll_x = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=text.xview)
        text.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        text.pack(fill=tk.BOTH, expand=True)

        try:
            content = _LOG_PATH.read_text(encoding="utf-8") if _LOG_PATH.exists() else "(no log file yet)"
        except Exception as exc:
            content = f"(could not read log: {exc})"

        text.insert("1.0", content)
        text.configure(state=tk.DISABLED)
        text.see(tk.END)

        bottom = ttk.Frame(frame)
        bottom.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(bottom, text=str(_LOG_PATH), font=("Courier", 9)).pack(side=tk.LEFT)

        def clear_log():
            if not messagebox.askyesno("Clear Log", "Clear the session log file?", parent=dialog):
                return
            try:
                _LOG_PATH.write_text("", encoding="utf-8")
            except Exception as exc:
                messagebox.showerror("Clear Log", str(exc), parent=dialog)
                return
            log.info("Session log cleared by user.")
            try:
                new_content = _LOG_PATH.read_text(encoding="utf-8")
            except Exception:
                new_content = ""
            text.configure(state=tk.NORMAL)
            text.delete("1.0", tk.END)
            text.insert("1.0", new_content)
            text.configure(state=tk.DISABLED)
            text.see(tk.END)

        ttk.Button(bottom, text="Clear Log", command=clear_log).pack(side=tk.RIGHT)

    def on_about(self):
        """Show about dialog"""
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

    def _build_ui(self):
        """Build the library window UI directly into the root window."""
        self.state.smartmedia_lib.ensure_dirs()

        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        # ── Branding header ─────────────────────────────────────────────────
        header_frame = ttk.Frame(frame)
        header_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(header_frame, text="Dr. Sidekick", font=("Courier", 18, "bold")).pack(side=tk.LEFT)
        ttk.Label(header_frame, text="Pattern editor and SmartMedia librarian for the Boss Dr. Sample SP-303.",
                  font=("Courier", 9)).pack(side=tk.LEFT, padx=(12, 0), anchor=tk.S, pady=(0, 3))
        ttk.Button(header_frame, text="Open Pattern Manager",
                   command=self.open_pattern_manager).pack(side=tk.RIGHT)
        ttk.Button(header_frame, text="Open Sample Manager",
                   command=self._open_sample_manager).pack(side=tk.RIGHT, padx=(0, 6))

        # ── Top status bar ──────────────────────────────────────────────────
        top_bar = ttk.Frame(frame)
        top_bar.pack(fill=tk.X, pady=(0, 8))

        card_status_var = tk.StringVar(value="Checking physical card...")
        card_status_lbl = ttk.Label(top_bar, textvariable=card_status_var, font=("Courier", 10))
        card_status_lbl.pack(side=tk.LEFT)

        write_to_card_var = tk.BooleanVar(value=self.state.config.get("write_to_card", True))
        def on_write_toggle():
            self.state.config["write_to_card"] = write_to_card_var.get()
            self.state.save_config()
        ttk.Checkbutton(top_bar, text="Write to Card", variable=write_to_card_var,
                        command=on_write_toggle).pack(side=tk.RIGHT)

        open_card_status_var = tk.StringVar(value="No card open")
        ttk.Label(top_bar, textvariable=open_card_status_var, font=("Courier", 10)).pack(side=tk.RIGHT, padx=(0, 16))

        def refresh_card_status():
            preferred = Path("/Volumes/BOSS DATA")
            if preferred.exists():
                card_status_var.set(f"● BOSS DATA mounted: {preferred}")
            else:
                card_status_var.set("○ No physical card mounted")
            self.root.after(2000, refresh_card_status)
        refresh_card_status()

        auto_backup_var = tk.BooleanVar(value=self.state.config.get("auto_backup_on_open", False))
        def on_auto_backup_toggle():
            self.state.config["auto_backup_on_open"] = auto_backup_var.get()
            self.state.save_config()

        def open_card():
            preferred = Path("/Volumes/BOSS DATA") / "SMPINFO0.SP0"
            if preferred.exists():
                path = preferred
            else:
                chosen = filedialog.askopenfilename(
                    parent=self.root,
                    title="Select SMPINFO0.SP0",
                    initialdir=str(self.state.default_card_mount_dir()),
                    filetypes=[("SMPINFO0.SP0", "SMPINFO0.SP0"), ("SP0 Files", "*.SP0"), ("All Files", "*.*")],
                )
                if not chosen:
                    return
                path = Path(chosen)
            active_smpinfo[0] = path
            open_card_status_var.set(f"Open: {path.parent.name}")
            if auto_backup_var.get():
                try:
                    source = path.parent
                    dest = self.state.smartmedia_lib.backup_dir / source.name
                    dest.mkdir(parents=True, exist_ok=True)
                    for f in sorted(source.glob("*.SP0")):
                        shutil.copy(f, dest / f.name)
                except Exception:
                    pass

        def backup_card():
            card_dir = Path("/Volumes/BOSS DATA")
            if not card_dir.exists():
                if active_smpinfo[0] is not None:
                    card_dir = active_smpinfo[0].parent
                else:
                    messagebox.showwarning("Backup Card", "No physical card mounted or open.", parent=self.root)
                    return
            sp0_files = sorted(card_dir.glob("*.SP0"))
            if not sp0_files:
                messagebox.showwarning("Backup Card", "No .SP0 files found on card.", parent=self.root)
                return
            try:
                dest = self.state.smartmedia_lib.backup_dir / card_dir.name
                dest.mkdir(parents=True, exist_ok=True)
                for f in sp0_files:
                    shutil.copy(f, dest / f.name)
                messagebox.showinfo("Backup Card", f"Backed up {len(sp0_files)} file(s) to Backup/{card_dir.name}/", parent=self.root)
            except Exception as exc:
                messagebox.showerror("Backup Card", str(exc), parent=self.root)

        ttk.Button(top_bar, text="Open Card", command=open_card).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(top_bar, text="Create Virtual Card", command=lambda: create_virtual_card_from_physical()).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(top_bar, text="Backup Card", command=backup_card).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Checkbutton(top_bar, text="Auto-backup on Open", variable=auto_backup_var,
                        command=on_auto_backup_toggle).pack(side=tk.LEFT, padx=(12, 0))

        # ── Main two-panel layout ────────────────────────────────────────────
        paned = ttk.PanedWindow(frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # ── Left panel: card browser ─────────────────────────────────────────
        left_frame = ttk.Frame(paned, padding=4)
        paned.add(left_frame, weight=1)

        ttk.Label(left_frame, text="VIRTUAL CARDS", font=("Courier", 11, "bold")).pack(anchor=tk.W, pady=(0, 4))

        filter_row = ttk.Frame(left_frame)
        filter_row.pack(fill=tk.X, pady=(0, 4))
        search_var = tk.StringVar()
        search_entry = ttk.Entry(filter_row, textvariable=search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        card_tree_cols = ("name", "author", "ptn")
        style = ttk.Style(self.root)
        style.configure("Library.Treeview", background="#000000", fieldbackground="#000000",
                        foreground="#ffffff", rowheight=22)
        style.map("Library.Treeview", background=[("selected", "#2a7fff")],
                  foreground=[("selected", "#ffffff")])
        card_tree = ttk.Treeview(left_frame, columns=card_tree_cols, show="headings",
                                 height=20, style="Library.Treeview")
        card_tree.heading("name", text="Name")
        card_tree.heading("author", text="Author")
        card_tree.heading("ptn", text="PTN")
        card_tree.column("name", width=160)
        card_tree.column("author", width=120)
        card_tree.column("ptn", width=40, anchor=tk.CENTER)
        card_tree.tag_configure("active", background="#2a7fff", foreground="#ffffff")
        card_tree.pack(fill=tk.BOTH, expand=True)

        left_btn_row = ttk.Frame(left_frame)
        left_btn_row.pack(fill=tk.X, pady=(6, 0))

        # ── Right panel: card detail ─────────────────────────────────────────
        right_frame = ttk.Frame(paned, padding=4)
        paned.add(right_frame, weight=2)

        detail_title = ttk.Label(right_frame, text="CARD DETAIL", font=("Courier", 11, "bold"))
        detail_title.pack(anchor=tk.W, pady=(0, 6))

        # Card detail fields
        detail_frame = ttk.Frame(right_frame)
        detail_frame.pack(fill=tk.X)

        def make_field(parent, label, row):
            ttk.Label(parent, text=label, width=12, anchor=tk.E).grid(row=row, column=0, sticky=tk.E, padx=(0, 6), pady=2)
            var = tk.StringVar()
            entry = ttk.Entry(parent, textvariable=var)
            entry.grid(row=row, column=1, sticky=tk.EW, pady=2)
            parent.columnconfigure(1, weight=1)
            return var, entry

        name_var, name_entry = make_field(detail_frame, "Name:", 0)
        author_var, author_entry = make_field(detail_frame, "Author:", 1)
        categories_var, categories_entry = make_field(detail_frame, "Categories:", 2)
        tags_var, tags_entry = make_field(detail_frame, "Tags:", 3)

        wp_var = tk.BooleanVar(value=False)
        wp_btn = ttk.Checkbutton(detail_frame, text="Write Protect", variable=wp_var)
        wp_btn.grid(row=4, column=1, sticky=tk.W, pady=4)

        # Pre-fill author from last used
        author_var.set(self.state.config.get("last_author", ""))

        # Pad notes
        pad_notes_frame = ttk.LabelFrame(right_frame, text="PAD NOTES", padding=4)
        pad_notes_frame.pack(fill=tk.X, pady=(8, 0))

        pad_note_vars: Dict[str, tk.StringVar] = {}
        for i, pad in enumerate(SP303_PADS):
            col = i % 8
            base_row = (i // 8) * 2
            ttk.Label(pad_notes_frame, text=pad, anchor=tk.CENTER, width=6).grid(
                row=base_row, column=col, padx=2, sticky=tk.EW)
            var = tk.StringVar()
            ttk.Entry(pad_notes_frame, textvariable=var, width=8).grid(
                row=base_row + 1, column=col, padx=2, pady=(0, 4), sticky=tk.EW)
            pad_note_vars[pad] = var
            pad_notes_frame.columnconfigure(col, weight=1)

        detail_btn_row = ttk.Frame(right_frame)
        detail_btn_row.pack(fill=tk.X, pady=(6, 0))

        # ── State ─────────────────────────────────────────────────────────────
        current_card: list = [None]
        active_smpinfo: list = [None]

        def get_all_cards():
            query = search_var.get().strip().lower()
            cards = self.state.smartmedia_lib.list_cards()
            if query:
                cards = [c for c in cards if query in c.name.lower() or query in c.author.lower()
                         or any(query in cat.lower() for cat in c.categories)]
            return cards

        def refresh_card_list():
            active_name = current_card[0].name if current_card[0] else None
            existing = set(card_tree.get_children())
            seen = set()
            for card in get_all_cards():
                if card.name in seen:
                    continue  # skip duplicate names (two dirs with same name in card.json)
                seen.add(card.name)
                tag = ("active",) if card.name == active_name else ()
                ptn_dot = "●" if self.state.smartmedia_lib.card_has_patterns(card.name) else "○"
                if card_tree.exists(card.name):
                    card_tree.item(card.name, values=(card.name, card.author, ptn_dot), tags=tag)
                else:
                    card_tree.insert("", tk.END, iid=card.name, values=(card.name, card.author, ptn_dot), tags=tag)
            for stale in existing - seen:
                card_tree.delete(stale)

        def on_card_select(event=None):
            sel = card_tree.selection()
            for item in card_tree.get_children():
                card_tree.item(item, tags=())
            if not sel:
                current_card[0] = None
                return
            card_tree.item(sel[0], tags=("active",))
            card = self.state.smartmedia_lib.get_card(sel[0])
            if card is None:
                return
            current_card[0] = card
            name_var.set(card.name)
            author_var.set(card.author)
            categories_var.set(", ".join(card.categories))
            tags_var.set(", ".join(card.tags))
            wp_var.set(card.write_protect)
            for pad, var in pad_note_vars.items():
                var.set(card.pad_notes.get(pad, ""))

        card_tree.bind("<<TreeviewSelect>>", on_card_select)
        search_var.trace_add("write", lambda *_: refresh_card_list())

        def save_current_card():
            card = current_card[0]
            if card is None:
                return
            new_name = name_var.get().strip()
            if new_name != card.name:
                try:
                    self.state.smartmedia_lib.rename_card(card, new_name)
                except ValueError as exc:
                    messagebox.showerror("Rename Card", str(exc), parent=self.root)
                    return
            card.author = author_var.get().strip()
            card.categories = [c.strip() for c in categories_var.get().split(",") if c.strip()]
            card.tags = [t.strip() for t in tags_var.get().split(",") if t.strip()]
            card.pad_notes = {pad: var.get().strip() for pad, var in pad_note_vars.items() if var.get().strip()}
            card.write_protect = wp_var.get()
            if card.author:
                self.state.config["last_author"] = card.author
                self.state.save_config()
            self.state.smartmedia_lib.save_card(card)
            refresh_card_list()

        def new_card():
            new_name = simpledialog.askstring("New Virtual Card", "Card name:", parent=self.root)
            if not new_name or not new_name.strip():
                return
            new_name = new_name.strip()
            if self.state.smartmedia_lib.get_card(new_name):
                messagebox.showwarning("New Card", f"A card named '{new_name}' already exists.", parent=self.root)
                return
            card = VirtualCard(name=new_name)
            self.state.smartmedia_lib.create_card(card)
            refresh_card_list()
            card_tree.selection_set(new_name)
            on_card_select()

        def delete_card():
            card = current_card[0]
            if card is None:
                messagebox.showinfo("Delete Card", "Select a card first.", parent=self.root)
                return
            if card.write_protect:
                messagebox.showwarning("Delete Card", "Card is write-protected.", parent=self.root)
                return
            if messagebox.askyesno("Delete Card", f"Delete '{card.name}'? This cannot be undone.", parent=self.root):
                self.state.smartmedia_lib.delete_card(card.name)
                current_card[0] = None
                refresh_card_list()

        def restore_to_card():
            card = current_card[0]
            if card is None:
                messagebox.showinfo("Restore to Card", "Select a virtual card first.", parent=self.root)
                return
            sp0_files = list((self.state.smartmedia_lib.cards_dir / card.name).glob("*.SP0"))
            if not sp0_files:
                messagebox.showwarning("Restore to Card", f"'{card.name}' has no SP0 files to restore.", parent=self.root)
                return
            preferred = Path("/Volumes/BOSS DATA")
            target = preferred if preferred.exists() else self.state.get_library_paths()["outgoing"]
            if not messagebox.askyesno("Restore to Card",
                                       f"Restore '{card.name}' to:\n{target}\n\nThis will overwrite files. Continue?",
                                       parent=self.root):
                return
            try:
                self.state.smartmedia_lib.restore_card(card.name, target)
                messagebox.showinfo("Restore to Card", f"Restored to {target}", parent=self.root)
            except Exception as exc:
                messagebox.showerror("Restore to Card", str(exc), parent=self.root)

        def open_in_manager():
            smpinfo = active_smpinfo[0]
            if smpinfo is None and current_card[0] is not None:
                candidate = self.state.smartmedia_lib.cards_dir / current_card[0].name / "SMPINFO0.SP0"
                if candidate.exists():
                    smpinfo = candidate
            if smpinfo is None:
                messagebox.showinfo(
                    "Sample Manager",
                    "Select a virtual card or open a physical card first.",
                    parent=self.root,
                )
                return
            self._open_sample_manager(smpinfo_path=smpinfo)

        def create_virtual_card_from_physical():
            if active_smpinfo[0] is None:
                messagebox.showinfo(
                    "Create Virtual Card",
                    "Open a physical card first using the Open Card button.",
                    parent=self.root,
                )
                return
            source_dir = active_smpinfo[0].parent
            suggested = source_dir.name if source_dir.name != "BOSS DATA" else ""
            name = simpledialog.askstring(
                "Create Virtual Card", "Name for this virtual card:", initialvalue=suggested, parent=self.root
            )
            if not name or not name.strip():
                return
            name = name.strip()
            if self.state.smartmedia_lib.get_card(name):
                messagebox.showwarning("Create Virtual Card", f"A card named '{name}' already exists.", parent=self.root)
                return
            card = VirtualCard(name=name, author=author_var.get().strip())
            self.state.smartmedia_lib.create_card(card)
            sp0_files = sorted(source_dir.glob("*.SP0"))
            self.state.smartmedia_lib.import_sp0_files(name, source_dir, auto_backup=False)
            card_dir = self.state.smartmedia_lib.cards_dir / name
            active_smpinfo[0] = card_dir / "SMPINFO0.SP0"
            open_card_status_var.set(f"Open: {name}")
            refresh_card_list()
            card_tree.selection_set(name)
            on_card_select()
            messagebox.showinfo(
                "Create Virtual Card",
                f"Created '{name}' with {len(sp0_files)} file(s) imported from {source_dir.name}.",
                parent=self.root,
            )

        # Wire up buttons
        ttk.Button(left_btn_row, text="New Card", command=new_card).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(left_btn_row, text="Delete Card", command=delete_card).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(detail_btn_row, text="Save Changes", command=save_current_card).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(detail_btn_row, text="Restore to Card", command=restore_to_card).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(detail_btn_row, text="Open in Sample Manager", command=open_in_manager).pack(side=tk.LEFT, padx=(0, 6))

        refresh_card_list()

        self._create_menu(
            open_card=open_card,
            backup_card=backup_card,
            new_card=new_card,
            delete_card=delete_card,
            save_current_card=save_current_card,
            restore_to_card=restore_to_card,
            open_in_manager=open_in_manager,
            create_virtual_card_from_physical=create_virtual_card_from_physical,
        )


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
    SmartMediaLibraryWindow(root, state)
    root.mainloop()
    log.info("Dr. Sidekick session ended")


if __name__ == '__main__':
    main()
