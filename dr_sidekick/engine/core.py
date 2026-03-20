"""Core SP-303 engine primitives extracted from the legacy monolith.

This module is intentionally kept close to the original implementation so the
Tk application can migrate incrementally without behavioural changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import asdict, dataclass, field
from copy import deepcopy
from datetime import datetime
from enum import Enum
import json
import logging
import math
import os
import random
import shutil
import struct
import subprocess
import tempfile
import textwrap
import threading
import traceback
import urllib.error
import urllib.request
import wave

PROJECT_ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("dr_sidekick")

PTNINFO_SIZE = 64
PTNDATA_SIZE = 65536
SLOT_COUNT = 16
SLOT_SIZE = 0x400
INTERNAL_PPQN = 96
TICKS_PER_BAR = 4 * INTERNAL_PPQN
MAX_PATTERN_EVENT_CAPACITY = 112

# Quantize values
QUANTIZE_VALUES = {
    "OFF": 0x00,
    "1/4": 0x01,
    "1/8": 0x02,
    "1/16": 0x03,
    "1/8T": 0x04,
    "1/16T": 0x05
}


class Bank(Enum):
    C = 'C'
    D = 'D'


@dataclass
class Event:
    """Single pattern event"""
    tick: int
    pad: int
    velocity: int = 0x7F


@dataclass
class GrooveTiming:
    """Groove timing template"""
    name: str
    timings: List[int]
    author: str = ""

    @classmethod
    def from_midi(cls, midi_path: Path) -> 'GrooveTiming':
        """Load groove from MIDI file"""
        notes, ppqn = load_midi_notes(str(midi_path))
        timings = [tick for tick, _, _ in notes]
        if timings:
            offset = timings[0]
            timings = [t - offset for t in timings]
        return cls(name=midi_path.stem, timings=timings)


@dataclass
class GrooveTemplate:
    """A groove loaded from the JSON groove library.

    Grid-type grooves store per-step tick offsets from the perfect grid.
    Compound grooves store raw tick positions (multiple grid sizes overlaid).
    """
    name: str
    groove_type: str          # "grid" or "compound"
    machine: str
    author: str
    ppqn: int = INTERNAL_PPQN
    grid: int = 0             # grid tick size (grid-type only)
    grid_label: str = ""      # human-readable grid label
    steps_per_beat: int = 0   # steps per beat (grid-type only)
    beats: int = 0
    offsets: List[int] = field(default_factory=list)  # grid-type
    ticks: List[int] = field(default_factory=list)    # compound-type


class GrooveLibrary:
    """Loads and indexes groove files from content packs."""

    def __init__(self, packs_dir: Optional[Path] = None):
        if packs_dir is None:
            packs_dir = PROJECT_ROOT / "packs"
        self.machines: List[str] = []
        self._by_machine: Dict[str, List[GrooveTemplate]] = {}
        self._attribution: Dict[str, dict] = {}
        self._load_packs(packs_dir)

    def _load_packs(self, packs_dir: Path):
        from .packs import discover_packs

        for pack in discover_packs(packs_dir):
            if pack.has_grooves:
                self._load_grooves(pack.grooves_path, pack.attribution)

    def _load_grooves(self, grooves_dir: Path, attribution: dict):
        if not grooves_dir.is_dir():
            log.warning("Groove library folder not found: %s", grooves_dir)
            return
        author = attribution.get("author", "")
        for json_path in sorted(grooves_dir.glob("*.json")):
            try:
                with open(json_path, "r") as f:
                    data = json.load(f)
                machine = data["machine"]
                self._attribution[machine] = attribution
                templates = []
                for g in data.get("grooves", []):
                    groove_type = g.get("type", "grid")
                    grid = g.get("grid", 0)
                    offsets = g.get("offsets", [])
                    ticks = g.get("ticks", [])
                    if (
                        groove_type == "grid"
                        and grid > 0
                        and any(abs(offset) > (grid / 2) for offset in offsets)
                    ):
                        groove_type = "compound"
                        ticks = sorted(
                            max(0, (step * grid) + offset)
                            for step, offset in enumerate(offsets)
                        )
                        offsets = []
                        log.warning(
                            "Reclassified groove '%s' in %s from grid to compound: "
                            "offset exceeded 50%% of grid size",
                            g.get("name", "<unnamed>"),
                            json_path.name,
                        )
                    tmpl = GrooveTemplate(
                        name=g["name"],
                        groove_type=groove_type,
                        machine=machine,
                        author=author,
                        ppqn=g.get("ppqn", INTERNAL_PPQN),
                        grid=grid,
                        grid_label=g.get("grid_label", ""),
                        steps_per_beat=g.get("steps_per_beat", 0),
                        beats=g.get("beats", 0),
                        offsets=offsets,
                        ticks=ticks,
                    )
                    templates.append(tmpl)
                self._by_machine[machine] = templates
                self.machines.append(machine)
            except Exception:
                log.error("Failed to load groove file %s:\n%s", json_path, traceback.format_exc())

    def get_grooves(self, machine: str) -> List[GrooveTemplate]:
        return self._by_machine.get(machine, [])

    def get_attribution(self, machine: str) -> dict:
        return self._attribution.get(machine, {})


@dataclass
class PatternSlot:
    """Represents one pattern slot in PTNINFO"""
    slot_index: int
    has_pattern: bool = False
    quantize: str = "OFF"

    @property
    def bank(self) -> Bank:
        return Bank.C if self.slot_index < 8 else Bank.D

    @property
    def pad(self) -> int:
        return (self.slot_index % 8) + 1

    def to_bytes(self) -> bytes:
        """
        PTNINFO format (hardware-verified):
        - EMPTY:  [B0 04 02 slot+1]
        - ACTIVE: [04 B0 quant 00]

        Quantize values:
        0x00=OFF, 0x01=1/4, 0x02=1/8, 0x03=1/16, 0x04=1/8T, 0x05=1/16T
        """
        if not self.has_pattern:
            # Empty slot format
            return bytes([0xb0, 0x04, 0x02, self.slot_index + 1])
        else:
            # Active slot format
            quant_map = {
                "OFF": 0x00,
                "1/4": 0x01,
                "1/8": 0x02,
                "1/16": 0x03,
                "1/8T": 0x04,
                "1/16T": 0x05
            }
            quant_byte = quant_map.get(self.quantize, 0x00)
            return bytes([0x04, 0xb0, quant_byte, 0x00])

    @classmethod
    def from_bytes(cls, slot_index: int, data: bytes) -> 'PatternSlot':
        """
        Parse PTNINFO slot data (hardware-verified):
        - EMPTY:  [B0 04 02 slot+1]
        - ACTIVE: [04 B0 quant 00]
        """
        if len(data) < 4:
            raise ValueError(f"Slot data must be 4 bytes, got {len(data)}")

        b0, b1, b2, b3 = data[0], data[1], data[2], data[3]

        if b0 == 0x04 and b1 == 0xb0:
            # Active slot
            quant_map = {
                0x00: "OFF",
                0x01: "1/4",
                0x02: "1/8",
                0x03: "1/16",
                0x04: "1/8T",
                0x05: "1/16T"
            }
            quantize = quant_map.get(b2, "OFF")
            return cls(slot_index=slot_index, has_pattern=True, quantize=quantize)
        elif b0 == 0xb0 and b1 == 0x04 and b2 == 0x02:
            # Empty slot
            return cls(slot_index=slot_index, has_pattern=False, quantize="OFF")
        else:
            # Unknown format - treat as empty
            return cls(slot_index=slot_index, has_pattern=False, quantize="OFF")


class PTNInfo:
    """PTNINFO0.SP0 handler"""
    
    def __init__(self):
        self.slots: List[PatternSlot] = []
        for i in range(SLOT_COUNT):
            self.slots.append(PatternSlot(i))
    
    def set_pattern(self, slot_index: int, quantize: str):
        if not 0 <= slot_index < SLOT_COUNT:
            raise ValueError(f"Slot must be 0-15, got {slot_index}")
        self.slots[slot_index].has_pattern = True
        self.slots[slot_index].quantize = quantize
    
    def clear_pattern(self, slot_index: int):
        if not 0 <= slot_index < SLOT_COUNT:
            raise ValueError(f"Slot must be 0-15, got {slot_index}")
        self.slots[slot_index].has_pattern = False
    
    def to_bytes(self) -> bytes:
        data = bytearray()
        for slot in self.slots:
            data.extend(slot.to_bytes())
        return bytes(data)
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'PTNInfo':
        if len(data) < PTNINFO_SIZE:
            raise ValueError(f"PTNINFO must be at least {PTNINFO_SIZE} bytes")
        ptninfo = cls()
        ptninfo.slots = []
        for i in range(SLOT_COUNT):
            offset = i * 4
            slot_data = data[offset:offset+4]
            ptninfo.slots.append(PatternSlot.from_bytes(i, slot_data))
        return ptninfo
    
    @classmethod
    def from_file(cls, filepath: Path) -> 'PTNInfo':
        with open(filepath, 'rb') as f:
            return cls.from_bytes(f.read())
    
    def save(self, filepath: Path):
        with open(filepath, 'wb') as f:
            f.write(self.to_bytes())


class PTNData:
    """
    PTNDATA0.SP0 handler v2.1-FINAL
    
    Uses byte-perfect initialization from actual SP-303
    """
    
    # Store initialization template as class variable
    _init_template = None
    
    def __init__(self, data: bytes = None, init_template_path: Path = None):
        if data is None:
            # Load from initialization template
            if init_template_path and init_template_path.exists():
                with open(init_template_path, 'rb') as f:
                    self.data = bytearray(f.read())
            elif PTNData._init_template is not None:
                self.data = bytearray(PTNData._init_template)
            else:
                # Try to load official template from standard location
                official_template = PROJECT_ROOT / 'PTNDATA_INIT_OFFICIAL.bin'
                if official_template.exists():
                    with open(official_template, 'rb') as f:
                        self.data = bytearray(f.read())
                else:
                    # Final fallback: create basic initialized structure
                    self.data = self._create_basic_init()
        else:
            self.data = bytearray(data)
    
    @classmethod
    def load_init_template(cls, template_path: Path):
        """Load initialization template for future use"""
        with open(template_path, 'rb') as f:
            cls._init_template = f.read()
    
    def _create_basic_init(self) -> bytearray:
        """Create basic initialization (fallback if no template)"""
        data = bytearray(PTNDATA_SIZE)

        # Header
        data[0:4] = bytes([0x8a, 0xb1, 0x07, 0x03])

        # Initialize ALL slots (0-15) with proper index tables and event headers
        for slot in range(16):
            slot_offset = 0xED90 - (slot * 0x400)

            # Index table for this slot (112 bytes = 0x70)
            for i in range(0, 112, 6):
                data[slot_offset + i:slot_offset + i + 6] = bytes([0xff, 0x80, 0x00, 0x00, 0x10, 0x00])

            # Event data header for this slot
            data_start = slot_offset + 0x70
            data[data_start:data_start + 12] = bytes([0x00, 0x00, 0x00, 0x00, 0xff, 0x80, 0x04, 0x03, 0x16, 0x00, 0xff, 0x80])
        
        # Metadata
        data[0xF4D0:0xF4D0 + 8] = bytes([0x00, 0x00, 0x00, 0x00, 0x42, 0x00, 0xbe, 0x41])
        data[0xFA3C:0xFA3C + 2] = bytes([0x09, 0x00])
        data[0xFABE:0xFABE + 2] = bytes([0x42, 0x00])
        data[0xFDA0 + 12:0xFDA0 + 14] = bytes([0x58, 0x00])
        data[0xFDA0 + 14:0xFDA0 + 16] = bytes([0x2f, 0x00])
        
        # Step bitmap
        data[0xFE00:0xFE00 + 12] = bytes(12)
        data[0xFE00 + 12:0xFE00 + 56] = bytes([0x01] * 44)
        data[0xFE00 + 56:0xFE00 + 60] = bytes(4)
        data[0xFE00 + 60:0xFE00 + 64] = bytes([0xff, 0xff, 0xff, 0xff])
        
        return data
    
    def get_slot_offset(self, slot_index: int) -> int:
        if not 0 <= slot_index < SLOT_COUNT:
            raise ValueError(f"Slot must be 0-15, got {slot_index}")
        return 0xED90 - (slot_index * SLOT_SIZE)
    
    def encode_events(self, events: List[Event], total_length_ticks: Optional[int] = None) -> bytes:
        """
        Encode events to serialized format (v2.7 - DELTA SPLIT FIX!)

        Based on actual SP-303 hardware dumps:
        - Header (6 bytes):  00 00 00 00 [checksum] 80
        - Pattern marker (4 bytes): 04 03 16 00
        - Events (6 bytes each): [delta] [pad] [vel] [flags] [sp1] [sp2]

        Delta encoding limitation:
        - Delta is 1 byte (0-255 ticks max)
        - For gaps > 255 ticks, insert rest events (velocity=0) to span the gap
        """
        data = bytearray()

        # Header (6 bytes) - CORRECTED FORMAT
        header_checksum = self._calculate_header_checksum(events)
        data.extend([0x00, 0x00, 0x00, 0x00, header_checksum, 0x80])

        # Pattern marker (4 bytes) - THIS WAS MISSING!
        data.extend([0x04, 0x03, 0x16, 0x00])

        # Preserve leading silence before the first note.
        # Without this, a pattern that intentionally starts later (e.g. bar 2)
        # will be shifted to start at tick 0 on hardware.
        if events and events[0].tick > 0:
            lead = events[0].tick
            while lead > 0:
                chunk = min(255, lead)
                data.extend([chunk & 0xFF, 0x80, 0x10, 0x00, 0x00, 0x00])
                lead -= chunk

        # Events (6 bytes each)
        for i, event in enumerate(events):
            # Calculate delta to next event
            if i < len(events) - 1:
                total_delta = events[i+1].tick - event.tick
            else:
                # Last event - calculate loop delta
                if total_length_ticks is not None:
                    total_delta = max(1, total_length_ticks - event.tick)
                else:
                    total_delta = self._calculate_last_event_delta(events)

            # If delta > 255, insert rest events to span the gap
            remaining_delta = total_delta
            current_event = event

            # Zero-delta events are valid and required for polyphonic notes
            # that start on the same tick. They must still emit one tuple.
            if remaining_delta == 0:
                data.extend([
                    0x00,
                    current_event.pad & 0xFF,
                    current_event.velocity & 0xFF,
                    0x00, 0x00, 0x00
                ])
                continue

            while remaining_delta > 0:
                # Determine delta for this event chunk
                chunk_delta = min(255, remaining_delta)

                # Flags and special bytes (v2.4 - ALL zeros works correctly)
                flags = 0x00
                special1 = 0x00
                special2 = 0x00

                # Build event (6 bytes)
                event_bytes = bytes([
                    chunk_delta & 0xFF,
                    current_event.pad & 0xFF,
                    current_event.velocity & 0xFF,
                    flags,
                    special1,
                    special2
                ])
                data.extend(event_bytes)

                # Move to next chunk
                remaining_delta -= chunk_delta

                # If there's more delta to encode, create a rest event
                if remaining_delta > 0:
                    # Rest event: HARDWARE FORMAT - pad=0x80, velocity=0x10
                    # This is how the SP-303 hardware encodes timing gaps > 255 ticks
                    current_event = Event(
                        tick=current_event.tick + chunk_delta,
                        pad=0x80,
                        velocity=0x10
                    )

        return bytes(data)
    
    def write_pattern(self, slot_index: int, events: List[Event], total_length_ticks: Optional[int] = None):
        """
        Write pattern to slot v2.1-FINAL
        
        Includes ALL required metadata updates based on hardware testing
        """
        if not 0 <= slot_index < SLOT_COUNT:
            raise ValueError(f"Slot must be 0-15, got {slot_index}")
        if not events:
            raise ValueError("Must have at least one event")
        
        slot_offset = self.get_slot_offset(slot_index)

        # 1. Write event index - CORRECTED format (v2.5)
        # Index table uses [ff 80 00 00 10 00] pattern (from hardware dumps)
        # NOT [ff 80 04 03 16 00] - that was incorrect
        index_offset = slot_offset
        while index_offset < slot_offset + 0x70:
            self.data[index_offset:index_offset+6] = bytes([0xff, 0x80, 0x00, 0x00, 0x10, 0x00])
            index_offset += 6

        # 2. Encode and write event data
        data_offset = slot_offset + 0x70
        serialized = self.encode_events(events, total_length_ticks=total_length_ticks)
        tuple_zone_end = slot_offset + 0x272
        max_serialized_len = (tuple_zone_end - data_offset) - TUPLE_ZONE_SENTINEL_BYTES
        if len(serialized) > max_serialized_len:
            raise ValueError(
                f"Pattern too large for tuple zone: {len(serialized)} bytes "
                f"(max {max_serialized_len}, reserving {TUPLE_ZONE_SENTINEL_BYTES} bytes for the end marker)"
            )
        self.data[data_offset:data_offset+len(serialized)] = serialized
        
        # Clear/fill only the tuple payload zone.
        #
        # Hardware captures show a fixed trailer beginning at slot+0x272.
        # Zeroing beyond this boundary destroys trailer metadata and causes
        # loop-length behavior regressions on device.
        clear_offset = data_offset + len(serialized)

        # Use the slot's native fill tuple so we preserve on-card semantics:
        #   ff 80 00 00 10 00   (common)
        #   ff 80 00 00 00 00   (also observed)
        fill_tuple = bytes(self.data[slot_offset + 0x74:slot_offset + 0x7A])
        if len(fill_tuple) != 6 or fill_tuple[0] != 0xFF or fill_tuple[1] != 0x80:
            fill_tuple = bytes([0xFF, 0x80, 0x00, 0x00, 0x10, 0x00])

        while clear_offset + 6 <= tuple_zone_end:
            self.data[clear_offset:clear_offset + 6] = fill_tuple
            clear_offset += 6

        # 3. Metadata should NOT be modified (v2.5 fix)
        # Hardware dumps show metadata stays identical to init template
        # Pattern presence indicated ONLY by pattern marker in event data
        # self._update_metadata(slot_index, len(events))  # REMOVED

    def clear_pattern(self, slot_index: int):
        """Clear slot event tuples while preserving fixed trailer metadata."""
        if not 0 <= slot_index < SLOT_COUNT:
            raise ValueError(f"Slot must be 0-15, got {slot_index}")

        slot_offset = self.get_slot_offset(slot_index)

        # Index table region.
        index_fill = bytes([0xFF, 0x80, 0x00, 0x00, 0x10, 0x00])
        index_offset = slot_offset
        while index_offset < slot_offset + 0x70:
            self.data[index_offset:index_offset + 6] = index_fill
            index_offset += 6

        # Event header + marker with immediate sentinel (empty pattern).
        data_offset = slot_offset + 0x70
        self.data[data_offset:data_offset + 10] = bytes([
            0x00, 0x00, 0x00, 0x00, 0xFF, 0x80,  # header
            0x04, 0x03, 0x16, 0x00               # marker
        ])

        # Fill tuple zone up to the fixed trailer boundary.
        tuple_zone_end = slot_offset + 0x272
        fill_tuple = bytes(self.data[slot_offset + 0x74:slot_offset + 0x7A])
        if len(fill_tuple) != 6 or fill_tuple[0] != 0xFF or fill_tuple[1] != 0x80:
            fill_tuple = index_fill
        clear_offset = data_offset + 10
        while clear_offset + 6 <= tuple_zone_end:
            self.data[clear_offset:clear_offset + 6] = fill_tuple
            clear_offset += 6
    
    def _update_metadata(self, slot_index: int, event_count: int):
        """
        Update ALL metadata fields (based on hardware test observations)
        
        These updates are MANDATORY - SP-303 validates them!
        """
        # Event counter (0xFA3C + 2)
        # Observed: +3 for 1 event, +5 for 2 events, +3 for 3 events
        # Using event_count + 2 as approximation
        current_count = struct.unpack('<H', self.data[0xFA3C:0xFA3C + 2])[0]
        new_count = current_count + event_count + 2
        self.data[0xFA3C:0xFA3C + 2] = struct.pack('<H', new_count)
        
        # Active flag (0xFABE)
        # Changes from 0x42 00 to 0x01 00
        self.data[0xFABE:0xFABE + 2] = bytes([0x01, 0x00])
        
        # Metadata region 1 (0xF4D0 + 4)
        # Increments: 0x42 → 0x43 (+1), 0x44 (+2), 0x45 (+3)
        current_meta = self.data[0xF4D0 + 4]
        new_meta = current_meta + event_count
        self.data[0xF4D0 + 4] = new_meta
        
        # Metadata complement (0xF4D0 + 6)
        # Decrements as F4D0+4 increments (complement pattern)
        self.data[0xF4D0 + 6] = (0xFF - new_meta) & 0xFF
        self.data[0xF4D0 + 7] = new_meta
        
        # Footer flag (0xFDA0 + 15)
        # Changes from 0x2f to 0x3f (adds 0x10 bit)
        self.data[0xFDA0 + 15] = 0x3f
    
    def _calculate_header_checksum(self, events: List[Event]) -> int:
        """
        Calculate header checksum

        Based on comprehensive test hardware dumps:
        - 1 event: always 0x00
        - 2 events: 0x04-0x06
        - 3 events: 0x06-0x09
        - 4+ events: varies

        Using actual comprehensive test values
        """
        count = len(events)

        if count == 1:
            # Comprehensive test: ALWAYS 0x00 for 1 event (any pad)
            return 0x00
        elif count == 2:
            # Comprehensive test shows 0x06 (slot 1) and 0x04 (slot 9)
            # Approximate
            return 0x06
        elif count == 3:
            # Comprehensive test shows 0x09, 0x06
            return 0x09
        elif count == 4:
            # Comprehensive test shows 0x08, 0x0c
            return 0x08
        else:
            # Larger patterns - use count-based formula
            return (count * 2) & 0xFF
    
    def _calculate_last_event_delta(self, events: List[Event]) -> int:
        """
        Calculate delta for last event

        Comprehensive test shows:
        - 1 event: 0xFF (always)
        - Multi-event: varies based on tick spacing

        For now, using 0xFF for single events
        """
        if not events:
            return 0xFF

        if len(events) == 1:
            # Single event: always 0xFF in comprehensive test
            return 0xFF

        # Multi-event patterns - use timing-based formula
        total_ticks = events[-1].tick
        event_count = len(events)
        return (0xFF - (total_ticks & 0x7F) + event_count * 16) & 0xFF
    
    def decode_events(self, slot_index: int) -> List[Event]:
        """
        Decode events from slot (v2.7 - WITH REST EVENT FILTERING)

        Hardware format:
        - Header (6 bytes):  00 00 00 00 [checksum] 80
        - Pattern marker (4 bytes): 04 03 16 00
        - Events (6 bytes each): [delta] [pad] [vel] [flags] [sp1] [sp2]

        Note: Filters out rest events (velocity=0) used for spanning large deltas
        """
        slot_offset = self.get_slot_offset(slot_index)
        data_offset = slot_offset + 0x70

        # Skip header (6 bytes) + pattern marker (4 bytes) = 10 bytes
        offset = data_offset + 10
        events = []
        current_tick = 0

        for i in range(200):  # Increased limit to handle rest events
            if offset + 6 > len(self.data):
                break

            event_bytes = self.data[offset:offset+6]

            # Stop at all-zero bytes
            if all(b == 0x00 for b in event_bytes):
                break

            delta = event_bytes[0]
            pad = event_bytes[1]
            velocity = event_bytes[2]
            flags = event_bytes[3]
            special1 = event_bytes[4]
            special2 = event_bytes[5]

            # Handle control/rest stream (pad=0x80).
            # End/fill markers use FF 80 with velocity 0x00; timing rests use velocity 0x10.
            if pad == 0x80:
                if delta == 0xFF and velocity == 0x00 and flags == 0x00:
                    break
                # Timing rest - advance time but don't add a note event.
                current_tick += delta
                offset += 6
                continue

            # Accept all 4 banks: A (0x00-0x07), B (0x08-0x0F), C (0x10-0x17), D (0x18-0x1F)
            if not (0x00 <= pad <= 0x1F):
                break

            # Add real events (velocity > 0)
            if velocity > 0:
                events.append(Event(tick=current_tick, pad=pad, velocity=velocity))

            current_tick += delta
            offset += 6

        return events
    
    @classmethod
    def from_file(cls, filepath: Path) -> 'PTNData':
        with open(filepath, 'rb') as f:
            return cls(f.read())
    
    def save(self, filepath: Path):
        with open(filepath, 'wb') as f:
            f.write(self.data)


# Helper functions (MIDI loading, etc.)
def read_varlen(f):
    value = 0
    while True:
        b = f.read(1)
        if not b:
            raise EOFError("Unexpected end of file")
        b = b[0]
        value = (value << 7) | (b & 0x7F)
        if not (b & 0x80):
            return value


def load_midi_notes(midi_path: str) -> Tuple[List[Tuple[int, int, int]], int]:
    notes = []
    
    with open(midi_path, 'rb') as f:
        if f.read(4) != b'MThd':
            raise ValueError("Not a MIDI file")
        
        header_len = struct.unpack('>I', f.read(4))[0]
        fmt, ntrks, ppqn = struct.unpack('>HHH', f.read(6))
        f.read(header_len - 6)
        
        if fmt != 0:
            raise ValueError("Only MIDI format 0 supported")
        
        if f.read(4) != b'MTrk':
            raise ValueError("Missing track chunk")
        
        track_len = struct.unpack('>I', f.read(4))[0]
        track_end = f.tell() + track_len
        
        abs_tick = 0
        running_status = None
        
        while f.tell() < track_end:
            delta = read_varlen(f)
            abs_tick += delta
            
            status = f.read(1)[0]
            if status < 0x80:
                f.seek(-1, 1)
                status = running_status
            else:
                running_status = status
            
            if status == 0xFF:
                meta_type = f.read(1)[0]
                length = read_varlen(f)
                f.read(length)
                if meta_type == 0x2F:
                    break
                continue
            
            if status & 0xF0 == 0x90:
                note = f.read(1)[0]
                vel = f.read(1)[0]
                if vel > 0:
                    notes.append((abs_tick, note, vel))
            elif status & 0xF0 == 0x80:
                f.read(2)
            else:
                if status & 0xF0 in (0xC0, 0xD0):
                    f.read(1)
                else:
                    f.read(2)
    
    return notes, ppqn


def apply_groove_to_slot(groove: GrooveTiming, target_pad: int, slot_index: int,
                         ptninfo: PTNInfo, ptndata: PTNData, quantize: str = "OFF"):
    """Apply groove to slot (v2.1-FINAL)"""
    events = [Event(tick=tick, pad=target_pad, velocity=0x7F) for tick in groove.timings]
    
    if not events:
        raise ValueError("Groove has no events")
    
    ptndata.write_pattern(slot_index, events)
    ptninfo.set_pattern(slot_index, quantize)
    
    slot = ptninfo.slots[slot_index]
    print(f"✓ Applied {groove.name} to Slot {slot_index} (Bank {slot.bank.value} Pad {slot.pad})")
    print(f"  Events: {len(events)}, Target pad: 0x{target_pad:02X}")



# File structure constants
FILE_SIZE = 65536  # 64KB total
SLOT_RECORDS_START = 0x0000
SLOT_RECORDS_SIZE = 48
SLOT_COUNT = 16
PAD_MAPPING_START = 0x0300
PAD_MAPPING_SIZE = 64  # 16 pads × 4 bytes
DSP_COMMANDS_START = 0x0380
DSP_COMMANDS_SIZE = 128
SAMPLE_FILE_SIZE = 16384  # 16KB per sample file

# SP-303 hardware constants (from firmware analysis)
SP303_CLOCK_HZ = 37_500_000       # Hardware clock frequency
SP303_FLAGS_K  = 312_500           # = SP303_CLOCK_HZ / 120 (BPM reference divisor)
SP303_DEFAULT_PARAMS = 0x04B0      # Default clock divider → 31,250 Hz
SP303_FLAGS_EMPTY    = 0x0000_0100 # flags value for empty slots
SP303_FLAGS_OCCUPIED_MIN = 0x0040  # smallest valid flags for a populated slot

# Template bytes (from firmware analysis)
TEMPLATE_BYTES_24_27_EMPTY = bytes.fromhex("00000100")
TEMPLATE_BYTES_28_31_EMPTY = bytes.fromhex("04b004b0")
TEMPLATE_BYTES_32_35 = bytes.fromhex("113a067f")
TEMPLATE_BYTES_40_43 = bytes.fromhex("00004000")
TEMPLATE_BYTES_44_47 = bytes.fromhex("03000000")


def _compute_flags(sample_len: int, params_word: int) -> int:
    """
    Compute the SP-303 flags field for a populated slot.

    flags = nearest power-of-2 to (sample_len × params_word / 312_500)

    Verified 100% correct across 55 populated sample slots from 5 real cards.
    """
    if sample_len == 0 or params_word == 0:
        return SP303_FLAGS_EMPTY
    raw = sample_len * params_word / SP303_FLAGS_K
    if raw <= 0:
        return SP303_FLAGS_EMPTY
    exp = round(math.log2(raw))
    return 1 << exp

# Pad mapping constants
PAD_MAPPING_OPCODE = bytes.fromhex("b00402")
# Observed hardware variants in existing cards/archives.
PAD_MAPPING_OPCODES_ACCEPTED = {
    PAD_MAPPING_OPCODE,
    bytes.fromhex("04b002"),
    bytes.fromhex("04b008"),
}


class SampleBank(Enum):
    """SP-303 Banks"""
    C = 'C'  # User bank, slots 0-7
    D = 'D'  # User bank, slots 8-15


@dataclass
class SlotRecord:
    """
    Represents one 48-byte slot record in SMPINFO0.SP0

    Structure (offsets relative to slot start):
    0x00-0x0B (12 bytes): Reserved — always zeros
    0x0C-0x0F (4 bytes):  sample_len  — audio data size in bytes (big-endian)
    0x10-0x13 (4 bytes):  loop_start  — loop point in bytes (big-endian)
    0x14-0x17 (4 bytes):  sample_end  — = sample_len (repeated, big-endian)
    0x18-0x1B (4 bytes):  flags       — power-of-2 duration category (computed)
    0x1C-0x1F (4 bytes):  params      — two copies of 16-bit clock divider
    0x20-0x23 (4 bytes):  dsp_const   — always 0x113A06xx (xx = 0x7F default)
    0x24      (1 byte):   stereo      — 0x00 = mono, 0x01 = stereo
    0x25      (1 byte):   gate        — 0x00 = normal, 0x01 = gate on
    0x26      (1 byte):   loop        — 0x00 = no loop, 0x01 = loop on
    0x27      (1 byte):   reverse     — 0x00 = normal, 0x01 = reverse on
    0x28-0x2B (4 bytes):  (const)     — always 0x00004000
    0x2C-0x2F (4 bytes):  (const)     — always 0x03000000
    """
    slot_index: int  # 0-15
    sample_length_bytes: int  # Actual audio data length
    loop_point_bytes: int  # Loop point or = length for no loop
    is_stereo: bool
    params_word: int = SP303_DEFAULT_PARAMS  # 16-bit clock divider; sample_rate = 37,500_000 / params_word
    sample_rate: int = 31250            # Derived hardware rate
    is_gate: bool = False              # Gate playback mode (byte 0x25)
    is_loop: bool = False              # Loop playback mode (byte 0x26)
    is_reverse: bool = False           # Reverse playback mode (byte 0x27)
    level: int = 0x7F                  # Sample level (byte 0x23 of dsp_const, 0-127)
    reserved_0_11: bytes = bytes(12)  # Reserved bytes 0-11
    
    @property
    def bank(self) -> SampleBank:
        """Get bank (C for slots 0-7, D for slots 8-15)"""
        return SampleBank.C if self.slot_index < 8 else SampleBank.D
    
    @property
    def pad(self) -> int:
        """Get pad number within bank (1-8)"""
        return (self.slot_index % 8) + 1
    
    @property
    def is_empty(self) -> bool:
        """Check if slot is empty"""
        return self.sample_length_bytes == 0
    
    @property
    def sample_filename_base(self) -> str:
        """Get sample filename without extension: SMP000X"""
        return f"SMP{self.slot_index:04X}"
    
    @property
    def sample_filenames(self) -> List[str]:
        """Get required sample file names"""
        base = self.sample_filename_base
        if self.is_stereo:
            return [f"{base}L.SP0", f"{base}R.SP0"]
        else:
            return [f"{base}L.SP0"]
    
    def to_bytes(self) -> bytes:
        """Convert slot record to 48-byte binary format"""
        record = bytearray(SLOT_RECORDS_SIZE)
        
        # Bytes 0-11: Reserved (usually zeros when empty)
        record[0:12] = self.reserved_0_11
        
        # Bytes 12-15: Sample length (big-endian)
        record[12:16] = struct.pack('>I', self.sample_length_bytes)
        
        # Bytes 16-19: Loop point (big-endian)
        record[16:20] = struct.pack('>I', self.loop_point_bytes)
        
        # Bytes 20-23: Sample length repeated (big-endian)
        record[20:24] = struct.pack('>I', self.sample_length_bytes)
        
        # Bytes 24-27: flags; bytes 28-31: params (two identical 16-bit clock dividers)
        if self.is_empty:
            record[24:28] = TEMPLATE_BYTES_24_27_EMPTY
            record[28:32] = TEMPLATE_BYTES_28_31_EMPTY
        else:
            flags = _compute_flags(self.sample_length_bytes, self.params_word)
            record[24:28] = struct.pack('>I', flags)
            divider = self.params_word & 0xFFFF
            record[28:32] = struct.pack('>HH', divider, divider)
        
        # Bytes 32-35: DSP constant (0x113A06xx where xx = level)
        record[32:36] = bytes([0x11, 0x3A, 0x06, self.level & 0x7F])
        
        # Byte 36: Stereo flag
        record[36] = 0x01 if self.is_stereo else 0x00

        # Byte 37: Gate flag
        record[37] = 0x01 if self.is_gate else 0x00

        # Byte 38: Loop flag
        record[38] = 0x01 if self.is_loop else 0x00

        # Byte 39: Reverse flag
        record[39] = 0x01 if self.is_reverse else 0x00
        
        # Bytes 40-43: Constant
        record[40:44] = TEMPLATE_BYTES_40_43
        
        # Bytes 44-47: Constant
        record[44:48] = TEMPLATE_BYTES_44_47
        
        return bytes(record)
    
    @classmethod
    def from_bytes(cls, slot_index: int, data: bytes) -> 'SlotRecord':
        """Parse 48-byte slot record"""
        if len(data) != SLOT_RECORDS_SIZE:
            raise ValueError(f"Slot record must be {SLOT_RECORDS_SIZE} bytes, got {len(data)}")
        
        # Parse fields
        reserved_0_11 = data[0:12]
        sample_length = struct.unpack('>I', data[12:16])[0]
        loop_point = struct.unpack('>I', data[16:20])[0]
        # Preserve params_word (bytes 28-29 = first 16-bit clock divider) for round-trip fidelity
        params_word = struct.unpack('>H', data[28:30])[0] or SP303_DEFAULT_PARAMS
        sample_rate = int(SP303_CLOCK_HZ / params_word)
            
        is_stereo = data[36] == 0x01
        is_gate = data[37] == 0x01
        is_loop = data[38] == 0x01
        is_reverse = data[39] == 0x01
        level = data[35] & 0x7F  # Last byte of dsp_const (0x113A06xx)

        return cls(
            slot_index=slot_index,
            sample_length_bytes=sample_length,
            loop_point_bytes=loop_point,
            is_stereo=is_stereo,
            params_word=params_word,
            sample_rate=sample_rate,
            is_gate=is_gate,
            is_loop=is_loop,
            is_reverse=is_reverse,
            level=level,
            reserved_0_11=reserved_0_11
        )
    
    def __repr__(self):
        bank_str = f"Bank {self.bank.value}"
        pad_str = f"Pad {self.pad}"
        type_str = "Stereo" if self.is_stereo else "Mono"
        gate_str = " Gate" if self.is_gate else ""
        loop_str = " Loop" if self.is_loop else ""
        rev_str = " Reverse" if self.is_reverse else ""
        status = "Empty" if self.is_empty else f"{self.sample_length_bytes}B"
        return f"Slot {self.slot_index:2d} ({bank_str}, {pad_str}): {type_str:6s}{gate_str}{loop_str}{rev_str} {status}"


@dataclass
class PadMapping:
    """
    Pad mapping table entry (4 bytes each, 16 total)
    
    Format: [b0] [04] [02] [pad_number]
    where pad_number is 1-16 (0x01-0x10)
    
    This maps physical pad presses to slot indices.
    """
    pad_index: int  # 0-15
    
    def to_bytes(self) -> bytes:
        """Convert to 4-byte pad mapping entry"""
        return PAD_MAPPING_OPCODE + bytes([self.pad_index + 1])
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'PadMapping':
        """Parse 4-byte pad mapping entry"""
        if len(data) != 4:
            raise ValueError(f"Pad mapping entry must be 4 bytes, got {len(data)}")
        if data[0:3] not in PAD_MAPPING_OPCODES_ACCEPTED:
            raise ValueError(f"Invalid pad mapping opcode: {data[0:3].hex()}")
        
        pad_number = data[3]
        return cls(pad_index=pad_number - 1)


# ── SP-303 RDAC MT1/MT2 decoder ───────────────────────────────────────────────
# Ported from RDAC decode research by Randy Gordon (randy@integrand.com), LGPL 2006.
# Updated 2026: Improved with Adaptive Alignment and 24-bit state tracking.

# Pattern lookup table: index = (block[0] & 0xf0) | (block[2] >> 4) → 0..36
_SP303_RDAC_PATTERNS = [
    0, 0, 0, 0,  1, 1, 1, 1,  2, 2, 2, 2,  3, 3, 3, 3,
    0, 0, 0, 0,  1, 1, 1, 1,  2, 2, 2, 2,  3, 3, 3, 3,
    0, 0, 0, 0,  1, 1, 1, 1,  2, 2, 2, 2,  3, 3, 3, 3,
    0, 0, 0, 0,  1, 1, 1, 1,  2, 2, 2, 2,  3, 3, 3, 3,
    4, 4, 4, 4,  5, 5, 5, 5,  6, 6, 6, 6,  7, 7, 7, 7,
    4, 4, 4, 4,  5, 5, 5, 5,  6, 6, 6, 6,  7, 7, 7, 7,
    4, 4, 4, 4,  5, 5, 5, 5,  6, 6, 6, 6,  7, 7, 7, 7,
    4, 4, 4, 4,  5, 5, 5, 5,  6, 6, 6, 6,  7, 7, 7, 7,
    8,  8,  8,  8,  9,  9,  9,  9,  10, 10, 10, 10, 11, 11, 11, 11,
    8,  8,  8,  8,  9,  9,  9,  9,  10, 10, 10, 10, 11, 11, 11, 11,
    8,  8,  8,  8,  9,  9,  9,  9,  10, 10, 10, 10, 11, 11, 11, 11,
    8,  8,  8,  8,  9,  9,  9,  9,  10, 10, 10, 10, 11, 11, 11, 11,
    12, 12, 13, 13, 14, 14, 15, 15, 16, 16, 17, 17, 18, 18, 19, 19,
    12, 12, 13, 13, 14, 14, 15, 15, 16, 16, 17, 17, 18, 18, 19, 19,
    20, 20, 21, 21, 22, 22, 23, 23, 24, 24, 25, 26, 27, 28, 29, 30,
    20, 20, 21, 21, 22, 22, 23, 23, 24, 24, 31, 32, 33, 34, 35, 36,
]

def _sp303_p(s): return s.replace(' ', '')
# MT1 Patterns (16-byte blocks)
_SP303_PAT_A  = _sp303_p("ppp88888 88888888 pppggggg gggggggg 87777776 66666655 gffffffe eeeeeedd"
                          " 55554444 44444333 ddddcccc cccccbbb 33322222 22111111 bbbaaaaa aa999999")
_SP303_PAT_B  = _sp303_p("pp888888 88888887 ppgggggg gggggggf 77777666 66666555 fffffeee eeeeeddd"
                          " 55544444 44443333 dddccccc ccccbbbb 33222222 22111111 bbaaaaaa aa999999")
_SP303_PAT_B3 = _sp303_p("ppp88888 88888887 pppggggg gggggggf 77777666 66666555 fffffeee eeeeeddd"
                          " 55544444 44443333 dddccccc ccccbbbb 33222222 22111111 bbaaaaaa aa999999")
_SP303_PAT_C  = _sp303_p("ppp88888 88888877 pppggggg ggggggff 77776666 66665555 ffffeeee eeeedddd"
                          " 55444444 44443333 ddcccccc ccccbbbb 33222222 22111111 bbaaaaaa aa999999")
_SP303_PAT_D  = _sp303_p("pp888888 88877777 ppgggggg gggfffff 77666666 66555555 ffeeeeee eedddddd"
                          " 54444444 44333333 dccccccc ccbbbbbb 32222222 21111111 baaaaaaa a9999999")
_SP303_PAT_E  = _sp303_p("pppp8888 88888877 ppppgggg ggggggff 77776666 66665555 ffffeeee eeeedddd"
                          " 55444444 44443333 ddcccccc ccccbbbb 33222222 22111111 bbaaaaaa aa999999")
_SP303_PAT_F  = _sp303_p("pppp8888 88887777 ppppgggg ggggffff 77766666 66655555 fffeeeee eeeddddd"
                          " 55444444 44333333 ddcccccc ccbbbbbb 32222222 21111111 baaaaaaa a9999999")
_SP303_PAT_B4 = _sp303_p("pppp8888 88888887 ppppgggg gggggggf 77777666 66665555 ffffffee eeeeeddd"
                          " 55554444 44433333 ddddcccc cccbbbbb 33222222 21111111 bbaaaaaa a9999999")

_SP303_SYM = {c: i for i, c in enumerate('123456789abcdefg')}
_SP303_SYM['p'] = -1

# Firmware shift LUT at 0xB4C0 — 64 entries indexed by block[0] >> 2.
_SP303_FW_SHIFT_LUT64 = [
    19, 19, 19, 18, 18, 18, 17, 17, 16, 16, 16, 15, 15, 15, 14, 14,
    13, 13, 13, 12, 12, 12, 11, 11, 10, 10, 10,  9,  9,  9,  8,  8,
     7,  7,  7,  6,  6,  5,  5,  5,  4,  4,  4,  3,  3,  2,  2,  2,
     1,  1,  1,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
]


def _sp303_apply_pattern(block, pattern):
    """Extract 16 symbols from a 16-byte block according to pattern.
    Each symbol is sign-extended at its natural bit depth — no alignment.
    Residuals stay smaller than anchors (they are corrections in the
    hierarchical DPCM scheme, not full-scale values).
    Returns (symbols, max_depth) where max_depth is the largest bit count.
    """
    out, out_pos = [0] * 16, [0] * 16
    for in_pos in range(15, -1, -1):
        byte_pat = pattern[in_pos * 8: in_pos * 8 + 8]
        byte_val = block[in_pos]
        for bit_pos in range(8):
            out_idx = _SP303_SYM[byte_pat[7 - bit_pos]]
            if out_idx == -1:
                continue
            if (byte_val >> bit_pos) & 1:
                out[out_idx] |= 1 << out_pos[out_idx]
            out_pos[out_idx] += 1
    max_depth = max(out_pos) if any(out_pos) else 1
    for i in range(16):
        if out_pos[i] > 0:
            mask = 1 << (out_pos[i] - 1)
            out[i] = -(out[i] & mask) | out[i]
    return out, max_depth


def _sp303_shift_round(out, pos):
    if pos == 0:
        return
    if pos > 0:
        half = 1 << (pos - 1)
        for i in range(16):
            out[i] = (out[i] << pos) | half
    else:
        rsh = -pos
        for i in range(16):
            out[i] >>= rsh


def _sp303_interp(a, b):
    s = a + b
    return -((-s + 1) // 2) if s < 0 else s // 2


def _sp303_interp2(d0, out):
    out[3]  += _sp303_interp(d0,      out[7]);  out[1]  += _sp303_interp(d0,      out[3])
    out[5]  += _sp303_interp(out[3],  out[7]);  out[11] += _sp303_interp(out[7],  out[15])
    out[9]  += _sp303_interp(out[7],  out[11]); out[13] += _sp303_interp(out[11], out[15])
    out[0]  += _sp303_interp(d0,      out[1]);  out[2]  += _sp303_interp(out[1],  out[3])
    out[4]  += _sp303_interp(out[3],  out[5]);  out[6]  += _sp303_interp(out[5],  out[7])
    out[8]  += _sp303_interp(out[7],  out[9]);  out[10] += _sp303_interp(out[9],  out[11])
    out[12] += _sp303_interp(out[11], out[13]); out[14] += _sp303_interp(out[13], out[15])


def _sp303_interp4(d0, out):
    out[1]  += _sp303_interp(d0,      out[3]);  out[5]  += _sp303_interp(out[3],  out[7])
    out[9]  += _sp303_interp(out[7],  out[11]); out[13] += _sp303_interp(out[11], out[15])
    out[0]  += _sp303_interp(d0,      out[1]);  out[2]  += _sp303_interp(out[1],  out[3])
    out[4]  += _sp303_interp(out[3],  out[5]);  out[6]  += _sp303_interp(out[5],  out[7])
    out[8]  += _sp303_interp(out[7],  out[9]);  out[10] += _sp303_interp(out[9],  out[11])
    out[12] += _sp303_interp(out[11], out[13]); out[14] += _sp303_interp(out[13], out[15])


def _sp303_interp8(d0, out):
    out[0]  += _sp303_interp(d0,      out[1]);  out[2]  += _sp303_interp(out[1],  out[3])
    out[4]  += _sp303_interp(out[3],  out[5]);  out[6]  += _sp303_interp(out[5],  out[7])
    out[8]  += _sp303_interp(out[7],  out[9]);  out[10] += _sp303_interp(out[9],  out[11])
    out[12] += _sp303_interp(out[11], out[13]); out[14] += _sp303_interp(out[13], out[15])


def _sp303_decode_mt1(d0: int, block: bytes) -> List[int]:
    """Decode one 16-byte MT1 RDAC block to high-precision (24-bit internal) samples."""
    p = _SP303_RDAC_PATTERNS[(block[0] & 0xf0) | ((block[2] & 0xf0) >> 4)]
    lut_val = _SP303_FW_SHIFT_LUT64[block[0] >> 2]
    
    pat_map = {
        0:  (_SP303_PAT_B,  _sp303_interp2), 1:  (_SP303_PAT_B,  _sp303_interp2),
        2:  (_SP303_PAT_B,  _sp303_interp2), 3:  (_SP303_PAT_B,  _sp303_interp2),
        4:  (_SP303_PAT_B,  _sp303_interp2), 5:  (_SP303_PAT_B,  _sp303_interp2),
        6:  (_SP303_PAT_D,  _sp303_interp4), 7:  (_SP303_PAT_D,  _sp303_interp4),
        8:  (_SP303_PAT_D,  _sp303_interp4), 9:  (_SP303_PAT_D,  _sp303_interp4),
        10: (_SP303_PAT_D,  _sp303_interp4), 11: (_SP303_PAT_D,  _sp303_interp4),
        12: (_SP303_PAT_A,  _sp303_interp2), 13: (_SP303_PAT_A,  _sp303_interp2),
        14: (_SP303_PAT_A,  _sp303_interp2), 15: (_SP303_PAT_A,  _sp303_interp2),
        16: (_SP303_PAT_A,  _sp303_interp2), 17: (_SP303_PAT_A,  _sp303_interp2),
        18: (_SP303_PAT_B3, _sp303_interp2),
        19: (_SP303_PAT_C,  _sp303_interp2), 20: (_SP303_PAT_C,  _sp303_interp2),
        21: (_SP303_PAT_C,  _sp303_interp2), 22: (_SP303_PAT_C,  _sp303_interp2),
        23: (_SP303_PAT_C,  _sp303_interp2), 24: (_SP303_PAT_C,  _sp303_interp2),
        25: (_SP303_PAT_F,  _sp303_interp8), 26: (_SP303_PAT_F,  _sp303_interp8),
        27: (_SP303_PAT_F,  _sp303_interp8), 28: (_SP303_PAT_F,  _sp303_interp8),
        29: (_SP303_PAT_F,  _sp303_interp8),
        30: (_SP303_PAT_F,  None),
        31: (_SP303_PAT_E,  _sp303_interp4),
        32: (_SP303_PAT_B4, _sp303_interp2), 33: (_SP303_PAT_B4, _sp303_interp2),
        34: (_SP303_PAT_B4, _sp303_interp2), 35: (_SP303_PAT_B4, _sp303_interp2),
        36: (_SP303_PAT_B4, _sp303_interp2)
    }
    
    if p not in pat_map:
        return [0] * 16
        
    pat_str, interp_func = pat_map[p]
    out, max_depth = _sp303_apply_pattern(block, pat_str)
    
    # Ensure max_depth is at least 1 to avoid division by zero/weird shifts
    max_depth = max(1, max_depth)
    shift = (23 - max_depth) - lut_val
    _sp303_shift_round(out, shift)
    if interp_func:
        interp_func(d0, out)
    elif p == 30:
        for i in range(0, 16, 2): out[i] <<= 1
    return out


def sp303_decode_sp0(path: str) -> List[int]:
    """Decode an SP0 file to a flat list of 16-bit PCM samples (32000 Hz native)."""
    file_size = os.path.getsize(path)
    samples: List[int] = []
    d0 = 0  # 24-bit internal predictor
    with open(path, 'rb') as f:
        for _ in range(file_size // 16):
            block = f.read(16)
            if len(block) < 16:
                break
            chunk = _sp303_decode_mt1(d0, block)
            chunk_16 = [max(-32768, min(32767, s >> 8)) for s in chunk]
            samples.extend(chunk_16)
            d0 = chunk[15]  # Preserve high-precision predictor
    return samples


def sp303_write_wav(f, num_samples: int, sample_rate: int, num_channels: int = 1) -> None:
    """Write a 16-bit PCM WAV header then expect the caller to write the sample data."""
    num_bytes = num_samples * 2 * num_channels
    f.write(b'RIFF'); f.write(struct.pack('<I', num_bytes + 36))
    f.write(b'WAVE'); f.write(b'fmt '); f.write(struct.pack('<I', 16))
    f.write(struct.pack('<H', 1))                          # PCM
    f.write(struct.pack('<H', num_channels))
    f.write(struct.pack('<I', sample_rate))
    f.write(struct.pack('<I', sample_rate * 2 * num_channels))
    f.write(struct.pack('<H', 2 * num_channels))
    f.write(struct.pack('<H', 16))                         # bits per sample
    f.write(b'data'); f.write(struct.pack('<I', num_bytes))


class SMPINFO:
    """
    Complete SMPINFO0.SP0 file handler
    
    Manages all sections:
    - 16 slot records
    - Pad mapping table
    - DSP command list
    - Reserved sections
    """
    
    def __init__(self):
        self.slots: List[SlotRecord] = []
        self.pad_mappings: List[PadMapping] = []
        self.dsp_commands: bytes = bytes(DSP_COMMANDS_SIZE)
        self.reserved_0x340: bytes = bytes(32)  # Section at 0x340
        self.reserved_0x360: bytes = bytes(32)  # Section at 0x360
        
        # Initialize with empty slots
        for i in range(SLOT_COUNT):
            self.slots.append(SlotRecord(
                slot_index=i,
                sample_length_bytes=0,
                loop_point_bytes=0,
                is_stereo=False
            ))
        
        # Initialize pad mappings (1:1 mapping by default)
        for i in range(SLOT_COUNT):
            self.pad_mappings.append(PadMapping(pad_index=i))
        
        # Initialize reserved sections with firmware defaults
        self.reserved_0x340 = b'\xff' * 32  # All 0xFF
        self.reserved_0x360 = b'\x00' * 32  # All 0x00
    
    def set_slot(self, slot_index: int, sample_length: int, is_stereo: bool = False,
                 loop_point: Optional[int] = None, is_gate: bool = False, is_loop: bool = False,
                 is_reverse: bool = False):
        """
        Set a slot's parameters

        Args:
            slot_index: Slot number (0-15)
            sample_length: Length of sample data in bytes
            is_stereo: True for stereo (L+R files), False for mono
            loop_point: Loop point in bytes, or None for no loop (one-shot)
            is_gate: True to enable gate playback mode
            is_loop: True to enable loop playback mode
            is_reverse: True to enable reverse playback mode
        """
        if not 0 <= slot_index < SLOT_COUNT:
            raise ValueError(f"Slot index must be 0-15, got {slot_index}")

        if loop_point is None:
            loop_point = sample_length

        self.slots[slot_index] = SlotRecord(
            slot_index=slot_index,
            sample_length_bytes=sample_length,
            loop_point_bytes=loop_point,
            is_stereo=is_stereo,
            is_gate=is_gate,
            is_loop=is_loop,
            is_reverse=is_reverse,
        )
    
    def clear_slot(self, slot_index: int):
        """Clear a slot (make it empty)"""
        if not 0 <= slot_index < SLOT_COUNT:
            raise ValueError(f"Slot index must be 0-15, got {slot_index}")
        
        self.slots[slot_index] = SlotRecord(
            slot_index=slot_index,
            sample_length_bytes=0,
            loop_point_bytes=0,
            is_stereo=False
        )
    
    def to_bytes(self) -> bytes:
        """Generate complete 65536-byte SMPINFO0.SP0 file"""
        buffer = bytearray(FILE_SIZE)
        
        # Section 1: Slot records (0x0000 - 0x02FF)
        for i, slot in enumerate(self.slots):
            offset = SLOT_RECORDS_START + (i * SLOT_RECORDS_SIZE)
            buffer[offset:offset + SLOT_RECORDS_SIZE] = slot.to_bytes()
        
        # Section 2: Pad mapping table (0x0300 - 0x033F)
        for i, mapping in enumerate(self.pad_mappings):
            offset = PAD_MAPPING_START + (i * 4)
            buffer[offset:offset + 4] = mapping.to_bytes()
        
        # Section 3: Reserved (0x0340 - 0x035F) - all 0xFF
        buffer[0x0340:0x0360] = self.reserved_0x340
        
        # Section 4: Reserved (0x0360 - 0x037F) - all 0x00
        buffer[0x0360:0x0380] = self.reserved_0x360
        
        # Section 5: DSP commands (0x0380 - 0x03FF)
        buffer[DSP_COMMANDS_START:DSP_COMMANDS_START + DSP_COMMANDS_SIZE] = self.dsp_commands
        
        # Section 6: Unused (0x0400 - 0xFFFF) - all 0xFF
        buffer[0x0400:] = b'\xff' * (FILE_SIZE - 0x0400)
        
        return bytes(buffer)
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'SMPINFO':
        """Parse complete SMPINFO0.SP0 file"""
        if len(data) != FILE_SIZE:
            raise ValueError(f"SMPINFO0.SP0 must be {FILE_SIZE} bytes, got {len(data)}")

        # The SP-303 uses a sequential write log: each settings change appends a new
        # 0x400-byte block. The current state is always in the last written block
        # (first block whose opening 4 bytes are 0xFFFFFFFF is unwritten; the one
        # before it is current). Files produced by Dr. Sidekick only write block 0,
        # so this logic is safe for both origins.
        block_size = 0x400
        num_blocks = FILE_SIZE // block_size
        last_written = 0
        for b in range(num_blocks):
            if data[b * block_size : b * block_size + 4] == b'\xff\xff\xff\xff':
                break
            last_written = b
        base = last_written * block_size

        smpinfo = cls()

        # Parse slot records
        smpinfo.slots = []
        for i in range(SLOT_COUNT):
            offset = base + SLOT_RECORDS_START + (i * SLOT_RECORDS_SIZE)
            slot_data = data[offset:offset + SLOT_RECORDS_SIZE]
            smpinfo.slots.append(SlotRecord.from_bytes(i, slot_data))

        # Parse pad mappings
        smpinfo.pad_mappings = []
        for i in range(SLOT_COUNT):
            offset = base + PAD_MAPPING_START + (i * 4)
            mapping_data = data[offset:offset + 4]
            smpinfo.pad_mappings.append(PadMapping.from_bytes(mapping_data))

        # Parse reserved sections
        smpinfo.reserved_0x340 = data[base + 0x0340 : base + 0x0360]
        smpinfo.reserved_0x360 = data[base + 0x0360 : base + 0x0380]

        # Parse DSP commands
        smpinfo.dsp_commands = data[base + DSP_COMMANDS_START : base + DSP_COMMANDS_START + DSP_COMMANDS_SIZE]

        return smpinfo
    
    @classmethod
    def from_file(cls, filepath: Path) -> 'SMPINFO':
        """Load SMPINFO0.SP0 from file"""
        with open(filepath, 'rb') as f:
            return cls.from_bytes(f.read())
    
    def save(self, filepath: Path):
        """Save SMPINFO0.SP0 to file"""
        with open(filepath, 'wb') as f:
            f.write(self.to_bytes())
    
    def get_bank_slots(self, bank: Bank) -> List[SlotRecord]:
        """Get all slots for a specific bank"""
        if bank == SampleBank.C:
            return self.slots[0:8]
        else:  # SampleBank.D
            return self.slots[8:16]
    
    def analyze(self) -> Dict:
        """Analyze and return file statistics"""
        stats = {
            'total_slots': SLOT_COUNT,
            'populated_slots': sum(1 for s in self.slots if not s.is_empty),
            'empty_slots': sum(1 for s in self.slots if s.is_empty),
            'mono_slots': sum(1 for s in self.slots if not s.is_empty and not s.is_stereo),
            'stereo_slots': sum(1 for s in self.slots if not s.is_empty and s.is_stereo),
            'bank_c_populated': sum(1 for s in self.slots[0:8] if not s.is_empty),
            'bank_d_populated': sum(1 for s in self.slots[8:16] if not s.is_empty),
            'total_sample_bytes': sum(s.sample_length_bytes for s in self.slots),
        }
        return stats
    
    def __repr__(self):
        stats = self.analyze()
        return (f"SMPINFO0.SP0: {stats['populated_slots']}/{SLOT_COUNT} slots populated "
                f"(Bank C: {stats['bank_c_populated']}/8, Bank D: {stats['bank_d_populated']}/8)")



# Constants
MIN_SAMPLE_DURATION_MS = 110.0  # SP-303 firmware requirement
SAMPLE_RATE = 44100
BIT_DEPTH = 16
SAMPLE_FILE_SIZE = 16384  # 16KB per .SP0 file
SLOT_COUNT = 16


class SourceType(Enum):
    """Type of sample source"""
    ARCHIVED_SP0 = "archived"  # Existing .SP0 file
    WAV_FILE = "wav"           # WAV file to be converted by SP-303
    AIFF_FILE = "aiff"         # AIFF file to be converted by SP-303
    EMPTY = "empty"            # Empty slot


@dataclass
class SampleSource:
    """
    Represents a sample source for a slot
    """
    slot_index: int  # 0-15 (0-7=Bank C, 8-15=Bank D)
    source_type: SourceType
    source_path: Optional[Path] = None  # Path to source file
    sample_length: int = 0  # Length in bytes (for .SP0 files)
    is_stereo: bool = False
    
    @property
    def bank(self) -> SampleBank:
        return SampleBank.C if self.slot_index < 8 else SampleBank.D
    
    @property
    def pad(self) -> int:
        return (self.slot_index % 8) + 1
    
    @property
    def bank_pad_str(self) -> str:
        return f"Bank {self.bank.value}, Pad {self.pad}"
    
    def __repr__(self):
        type_str = self.source_type.value
        if self.source_type == SourceType.EMPTY:
            return f"Slot {self.slot_index:2d} ({self.bank_pad_str}): Empty"
        elif self.source_type == SourceType.ARCHIVED_SP0:
            stereo = "Stereo" if self.is_stereo else "Mono"
            return f"Slot {self.slot_index:2d} ({self.bank_pad_str}): {stereo} .SP0 ({self.sample_length}B) <- {self.source_path.name}"
        else:  # WAV or AIFF
            return f"Slot {self.slot_index:2d} ({self.bank_pad_str}): {type_str.upper()} import <- {self.source_path.name}"


class SP303CardPrep:
    """
    Main card preparation manager
    """
    
    def __init__(self):
        self.sources: List[SampleSource] = []
        
        # Initialize with empty slots
        for i in range(SLOT_COUNT):
            self.sources.append(SampleSource(
                slot_index=i,
                source_type=SourceType.EMPTY
            ))
    
    def assign_archived_sp0(self, slot_index: int, sp0_file: Path, 
                           is_stereo: bool = False):
        """
        Assign an archived .SP0 file to a slot
        
        Args:
            slot_index: Target slot (0-15)
            sp0_file: Path to existing .SP0 file
            is_stereo: True if stereo (requires both L and R files)
        """
        if not 0 <= slot_index < SLOT_COUNT:
            raise ValueError(f"Slot must be 0-15, got {slot_index}")
        
        if not sp0_file.exists():
            raise FileNotFoundError(f"SP0 file not found: {sp0_file}")
        
        # Get file size
        file_size = sp0_file.stat().st_size
        
        # For stereo, check if R file exists
        if is_stereo:
            r_file = sp0_file.parent / sp0_file.name.replace('L.SP0', 'R.SP0')
            if not r_file.exists():
                raise FileNotFoundError(f"Stereo R file not found: {r_file}")
        
        self.sources[slot_index] = SampleSource(
            slot_index=slot_index,
            source_type=SourceType.ARCHIVED_SP0,
            source_path=sp0_file,
            sample_length=file_size,
            is_stereo=is_stereo
        )
    
    def assign_wav_for_import(self, slot_index: int, wav_file: Path):
        """
        Assign a WAV file for SP-303 import
        
        Note: The WAV will be copied to the card for device-side conversion.
        The slot will be marked for WAV import.
        
        Args:
            slot_index: Target slot (0-15)
            wav_file: Path to WAV file
        """
        if not 0 <= slot_index < SLOT_COUNT:
            raise ValueError(f"Slot must be 0-15, got {slot_index}")
        
        if not wav_file.exists():
            raise FileNotFoundError(f"WAV file not found: {wav_file}")
        
        # Validate WAV file
        with wave.open(str(wav_file), 'rb') as w:
            channels = w.getnchannels()
            if channels > 2:
                raise ValueError(f"WAV has {channels} channels, max is 2 (stereo)")
        
        self.sources[slot_index] = SampleSource(
            slot_index=slot_index,
            source_type=SourceType.WAV_FILE,
            source_path=wav_file,
            is_stereo=(channels == 2)
        )
    
    def assign_aiff_for_import(self, slot_index: int, aiff_file: Path):
        """
        Assign an AIFF file for SP-303 import
        
        Args:
            slot_index: Target slot (0-15)
            aiff_file: Path to AIFF file
        """
        if not 0 <= slot_index < SLOT_COUNT:
            raise ValueError(f"Slot must be 0-15, got {slot_index}")
        
        if not aiff_file.exists():
            raise FileNotFoundError(f"AIFF file not found: {aiff_file}")
        
        # Basic validation (full AIFF parsing would be more complex)
        # For now, just mark it
        self.sources[slot_index] = SampleSource(
            slot_index=slot_index,
            source_type=SourceType.AIFF_FILE,
            source_path=aiff_file
        )
    
    def clear_slot(self, slot_index: int):
        """Clear a slot (make it empty)"""
        if not 0 <= slot_index < SLOT_COUNT:
            raise ValueError(f"Slot must be 0-15, got {slot_index}")
        
        self.sources[slot_index] = SampleSource(
            slot_index=slot_index,
            source_type=SourceType.EMPTY
        )
    
    def validate_wav_import_banks(self) -> Tuple[bool, str]:
        """
        Validate that WAV imports follow SP-303 bank isolation rule
        
        Returns:
            (is_valid, error_message)
        """
        # Find which banks have WAV imports
        banks_with_wavs = set()
        
        for source in self.sources:
            if source.source_type in [SourceType.WAV_FILE, SourceType.AIFF_FILE]:
                banks_with_wavs.add(source.bank)
        
        # SP-303 can only import WAVs into one bank at a time
        if len(banks_with_wavs) > 1:
            return False, (
                "SP-303 can only import WAV/AIFF files into ONE bank per operation.\n"
                f"Found WAV/AIFF imports in multiple banks: {', '.join(b.value for b in banks_with_wavs)}\n"
                "Solution: Prepare Bank C first, then Bank D in a separate operation."
            )
        
        return True, ""
    
    def prepare_card(self, output_dir: Path, create_smpinfo: bool = True) -> Dict:
        """
        Prepare complete SmartMedia card layout
        
        Args:
            output_dir: Directory to write card files
            create_smpinfo: If True, generate SMPINFO0.SP0 for archived samples
        
        Returns:
            Dictionary with preparation results
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        
        results = {
            'archived_sp0_copied': [],
            'wav_prepared': [],
            'aiff_prepared': [],
            'smpinfo_created': False,
            'warnings': [],
            'errors': []
        }
        
        # Validate WAV import banks
        valid, error = self.validate_wav_import_banks()
        if not valid:
            results['warnings'].append(error)
            results['warnings'].append("Files will still be created, but you'll need to import in separate operations.")
        
        # Process archived .SP0 files
        smpinfo = SMPINFO()

        def same_file(path_a: Path, path_b: Path) -> bool:
            try:
                return path_a.resolve() == path_b.resolve()
            except Exception:
                return str(path_a) == str(path_b)

        archived_copy_ops: List[Tuple[Path, Path, str]] = []
        for source in self.sources:
            if source.source_type == SourceType.ARCHIVED_SP0:
                target_name = f"SMP{source.slot_index:04X}L.SP0"
                target_path = output_dir / target_name
                archived_copy_ops.append((source.source_path, target_path, target_name))
                
                if source.is_stereo:
                    r_source = source.source_path.parent / source.source_path.name.replace('L.SP0', 'R.SP0')
                    r_target = output_dir / f"SMP{source.slot_index:04X}R.SP0"
                    archived_copy_ops.append((r_source, r_target, r_target.name))
                
                # Update SMPINFO
                smpinfo.set_slot(source.slot_index, source.sample_length, source.is_stereo)

        if archived_copy_ops:
            import tempfile
            stage_bytes_needed = sum(source_path.stat().st_size for source_path, _, _ in archived_copy_ops)
            free_bytes = shutil.disk_usage(output_dir).free
            if free_bytes < stage_bytes_needed:
                raise RuntimeError(
                    "Not enough free space on output device. "
                    f"Required: {stage_bytes_needed:,} bytes, available: {free_bytes:,} bytes."
                )

            stage_dir = Path(tempfile.mkdtemp(prefix="dr_sidekick_stage_"))
            try:
                staged_ops: List[Tuple[Path, Path, str]] = []
                for idx, (source_path, target_path, target_name) in enumerate(archived_copy_ops):
                    if not source_path.exists():
                        raise FileNotFoundError(f"SP0 file not found: {source_path}")
                    staged_path = stage_dir / f"{idx:04d}_{source_path.name}"
                    shutil.copyfile(source_path, staged_path)
                    staged_ops.append((staged_path, target_path, target_name))

                overwritten_count = 0
                for staged_path, target_path, target_name in staged_ops:
                    if target_path.exists():
                        overwritten_count += 1
                    shutil.copyfile(staged_path, target_path)
                    results['archived_sp0_copied'].append(target_name)
            finally:
                shutil.rmtree(stage_dir, ignore_errors=True)

            if overwritten_count > 0:
                results['warnings'].append(
                    f"Overwrote {overwritten_count} existing SP0 file(s) on card."
                )
        
        # Process WAV files
        wav_sources = [s for s in self.sources if s.source_type == SourceType.WAV_FILE]
        if wav_sources:
            # Prepare WAV files with correct naming for SP-303 import
            for i, source in enumerate(wav_sources, start=1):
                target_name = f"SMPL{i:04d}.WAV"
                target_path = output_dir / target_name
                
                # Process WAV (pad if needed, convert stereo to mono if needed)
                conversion_actions = self._prepare_wav(source.source_path, target_path)
                result_entry = {
                    'file': target_name,
                    'source_file': source.source_path.name,
                    'slot': source.slot_index,
                    'bank_pad': source.bank_pad_str,
                }
                if conversion_actions:
                    result_entry['conversion_summary'] = (
                        f"Converted {source.source_path.name} -> {target_name}, {', '.join(conversion_actions)}"
                    )
                results['wav_prepared'].append(result_entry)
            
            results['warnings'].append(
                f"â†’ WAV files ready for import into Bank {wav_sources[0].bank.value}. Copy to card and run SP-303 import."
            )
        
        # Process AIFF files
        aiff_sources = [s for s in self.sources if s.source_type == SourceType.AIFF_FILE]
        if aiff_sources:
            for i, source in enumerate(aiff_sources, start=1):
                target_name = f"SMPL{i:04d}.AIF"
                target_path = output_dir / target_name
                
                shutil.copy2(source.source_path, target_path)
                results['aiff_prepared'].append({
                    'file': target_name,
                    'slot': source.slot_index,
                    'bank_pad': source.bank_pad_str
                })
            
            results['warnings'].append(
                f"AIFF files prepared for import into {aiff_sources[0].bank.value}. "
                "Run SP-303 import after copying to card."
            )
        
        # Create SMPINFO0.SP0 if requested and we have archived samples
        if create_smpinfo and results['archived_sp0_copied']:
            smpinfo_path = output_dir / "SMPINFO0.SP0"
            smpinfo.save(smpinfo_path)
            results['smpinfo_created'] = True
        
        return results
    
    def _prepare_wav(self, source: Path, target: Path) -> List[str]:
        """
        Prepare WAV file for SP-303 import
        
        - Convert 24-bit to 16-bit if needed
        - Ensure minimum duration (pad if needed)
        - Validate format
        """
        actions: List[str] = []
        with wave.open(str(source), 'rb') as w:
            frames = w.readframes(w.getnframes())
            
            # Convert to samples
            sample_width = w.getsampwidth()
            n_channels = w.getnchannels()
            n_frames = w.getnframes()
            sample_rate = w.getframerate()
            
            duration_ms = (n_frames / sample_rate) * 1000.0
            
            # Normalize to complete frames to avoid partial/truncated buffer errors.
            frame_width = sample_width * n_channels
            complete_frame_count = len(frames) // frame_width
            if complete_frame_count != n_frames:
                frames = frames[:complete_frame_count * frame_width]
                n_frames = complete_frame_count

            # Convert 24-bit to 16-bit if needed.
            if sample_width == 3:
                samples_24bit = []
                total_samples = len(frames) // 3
                for i in range(total_samples):
                    offset = i * 3
                    val = int.from_bytes(frames[offset:offset + 3], byteorder='little', signed=True)
                    val_16bit = max(-32768, min(32767, val >> 8))
                    samples_24bit.append(val_16bit)

                frames = struct.pack(f"<{len(samples_24bit)}h", *samples_24bit)
                sample_width = 2
                n_frames = len(samples_24bit) // n_channels
                actions.append("24-bit -> 16-bit")
            
            # Check if padding needed
            if duration_ms < MIN_SAMPLE_DURATION_MS:
                # Calculate padding needed
                min_frames = int((MIN_SAMPLE_DURATION_MS / 1000.0) * sample_rate)
                pad_frames = min_frames - n_frames
                
                # Create silence padding
                silence = b'\x00' * (pad_frames * sample_width * n_channels)
                frames = frames + silence
                n_frames = min_frames
                actions.append(f"padded to {MIN_SAMPLE_DURATION_MS:.0f}ms minimum")
                
            
        # Write output WAV
        with wave.open(str(target), 'wb') as w:
            w.setnchannels(n_channels)
            w.setsampwidth(2)  # Always 16-bit output
            w.setframerate(sample_rate)
            w.writeframes(frames)
        return actions
    
    def generate_manifest(self) -> Dict:
        """Generate human-readable manifest of card layout"""
        manifest = {
            'bank_c': [],
            'bank_d': [],
            'summary': {
                'total_slots': SLOT_COUNT,
                'populated': sum(1 for s in self.sources if s.source_type != SourceType.EMPTY),
                'empty': sum(1 for s in self.sources if s.source_type == SourceType.EMPTY),
                'archived': sum(1 for s in self.sources if s.source_type == SourceType.ARCHIVED_SP0),
                'wav_import': sum(1 for s in self.sources if s.source_type == SourceType.WAV_FILE),
                'aiff_import': sum(1 for s in self.sources if s.source_type == SourceType.AIFF_FILE),
            }
        }
        
        for source in self.sources:
            slot_info = {
                'slot': source.slot_index,
                'pad': source.pad,
                'type': source.source_type.value,
                'source': str(source.source_path) if source.source_path else None,
                'stereo': source.is_stereo
            }
            
            if source.slot_index < 8:
                manifest['bank_c'].append(slot_info)
            else:
                manifest['bank_d'].append(slot_info)
        
        return manifest
    
    def save_project(self, filepath: Path):
        """Save project configuration to JSON"""
        project = {
            'version': '1.0',
            'sources': []
        }
        
        for source in self.sources:
            if source.source_type != SourceType.EMPTY:
                project['sources'].append({
                    'slot': source.slot_index,
                    'type': source.source_type.value,
                    'path': str(source.source_path) if source.source_path else None,
                    'stereo': source.is_stereo,
                    'length': source.sample_length
                })
        
        with open(filepath, 'w') as f:
            json.dump(project, f, indent=2)
    
    @classmethod
    def load_project(cls, filepath: Path) -> 'SP303CardPrep':
        """Load project configuration from JSON"""
        with open(filepath, 'r') as f:
            project = json.load(f)
        
        prep = cls()
        
        for source_data in project['sources']:
            slot = source_data['slot']
            source_type = SourceType(source_data['type'])
            path = Path(source_data['path']) if source_data['path'] else None
            
            if source_type == SourceType.ARCHIVED_SP0:
                prep.assign_archived_sp0(slot, path, source_data.get('stereo', False))
            elif source_type == SourceType.WAV_FILE:
                prep.assign_wav_for_import(slot, path)
            elif source_type == SourceType.AIFF_FILE:
                prep.assign_aiff_for_import(slot, path)
        
        return prep


