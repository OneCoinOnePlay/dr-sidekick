"""Pattern editing model extracted from the legacy monolith."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox
from typing import List, Optional, Tuple

from dr_sidekick.ui.constants import PAD_ORDER

from .core import (
    DEFAULT_PATTERN_LENGTH_BARS,
    INTERNAL_PPQN,
    MAX_PATTERN_EVENT_CAPACITY,
    MAX_PATTERN_LENGTH_BARS,
    PROJECT_ROOT,
    PTNData,
    PTNInfo,
    PatternSlot,
    SLOT_COUNT,
    TICKS_PER_BAR,
    TUPLE_ZONE_MAX_BYTES,
    TUPLE_ZONE_SENTINEL_BYTES,
    Event,
    GrooveTemplate,
    load_midi_notes,
)

log = logging.getLogger("dr_sidekick")


@dataclass
class ModelState:
    """Snapshot of pattern model state for undo/redo."""

    slot: int
    events: List[Event]


class PatternModel:
    """
    Data layer that manages pattern state with undo/redo.

    Interfaces with the extracted PTNInfo/PTNData engine.
    """

    def __init__(self):
        self.ptninfo: Optional[PTNInfo] = None
        self.ptndata: Optional[PTNData] = None
        self.ptninfo_raw: Optional[bytearray] = None
        self.current_slot: int = 0
        self.current_storage_slot: int = 0
        self.events: List[Event] = []
        self.undo_stack: List[ModelState] = []
        self.redo_stack: List[ModelState] = []
        self.max_undo_states = 50
        self.dirty = False
        self.ptninfo_path: Optional[Path] = None
        self.ptndata_path: Optional[Path] = None
        self.slot_clipboard: Optional[List[Event]] = None
        self.last_save_warning: Optional[str] = None
        self.last_stamp_warning: Optional[str] = None

    def new_pattern(self):
        """Create new pattern files."""
        self.ptninfo = PTNInfo()
        self.ptninfo_raw = bytearray(self.ptninfo.to_bytes())
        init_template = PROJECT_ROOT / "PTNDATA_INIT_OFFICIAL.bin"
        if init_template.exists():
            self.ptndata = PTNData(init_template_path=init_template)
        else:
            self.ptndata = PTNData()
            messagebox.showwarning(
                "Missing Pattern Template",
                "Copy PTNDATA0.SP0 from your SP-303's SmartMedia card (format it first)"
                " and save it as PTNDATA_INIT_OFFICIAL.bin",
            )
        self.current_slot = 0
        self.events = []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False
        self.ptninfo_path = None
        self.ptndata_path = None

    def load_pattern(self, ptninfo_path: Path, ptndata_path: Path):
        """Load pattern files."""
        self.ptninfo = PTNInfo.from_file(ptninfo_path)
        self.ptndata = PTNData.from_file(ptndata_path)
        self.ptninfo_raw = bytearray(ptninfo_path.read_bytes())
        self.ptninfo_path = ptninfo_path
        self.ptndata_path = ptndata_path
        self.current_slot = 0
        self.load_slot(0)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False

    def save_pattern(
        self,
        ptninfo_path: Optional[Path] = None,
        ptndata_path: Optional[Path] = None,
    ):
        """Save pattern files."""
        if ptninfo_path is None:
            ptninfo_path = self.ptninfo_path
        if ptndata_path is None:
            ptndata_path = self.ptndata_path

        if ptninfo_path is None or ptndata_path is None:
            raise ValueError("No file paths specified for save")

        self.save_slot()

        if self.ptninfo_raw is not None:
            with open(ptninfo_path, "wb") as handle:
                handle.write(self.ptninfo_raw)
        else:
            self.ptninfo.save(ptninfo_path)
        self.ptndata.save(ptndata_path)

        self.ptninfo_path = ptninfo_path
        self.ptndata_path = ptndata_path
        self.dirty = False

    def load_slot(self, slot_index: int):
        """Load events from slot."""
        if not (0 <= slot_index < SLOT_COUNT):
            raise ValueError(f"Slot must be 0-15, got {slot_index}")

        if self.current_slot != slot_index and self.ptndata is not None:
            self.save_slot()

        self.current_slot = slot_index
        storage_slot = slot_index
        mapping_index = self.get_mapping_index(slot_index)
        if mapping_index is not None and 1 <= mapping_index <= 16:
            storage_slot = mapping_index - 1
        self.current_storage_slot = storage_slot

        if not self.slot_has_pattern(slot_index):
            self.events = []
        elif self.ptndata is not None:
            self.events = self.ptndata.decode_events(storage_slot)
            self.events.sort(key=lambda event: event.tick)
        else:
            self.events = []

    def _bars_for_events(self, events: List[Event]) -> int:
        """Return a deterministic 96 PPQN bar count for the current events."""
        if not events:
            return DEFAULT_PATTERN_LENGTH_BARS
        last_tick = max(event.tick for event in events)
        return max(
            1,
            min(
                MAX_PATTERN_LENGTH_BARS,
                math.ceil((last_tick + 1) / TICKS_PER_BAR),
            ),
        )

    def _total_length_ticks_for_bars(self, bars: int) -> int:
        bars = max(1, min(MAX_PATTERN_LENGTH_BARS, round(bars)))
        return max(1, bars * TICKS_PER_BAR)

    def get_pattern_length_bars(self) -> int:
        """Calculate pattern length in bars from events."""
        if not self.slot_has_pattern(self.current_slot):
            return DEFAULT_PATTERN_LENGTH_BARS

        ptninfo_length = self.get_ptninfo_length_bars(self.current_slot)
        if ptninfo_length is not None:
            return ptninfo_length

        if not self.events:
            return DEFAULT_PATTERN_LENGTH_BARS

        return self._bars_for_events(self.events)

    def get_ptninfo_length_bars(self, slot_index: int) -> Optional[int]:
        """Return per-slot length from PTNINFO active entry byte when available."""
        entry = self.get_ptninfo_entry(slot_index)
        if entry is None or len(entry) != 4:
            return None
        b0, b1, b2, _ = entry
        if b0 == 0x04 and b1 == 0xB0 and 1 <= b2 <= MAX_PATTERN_LENGTH_BARS:
            return b2
        return None

    def get_ptninfo_quantize_display(self, slot_index: int) -> str:
        """Best-effort quantize display from PTNINFO active entry byte."""
        entry = self.get_ptninfo_entry(slot_index)
        if entry is None or len(entry) != 4:
            return "Off"
        b0, b1, b2, b3 = entry
        if b0 != 0x04 or b1 != 0xB0:
            return "Off"
        if 1 <= b3 <= 16:
            return "Off"
        quant_map = {
            0x00: "Off",
            0x01: "4",
            0x02: "8",
            0x03: "16",
            0x04: "8-3",
        }
        return quant_map.get(b2, "Off")

    def save_slot(self):
        """Save current events to slot."""
        if self.ptndata is None or not self.dirty:
            return
        self.last_save_warning = None

        length_bars = (
            self.get_ptninfo_length_bars(self.current_slot) or DEFAULT_PATTERN_LENGTH_BARS
        )
        mapping_index = self.get_mapping_index(self.current_slot)
        if mapping_index is None:
            mapping_index = self.current_slot + 1

        if self.events:
            self.events.sort(key=lambda event: event.tick)
            total_length_ticks = self._total_length_ticks_for_bars(length_bars)
            fitted_events, truncated_events, fitted_total_length_ticks = (
                self._fit_events_to_tuple_capacity(
                    self.events,
                    total_length_ticks=total_length_ticks,
                )
            )
            if truncated_events > 0:
                self.events = fitted_events
                self.last_save_warning = (
                    "This pattern is too dense for the SP-303 and some notes were removed when saving. "
                    f"Try reducing the pattern length below {length_bars} bar(s) to fit more notes."
                )
                log.warning(
                    "Pattern save truncated slot %s: removed %d trailing event(s); "
                    "kept=%d loop_bars=%d storage_slot=%d",
                    self.current_slot + 1,
                    truncated_events,
                    len(fitted_events),
                    length_bars,
                    self.current_storage_slot + 1,
                )

            self.ptndata.write_pattern(
                self.current_storage_slot,
                fitted_events,
                total_length_ticks=fitted_total_length_ticks,
            )

            fitted_length_bars = max(
                1,
                min(
                    MAX_PATTERN_LENGTH_BARS,
                    math.ceil(fitted_total_length_ticks / TICKS_PER_BAR),
                ),
            )
            self._set_ptninfo_active_entry(
                self.current_slot,
                "OFF",
                mapping_index=mapping_index,
                active_value=fitted_length_bars,
            )
        else:
            self.ptndata.clear_pattern(self.current_storage_slot)
            self._set_ptninfo_empty_entry(self.current_slot)
        self.dirty = False

    def _fit_events_to_tuple_capacity(
        self,
        events: List[Event],
        total_length_ticks: int,
    ) -> Tuple[List[Event], int, int]:
        """Trim trailing events so encoded payload fits the SP-303 tuple zone."""
        if self.ptndata is None or not events:
            return events, 0, max(1, total_length_ticks)

        def encoded_len(prefix: List[Event]) -> int:
            if not prefix:
                return 0
            return len(
                self.ptndata.encode_events(
                    prefix,
                    total_length_ticks=total_length_ticks,
                )
            )

        capped_events = events[:MAX_PATTERN_EVENT_CAPACITY]
        truncated_events = len(events) - len(capped_events)

        max_serialized_len = TUPLE_ZONE_MAX_BYTES - TUPLE_ZONE_SENTINEL_BYTES
        serialized_len = encoded_len(capped_events)
        if serialized_len <= max_serialized_len:
            return capped_events, truncated_events, max(1, total_length_ticks)

        low, high = 1, len(capped_events)
        fit_count = 0
        while low <= high:
            mid = (low + high) // 2
            mid_len = encoded_len(capped_events[:mid])
            if mid_len <= max_serialized_len:
                fit_count = mid
                low = mid + 1
            else:
                high = mid - 1

        if fit_count > 0:
            fitted = capped_events[:fit_count]
            return fitted, truncated_events + (len(capped_events) - fit_count), max(1, total_length_ticks)

        first = capped_events[0]
        fallback = [Event(tick=0, pad=first.pad, velocity=first.velocity)]
        fallback_len = len(
            self.ptndata.encode_events(
                fallback,
                total_length_ticks=max(1, total_length_ticks),
            )
        )
        if fallback_len <= max_serialized_len:
            return fallback, max(0, len(events) - 1), max(1, total_length_ticks)
        return [], len(events), max(1, total_length_ticks)

    def clear_slot(self):
        """Clear current slot."""
        self.push_undo_state()
        self.events.clear()
        self._set_ptninfo_empty_entry(self.current_slot)
        self.dirty = True

    def add_event(self, tick: int, pad: int, velocity: int = 0x7F):
        """Add event to current slot."""
        self.push_undo_state()
        self.events.append(Event(tick=tick, pad=pad, velocity=velocity))
        self.events.sort(key=lambda event: event.tick)
        self.dirty = True

    def remove_event(self, event: Event):
        """Remove event from current slot."""
        self.push_undo_state()
        if event in self.events:
            self.events.remove(event)
            self.dirty = True

    def remove_events(self, events: List[Event]):
        """Remove multiple events."""
        if not events:
            return
        self.push_undo_state()
        for event in events:
            if event in self.events:
                self.events.remove(event)
        self.dirty = True

    def move_event(self, event: Event, new_tick: int, new_pad: Optional[int] = None):
        """Move event to new position."""
        self.push_undo_state()
        event.tick = new_tick
        if new_pad is not None:
            event.pad = new_pad
        self.events.sort(key=lambda event: event.tick)
        self.dirty = True

    def set_event_velocity(self, event: Event, velocity: int):
        """Set velocity for event."""
        self.push_undo_state()
        event.velocity = max(0, min(127, velocity))
        self.dirty = True

    def reassign_pad(
        self,
        source_pad: int,
        target_pad: int,
        events: Optional[List[Event]] = None,
    ) -> int:
        """Move events from one pad to another as a single undoable operation."""
        if source_pad == target_pad:
            return 0

        if events is None:
            candidates = [event for event in self.events if event.pad == source_pad]
        else:
            candidates = [
                event for event in events
                if event in self.events and event.pad == source_pad
            ]

        if not candidates:
            return 0

        self.push_undo_state()
        for event in candidates:
            event.pad = target_pad
        self.events.sort(key=lambda event: event.tick)
        self.dirty = True
        return len(candidates)

    def quantize_events(self, events: List[Event], quantize_ticks: int):
        """Quantize selected events to grid."""
        if quantize_ticks <= 0:
            return
        self.push_undo_state()
        for event in events:
            event.tick = round(event.tick / quantize_ticks) * quantize_ticks
        self.events.sort(key=lambda event: event.tick)
        self.dirty = True

    def apply_groove(self, events: List[Event], groove: GrooveTemplate) -> int:
        """Apply a groove template to selected events.

        Quantizes each event to the groove's grid, then shifts by the
        per-step offset. Only works with grid-type grooves.

        Returns the number of events that moved.
        """
        if not events or groove.groove_type != "grid" or groove.grid <= 0 or not groove.offsets:
            return 0
        self.push_undo_state()
        moved = 0
        grid = groove.grid
        n_offsets = len(groove.offsets)
        for event in events:
            quantized = round(event.tick / grid) * grid
            step = quantized // grid
            offset = groove.offsets[step % n_offsets]
            new_tick = max(0, quantized + offset)
            if new_tick != event.tick:
                moved += 1
            event.tick = new_tick
        self.events.sort(key=lambda event: event.tick)
        self.dirty = True
        return moved

    def stamp_pattern(self, groove: GrooveTemplate, pad: int,
                      velocity: int = 0x7F) -> int:
        """Stamp a groove pattern into the current slot on a chosen pad.

        For compound grooves, creates new events at each tick position in the
        groove's tick list.
        For grid grooves, creates new events at each grid step, applying the
        groove's timing offsets.

        Returns the number of events added.
        """
        self.push_undo_state()
        self.last_stamp_warning = None
        length_bars = (
            self.get_ptninfo_length_bars(self.current_slot) or DEFAULT_PATTERN_LENGTH_BARS
        )
        total_length_ticks = self._total_length_ticks_for_bars(length_bars)
        original_count = len(self.events)
        stamped_events: List[Event] = []

        if groove.groove_type == "compound" and groove.ticks:
            for tick in groove.ticks:
                stamped_events.append(Event(tick=tick, pad=pad, velocity=velocity))
        elif groove.groove_type == "grid" and groove.grid > 0 and groove.offsets:
            for step, offset in enumerate(groove.offsets):
                tick = max(0, (step * groove.grid) + offset)
                stamped_events.append(Event(tick=tick, pad=pad, velocity=velocity))

        if not stamped_events:
            return 0

        kept_stamped_events = [
            event for event in stamped_events
            if 0 <= event.tick < total_length_ticks
        ]
        clipped_events = len(stamped_events) - len(kept_stamped_events)
        candidate_events = self.events + kept_stamped_events
        candidate_events.sort(key=lambda event: event.tick)
        fitted_events, truncated_events, _ = self._fit_events_to_tuple_capacity(
            candidate_events,
            total_length_ticks=total_length_ticks,
        )
        self.events = fitted_events
        added = max(0, len(self.events) - original_count)
        self.dirty = True

        warning_parts = []
        if clipped_events > 0:
            warning_parts.append(
                f"Removed {clipped_events} event(s) beyond the current {length_bars}-bar pattern length."
            )
        if truncated_events > 0:
            requested_events = len(candidate_events)
            fitted_count = len(fitted_events)
            warning_parts.append(
                "This pattern is too dense for the SP-303. "
                f"The SP-303 has a nominal {MAX_PATTERN_EVENT_CAPACITY}-event cap, "
                f"{requested_events} requested, {fitted_count} fit, {truncated_events} dropped. "
                f"Try reducing the pattern length below {length_bars} bar(s) to fit more notes."
            )
        if warning_parts:
            self.last_stamp_warning = " ".join(warning_parts)
            log.warning(
                "Pattern stamp adjusted slot %s: groove='%s' pad=0x%02X stamped=%d kept=%d "
                "clipped_by_length=%d clipped_by_capacity=%d loop_bars=%d",
                self.current_slot + 1,
                groove.name,
                pad,
                len(stamped_events),
                added,
                clipped_events,
                truncated_events,
                length_bars,
            )

        return added

    def copy_slot(self):
        """Copy current slot events to clipboard."""
        self.slot_clipboard = [Event(event.tick, event.pad, event.velocity) for event in self.events]

    def paste_slot(self):
        """Paste clipboard events to current slot."""
        if self.slot_clipboard is None:
            return
        self.push_undo_state()
        self.events = [
            Event(event.tick, event.pad, event.velocity)
            for event in self.slot_clipboard
        ]
        self._set_ptninfo_active_entry(self.current_slot, "OFF")
        self.dirty = True

    def generate_test_data(self, seed: Optional[int] = None):
        """Generate test data across all slots."""
        if self.ptninfo is None or self.ptndata is None or self.ptninfo_raw is None:
            raise ValueError("No pattern files loaded")

        all_pads = PAD_ORDER
        quantize_options = ["OFF", "1/4", "1/8", "1/16", "1/8T", "1/16T"]
        pattern_lengths = list(range(1, 9))

        for slot in range(16):
            length_bars = pattern_lengths[slot % len(pattern_lengths)]
            quantize = quantize_options[slot % len(quantize_options)]
            if slot >= 8:
                pads = all_pads + list(reversed(all_pads))
            else:
                pads = all_pads if slot % 2 == 0 else list(reversed(all_pads))
            total_ticks = max(1, length_bars * 4 * INTERNAL_PPQN)
            steps = len(pads)
            if steps == 1:
                events = [Event(tick=0, pad=pads[0], velocity=0x7F)]
            else:
                events = []
                for index, pad in enumerate(pads):
                    tick = round(index * (total_ticks - 1) / (steps - 1))
                    events.append(Event(tick=tick, pad=pad, velocity=0x7F))

            self.ptndata.write_pattern(slot, events, total_length_ticks=total_ticks)
            self._set_ptninfo_active_entry(
                slot,
                quantize,
                mapping_index=slot + 1,
                active_value=length_bars,
            )

        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = True
        self.current_slot = 0
        self.current_storage_slot = 0
        self.events = self.ptndata.decode_events(0)
        self.events.sort(key=lambda event: event.tick)

    def import_midi(
        self,
        midi_path: Path,
        replace: bool = True,
        notes_override: Optional[List[Tuple[int, int, int]]] = None,
        ppqn_override: Optional[int] = None,
        out_of_range: str = "skip",
    ) -> dict:
        """Import MIDI file to current slot."""
        if notes_override is not None and ppqn_override is not None:
            notes, ppqn = notes_override, ppqn_override
        else:
            notes, ppqn = load_midi_notes(str(midi_path))

        if not notes:
            raise ValueError("No notes found in MIDI file")

        scale_factor = INTERNAL_PPQN / ppqn
        transpose_shift = 0
        if out_of_range == "transpose":
            best_shift = 0
            best_count = sum(1 for _, note, _ in notes if 60 <= note <= 75)
            for shift in range(-96, 97, 12):
                if shift == 0:
                    continue
                count = sum(1 for _, note, _ in notes if 60 <= note + shift <= 75)
                if count > best_count or (
                    count == best_count and abs(shift) < abs(best_shift)
                ):
                    best_count = count
                    best_shift = shift
            transpose_shift = best_shift

        imported_events = []
        skipped_out_of_range = 0
        for tick, note, velocity in notes:
            sp303_tick = round(tick * scale_factor)
            mapped_note = note + transpose_shift

            if 60 <= mapped_note <= 75:
                pad = 0x10 + (mapped_note - 60)
            else:
                skipped_out_of_range += 1
                continue
            imported_events.append(
                Event(tick=sp303_tick, pad=pad, velocity=velocity)
            )
        imported_events.sort(key=lambda event: event.tick)

        source_bars = 0.0
        if imported_events:
            source_bars = self._bars_for_events(imported_events)

        max_ticks = MAX_PATTERN_LENGTH_BARS * TICKS_PER_BAR
        truncated_event_count = 0
        truncated_bars = 0.0
        if imported_events:
            last_tick = imported_events[-1].tick
            if last_tick >= max_ticks:
                truncated_bars = max(0.0, source_bars - MAX_PATTERN_LENGTH_BARS)
                kept_events = [
                    event for event in imported_events if event.tick < max_ticks
                ]
                truncated_event_count = len(imported_events) - len(kept_events)
                imported_events = kept_events
                if imported_events:
                    source_bars = self._bars_for_events(imported_events)
                else:
                    source_bars = 0.0

        self.push_undo_state()
        existing_count = len(self.events)
        if replace:
            candidate_events = imported_events
        else:
            candidate_events = [
                Event(event.tick, event.pad, event.velocity)
                for event in self.events
            ]
            candidate_events.extend(imported_events)
            candidate_events.sort(key=lambda event: event.tick)

        total_length_ticks = 1
        if candidate_events:
            total_length_ticks = min(
                max_ticks,
                self._total_length_ticks_for_bars(self._bars_for_events(candidate_events)),
            )
        fitted_events, density_truncated, _ = self._fit_events_to_tuple_capacity(
            candidate_events,
            total_length_ticks=total_length_ticks,
        )
        self.events = fitted_events
        if self.events:
            imported_length_bars = self._bars_for_events(self.events)
        else:
            imported_length_bars = DEFAULT_PATTERN_LENGTH_BARS
        self._set_ptninfo_active_entry(
            self.current_slot,
            "OFF",
            mapping_index=self.current_slot + 1,
            active_value=imported_length_bars,
        )
        self.dirty = True

        imported_count = (
            len(self.events)
            if replace
            else max(0, len(self.events) - existing_count)
        )

        return {
            "imported_events": imported_count,
            "source_bars": source_bars,
            "truncated_events": truncated_event_count,
            "truncated_bars": truncated_bars,
            "density_truncated_events": density_truncated,
            "imported_length_bars": imported_length_bars,
            "max_bars": MAX_PATTERN_LENGTH_BARS,
            "skipped_out_of_range": skipped_out_of_range,
            "transpose_shift": transpose_shift,
        }

    def set_current_slot_length_bars(self, bars: int):
        """Update PTNINFO length byte for current slot without altering mapping."""
        bars = max(1, min(MAX_PATTERN_LENGTH_BARS, round(bars)))
        mapping_index = self.get_mapping_index(self.current_slot)
        if mapping_index is None:
            mapping_index = self.current_slot + 1
        self._set_ptninfo_active_entry(
            self.current_slot,
            "OFF",
            mapping_index=mapping_index,
            active_value=bars,
        )
        self.dirty = True

    def _set_ptninfo_active_entry(
        self,
        slot_index: int,
        quantize: str,
        mapping_index: Optional[int] = None,
        active_value: Optional[int] = None,
    ):
        if self.ptninfo is None:
            return
        self.ptninfo.set_pattern(slot_index, quantize)
        if self.ptninfo_raw is None:
            return
        quant_map = {
            "OFF": 0x00,
            "1/4": 0x01,
            "1/8": 0x02,
            "1/16": 0x03,
            "1/8T": 0x04,
            "1/16T": 0x05,
        }
        if active_value is not None:
            quant_byte = max(0, min(0x63, round(active_value)))
        else:
            quant_byte = quant_map.get(quantize, 0x00)
        if mapping_index is None:
            existing = self.get_mapping_index(slot_index)
            mapping_index = existing if existing is not None else (slot_index + 1)
        mapping_index = max(1, min(16, mapping_index))
        offset = slot_index * 4
        self.ptninfo_raw[offset:offset + 4] = bytes(
            [0x04, 0xB0, quant_byte, mapping_index]
        )

    def _set_ptninfo_empty_entry(self, slot_index: int):
        if self.ptninfo is not None:
            self.ptninfo.clear_pattern(slot_index)
        if self.ptninfo_raw is None:
            return
        offset = slot_index * 4
        self.ptninfo_raw[offset:offset + 4] = bytes(
            [0xB0, 0x04, 0x02, slot_index + 1]
        )

    def get_ptninfo_entry(self, slot_index: int) -> Optional[bytes]:
        if self.ptninfo_raw is None:
            return None
        offset = slot_index * 4
        return bytes(self.ptninfo_raw[offset:offset + 4])

    def slot_has_pattern(self, slot_index: int) -> bool:
        if self.ptninfo is None or not (0 <= slot_index < SLOT_COUNT):
            return False
        return bool(self.ptninfo.slots[slot_index].has_pattern)

    def get_mapping_index(self, slot_index: int) -> Optional[int]:
        entry = self.get_ptninfo_entry(slot_index)
        if entry is None or len(entry) != 4:
            return None
        b0, b1, b2, b3 = entry
        if b0 == 0xB0 and b1 == 0x04 and b2 == 0x02:
            return b3 if 1 <= b3 <= 16 else None
        if b0 == 0x04 and b1 == 0xB0 and 1 <= b3 <= 16:
            return b3
        return None

    def swap_ptninfo_entries(self, slot_a: int, slot_b: int):
        if self.ptninfo_raw is None:
            raise ValueError("PTNINFO not loaded")
        if not (0 <= slot_a < SLOT_COUNT and 0 <= slot_b < SLOT_COUNT):
            raise ValueError("Slot must be 0-15")
        self.save_slot()
        a_off = slot_a * 4
        b_off = slot_b * 4
        a_entry = bytes(self.ptninfo_raw[a_off:a_off + 4])
        b_entry = bytes(self.ptninfo_raw[b_off:b_off + 4])
        self.ptninfo_raw[a_off:a_off + 4] = b_entry
        self.ptninfo_raw[b_off:b_off + 4] = a_entry
        if self.ptninfo is not None:
            self.ptninfo.slots[slot_a] = PatternSlot.from_bytes(
                slot_a,
                self.ptninfo_raw[a_off:a_off + 4],
            )
            self.ptninfo.slots[slot_b] = PatternSlot.from_bytes(
                slot_b,
                self.ptninfo_raw[b_off:b_off + 4],
            )
        mapping_index = self.get_mapping_index(self.current_slot)
        if mapping_index is not None and 1 <= mapping_index <= 16:
            self.current_storage_slot = mapping_index - 1
        else:
            self.current_storage_slot = self.current_slot
        if self.ptndata is not None:
            self.events = self.ptndata.decode_events(self.current_storage_slot)
            self.events.sort(key=lambda event: event.tick)
        self.dirty = True

    def push_undo_state(self):
        """Save current state to undo stack."""
        state = ModelState(
            slot=self.current_slot,
            events=[Event(event.tick, event.pad, event.velocity) for event in self.events],
        )
        self.undo_stack.append(state)
        if len(self.undo_stack) > self.max_undo_states:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self):
        """Undo last operation."""
        if not self.undo_stack:
            return False

        current = ModelState(
            slot=self.current_slot,
            events=[Event(event.tick, event.pad, event.velocity) for event in self.events],
        )
        self.redo_stack.append(current)

        state = self.undo_stack.pop()
        self.events = [Event(event.tick, event.pad, event.velocity) for event in state.events]
        self.dirty = True
        return True

    def redo(self):
        """Redo last undone operation."""
        if not self.redo_stack:
            return False

        current = ModelState(
            slot=self.current_slot,
            events=[Event(event.tick, event.pad, event.velocity) for event in self.events],
        )
        self.undo_stack.append(current)

        state = self.redo_stack.pop()
        self.events = [Event(event.tick, event.pad, event.velocity) for event in state.events]
        self.dirty = True
        return True
