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
PTNINFO_SIZE = 64
PTNDATA_SIZE = 65536
SLOT_COUNT = 16
SLOT_SIZE = 0x400
INTERNAL_PPQN = 96

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
                official_template = Path(__file__).parent / 'PTNDATA_INIT_OFFICIAL.bin'
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
        if data_offset + len(serialized) > tuple_zone_end:
            raise ValueError(
                f"Pattern too large for tuple zone: {len(serialized)} bytes (max {tuple_zone_end - data_offset})"
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
    params_word: int = SP303_DEFAULT_PARAMS  # 16-bit clock divider; sample_rate = 37_500_000 / params_word
    is_gate: bool = False              # Gate playback mode (byte 0x25)
    is_loop: bool = False              # Loop playback mode (byte 0x26)
    is_reverse: bool = False           # Reverse playback mode (byte 0x27)
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
        
        # Bytes 32-35: Constant
        record[32:36] = TEMPLATE_BYTES_32_35
        
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
        is_stereo = data[36] == 0x01
        is_gate = data[37] == 0x01
        is_loop = data[38] == 0x01
        is_reverse = data[39] == 0x01

        return cls(
            slot_index=slot_index,
            sample_length_bytes=sample_length,
            loop_point_bytes=loop_point,
            is_stereo=is_stereo,
            params_word=params_word,
            is_gate=is_gate,
            is_loop=is_loop,
            is_reverse=is_reverse,
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
                    f"Overwrote {overwritten_count} existing SP0 file(s) on card. Snapshot taken before write."
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


@dataclass
class VirtualCard:
    name: str
    device: str = "SP-303"
    author: str = ""
    categories: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    write_protect: bool = False
    created: str = ""
    modified: str = ""
    path: Path = field(default_factory=Path)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "device": self.device, "author": self.author,
            "categories": self.categories, "tags": self.tags,
            "write_protect": self.write_protect,
            "created": self.created, "modified": self.modified,
        }

    @classmethod
    def from_dict(cls, d: dict, path: Path) -> "VirtualCard":
        return cls(
            name=d.get("name", path.name), device=d.get("device", "SP-303"),
            author=d.get("author", ""), categories=d.get("categories", []),
            tags=d.get("tags", []), write_protect=d.get("write_protect", False),
            created=d.get("created", ""), modified=d.get("modified", ""),
            path=path,
        )


class SmartMediaLibrary:
    def __init__(self, root: Path):
        self.root = root
        self.cards_dir = root / "Cards"
        self.autosaves_dir = root / "AutoSaves"
        self.incoming_dir = root / "BOSS DATA_INCOMING"
        self.outgoing_dir = root / "BOSS DATA_OUTGOING"

    def ensure_dirs(self):
        for d in (self.cards_dir, self.autosaves_dir, self.incoming_dir, self.outgoing_dir):
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

    def delete_card(self, name: str):
        card_dir = self.cards_dir / name
        if card_dir.exists():
            shutil.rmtree(card_dir)

    def list_snapshots(self, card_name: str) -> List[Path]:
        snap_dir = self.autosaves_dir / card_name
        if not snap_dir.exists():
            return []
        snaps = sorted([d for d in snap_dir.iterdir() if d.is_dir()], reverse=True)
        return snaps

    def create_snapshot(self, card_name: str, label: str = "") -> Path:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        folder_name = f"{ts}_{label}" if label else ts
        snap_dir = self.autosaves_dir / card_name / folder_name
        snap_dir.mkdir(parents=True, exist_ok=True)
        card_dir = self.cards_dir / card_name
        if card_dir.exists():
            for f in card_dir.iterdir():
                if f.is_file():
                    shutil.copy2(f, snap_dir / f.name)
        log.info("Snapshot created: %s", snap_dir)
        return snap_dir

    def rename_snapshot(self, card_name: str, snapshot_path: Path, new_label: str):
        ts = snapshot_path.name.split("_")[0] if "_" in snapshot_path.name else snapshot_path.name
        new_name = f"{ts}_{new_label}" if new_label else ts
        new_path = snapshot_path.parent / new_name
        snapshot_path.rename(new_path)
        return new_path

    def restore_snapshot(self, snapshot_path: Path, card: "VirtualCard", target_dir: Path):
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in snapshot_path.iterdir():
            if f.is_file():
                shutil.copy2(f, target_dir / f.name)


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
            lines.append(f"{slot:02d} ({bank}{pad}): {source.source_type.value} -> {source.path.name}")
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

    destination_root = destination_root or (Path(__file__).parent / "User-Library" / "Patterns")
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
APP_VERSION = "0.3.0"


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

# Pad configuration (All 4 banks: A, B, C, D)
PAD_ORDER = [
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,  # Bank A Pads 1-8
    0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,  # Bank B Pads 1-8
    0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17,  # Bank C Pads 1-8
    0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F   # Bank D Pads 1-8
]

PAD_NAMES = {
    0x00: "A1", 0x01: "A2", 0x02: "A3", 0x03: "A4",
    0x04: "A5", 0x05: "A6", 0x06: "A7", 0x07: "A8",
    0x08: "B1", 0x09: "B2", 0x0A: "B3", 0x0B: "B4",
    0x0C: "B5", 0x0D: "B6", 0x0E: "B7", 0x0F: "B8",
    0x10: "C1", 0x11: "C2", 0x12: "C3", 0x13: "C4",
    0x14: "C5", 0x15: "C6", 0x16: "C7", 0x17: "C8",
    0x18: "D1", 0x19: "D2", 0x1A: "D3", 0x1B: "D4",
    0x1C: "D5", 0x1D: "D6", 0x1E: "D7", 0x1F: "D8",
}

# Grid snap values (in ticks at 96 PPQN) - matching SP-303 device labels
GRID_SNAPS = {
    "Off": 0,     # Quantise Off
    "4": 96,      # Quarter note
    "8": 48,      # Eighth note
    "8-3": 32,    # Eighth note triplet (96/3)
    "16": 24,     # Sixteenth note
}

# Color palettes
COLOR_PALETTES = {
    "Dark": {
        "background": "#1a1a1a",
        "grid_major": "#333333",
        "grid_minor": "#222222",
        "ruler_bg": "#252525",
        "ruler_text": "#aaaaaa",
        "lane_separator": "#2a2a2a",
        "lane_label_bg": "#202020",
        "lane_label_text": "#cccccc",
        "selection_rect": "#60a5fa",
        "selection_fill": "#60a5fa33",
        "pattern_end": "#ff4444",
        # Bank A (red/orange shades)
        "pad_a": [
            "#EF4444", "#F87171", "#FCA5A5", "#FECACA",
            "#DC2626", "#B91C1C", "#991B1B", "#7F1D1D"
        ],
        # Bank B (purple shades)
        "pad_b": [
            "#A855F7", "#C084FC", "#D8B4FE", "#E9D5FF",
            "#9333EA", "#7E22CE", "#6B21A8", "#581C87"
        ],
        # Bank C (blue shades)
        "pad_c": [
            "#3B82F6", "#60A5FA", "#93C5FD", "#BFDBFE",
            "#2563EB", "#1D4ED8", "#1E40AF", "#1E3A8A"
        ],
        # Bank D (green shades)
        "pad_d": [
            "#10B981", "#34D399", "#6EE7B7", "#A7F3D0",
            "#059669", "#047857", "#065F46", "#064E3B"
        ],
    },
    "High Contrast (White on Black)": {
        "background": "#000000",
        "grid_major": "#444444",
        "grid_minor": "#222222",
        "ruler_bg": "#0a0a0a",
        "ruler_text": "#ffffff",
        "lane_separator": "#333333",
        "lane_label_bg": "#000000",
        "lane_label_text": "#ffffff",
        "selection_rect": "#ffffff",
        "selection_fill": "#ffffff33",
        "pattern_end": "#ff0000",
        # Bank A (bright red/orange on black)
        "pad_a": [
            "#ff0000", "#ff2200", "#ff4400", "#ff6600",
            "#ff8800", "#ffaa00", "#ffcc00", "#ffee00"
        ],
        # Bank B (bright purple/magenta on black)
        "pad_b": [
            "#ff00ff", "#ee00ff", "#dd00ff", "#cc00ff",
            "#bb00ff", "#aa00ff", "#9900ff", "#8800ff"
        ],
        # Bank C (bright blue/cyan on black)
        "pad_c": [
            "#0088ff", "#00aaff", "#00ccff", "#00eeff",
            "#0066ff", "#0044ff", "#0022ff", "#0000ff"
        ],
        # Bank D (bright green on black)
        "pad_d": [
            "#00ff88", "#00ffaa", "#00ffcc", "#00ffee",
            "#00ff66", "#00ff44", "#00ff22", "#00ff00"
        ],
    },
    "High Contrast (Black on White)": {
        "background": "#ffffff",
        "grid_major": "#cccccc",
        "grid_minor": "#e8e8e8",
        "ruler_bg": "#f5f5f5",
        "ruler_text": "#000000",
        "lane_separator": "#dddddd",
        "lane_label_bg": "#ffffff",
        "lane_label_text": "#000000",
        "selection_rect": "#000000",
        "selection_fill": "#00000033",
        "pattern_end": "#cc0000",
        # Bank A (dark red/orange on white)
        "pad_a": [
            "#cc0000", "#aa0000", "#880000", "#660000",
            "#dd2200", "#ee4400", "#ff6600", "#ff8800"
        ],
        # Bank B (dark purple on white)
        "pad_b": [
            "#880088", "#770077", "#660066", "#550055",
            "#990099", "#aa00aa", "#bb00bb", "#cc00cc"
        ],
        # Bank C (dark blue on white)
        "pad_c": [
            "#0066cc", "#0055aa", "#004488", "#003366",
            "#0077dd", "#0088ee", "#0099ff", "#00aaff"
        ],
        # Bank D (dark green on white)
        "pad_d": [
            "#008844", "#007733", "#006622", "#005511",
            "#009955", "#00aa66", "#00bb77", "#00cc88"
        ],
    },
    "Apple Green": {
        "background": "#001100",
        "grid_major": "#003300",
        "grid_minor": "#002200",
        "ruler_bg": "#001a00",
        "ruler_text": "#00ff00",
        "lane_separator": "#002200",
        "lane_label_bg": "#001100",
        "lane_label_text": "#00ff00",
        "selection_rect": "#00ff00",
        "selection_fill": "#00ff0033",
        "pattern_end": "#00ff00",
        # Bank A (red-green variations)
        "pad_a": [
            "#ff4400", "#ee3300", "#dd2200", "#cc1100",
            "#ff5500", "#ff6600", "#ff7700", "#ff8800"
        ],
        # Bank B (purple-green variations)
        "pad_b": [
            "#cc00ff", "#bb00ee", "#aa00dd", "#9900cc",
            "#dd00ff", "#ee00ff", "#ff00ff", "#ff22ff"
        ],
        # Bank C (green variations)
        "pad_c": [
            "#00ff00", "#00ee00", "#00dd00", "#00cc00",
            "#00bb00", "#00aa00", "#009900", "#008800"
        ],
        # Bank D (yellow-green variations)
        "pad_d": [
            "#88ff00", "#99ff00", "#aaff00", "#bbff00",
            "#77ff00", "#66ff00", "#55ff00", "#44ff00"
        ],
    },
}

# Default color scheme
COLORS = COLOR_PALETTES["High Contrast (White on Black)"]


@dataclass
class ModelState:
    """Snapshot of pattern model state for undo/redo"""
    slot: int
    events: List[Event]


class PatternModel:
    """
    Data layer - manages pattern state with undo/redo

    Interfaces with inlined PTNInfo/PTNData engine
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

    def new_pattern(self):
        """Create new pattern files"""
        self.ptninfo = PTNInfo()
        self.ptninfo_raw = bytearray(self.ptninfo.to_bytes())
        init_template = Path(__file__).parent / "PTNDATA_INIT_OFFICIAL.bin"
        if init_template.exists():
            self.ptndata = PTNData(init_template_path=init_template)
        else:
            self.ptndata = PTNData()
            messagebox.showwarning(
                "Missing Pattern Template",
                "Copy PTNDATA0.SP0 from your SP-303's SmartMedia card (format it first)"
                " and save it as PTNDATA_INIT_OFFICIAL.bin"
            )
        self.current_slot = 0
        self.events = []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False
        self.ptninfo_path = None
        self.ptndata_path = None

    def load_pattern(self, ptninfo_path: Path, ptndata_path: Path):
        """Load pattern files"""
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

    def save_pattern(self, ptninfo_path: Optional[Path] = None, ptndata_path: Optional[Path] = None):
        """Save pattern files"""
        if ptninfo_path is None:
            ptninfo_path = self.ptninfo_path
        if ptndata_path is None:
            ptndata_path = self.ptndata_path

        if ptninfo_path is None or ptndata_path is None:
            raise ValueError("No file paths specified for save")

        # Save current slot
        self.save_slot()

        # Write files
        if self.ptninfo_raw is not None:
            with open(ptninfo_path, "wb") as f:
                f.write(self.ptninfo_raw)
        else:
            self.ptninfo.save(ptninfo_path)
        self.ptndata.save(ptndata_path)

        self.ptninfo_path = ptninfo_path
        self.ptndata_path = ptndata_path
        self.dirty = False

    def load_slot(self, slot_index: int):
        """Load events from slot"""
        if not (0 <= slot_index < SLOT_COUNT):
            raise ValueError(f"Slot must be 0-15, got {slot_index}")

        # Save current slot before switching
        if self.current_slot != slot_index and self.ptndata is not None:
            self.save_slot()

        self.current_slot = slot_index
        storage_slot = slot_index
        mapping_index = self.get_mapping_index(slot_index)
        if mapping_index is not None and 1 <= mapping_index <= 16:
            storage_slot = mapping_index - 1
        self.current_storage_slot = storage_slot

        if self.ptndata is not None:
            self.events = self.ptndata.decode_events(storage_slot)
            # Sort by tick
            self.events.sort(key=lambda e: e.tick)
        else:
            self.events = []

    def get_pattern_length_bars(self) -> int:
        """Calculate pattern length in bars from events (max 99 bars - hardware limit)"""
        # Prefer explicit per-slot length from PTNINFO active entry when present.
        ptninfo_length = self.get_ptninfo_length_bars(self.current_slot)
        if ptninfo_length is not None:
            return ptninfo_length

        if not self.events:
            return DEFAULT_PATTERN_LENGTH_BARS

        # Find last event tick
        last_tick = max(e.tick for e in self.events)

        # Convert to bars (round up to nearest bar)
        bars = int((last_tick / (4 * INTERNAL_PPQN)) + 0.999)

        # Cap at hardware maximum (99 bars)
        return max(1, min(bars, MAX_PATTERN_LENGTH_BARS))

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
        # Hardware captures with a valid pattern index (b3=1..16) use b2 as length-like value.
        # Quantize encoding in that mode is not resolved yet, so show Off instead of mislabeling.
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
        """Save current events to slot"""
        if self.ptndata is None:
            return
        if not self.dirty:
            return
        self.last_save_warning = None

        # Persist length/mapping metadata even when slot is empty.
        length_bars = self.get_ptninfo_length_bars(self.current_slot) or DEFAULT_PATTERN_LENGTH_BARS
        mapping_index = self.get_mapping_index(self.current_slot)
        if mapping_index is None:
            mapping_index = self.current_slot + 1

        if self.events:
            # Sort events by tick before saving
            self.events.sort(key=lambda e: e.tick)

            total_length_ticks = max(1, length_bars * 4 * INTERNAL_PPQN)
            fitted_events, truncated_events, fitted_total_length_ticks = self._fit_events_to_tuple_capacity(
                self.events, total_length_ticks=total_length_ticks
            )
            if truncated_events > 0:
                self.events = fitted_events
                self.last_save_warning = (
                    "Current pattern was too dense for SP-303 storage. "
                    f"Truncated {truncated_events} event(s) to fit device limits."
                )
            # Write pattern to mapped storage slot.
            self.ptndata.write_pattern(
                self.current_storage_slot,
                fitted_events,
                total_length_ticks=fitted_total_length_ticks
            )

            fitted_length_bars = max(
                1,
                min(
                    MAX_PATTERN_LENGTH_BARS,
                    int((fitted_total_length_ticks - 1) / (4 * INTERNAL_PPQN)) + 1,
                ),
            )

            # Keep active entry in hardware-observed shape: [04 B0 length slot+1]
            self._set_ptninfo_active_entry(
                self.current_slot,
                "OFF",
                mapping_index=mapping_index,
                active_value=fitted_length_bars
            )
        else:
            # Commit clear to disk-backed model; otherwise old notes reappear on slot switch.
            self.ptndata.clear_pattern(self.current_storage_slot)
            self._set_ptninfo_empty_entry(self.current_slot)
        self.dirty = False

    def _fit_events_to_tuple_capacity(
        self, events: List[Event], total_length_ticks: int
    ) -> Tuple[List[Event], int, int]:
        """Trim trailing events so encoded payload fits the SP-303 tuple zone.

        Returns:
            fitted_events, truncated_event_count, fitted_total_length_ticks
        """
        if self.ptndata is None or not events:
            return events, 0, max(1, total_length_ticks)

        def encoded_len(prefix: List[Event]) -> int:
            if not prefix:
                return 0
            prefix_last_tick = prefix[-1].tick
            # Use the prefix's own end to avoid pathological rest-event blowups.
            effective_total_ticks = max(1, min(total_length_ticks, prefix_last_tick + 1))
            return len(self.ptndata.encode_events(prefix, total_length_ticks=effective_total_ticks))

        serialized_len = encoded_len(events)
        if serialized_len <= TUPLE_ZONE_MAX_BYTES:
            fitted_total_ticks = max(1, min(total_length_ticks, events[-1].tick + 1))
            return events, 0, fitted_total_ticks

        low, high = 1, len(events)
        fit_count = 0
        while low <= high:
            mid = (low + high) // 2
            mid_len = encoded_len(events[:mid])
            if mid_len <= TUPLE_ZONE_MAX_BYTES:
                fit_count = mid
                low = mid + 1
            else:
                high = mid - 1

        # Strict behavior-preserving fit:
        # keep chronological order and drop only trailing events.
        if fit_count > 0:
            fitted = events[:fit_count]
            fitted_total_ticks = max(1, min(total_length_ticks, fitted[-1].tick + 1))
            return fitted, len(events) - fit_count, fitted_total_ticks

        if fit_count <= 0:
            # If first note is very late, delta/rest tuples can overflow even for one event.
            # Rebase the first event to tick 0 so at least one audible event survives.
            first = events[0]
            fallback = [Event(tick=0, pad=first.pad, velocity=first.velocity)]
            fallback_total_ticks = 1
            fallback_len = len(self.ptndata.encode_events(fallback, total_length_ticks=fallback_total_ticks))
            if fallback_len <= TUPLE_ZONE_MAX_BYTES:
                return fallback, max(0, len(events) - 1), fallback_total_ticks
            return [], len(events), 1
        return [], len(events), 1

    def clear_slot(self):
        """Clear current slot"""
        self.push_undo_state()
        self.events.clear()
        self._set_ptninfo_empty_entry(self.current_slot)
        self.dirty = True

    def add_event(self, tick: int, pad: int, velocity: int = 0x7F):
        """Add event to current slot"""
        self.push_undo_state()
        event = Event(tick=tick, pad=pad, velocity=velocity)
        self.events.append(event)
        self.events.sort(key=lambda e: e.tick)
        self.dirty = True

    def remove_event(self, event: Event):
        """Remove event from current slot"""
        self.push_undo_state()
        if event in self.events:
            self.events.remove(event)
            self.dirty = True

    def remove_events(self, events: List[Event]):
        """Remove multiple events"""
        if not events:
            return
        self.push_undo_state()
        for event in events:
            if event in self.events:
                self.events.remove(event)
        self.dirty = True

    def move_event(self, event: Event, new_tick: int, new_pad: Optional[int] = None):
        """Move event to new position"""
        self.push_undo_state()
        event.tick = new_tick
        if new_pad is not None:
            event.pad = new_pad
        self.events.sort(key=lambda e: e.tick)
        self.dirty = True

    def set_event_velocity(self, event: Event, velocity: int):
        """Set velocity for event"""
        self.push_undo_state()
        event.velocity = max(0, min(127, velocity))
        self.dirty = True

    def quantize_events(self, events: List[Event], quantize_ticks: int):
        """Quantize selected events to grid"""
        if quantize_ticks <= 0:
            return
        self.push_undo_state()
        for event in events:
            event.tick = round(event.tick / quantize_ticks) * quantize_ticks
        self.events.sort(key=lambda e: e.tick)
        self.dirty = True

    def copy_slot(self):
        """Copy current slot events to clipboard"""
        self.slot_clipboard = [Event(e.tick, e.pad, e.velocity) for e in self.events]

    def paste_slot(self):
        """Paste clipboard events to current slot"""
        if self.slot_clipboard is None:
            return
        self.push_undo_state()
        self.events = [Event(e.tick, e.pad, e.velocity) for e in self.slot_clipboard]
        self._set_ptninfo_active_entry(self.current_slot, "OFF")
        self.dirty = True

    def generate_test_data(self, seed: Optional[int] = None):
        """Generate test data across all slots"""
        if self.ptninfo is None or self.ptndata is None or self.ptninfo_raw is None:
            raise ValueError("No pattern files loaded")

        rng = random.Random(seed)
        all_pads = PAD_ORDER
        quantize_options = ["OFF", "1/4", "1/8", "1/16", "1/8T", "1/16T"]
        pattern_lengths = list(range(1, 9))

        # Bank C: ascending/descending patterns across lengths and quantize options
        for slot in range(16):
            length_bars = pattern_lengths[slot % len(pattern_lengths)]
            quantize = quantize_options[slot % len(quantize_options)]
            is_bank_d = slot >= 8
            if is_bank_d:
                # Bank D: ascend then reverse within the same pattern
                pads = all_pads + list(reversed(all_pads))
            else:
                pads = all_pads if slot % 2 == 0 else list(reversed(all_pads))
            total_ticks = max(1, length_bars * 4 * INTERNAL_PPQN)
            steps = len(pads)
            events: List[Event] = []
            if steps == 1:
                events = [Event(tick=0, pad=pads[0], velocity=0x7F)]
            else:
                for i, pad in enumerate(pads):
                    tick = int(round(i * (total_ticks - 1) / (steps - 1)))
                    events.append(Event(tick=tick, pad=pad, velocity=0x7F))

            self.ptndata.write_pattern(slot, events, total_length_ticks=total_ticks)
            # Hardware capture indicates byte 3 in active PTNINFO entries tracks bar length.
            self._set_ptninfo_active_entry(
                slot,
                quantize,
                mapping_index=slot + 1,
                active_value=length_bars
            )

        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = True
        self.current_slot = 0
        self.current_storage_slot = 0
        self.events = self.ptndata.decode_events(0)
        self.events.sort(key=lambda e: e.tick)

    def import_midi(
        self,
        midi_path: Path,
        replace: bool = True,
        notes_override: Optional[List[Tuple[int, int, int]]] = None,
        ppqn_override: Optional[int] = None,
        out_of_range: str = "skip",
    ) -> dict:
        """Import MIDI file to current slot.

        Notes beyond the SP-303 maximum pattern length (99 bars) are truncated.
        Returns import metadata for user-facing reporting.
        """
        # Load MIDI notes
        if notes_override is not None and ppqn_override is not None:
            notes, ppqn = notes_override, ppqn_override
        else:
            notes, ppqn = load_midi_notes(str(midi_path))

        if not notes:
            raise ValueError("No notes found in MIDI file")

        # Convert MIDI notes to events
        # Scale timing from MIDI PPQN to SP-303 96 PPQN
        scale_factor = INTERNAL_PPQN / ppqn

        # Map MIDI note numbers to SP-303 pads (MIDI 60-75 -> pads 0x10-0x1F, C1-D8)
        transpose_shift = 0
        if out_of_range == "transpose":
            # Find the octave shift (multiple of 12) that places the most notes in range 60-75
            best_shift = 0
            best_count = sum(1 for _, n, _ in notes if 60 <= n <= 75)
            for s in range(-96, 97, 12):
                if s == 0:
                    continue
                count = sum(1 for _, n, _ in notes if 60 <= n + s <= 75)
                if count > best_count or (count == best_count and abs(s) < abs(best_shift)):
                    best_count = count
                    best_shift = s
            transpose_shift = best_shift

        imported_events = []
        skipped_out_of_range = 0
        for tick, note, vel in notes:
            sp303_tick = int(tick * scale_factor)
            mapped_note = note + transpose_shift
            if 60 <= mapped_note <= 75:
                pad = 0x10 + (mapped_note - 60)
            else:
                skipped_out_of_range += 1
                continue
            imported_events.append(Event(tick=sp303_tick, pad=pad, velocity=vel))

        # Sort by tick
        imported_events.sort(key=lambda e: e.tick)

        source_bars = 0.0
        if imported_events:
            source_bars = (imported_events[-1].tick / (4 * INTERNAL_PPQN)) + 1.0

        max_ticks = MAX_PATTERN_LENGTH_BARS * 4 * INTERNAL_PPQN
        truncated_event_count = 0
        truncated_bars = 0.0
        if imported_events:
            last_tick = imported_events[-1].tick
            if last_tick >= max_ticks:
                source_bars = (last_tick / (4 * INTERNAL_PPQN)) + 1.0
                truncated_bars = max(0.0, source_bars - MAX_PATTERN_LENGTH_BARS)
                kept_events = [e for e in imported_events if e.tick < max_ticks]
                truncated_event_count = len(imported_events) - len(kept_events)
                imported_events = kept_events

        # Replace or append
        self.push_undo_state()
        existing_count = len(self.events)
        candidate_events: List[Event]
        if replace:
            candidate_events = imported_events
        else:
            candidate_events = [Event(e.tick, e.pad, e.velocity) for e in self.events]
            candidate_events.extend(imported_events)
            candidate_events.sort(key=lambda e: e.tick)

        total_length_ticks = 1
        if candidate_events:
            max_ticks = MAX_PATTERN_LENGTH_BARS * 4 * INTERNAL_PPQN
            total_length_ticks = max(1, min(max_ticks, max(e.tick for e in candidate_events) + 1))
        fitted_events, density_truncated, fitted_total_length_ticks = self._fit_events_to_tuple_capacity(
            candidate_events, total_length_ticks=total_length_ticks
        )
        self.events = fitted_events
        if self.events:
            imported_length_bars = max(
                1,
                min(
                    MAX_PATTERN_LENGTH_BARS,
                    int((max(e.tick for e in self.events) / (4 * INTERNAL_PPQN)) + 0.999),
                ),
            )
        else:
            imported_length_bars = DEFAULT_PATTERN_LENGTH_BARS
        # MIDI import should target the selected pattern directly, not preserve prior remaps.
        self._set_ptninfo_active_entry(
            self.current_slot,
            "OFF",
            mapping_index=self.current_slot + 1,
            active_value=imported_length_bars,
        )
        self.dirty = True

        imported_count = len(self.events) if replace else max(0, len(self.events) - existing_count)

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
        bars = max(1, min(MAX_PATTERN_LENGTH_BARS, int(bars)))
        mapping_index = self.get_mapping_index(self.current_slot)
        if mapping_index is None:
            mapping_index = self.current_slot + 1
        self._set_ptninfo_active_entry(
            self.current_slot,
            "OFF",
            mapping_index=mapping_index,
            active_value=bars
        )
        self.dirty = True

    def _set_ptninfo_active_entry(
        self,
        slot_index: int,
        quantize: str,
        mapping_index: Optional[int] = None,
        active_value: Optional[int] = None
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
            "1/16T": 0x05
        }
        if active_value is not None:
            quant_byte = max(0, min(0x63, int(active_value)))
        else:
            quant_byte = quant_map.get(quantize, 0x00)
        if mapping_index is None:
            existing = self.get_mapping_index(slot_index)
            mapping_index = existing if existing is not None else (slot_index + 1)
        mapping_index = max(1, min(16, mapping_index))
        offset = slot_index * 4
        self.ptninfo_raw[offset:offset+4] = bytes([0x04, 0xB0, quant_byte, mapping_index])

    def _set_ptninfo_empty_entry(self, slot_index: int):
        if self.ptninfo is not None:
            self.ptninfo.clear_pattern(slot_index)
        if self.ptninfo_raw is None:
            return
        offset = slot_index * 4
        self.ptninfo_raw[offset:offset+4] = bytes([0xB0, 0x04, 0x02, slot_index + 1])

    def get_ptninfo_entry(self, slot_index: int) -> Optional[bytes]:
        if self.ptninfo_raw is None:
            return None
        offset = slot_index * 4
        return bytes(self.ptninfo_raw[offset:offset+4])

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
        a_entry = bytes(self.ptninfo_raw[a_off:a_off+4])
        b_entry = bytes(self.ptninfo_raw[b_off:b_off+4])
        self.ptninfo_raw[a_off:a_off+4] = b_entry
        self.ptninfo_raw[b_off:b_off+4] = a_entry
        # Best-effort sync of PTNInfo object
        if self.ptninfo is not None:
            self.ptninfo.slots[slot_a] = PatternSlot.from_bytes(slot_a, self.ptninfo_raw[a_off:a_off+4])
            self.ptninfo.slots[slot_b] = PatternSlot.from_bytes(slot_b, self.ptninfo_raw[b_off:b_off+4])
        mapping_index = self.get_mapping_index(self.current_slot)
        if mapping_index is not None and 1 <= mapping_index <= 16:
            self.current_storage_slot = mapping_index - 1
        else:
            self.current_storage_slot = self.current_slot
        if self.ptndata is not None:
            self.events = self.ptndata.decode_events(self.current_storage_slot)
            self.events.sort(key=lambda e: e.tick)
        self.dirty = True

    def push_undo_state(self):
        """Save current state to undo stack"""
        state = ModelState(
            slot=self.current_slot,
            events=[Event(e.tick, e.pad, e.velocity) for e in self.events]
        )
        self.undo_stack.append(state)

        # Limit stack size
        if len(self.undo_stack) > self.max_undo_states:
            self.undo_stack.pop(0)

        # Clear redo stack
        self.redo_stack.clear()

    def undo(self):
        """Undo last operation"""
        if not self.undo_stack:
            return False

        # Save current state to redo stack
        current = ModelState(
            slot=self.current_slot,
            events=[Event(e.tick, e.pad, e.velocity) for e in self.events]
        )
        self.redo_stack.append(current)

        # Restore previous state
        state = self.undo_stack.pop()
        self.events = [Event(e.tick, e.pad, e.velocity) for e in state.events]
        self.dirty = True
        return True

    def redo(self):
        """Redo last undone operation"""
        if not self.redo_stack:
            return False

        # Save current state to undo stack
        current = ModelState(
            slot=self.current_slot,
            events=[Event(e.tick, e.pad, e.velocity) for e in self.events]
        )
        self.undo_stack.append(current)

        # Restore redone state
        state = self.redo_stack.pop()
        self.events = [Event(e.tick, e.pad, e.velocity) for e in state.events]
        self.dirty = True
        return True


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


class SP303PatternEditor:
    """Main application window"""

    def __init__(self, root, debug_mode: bool = False):
        self.root = root
        self.debug_mode = debug_mode
        self.root.title("Dr. Sidekick by One Coin One Play")

        # Calculate window height: toolbar (~40) + ruler (25) + 32 lanes + statusbar (~25) + padding
        window_height = 40 + 25 + (32 * 25) + 25 + 30  # ~920 pixels
        self.root.geometry(f"1200x{window_height}")

        # Set dark theme colors
        style = ttk.Style()
        style.theme_use('clam')
        self.root.configure(bg="#000000")

        # Global white-on-black styling for ttk widgets/windows.
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

        # Model
        self.model = PatternModel()
        self.current_palette = "Apple Green"
        self.slot_combo: Optional[ttk.Combobox] = None
        self.smartmedia_library_root = Path(__file__).parent / "SmartMedia-Library"
        self.smartmedia_library_root.mkdir(parents=True, exist_ok=True)
        self.smartmedia_lib = SmartMediaLibrary(self.smartmedia_library_root)
        self.load_config()
        self.ensure_library_dirs()
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
        self.root.bind("<Control-Shift-L>", lambda e: self.show_home_launcher())
        self.root.bind("<Control-Shift-l>", lambda e: self.show_home_launcher())
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
        self.root.after(150, self.show_home_launcher)

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
            label="Home Launcher...",
            command=self.show_home_launcher,
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
        card_menu.add_command(label="SmartMedia Manager...", command=self.on_custom_pad_assignment)
        card_menu.add_command(label="SmartMedia Library...", command=self.on_smartmedia_library)
        card_menu.add_separator()
        card_menu.add_command(label="Convert MPC1000 Program (.pgm)...", command=self.on_import_mpc1000)
        self.pattern_menu = pattern_menu
        self.samples_menu = card_menu

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Quick Start", command=self.on_help_quick_start)
        help_menu.add_command(label="Workflow Examples", command=self.on_help_workflow_examples)
        help_menu.add_command(label="FAQ / Troubleshooting", command=self.on_help_faq)
        help_menu.add_separator()
        help_menu.add_command(label="Keyboard Shortcuts", command=self.on_show_shortcuts)
        help_menu.add_separator()
        help_menu.add_command(label="Check for Update...", command=self.on_check_for_update)
        help_menu.add_command(label="About", command=self.on_about)
        help_menu.add_separator()
        help_menu.add_command(label="View Session Log...", command=self.on_view_log)

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
            text=self.config.get("device", "BOSS Dr. Sample SP-303"),
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

    def on_backup_card_quick(self):
        smpinfo_file = filedialog.askopenfilename(
            title="Select Card Setup File (SMPINFO0.SP0)",
            initialdir=str(self.default_card_mount_dir()),
            filetypes=[("SMPINFO0.SP0", "SMPINFO0.SP0"), ("SP0 Files", "*.SP0"), ("All Files", "*.*")],
        )
        if not smpinfo_file:
            return
        card_dir = Path(smpinfo_file).parent
        snap_path = self.smartmedia_lib.create_snapshot("PHYSICAL_CARD")
        copied = 0
        for sp0_file in sorted(card_dir.glob("*.SP0")):
            shutil.copy2(sp0_file, snap_path / sp0_file.name)
            copied += 1
        if copied == 0:
            messagebox.showwarning("Backup Card", "No .SP0 files found to back up.")
            shutil.rmtree(snap_path)
            return
        self.set_active_workflow("Backup/Restore")
        self.set_loaded_card_context(str(card_dir))
        self.show_text_dialog("Backup Card", f"Backed up {copied} .SP0 files\nDestination: {snap_path}", geometry="700x260")

    def on_restore_backup_quick(self):
        snapshots_root = self.smartmedia_lib.autosaves_dir
        backup_dir = filedialog.askdirectory(
            title="Select Snapshot Folder",
            initialdir=str(snapshots_root if snapshots_root.exists() else Path.home()),
        )
        if not backup_dir:
            return
        preferred_output = Path("/Volumes/BOSS DATA")
        if not preferred_output.exists():
            preferred_output = self.get_library_paths()["outgoing"]
        output_dir = self.ask_output_directory(preferred_output)
        if output_dir is None:
            return
        restored = 0
        for sp0_file in sorted(Path(backup_dir).glob("*.SP0")):
            shutil.copy2(sp0_file, output_dir / sp0_file.name)
            restored += 1
        if restored == 0:
            messagebox.showwarning("Restore Snapshot", "No .SP0 files found in selected snapshot folder.")
            return
        self.set_active_workflow("Backup/Restore")
        self.set_loaded_card_context(str(output_dir))
        messagebox.showinfo("Restore Snapshot", f"Restored {restored} .SP0 file(s).")

    def show_home_launcher(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Dr. Sidekick Home")
        dialog.geometry("560x340")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#000000")

        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Dr. Sidekick", font=("Courier", 18, "bold")).pack(anchor=tk.W)
        ttk.Label(
            frame,
            text="Pattern editor and SmartMedia librarian for the Boss Dr. Sample SP-303.",
        ).pack(anchor=tk.W, pady=(4, 12))

        def create_task_button(title: str, desc: str, action, workflow: str):
            btn_frame = tk.Frame(frame, bg="#090909", highlightbackground="#2f2f2f", highlightthickness=1, bd=0)
            btn_frame.pack(fill=tk.X, pady=4)

            def run_action():
                self.set_active_workflow(workflow)
                dialog.destroy()
                action()

            ttk.Button(btn_frame, text=title, command=run_action).pack(fill=tk.X, padx=8, pady=(8, 4))
            tk.Label(
                btn_frame,
                text=desc,
                justify=tk.LEFT,
                anchor="w",
                bg="#090909",
                fg="#d0d0d0",
                wraplength=500,
            ).pack(fill=tk.X, padx=8, pady=(0, 8))

        create_task_button(
            "Pattern Editor",
            "Create new patterns or edit existing patterns. Import MIDI and apply grooves.",
            lambda: None,
            "Patterns",
        )
        create_task_button(
            "SmartMedia Manager",
            "Helps you manage your SmartMedia card.",
            self.on_custom_pad_assignment,
            "Samples/Card",
        )
        create_task_button(
            "Quick Import",
            "Get a whole bank of audio files onto your SmartMedia card fast.",
            self.on_quick_import_card,
            "Samples/Card",
        )


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
            initialdir=str(self.default_pattern_open_dir()),
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
            initialdir=str(self.default_pattern_save_dir())
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
        self.root.quit()

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

    def on_slot_map(self):
        """Show slot-to-pattern mapping with event presence"""
        if self.model.ptninfo is None or self.model.ptndata is None:
            messagebox.showwarning("No Pattern Files", "Load or create pattern files first.")
            return

        lines = []
        for slot in range(16):
            label = self.slot_index_to_label(slot)
            entry = self.model.get_ptninfo_entry(slot)
            entry_hex = entry.hex() if entry is not None else "----"
            mapping_index = self.model.get_mapping_index(slot)
            if mapping_index is not None and 1 <= mapping_index <= 16:
                mapped_slot = mapping_index - 1
                events = self.model.ptndata.decode_events(mapped_slot)
                event_count = len(events)
                pads_used = len(set(e.pad for e in events)) if events else 0
                lines.append(
                    f"{label:>2} (slot {slot:02d}) | entry {entry_hex} | idx {mapping_index:02d} -> slot {mapped_slot:02d} | events {event_count:02d} pads {pads_used}"
                )
            else:
                events = self.model.ptndata.decode_events(slot)
                event_count = len(events)
                pads_used = len(set(e.pad for e in events)) if events else 0
                lines.append(
                    f"{label:>2} (slot {slot:02d}) | entry {entry_hex} | idx -- -> slot -- | events {event_count:02d} pads {pads_used}"
                )

        info_text = "Slot Map (PTNINFO -> PTNDATA)\n\n" + "\n".join(lines)
        messagebox.showinfo("Slot Map", info_text)

    def ask_output_directory(self, initialdir: Optional[Path] = None) -> Optional[Path]:
        kwargs = {"title": "Select Output Directory"}
        if initialdir is not None:
            kwargs["initialdir"] = str(initialdir)
        output_dir = filedialog.askdirectory(**kwargs)
        return Path(output_dir) if output_dir else None

    def get_library_paths(self) -> dict:
        return {
            "root": self.smartmedia_library_root,
            "cards": self.smartmedia_library_root / "Cards",
            "autosaves": self.smartmedia_library_root / "AutoSaves",
            "incoming": self.smartmedia_library_root / "BOSS DATA_INCOMING",
            "outgoing": self.smartmedia_library_root / "BOSS DATA_OUTGOING",
        }

    def ensure_library_dirs(self):
        self.smartmedia_lib.ensure_dirs()

    def default_card_mount_dir(self) -> Path:
        if sys.platform == "darwin":
            preferred = Path("/Volumes/BOSS DATA")
            if preferred.exists():
                return preferred
            incoming = self.get_library_paths()["incoming"]
            return incoming if incoming.exists() else Path("/Volumes")

        config_path = self.config.get("card_mount_path", "")
        if config_path:
            candidate = Path(config_path)
            if candidate.exists():
                return candidate

        outgoing = self.get_library_paths()["outgoing"]
        return outgoing if outgoing.exists() else Path.cwd()

    def default_pattern_open_dir(self) -> Path:
        """Default directory for File > Open."""
        preferred = Path("/Volumes/BOSS DATA")
        if preferred.exists():
            return preferred
        incoming = self.get_library_paths()["incoming"]
        if incoming.exists():
            return incoming
        return self.smartmedia_library_root

    def default_pattern_save_dir(self) -> Path:
        """Default directory for File > Save As."""
        preferred = Path("/Volumes/BOSS DATA")
        if preferred.exists():
            return preferred
        outgoing = self.get_library_paths()["outgoing"]
        if outgoing.exists():
            return outgoing
        return self.smartmedia_library_root

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
        self.show_text_dialog(title, "\n".join(lines), geometry="1024x640")

    def show_text_dialog(self, title: str, content: str, geometry: str = "1024x640"):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry(geometry)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#000000")

        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True)

        text = tk.Text(
            text_frame,
            wrap=tk.NONE,
            font=("TkFixedFont", 11),
            bg="#000000",
            fg="#ffffff",
            insertbackground="#ffffff",
            relief=tk.FLAT,
            highlightthickness=0,
        )
        text.insert("1.0", content)
        text.configure(state=tk.DISABLED)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        y_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text.yview)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        text.configure(yscrollcommand=y_scroll.set)

        x_scroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=text.xview)
        x_scroll.pack(fill=tk.X)
        text.configure(xscrollcommand=x_scroll.set)


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

        output_dir = self.get_library_paths()["outgoing"]
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            archive_dir = self.archive_existing_outgoing_wavs(output_dir)
            payload = quick_import(wav_dir_path, output_dir, None)
            summary_lines = [
                f"WAV files processed: {payload['imported_count']}",
            ]
            if archive_dir is not None:
                summary_lines.append(
                    f"Archived existing WAV files in BOSS DATA_OUTGOING to /BOSS DATA_OUTGOING/{archive_dir.name}"
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

        self.smartmedia_lib.ensure_dirs()
        card_name = "MPC1000"
        card_dir = self.smartmedia_lib.cards_dir / card_name
        if card_dir.exists():
            shutil.rmtree(card_dir)
        card_dir.mkdir(parents=True, exist_ok=True)
        card = VirtualCard(name=card_name, tags=["mpc1000", pgm_path.stem])
        self.smartmedia_lib.create_card(card)

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

        self.show_text_dialog("MPC1000 Import Complete", "\n".join(summary_lines))
        self.update_status(
            f"MPC1000 import complete: {total_written} samples written to Cards/{card_name}"
        )
        log.info("MPC1000 import: %s -> Cards/%s (%d samples)", pgm_path.name, card_name, total_written)

    def on_smartmedia_library(self):
        """Virtual SmartMedia Library dialog."""
        self.smartmedia_lib.ensure_dirs()
        dialog = tk.Toplevel(self.root)
        dialog.title("SmartMedia Library")
        dialog.geometry("1200x720")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#000000")

        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        # ── Top status bar ──────────────────────────────────────────────────
        top_bar = ttk.Frame(frame)
        top_bar.pack(fill=tk.X, pady=(0, 8))

        card_status_var = tk.StringVar(value="Checking physical card...")
        card_status_lbl = ttk.Label(top_bar, textvariable=card_status_var, font=("Courier", 10))
        card_status_lbl.pack(side=tk.LEFT)

        write_to_card_var = tk.BooleanVar(value=self.config.get("write_to_card", True))
        def on_write_toggle():
            self.config["write_to_card"] = write_to_card_var.get()
            self.save_config()
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
            if dialog.winfo_exists():
                dialog.after(2000, refresh_card_status)
        refresh_card_status()

        def open_card():
            preferred = Path("/Volumes/BOSS DATA") / "SMPINFO0.SP0"
            if preferred.exists():
                path = preferred
            else:
                chosen = filedialog.askopenfilename(
                    parent=dialog,
                    title="Select SMPINFO0.SP0",
                    initialdir=str(self.default_card_mount_dir()),
                    filetypes=[("SMPINFO0.SP0", "SMPINFO0.SP0"), ("SP0 Files", "*.SP0"), ("All Files", "*.*")],
                )
                if not chosen:
                    return
                path = Path(chosen)
            active_smpinfo[0] = path
            open_card_status_var.set(f"Open: {path.parent.name}")
            # Auto-snapshot on open
            try:
                self.smartmedia_lib.create_snapshot("PHYSICAL_CARD")
            except Exception:
                pass
            refresh_snap_list()

        def backup_card():
            card_dir = Path("/Volumes/BOSS DATA")
            if not card_dir.exists():
                if active_smpinfo[0] is not None:
                    card_dir = active_smpinfo[0].parent
                else:
                    messagebox.showwarning("Backup Card", "No physical card mounted or open.", parent=dialog)
                    return
            try:
                snap = self.smartmedia_lib.create_snapshot("PHYSICAL_CARD", "backup")
                copied = 0
                for sp0_file in sorted(card_dir.glob("*.SP0")):
                    shutil.copy2(sp0_file, snap / sp0_file.name)
                    copied += 1
                refresh_snap_list()
                messagebox.showinfo("Backup Card", f"Backed up {copied} file(s) to:\n{snap}", parent=dialog)
            except Exception as exc:
                messagebox.showerror("Backup Card", str(exc), parent=dialog)

        ttk.Button(top_bar, text="Open Card", command=open_card).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(top_bar, text="Backup Card", command=backup_card).pack(side=tk.LEFT, padx=(6, 0))

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

        card_tree_cols = ("name", "device", "author")
        style = ttk.Style(dialog)
        style.configure("Library.Treeview", background="#000000", fieldbackground="#000000",
                        foreground="#ffffff", rowheight=22)
        style.map("Library.Treeview", background=[("selected", "#2a7fff")],
                  foreground=[("selected", "#ffffff")])
        card_tree = ttk.Treeview(left_frame, columns=card_tree_cols, show="headings",
                                 height=20, style="Library.Treeview")
        card_tree.heading("name", text="Name")
        card_tree.heading("device", text="Device")
        card_tree.heading("author", text="Author")
        card_tree.column("name", width=160)
        card_tree.column("device", width=70, anchor=tk.CENTER)
        card_tree.column("author", width=120)
        card_tree.pack(fill=tk.BOTH, expand=True)

        left_btn_row = ttk.Frame(left_frame)
        left_btn_row.pack(fill=tk.X, pady=(6, 0))

        # ── Right panel: card detail + history ───────────────────────────────
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
        device_var, device_entry = make_field(detail_frame, "Device:", 1)
        author_var, author_entry = make_field(detail_frame, "Author:", 2)
        categories_var, categories_entry = make_field(detail_frame, "Categories:", 3)
        tags_var, tags_entry = make_field(detail_frame, "Tags:", 4)

        wp_var = tk.BooleanVar(value=False)
        wp_btn = ttk.Checkbutton(detail_frame, text="Write Protect", variable=wp_var)
        wp_btn.grid(row=5, column=1, sticky=tk.W, pady=4)

        detail_btn_row = ttk.Frame(right_frame)
        detail_btn_row.pack(fill=tk.X, pady=(6, 0))

        # Snapshot history
        snap_history_label = ttk.Label(right_frame, text="SNAPSHOT HISTORY", font=("Courier", 10, "bold"))
        snap_history_label.pack(anchor=tk.W, pady=(12, 4))

        snap_cols = ("timestamp", "type")
        snap_tree = ttk.Treeview(right_frame, columns=snap_cols, show="headings", height=8, style="Library.Treeview")
        snap_tree.heading("timestamp", text="Snapshot")
        snap_tree.heading("type", text="Type")
        snap_tree.column("timestamp", width=180)
        snap_tree.column("type", width=200)
        snap_tree.pack(fill=tk.BOTH, expand=True)

        snap_btn_row = ttk.Frame(right_frame)
        snap_btn_row.pack(fill=tk.X, pady=(6, 0))

        # ── State ─────────────────────────────────────────────────────────────
        current_card: list = [None]   # list-of-one to allow mutation in closures
        active_smpinfo: list = [None]  # Path to currently opened SMPINFO0.SP0

        def get_all_cards():
            query = search_var.get().strip().lower()
            cards = self.smartmedia_lib.list_cards()
            if query:
                cards = [c for c in cards if query in c.name.lower() or query in c.author.lower()
                         or any(query in cat.lower() for cat in c.categories)]
            return cards

        def refresh_card_list():
            for item in card_tree.get_children():
                card_tree.delete(item)
            for card in get_all_cards():
                card_tree.insert("", tk.END, iid=card.name, values=(card.name, card.device, card.author))

        def refresh_snap_list():
            for item in snap_tree.get_children():
                snap_tree.delete(item)
            # Physical card takes priority when open; fall back to selected virtual card
            if active_smpinfo[0] is not None:
                snap_source = "PHYSICAL_CARD"
            elif current_card[0] is not None:
                snap_source = current_card[0].name
            else:
                return
            snap_history_label.config(text=f"SNAPSHOT HISTORY — {snap_source}")
            for snap in self.smartmedia_lib.list_snapshots(snap_source):
                parts = snap.name.split("_", 2)
                # format: YYYY-MM-DD_HH-MM-SS[_suffix]
                ts = f"{parts[0]} {parts[1].replace('-', ':')}" if len(parts) >= 2 else snap.name
                suffix = parts[2] if len(parts) > 2 else ""
                if not suffix:
                    snap_type = "Auto"
                elif suffix.lower() == "backup":
                    snap_type = "Backup"
                else:
                    snap_type = suffix
                snap_tree.insert("", tk.END, iid=str(snap), values=(ts, snap_type))

        def on_card_select(event=None):
            sel = card_tree.selection()
            if not sel:
                current_card[0] = None
                return
            card = self.smartmedia_lib.get_card(sel[0])
            if card is None:
                return
            current_card[0] = card
            name_var.set(card.name)
            device_var.set(card.device)
            author_var.set(card.author)
            categories_var.set(", ".join(card.categories))
            tags_var.set(", ".join(card.tags))
            wp_var.set(card.write_protect)
            refresh_snap_list()

        card_tree.bind("<<TreeviewSelect>>", on_card_select)
        search_var.trace_add("write", lambda *_: refresh_card_list())

        def save_current_card():
            card = current_card[0]
            if card is None:
                return
            card.name = name_var.get().strip()
            card.device = device_var.get().strip()
            card.author = author_var.get().strip()
            card.categories = [c.strip() for c in categories_var.get().split(",") if c.strip()]
            card.tags = [t.strip() for t in tags_var.get().split(",") if t.strip()]
            card.write_protect = wp_var.get()
            self.smartmedia_lib.save_card(card)
            refresh_card_list()

        def new_card():
            new_name = simpledialog.askstring("New Virtual Card", "Card name:", parent=dialog)
            if not new_name or not new_name.strip():
                return
            new_name = new_name.strip()
            if self.smartmedia_lib.get_card(new_name):
                messagebox.showwarning("New Card", f"A card named '{new_name}' already exists.", parent=dialog)
                return
            card = VirtualCard(name=new_name)
            self.smartmedia_lib.create_card(card)
            refresh_card_list()
            card_tree.selection_set(new_name)
            on_card_select()

        def delete_card():
            card = current_card[0]
            if card is None:
                messagebox.showinfo("Delete Card", "Select a card first.", parent=dialog)
                return
            if card.write_protect:
                messagebox.showwarning("Delete Card", "Card is write-protected.", parent=dialog)
                return
            if messagebox.askyesno("Delete Card", f"Delete '{card.name}'? This cannot be undone.", parent=dialog):
                self.smartmedia_lib.delete_card(card.name)
                current_card[0] = None
                refresh_card_list()
                refresh_snap_list()

        def backup_now():
            card = current_card[0]
            if card is None:
                messagebox.showinfo("Backup Now", "Select a card first.", parent=dialog)
                return
            label = simpledialog.askstring("Snapshot Label", "Label (leave blank for 'backup'):", parent=dialog)
            if label is None:
                return
            snap = self.smartmedia_lib.create_snapshot(card.name, label.strip() or "backup")
            refresh_snap_list()
            messagebox.showinfo("Backup Now", f"Snapshot created:\n{snap}", parent=dialog)

        def rename_snapshot():
            sel = snap_tree.selection()
            if not sel:
                messagebox.showinfo("Rename", "Select a snapshot first.", parent=dialog)
                return
            card = current_card[0]
            if card is None:
                return
            snap_path = Path(sel[0])
            new_label = simpledialog.askstring("Rename Snapshot", "New label:", parent=dialog)
            if new_label is None:
                return
            self.smartmedia_lib.rename_snapshot(card.name, snap_path, new_label.strip())
            refresh_snap_list()

        def restore_snapshot():
            sel = snap_tree.selection()
            if not sel:
                messagebox.showinfo("Restore", "Select a snapshot first.", parent=dialog)
                return
            card = current_card[0]
            if card is None:
                return
            if card.write_protect:
                messagebox.showwarning("Restore", "Card is write-protected.", parent=dialog)
                return
            snap_path = Path(sel[0])
            preferred = Path("/Volumes/BOSS DATA")
            target = preferred if preferred.exists() else self.get_library_paths()["outgoing"]
            if not messagebox.askyesno("Restore Snapshot",
                                       f"Restore snapshot to:\n{target}\n\nThis will overwrite files. Continue?",
                                       parent=dialog):
                return
            try:
                self.smartmedia_lib.restore_snapshot(snap_path, card, target)
                messagebox.showinfo("Restore", f"Restored to {target}", parent=dialog)
            except Exception as exc:
                messagebox.showerror("Restore", str(exc), parent=dialog)

        def open_in_manager():
            if active_smpinfo[0] is None:
                messagebox.showinfo(
                    "SmartMedia Manager",
                    "Open a card first using the Open Card button.",
                    parent=dialog,
                )
                return
            self.on_custom_pad_assignment(smpinfo_path=active_smpinfo[0])

        # Wire up buttons
        ttk.Button(left_btn_row, text="New Card", command=new_card).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(left_btn_row, text="Delete Card", command=delete_card).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(detail_btn_row, text="Save Changes", command=save_current_card).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(detail_btn_row, text="Open in SmartMedia Manager", command=open_in_manager).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(snap_btn_row, text="Backup Now", command=backup_now).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(snap_btn_row, text="Rename", command=rename_snapshot).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(snap_btn_row, text="Restore", command=restore_snapshot).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(frame, text="Close", command=dialog.destroy).pack(side=tk.BOTTOM, anchor=tk.E, pady=(8, 0))

        refresh_card_list()

    def on_custom_pad_assignment(self, smpinfo_path: Optional[Path] = None):
        session = AssignmentSession()
        dialog = tk.Toplevel(self.root)
        card_label = smpinfo_path.parent.name if smpinfo_path else "No card loaded"
        dialog.title(f"SmartMedia Manager — {card_label}" if smpinfo_path else "SmartMedia Manager")
        dialog.geometry("1180x680")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#000000")

        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="SmartMedia Manager", font=("Courier", 13, "bold")).pack(anchor=tk.W, pady=(0, 2))
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
                messagebox.showinfo("SmartMedia Manager", "Select a pad first.", parent=dialog)
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
                    channels = 2 if slot_record.is_stereo else 1
                    samples = slot_record.sample_length_bytes / (2 * channels)
                    seconds = samples / 44100.0
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
                dialog.title(f"SmartMedia Manager — {source_dir.name}")
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
                initialdir=str(self.default_card_mount_dir()),
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
                preferred_output = self.get_library_paths()["outgoing"]
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
                    existing_smpinfo = output_dir / "SMPINFO0.SP0"
                    if existing_smpinfo.exists():
                        try:
                            snap = self.smartmedia_lib.create_snapshot("PHYSICAL_CARD")
                            shutil.copy2(existing_smpinfo, snap / existing_smpinfo.name)
                        except Exception:
                            pass

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
                    # Auto-snapshot written card state
                    try:
                        snap_name = "PHYSICAL_CARD" if output_dir == Path("/Volumes/BOSS DATA") else output_dir.name
                        self.smartmedia_lib.create_snapshot(snap_name)
                    except Exception:
                        pass
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

        ttk.Button(setup_row, text="Load Card Setup", command=load_smpinfo_metadata).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(route_row, text="Assign WAV", command=assign_wav).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(route_row, text="Assign SP0", command=assign_sp0).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(route_row, text="Clear Pad", command=clear_pad).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(route_row, text="Refresh", command=refresh_tree).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(write_row, text="Write Changes to Card", command=prepare_card_now).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(write_row, text="Close", command=dialog.destroy).pack(side=tk.RIGHT)

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
        groove_dir = self.get_library_paths()["incoming"]
        groove_file = filedialog.askopenfilename(
            title="Select Groove MIDI",
            initialdir=str(groove_dir),
            filetypes=[("MIDI Files", "*.mid *.MID"), ("All Files", "*.*")],
        )
        if not groove_file:
            return

        card_dir = filedialog.askdirectory(title="Select Card Directory", initialdir=str(self.default_card_mount_dir()))
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

    def on_analyze_existing_card(self):
        default_mount = self.default_card_mount_dir()
        smpinfo_file = filedialog.askopenfilename(
            title="Select SMPINFO0.SP0 (Analyse)",
            initialdir=str(default_mount),
            filetypes=[("SMPINFO0.SP0", "SMPINFO0.SP0"), ("SP0 Files", "*.SP0"), ("All Files", "*.*")],
        )
        if not smpinfo_file:
            return

        try:
            analysis = analyze_existing_card(Path(smpinfo_file))
            stats = analysis["sample_stats"]
            lines = [
                f"Populated slots: {stats['populated_slots']}/{stats['total_slots']}",
                f"Bank C: {stats['bank_c_populated']}/8",
                f"Bank D: {stats['bank_d_populated']}/8",
                f"Mono: {stats['mono_slots']}  Stereo: {stats['stereo_slots']}",
                "",
                "Populated sample slots:",
                *analysis["sample_slots"],
            ]
            if analysis["ptninfo_exists"]:
                lines.extend(
                    [
                        "",
                        f"Active patterns: {analysis['pattern_active_count']}/16",
                        *analysis["pattern_slots"],
                    ]
                )
            messagebox.showinfo("Card Analysis", "\n".join(lines))
            self.update_status("Card analysis complete")
        except Exception as exc:
            messagebox.showerror("Analyse Existing Card", str(exc))

    def on_archive_card_as_song(self):
        card_dir = filedialog.askdirectory(title="Select SmartMedia Card Directory")
        if not card_dir:
            return

        song_name = simpledialog.askstring("Archive Card", "Song name:")
        if not song_name:
            return

        artist = simpledialog.askstring("Archive Card", "Artist (optional):") or "Unknown"
        description = simpledialog.askstring("Archive Card", "Description (optional):") or ""

        default_cards = self.get_library_paths()["cards"]
        destination_root = filedialog.askdirectory(title="Select Song Archive Root", initialdir=str(default_cards))
        destination = Path(destination_root) if destination_root else default_cards

        try:
            archived = archive_card_as_song(Path(card_dir), song_name, artist, description, destination)
            self.update_status(f"Archived song: {song_name}")
            messagebox.showinfo(
                "Archive Complete",
                f"Saved to: {archived['song_dir']}\n"
                f"Copied files: {len(archived['copied_files'])}\n"
                f"Samples: {archived['sample_count']}\n"
                f"Patterns: {'Yes' if archived['has_patterns'] else 'No'}",
            )
        except Exception as exc:
            messagebox.showerror("Archive Card", str(exc))

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
                ("Ctrl+Shift+L",  "Home Launcher"),
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


    def on_help_quick_start(self):
        """Show quick-start guide for beta users."""
        quick_start = """Dr. Sidekick — Quick Start

Welcome! Here's everything you need to get going.


1. Program a Pattern
   Head to Home -> Edit Patterns (or just press Ctrl+Shift+L to open the launcher).
   Pick a pattern slot (C1–D8), switch to Draw mode, and click the pad rows to
   place your hits. Drag to move them, right-click to delete.
   When you're happy, hit Ctrl+S to save.

   You can also import your own MIDI file via File -> Import MIDI File,
   and apply a groove using Patterns -> Add Groove Pattern.


2. Load Samples onto Your Card
   Got a folder of WAVs? Go to Samples -> Quick Import WAV Folder and point
   it at your folder. Dr. Sidekick handles the conversion and drops everything
   into BOSS DATA_OUTGOING ready to load onto the SP-303.

   If you have more than 8 samples, they'll be split into BANK_LOAD_01,
   BANK_LOAD_02 etc. — just load one bank at a time on the device.


3. Convert an MPC1000 Program
   Got an MPC1000 .pgm file and its WAV samples? Go to
   Samples -> Convert MPC1000 Program (.pgm) and select the .pgm file.
   Dr. Sidekick finds the WAVs automatically (they're usually in the same
   folder), maps all 64 pads to SP-303 banks, applies the same 110ms padding
   and format fixes as Quick Import, and saves the result as a new virtual
   card in SmartMedia-Library/Cards/MPC1000.

   Load the BANK_LOAD folders onto your SP-303 one at a time.


4. Reassign Pads (SmartMedia Manager)
   Want to change which sample lives on which pad? Go to
   Samples -> SmartMedia Manager, load your card setup, make your changes,
   then hit Write Changes to Card.

   Tip: always back up first — use Backup Card before making any changes.
   If something doesn't look right, Restore Backup has you covered.


First time? Start small.
   Load your card setup, reassign just one pad, write to card, eject safely,
   and check it on the hardware before going further.
"""
        self.show_text_dialog("Quick Start", quick_start, geometry="980x580")

    def on_help_workflow_examples(self):
        """Show real-world workflow examples."""
        examples = """WORKFLOW EXAMPLES


─────────────────────────────────────────────────────────────
Example 1: Load a Kit and Program a Pattern from Scratch
─────────────────────────────────────────────────────────────

Goal: Get your own samples onto the SP-303 and program a beat
      ready to play back on the hardware.

Step 1 — Load your samples onto the card.
  Samples -> Quick Import WAV Folder -> select your kit folder.
  Files are prepared in User-Library/BOSS DATA_OUTGOING.
  If more than 8 WAVs, load BANK_LOAD_01 first, then BANK_LOAD_02
  on the device. Samples land on pads A1–D8 in file order.

Step 2 — Program the pattern.
  Home -> Edit Patterns. Select a pattern slot (C1–D8).
  Switch to Draw mode. Click pad rows to place hits.
  Drag to move. Right-click to delete.
  Set bar length with the Pattern Length spinner.
  Adjust velocity by selecting notes and using [ / ] keys.

  Alternatively, import your own MIDI file:
  File -> Import MIDI File -> select your file.
  Review events in the editor and adjust as needed.

  Optionally apply a groove to the pattern:
  Patterns -> Add Groove Pattern -> select your groove file.

Step 3 — Save and load onto the SP-303.
  File -> Save (Ctrl+S).
  Eject the card safely, insert into SP-303, and play.

Note: A library of example MIDI patterns and grooves is planned for a future release.


─────────────────────────────────────────────────────────────
Example 2: Convert an MPC1000 Kit to SP-303
─────────────────────────────────────────────────────────────

Goal: Bring an MPC1000 drum program straight onto the SP-303,
      preserving the original pad layout as closely as possible.

Step 1 — Samples -> Convert MPC1000 Program (.pgm).
  Select the .pgm file. If the WAV samples are in the same folder
  (or a subfolder), no further prompt appears.
  If WAVs live elsewhere, a folder picker opens.

Step 2 — Review the results dialog.
  Each bank (A–H) shows which WAV landed on which SMPL slot.
  NOT FOUND entries mean the .pgm referenced a sample name that
  wasn't matched in the WAV folder — check spelling or relocate.

Step 3 — Load onto the SP-303.
  Open SmartMedia-Library/Cards/MPC1000 in Finder.
  Copy BANK_LOAD_01 contents (SMPL0001–SMPL0008.WAV) to your card.
  On the SP-303 select the target bank and run Import.
  Repeat for each BANK_LOAD folder.

Note: Each run of Convert MPC1000 overwrites the MPC1000 card.
  Use Samples -> SmartMedia Library to snapshot it first if needed.


─────────────────────────────────────────────────────────────
Example 3: Reorganize a Card Without Losing Anything
─────────────────────────────────────────────────────────────

Goal: Safely reassign pads and shuffle patterns on an existing card.

Step 1 — Back up first.
  Samples -> SmartMedia Manager -> Backup Card.
  A timestamped backup folder is created in User-Library/Backups.

Step 2 — Load the current card setup.
  Samples -> SmartMedia Manager -> Load Card Setup.
  All current pad assignments appear in the table.

Step 3 — Reassign pads.
  Select a pad row, then use Assign WAV/SP0 to swap samples.
  The status bar confirms every change.

Step 4 — Remap or exchange patterns.
  Open the pattern editor. Use Edit -> Copy Pattern / Paste Pattern
  to move patterns between slots without re-programming.

Step 5 — Write changes.
  Samples -> SmartMedia Manager -> Write Changes to Card.
  Eject safely and verify on device.
  If anything is wrong, restore from the backup created in Step 1.
"""
        self.show_text_dialog("Workflow Examples", examples, geometry="980x560")

    def on_help_faq(self):
        """Show FAQ and troubleshooting notes for beta users."""
        faq = """FAQ / Troubleshooting (Beta)

Q: I selected a single WAV file in Quick Import. Is that valid?
A: Yes. The app uses that file's parent folder automatically.

Q: Why do I get BANK_LOAD_01 folders?
A: More than 8 WAV files were found. SP-303 loads one bank (8 samples) at a time.

Q: Where are Quick Import files written?
A: /Volumes/BOSS DATA or User-Library/BOSS DATA_OUTGOING.

Q: Existing WAVs disappeared from BOSS DATA_OUTGOING.
A: They are archived into the subfolder wav_archive_YYYYMMDD_HHMMSS.

Q: Write Changes completed, but device did not reflect changes.
A: Most common causes:
- Card not ejected safely before inserting into SP-303
- Wrong target output path selected check in both /Volumes/BOSS DATA and /BOSS DATA_OUTGOING/

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

Q: I ran Convert MPC1000 twice and my first import is gone.
A: Each run overwrites SmartMedia-Library/Cards/MPC1000. Use
   Samples -> SmartMedia Library to create a snapshot before re-running.

"""
        self.show_text_dialog("FAQ / Troubleshooting", faq, geometry="1024x680")

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
            self.root.after(0, lambda: (messagebox.showinfo(title, msg), self.update_status("Ready")))

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

        self.update_status("Checking for updates...")
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

        ttk.Label(frame, text=str(_LOG_PATH), font=("Courier", 9)).pack(anchor=tk.W, pady=(6, 0))

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

    def load_config(self):
        """Load app config from JSON file, migrating old recent files if present."""
        self.config: dict = {
            "device": "BOSS Dr. Sample SP-303",
            "card_mount_path": "",
            "write_to_card": True,
            "recent_files": [],
        }
        config_path = Path(__file__).parent / "dr_sidekick_config.json"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self.config.update(saved)
            except Exception:
                pass
        # Migrate old recent files file
        old_path = Path.home() / ".dr_sidekick_recent"
        if old_path.exists() and not self.config.get("recent_files"):
            try:
                lines = old_path.read_text(encoding="utf-8").splitlines()
                self.config["recent_files"] = [l.strip() for l in lines if l.strip()]
                self.save_config()
                old_path.unlink(missing_ok=True)
            except Exception:
                pass

    def save_config(self):
        """Save app config to JSON file."""
        config_path = Path(__file__).parent / "dr_sidekick_config.json"
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception:
            pass

    def get_card_mount_path(self) -> Path:
        """Return configured card mount path or auto-detect."""
        if sys.platform == "darwin":
            preferred = Path("/Volumes/BOSS DATA")
            if preferred.exists():
                return preferred
        config_val = self.config.get("card_mount_path", "")
        if config_val:
            return Path(config_val)
        return self.get_library_paths()["outgoing"]

    def load_recent_files(self):
        """Load recent files from config."""
        for line in self.config.get("recent_files", []):
            path = Path(line)
            if path.exists():
                self.recent_files.append(path)

    def save_recent_files(self):
        """Save recent files to config."""
        self.config["recent_files"] = [str(p) for p in self.recent_files]
        self.save_config()

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

    debug_mode = "--debug" in sys.argv[1:]
    root = TkinterDnD.Tk() if TKDND_AVAILABLE else tk.Tk()
    app = SP303PatternEditor(root, debug_mode=debug_mode)
    root.mainloop()
    log.info("Dr. Sidekick session ended")


if __name__ == '__main__':
    main()