SP303_PADS = [f"C{i}" for i in range(1, 9)] + [f"D{i}" for i in range(1, 9)]


@dataclass
class VirtualCard:
    name: str
    device: str = "SP-303"
    author: str = ""
    categories: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    pad_notes: Dict[str, str] = field(default_factory=dict)
    write_protect: bool = False
    created: str = ""
    modified: str = ""
    path: Path = field(default_factory=Path)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "device": self.device, "author": self.author,
            "categories": self.categories, "tags": self.tags,
            "pad_notes": self.pad_notes,
            "write_protect": self.write_protect,
            "created": self.created, "modified": self.modified,
        }

    @classmethod
    def from_dict(cls, d: dict, path: Path) -> "VirtualCard":
        return cls(
            name=d.get("name", path.name), device=d.get("device", "SP-303"),
            author=d.get("author", ""), categories=d.get("categories", []),
            tags=d.get("tags", []), pad_notes=d.get("pad_notes", {}),
            write_protect=d.get("write_protect", False),
            created=d.get("created", ""), modified=d.get("modified", ""),
            path=path,
        )


class SmartMediaLibrary:
    def __init__(self, root: Path):
        self.root = root
        self.cards_dir = root / "Cards"
        self.backup_dir = root.parent / "Backup"
        self.incoming_dir = self.cards_dir / "BOSS DATA_INCOMING"
        self.outgoing_dir = self.cards_dir / "BOSS DATA_OUTGOING"

    def ensure_dirs(self):
        for d in (self.cards_dir, self.backup_dir, self.incoming_dir, self.outgoing_dir):
            d.mkdir(parents=True, exist_ok=True)

    def list_cards(self) -> List["VirtualCard"]:
        cards = []
        if not self.cards_dir.exists():
            return cards
        for card_dir in sorted(self.cards_dir.iterdir()):
            if not card_dir.is_dir():
                continue
            json_path = card_dir / "card.json"
            if json_path.exists():
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    cards.append(VirtualCard.from_dict(data, card_dir))
                except Exception:
                    continue
        return cards

    def get_card(self, name: str) -> Optional["VirtualCard"]:
        card_dir = self.cards_dir / name
        json_path = card_dir / "card.json"
        if not json_path.exists():
            return None
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return VirtualCard.from_dict(data, card_dir)
        except Exception:
            return None

    def create_card(self, card: "VirtualCard"):
        card_dir = self.cards_dir / card.name
        card_dir.mkdir(parents=True, exist_ok=True)
        card.path = card_dir
        if not card.created:
            card.created = datetime.now().isoformat(timespec="seconds")
        card.modified = datetime.now().isoformat(timespec="seconds")
        with open(card_dir / "card.json", "w", encoding="utf-8") as f:
            json.dump(card.to_dict(), f, indent=2)

    def save_card(self, card: "VirtualCard"):
        card.modified = datetime.now().isoformat(timespec="seconds")
        with open(card.path / "card.json", "w", encoding="utf-8") as f:
            json.dump(card.to_dict(), f, indent=2)

    def rename_card(self, card: "VirtualCard", new_name: str):
        """Rename the card folder on disk and update card.name and card.path."""
        new_name = new_name.strip()
        if not new_name or new_name == card.name:
            return
        old_path = card.path
        new_path = self.cards_dir / new_name
        if new_path.exists():
            raise ValueError(f"A card named '{new_name}' already exists.")
        old_path.rename(new_path)
        card.name = new_name
        card.path = new_path
        log.info("Card renamed: %s → %s", old_path.name, new_name)

    def delete_card(self, name: str):
        card_dir = self.cards_dir / name
        if card_dir.exists():
            shutil.rmtree(card_dir)

    def backup_card_files(self, card_name: str) -> Path:
        """Copy SP0 files from card dir into Backup/{card_name}/."""
        card_dir = self.cards_dir / card_name
        dest = self.backup_dir / card_name
        dest.mkdir(parents=True, exist_ok=True)
        for f in sorted(card_dir.glob("*.SP0")):
            shutil.copy(f, dest / f.name)
        log.info("Card backup created: %s", dest)
        return dest

    def import_sp0_files(self, card_name: str, source_dir: Path, auto_backup: bool = False):
        """Copy SP0 files from source_dir directly into the card dir."""
        card_dir = self.cards_dir / card_name
        card_dir.mkdir(parents=True, exist_ok=True)
        if auto_backup and any(card_dir.glob("*.SP0")):
            self.backup_card_files(card_name)
        for f in sorted(source_dir.glob("*.SP0")):
            shutil.copy(f, card_dir / f.name)
        log.info("Imported SP0 files from %s to %s", source_dir, card_dir)

    def card_has_patterns(self, card_name: str) -> bool:
        """Return True if PTNINFO0.SP0 in the card dir has any active pattern slots."""
        ptninfo = self.cards_dir / card_name / "PTNINFO0.SP0"
        if not ptninfo.exists():
            return False
        try:
            data = ptninfo.read_bytes()
            return any(data[i] == 0x04 for i in range(0, min(len(data), 64), 4))
        except Exception:
            return False

    def restore_card(self, card_name: str, target_dir: Path):
        """Copy SP0 files from card dir to target_dir (e.g. physical card)."""
        card_dir = self.cards_dir / card_name
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(card_dir.glob("*.SP0")):
            shutil.copy(f, target_dir / f.name)
        log.info("Restored %s to %s", card_name, target_dir)


