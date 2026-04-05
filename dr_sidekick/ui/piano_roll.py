"""Piano roll canvas extracted from the legacy monolith."""

from __future__ import annotations

import tkinter as tk
from typing import List, Optional, Tuple

from dr_sidekick.engine import (
    DEFAULT_PATTERN_LENGTH_BARS,
    Event,
    INTERNAL_PPQN,
    MAX_PATTERN_LENGTH_BARS,
    PatternModel,
)
from dr_sidekick.ui.constants import COLOR_PALETTES, COLORS, GRID_SNAPS, PAD_ORDER


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
        self.on_view_changed = None
        self.on_modified = None
        self.selected_pad_row: Optional[int] = None
        self._drag_modified = False

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
        self.selected_pad_row = None
        self.redraw()

    def select_pad_row(self, pad: Optional[int]):
        """Select an entire pad lane."""
        self.selected_pad_row = pad
        if pad is None:
            self.selected_events.clear()
        else:
            self.selected_events = [event for event in self.model.events if event.pad == pad]
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

        # Draw lane highlight before event blocks.
        self._draw_selected_pad_row(width, height)

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
        total_width = round(max_ticks * self.zoom_x) + 100  # Add padding

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
        if callable(self.on_view_changed):
            self.on_view_changed()
        return result

    def yview(self, *args):
        """Track vertical scroll so lane hit-testing remains accurate."""
        result = super().yview(*args)
        self._refresh_view_offsets()
        self.redraw()
        if callable(self.on_view_changed):
            self.on_view_changed()
        return result

    def _draw_ruler(self, width: int):
        """Draw ruler at top showing bar numbers"""
        ruler_height = 25
        left_px = self.canvasx(0)
        right_px = self.canvasx(width)
        left_tick = round(left_px / self.zoom_x)
        right_tick = round(right_px / self.zoom_x)

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
        left_tick = round(left_px / self.zoom_x)
        right_tick = round(right_px / self.zoom_x)

        # Vertical grid lines (time)
        # Draw grid based on snap setting, or every quarter note if snap is off
        grid_interval = self.grid_snap if self.grid_snap > 0 else 96

        bar_ticks = 4 * INTERNAL_PPQN  # 384 ticks per bar

        for tick in range(left_tick - (left_tick % grid_interval), right_tick + 1, grid_interval):
            x = self.tick_to_x(tick)
            # Bar line every 384 ticks (4 quarter notes)
            if (tick % bar_ticks) == 0:
                color = self.colors["grid_bar"]
                width_val = 2
            # Major beat line every 96 ticks (quarter note)
            elif (tick % 96) == 0:
                color = self.colors["grid_major"]
                width_val = 1
            else:
                color = self.colors["grid_minor"]
                width_val = 1
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

    def _draw_selected_pad_row(self, width: int, height: int):
        """Highlight the currently selected pad lane."""
        if self.selected_pad_row not in PAD_ORDER:
            return
        ruler_height = 25
        lane_index = PAD_ORDER.index(self.selected_pad_row)
        y0 = (lane_index * self.zoom_y) - self.offset_y + ruler_height
        y1 = y0 + self.zoom_y
        left_x = self.canvasx(0)
        right_x = self.canvasx(width)
        fill_color, fill_stipple = self._tk_fill_style(self.colors["selection_fill"])
        self.create_rectangle(
            left_x,
            y0,
            right_x,
            y1,
            outline="",
            fill=fill_color,
            stipple=fill_stipple,
            tags="pad_row_highlight",
        )

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

    def _event_tick_span(self, event: Event) -> int:
        """Return the rendered tick span for one event."""
        if event.render_style == "span" and event.duration_ticks > 0:
            return event.duration_ticks
        if self.grid_snap > 0:
            return max(1, self.grid_snap)
        return INTERNAL_PPQN

    def _draw_event(self, event: Event, selected: bool):
        """Draw single event block"""
        ruler_height = 25

        # Get coordinates
        x = self.tick_to_x(event.tick)
        max_ticks = self.pattern_length_bars * 4 * INTERNAL_PPQN
        end_x = self.tick_to_x(max_ticks)
        lane_index = PAD_ORDER.index(event.pad) if event.pad in PAD_ORDER else 0
        y = lane_index * self.zoom_y + ruler_height

        span_ticks = self._event_tick_span(event)
        raw_right = self.tick_to_x(min(max_ticks, event.tick + span_ticks))
        clipped_right = min(max(x + 8, raw_right), end_x)
        if clipped_right <= x:
            return

        # Get color based on pad
        color = self._get_event_color(event.pad, event.velocity)

        # Draw rectangle
        outline_color = "#ffffff" if selected else color
        outline_width = 2 if selected else 1

        self.create_rectangle(
            x, y + 2, clipped_right, y + self.zoom_y - 2,
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
        return round(tick * self.zoom_x)

    def x_to_tick(self, x: int) -> int:
        """Convert X pixel to tick (with snap)"""
        tick = round(self.canvasx(x) / self.zoom_x)
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
        tick_raw = round(self.canvasx(x) / self.zoom_x)
        pad = self.y_to_pad(y)

        max_ticks = self.pattern_length_bars * 4 * INTERNAL_PPQN
        for event in self.model.events:
            if event.pad == pad:
                event_width_ticks = max(
                    float(self._event_tick_span(event)),
                    8.0 / max(self.zoom_x, 1e-6),
                )
                event_right_tick = min(max_ticks - 1, event.tick + event_width_ticks)
                # Check if click is within the visual event block
                if event.tick <= tick_raw <= event_right_tick:
                    return event
        return None

    def _notify_modified(self):
        """Notify the parent window after a model mutation."""
        if callable(self.on_modified):
            self.on_modified()

    def on_mouse_down(self, event):
        """Handle mouse down"""
        self.focus_set()
        self.selected_pad_row = None
        self._drag_modified = False
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
                self._notify_modified()

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
                self._notify_modified()

    def on_mouse_drag(self, event):
        """Handle mouse drag"""
        if self.edit_mode == "Draw" and self.dragging_event:
            # Move event
            new_tick = self.x_to_tick(event.x)
            new_pad = self.y_to_pad(event.y)
            if new_tick == self.dragging_event.tick and new_pad == self.dragging_event.pad:
                return
            self.selected_pad_row = None
            self.model.move_event(self.dragging_event, new_tick, new_pad)
            self._drag_modified = True
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
            self.selected_pad_row = None
            self.redraw()

        if self._drag_modified:
            self._notify_modified()

        self.dragging_event = None
        self.drag_start_pos = None
        self._drag_modified = False

    def on_mouse_move(self, event):
        """Handle mouse move (for cursor changes)"""
        pass

    def on_delete_key(self, event):
        """Handle delete key"""
        if self.selected_events:
            self.model.remove_events(list(self.selected_events))
            self.selected_events.clear()
            self.redraw()
            self._notify_modified()

    def on_velocity_decrease(self, event):
        """Decrease velocity of selected events"""
        if self.selected_events:
            self.model.push_undo_state()
            for evt in self.selected_events:
                evt.velocity = max(0, min(127, evt.velocity - 10))
            self.model.dirty = True
            self.redraw()
            self._notify_modified()
            return "break"

    def on_velocity_increase(self, event):
        """Increase velocity of selected events"""
        if self.selected_events:
            self.model.push_undo_state()
            for evt in self.selected_events:
                evt.velocity = max(0, min(127, evt.velocity + 10))
            self.model.dirty = True
            self.redraw()
            self._notify_modified()
            return "break"

    def on_right_click(self, event):
        """Handle right-click to delete event"""
        clicked_event = self.find_event_at(event.x, event.y)
        if clicked_event:
            self.model.remove_event(clicked_event)
            if clicked_event in self.selected_events:
                self.selected_events.remove(clicked_event)
            self.redraw()
            self._notify_modified()