class AssignmentSession:
    def __init__(self):
        self.prep = SP303CardPrep()

    def assign_wav(self, slot: int, wav_file: Path):
        self.prep.assign_wav_for_import(slot, wav_file)

    def assign_archived_sp0(self, slot: int, sp0_file: Path, stereo: bool):
        self.prep.assign_archived_sp0(slot, sp0_file, stereo)

    def clear_slot(self, slot: int):
        self.prep.clear_slot(slot)

    def prepare_card(self, output_dir: Path) -> Dict:
        return self.prep.prepare_card(output_dir)

    def describe_assignments(self) -> List[str]:
        lines: List[str] = []
        for slot, source in enumerate(self.prep.sources):
            if source.source_type == SourceType.EMPTY:
                continue
            bank = "C" if slot < 8 else "D"
            pad = (slot % 8) + 1
            lines.append(f"{slot:02d} ({bank}{pad}): {source.source_type.value} -> {source.source_path.name}")
        return lines


def parse_mpc1000_pgm(pgm_path: Path) -> dict:
    """Parse MPC1000 .pgm file, return {pad_index: sample_name} for assigned pads (0-63)."""
    with open(pgm_path, 'rb') as f:
        data = f.read()
    header = data[4:20].decode('ascii', errors='ignore')
    if 'MPC1000 PGM' not in header:
        raise ValueError(f"Not a valid MPC1000 PGM file (header: {header!r})")
    PAD_ENTRY_SIZE = 0xA4
    FIRST_SAMPLE_OFFSET = 0x18
    SAMPLE_NAME_SIZE = 16
    pads = {}
    for pad_index in range(64):
        offset = FIRST_SAMPLE_OFFSET + pad_index * PAD_ENTRY_SIZE
        if offset + SAMPLE_NAME_SIZE > len(data):
            break
        name = ''
        for byte in data[offset:offset + SAMPLE_NAME_SIZE]:
            if byte == 0:
                break
            if 32 <= byte <= 126:
                name += chr(byte)
        name = name.strip()
        if name:
            pads[pad_index] = name
    return pads


def find_wav_files(wav_dir: Path, recursive: bool = False) -> List[Path]:
    if recursive:
        wav_files = list(wav_dir.rglob("*.wav")) + list(wav_dir.rglob("*.WAV"))
    else:
        wav_files = list(wav_dir.glob("*.wav")) + list(wav_dir.glob("*.WAV"))
    return sorted(wav_files, key=lambda path: str(path).lower())


def quick_import(wav_dir: Path, output_dir: Path, groove_file: Optional[Path] = None) -> Dict:
    wav_files = find_wav_files(wav_dir, recursive=True)
    if not wav_files:
        raise ValueError(f"No WAV files found in {wav_dir} (searched recursively)")

    prep = SP303CardPrep()
    per_bank_limit = 8
    batches: List[List[Path]] = [
        wav_files[idx:idx + per_bank_limit] for idx in range(0, len(wav_files), per_bank_limit)
    ]
    use_batch_dirs = len(wav_files) > per_bank_limit

    assignments = []
    results = {
        "archived_sp0_copied": [],
        "wav_prepared": [],
        "aiff_prepared": [],
        "smpinfo_created": False,
        "warnings": [],
        "errors": [],
    }
    batch_dirs: List[str] = []

    for batch_index, batch_files in enumerate(batches, start=1):
        if use_batch_dirs:
            batch_dir = output_dir / f"BANK_LOAD_{batch_index:02d}"
            batch_dir.mkdir(parents=True, exist_ok=True)
            batch_dirs.append(batch_dir.name)
        else:
            batch_dir = output_dir

        for smpl_index, wav_file in enumerate(batch_files, start=1):
            target_name = f"SMPL{smpl_index:04d}.WAV"
            target_path = batch_dir / target_name
            conversion_actions = prep._prepare_wav(wav_file, target_path)
            display_target = f"{batch_dir.name}/{target_name}" if use_batch_dirs else target_name

            entry = {
                "file": display_target,
                "source_file": wav_file.name,
            }
            if conversion_actions:
                entry["conversion_summary"] = (
                    f"Converted {wav_file.name} -> {display_target}, {', '.join(conversion_actions)}"
                )
            results["wav_prepared"].append(entry)

            assignments.append(
                {
                    "batch": batch_index,
                    "slot_in_batch": smpl_index,
                    "file": wav_file.name,
                    "target": display_target,
                }
            )

    if len(batches) > 1:
        results["warnings"].append(
            "Prepared multiple bank-load folders. Import one folder at a time on the SP-303."
        )

    if groove_file:
        apply_groove_to_card(output_dir, groove_file, 0, 0)
    return {
        "results": results,
        "assignments": assignments,
        "total_found": len(wav_files),
        "imported_count": len(wav_files),
        "skipped_count": 0,
        "batch_count": len(batches),
        "batch_dirs": batch_dirs,
    }


def apply_groove_to_card(card_dir: Path, groove_file: Path, pattern_slot: int, target_pad: int):
    ptninfo_path = card_dir / "PTNINFO0.SP0"
    ptndata_path = card_dir / "PTNDATA0.SP0"

    ptninfo = PTNInfo.from_file(ptninfo_path) if ptninfo_path.exists() else PTNInfo()
    ptndata = PTNData.from_file(ptndata_path) if ptndata_path.exists() else PTNData()

    groove = GrooveTiming.from_midi(groove_file)
    apply_groove_to_slot(
        groove=groove,
        target_pad=target_pad,
        slot_index=pattern_slot,
        ptninfo=ptninfo,
        ptndata=ptndata,
        quantize="OFF",
    )

    ptninfo.save(ptninfo_path)
    ptndata.save(ptndata_path)


def analyze_existing_card(smpinfo_path: Path) -> Dict:
    smpinfo = SMPINFO.from_file(smpinfo_path)
    sample_stats = smpinfo.analyze()

    sample_slots = [str(slot) for slot in smpinfo.slots if not slot.is_empty]

    ptninfo_path = smpinfo_path.parent / "PTNINFO0.SP0"
    pattern_slots: List[str] = []
    active_count = 0
    if ptninfo_path.exists():
        ptninfo = PTNInfo.from_file(ptninfo_path)
        active_count = sum(1 for slot in ptninfo.slots if slot.has_pattern)
        for slot in ptninfo.slots:
            if slot.has_pattern:
                pattern_slots.append(
                    f"Slot {slot.slot_index:2d} (Bank {slot.bank.value} Pad {slot.pad}): {slot.quantize}"
                )

    return {
        "sample_stats": sample_stats,
        "sample_slots": sample_slots,
        "pattern_active_count": active_count,
        "pattern_slots": pattern_slots,
        "ptninfo_exists": ptninfo_path.exists(),
    }


def archive_card_as_song(
    card_dir: Path,
    song_name: str,
    artist: str = "Unknown",
    description: str = "",
    destination_root: Optional[Path] = None,
) -> Dict:
    smpinfo_path = card_dir / "SMPINFO0.SP0"
    if not smpinfo_path.exists():
        raise ValueError(f"No SMPINFO0.SP0 found in {card_dir}")

    destination_root = destination_root or (PROJECT_ROOT / "SmartMedia-Library" / "Cards")
    safe_name = song_name.replace(" ", "-").replace("/", "-")
    song_dir = destination_root / safe_name
    song_dir.mkdir(parents=True, exist_ok=True)

    copied_files: List[str] = []
    for sp0_file in sorted(card_dir.glob("*.SP0")):
        shutil.copy2(sp0_file, song_dir / sp0_file.name)
        copied_files.append(sp0_file.name)

    smpinfo = SMPINFO.from_file(smpinfo_path)
    samples = []
    for slot in smpinfo.slots:
        if slot.is_empty:
            continue
        bank = "C" if slot.slot_index < 8 else "D"
        pad = (slot.slot_index % 8) + 1
        samples.append(
            {
                "slot": slot.slot_index,
                "bank": bank,
                "pad": pad,
                "files": slot.sample_filenames,
                "stereo": slot.is_stereo,
            }
        )

    has_patterns = (card_dir / "PTNINFO0.SP0").exists() and (card_dir / "PTNDATA0.SP0").exists()

    pack_data = {
        "format": "sp303-pack",
        "version": "2.0",
        "type": "song",
        "title": song_name,
        "artist": artist or "Unknown",
        "description": description or "Archived from SmartMedia card",
        "metadata": {
            "archived_date": datetime.now().isoformat(timespec="seconds"),
            "sample_count": len(samples),
            "has_patterns": has_patterns,
        },
        "banks": {
            "encoding": "rdac",
            "samples": samples,
        },
    }

    if has_patterns:
        pack_data["patterns"] = {"files": ["PTNINFO0.SP0", "PTNDATA0.SP0"]}

    pack_json_path = song_dir / "pack.json"
    with open(pack_json_path, "w", encoding="utf-8") as handle:
        json.dump(pack_data, handle, indent=2)

    return {
        "song_dir": song_dir,
        "copied_files": copied_files,
        "sample_count": len(samples),
        "has_patterns": has_patterns,
        "pack_data": pack_data,
    }


def load_song_pack(pack_dir: Path, output_dir: Path) -> Dict:
    manifest_path = pack_dir / "pack.json"
    if not manifest_path.exists():
        raise ValueError(f"No pack.json found in {pack_dir}")

    with open(manifest_path, "r", encoding="utf-8") as handle:
        pack_data = json.load(handle)

    prep = SP303CardPrep()
    loaded_items: List[str] = []

    banks = pack_data.get("banks", {})
    for bank_name, bank_info in banks.items():
        encoding = bank_info.get("encoding", "wav")
        bank_dir = pack_dir / "banks" / bank_name

        if encoding == "wav":
            wav_dir = bank_dir / "wav"
            if not wav_dir.exists():
                continue
            wav_files = sorted(wav_dir.glob("*.wav"))
            base_slot = 0 if bank_name in {"A", "C"} else 8
            for offset, wav in enumerate(wav_files[:8]):
                slot = base_slot + offset
                prep.assign_wav_for_import(slot, wav)
                loaded_items.append(f"{wav.name} -> slot {slot}")

        elif encoding == "rdac":
            rdac_dir = bank_dir / "rdac"
            if not rdac_dir.exists():
                continue
            for sp0_file in sorted(rdac_dir.glob("*.SP0")):
                slot_hex = sp0_file.stem[3:7]
                try:
                    slot = int(slot_hex, 16)
                except ValueError:
                    continue
                stereo_matches = list(rdac_dir.glob(f"SMP{slot_hex}*.SP0"))
                is_stereo = len(stereo_matches) > 1
                prep.assign_archived_sp0(slot, sp0_file, is_stereo)
                loaded_items.append(f"{sp0_file.name} -> slot {slot}")

    results = prep.prepare_card(output_dir)

    copied_patterns: List[str] = []
    patterns_dir = pack_dir / "patterns"
    if patterns_dir.exists():
        for ptn_file in sorted(patterns_dir.glob("PTN*.SP0")):
            shutil.copy2(ptn_file, output_dir / ptn_file.name)
            copied_patterns.append(ptn_file.name)

    return {
        "results": results,
        "loaded_items": loaded_items,
        "copied_patterns": copied_patterns,
        "pack_title": pack_data.get("title", pack_dir.name),
    }


# Constants
INTERNAL_PPQN = 96
SLOT_COUNT = 16
DEFAULT_PATTERN_LENGTH_BARS = 4  # Default pattern length
MAX_PATTERN_LENGTH_BARS = 99  # SP-303 hardware maximum
TUPLE_ZONE_MAX_BYTES = 0x272 - 0x70  # PTNDATA event payload capacity per pattern slot
TUPLE_ZONE_SENTINEL_BYTES = 6  # Reserve one fill/end tuple so decoding stops inside the tuple zone.
APP_VERSION = "0.5.0"


def load_midi_notes_by_channel(midi_path: str) -> Tuple[Dict[int, List[Tuple[int, int, int]]], int]:
    """Load MIDI note-ons grouped by channel (1-16)."""
    channel_notes: Dict[int, List[Tuple[int, int, int]]] = {ch: [] for ch in range(1, 17)}

    def read_varlen_local(handle) -> int:
        value = 0
        while True:
            b = handle.read(1)
            if not b:
                raise EOFError("Unexpected end of file")
            b = b[0]
            value = (value << 7) | (b & 0x7F)
            if not (b & 0x80):
                return value

    with open(midi_path, "rb") as handle:
        if handle.read(4) != b"MThd":
            raise ValueError("Not a MIDI file")

        header_len = struct.unpack(">I", handle.read(4))[0]
        fmt, ntrks, ppqn = struct.unpack(">HHH", handle.read(6))
        handle.read(header_len - 6)

        if fmt != 0:
            raise ValueError("Only MIDI format 0 supported")
        if ntrks < 1:
            raise ValueError("MIDI file has no tracks")

        if handle.read(4) != b"MTrk":
            raise ValueError("Missing track chunk")

        track_len = struct.unpack(">I", handle.read(4))[0]
        track_end = handle.tell() + track_len

        abs_tick = 0
        running_status = None

        while handle.tell() < track_end:
            delta = read_varlen_local(handle)
            abs_tick += delta

            status = handle.read(1)[0]
            if status < 0x80:
                handle.seek(-1, 1)
                status = running_status
            else:
                running_status = status

            if status == 0xFF:
                meta_type = handle.read(1)[0]
                length = read_varlen_local(handle)
                handle.read(length)
                if meta_type == 0x2F:
                    break
                continue

            if status & 0xF0 == 0x90:
                note = handle.read(1)[0]
                vel = handle.read(1)[0]
                if vel > 0:
                    channel = (status & 0x0F) + 1
                    channel_notes[channel].append((abs_tick, note, vel))
            elif status & 0xF0 == 0x80:
                handle.read(2)
            else:
                if status & 0xF0 in (0xC0, 0xD0):
                    handle.read(1)
                else:
                    handle.read(2)

    return channel_notes, ppqn
