# Dr. Sidekick

Standalone graphical pattern editor and SmartMedia librarian for the **Boss SP-303 Dr. Sample**.

![Beta](https://img.shields.io/badge/status-beta-orange)
![Python 3](https://img.shields.io/badge/python-3.x-blue)
![macOS](https://img.shields.io/badge/platform-macOS-lightgrey)

---

## What It Does

- **Pattern Editor** — draw, edit, and arrange pad events on a piano-roll canvas; import from MIDI files
- **Sample Management** — load a card setup (SMPINFO), reassign pads, write changes back to the SmartMedia card
- **Quick Import WAV Folder** — prepare WAV sets for one-bank-at-a-time loading onto the SP-303
- **Library Manager** — catalog and load sample packs and groove sets
- **Backup / Restore** — create and restore full SP0 card backups

## Requirements

- Python 3.9 or later
- Tkinter (included with most Python distributions)
- macOS (primary target; Linux/Windows untested)
- `PTNDATA_INIT_OFFICIAL.bin` alongside `Dr_Sidekick.py` (included in this repo) — a byte-perfect initialization template captured from real SP-303 hardware. Without it the app falls back to a software-generated template that may not produce fully hardware-compatible files.

Optional: `tkinterdnd2` enables drag-and-drop support. The app runs without it.

## Run

```bash
python3 Dr_Sidekick.py
```

Or make it executable:

```bash
chmod +x Dr_Sidekick.py
./Dr_Sidekick.py
```

Debug mode (extra logging):

```bash
python3 Dr_Sidekick.py --debug
```

Syntax check:

```bash
python3 -m py_compile Dr_Sidekick.py
```

## Status

Beta. Core workflows are functional and have been tested against real SP-303 hardware.
Please report issues at [github.com/OneCoinOnePlay/dr-sidekick/issues](https://github.com/OneCoinOnePlay/dr-sidekick/issues).

## File Format Notes

Dr. Sidekick reads and writes the SP-303's native SmartMedia card format:

| File | Purpose |
|------|---------|
| `PTNDATA0.SP0` | Pattern event data (16 slots × 1024 bytes) |
| `PTNINFO0.SP0` | Pattern metadata and slot mapping (64 bytes) |
| `SMPINFO0.SP0` | Sample slot assignments (65 536 bytes) |
| `SMPxxxxL/R.SP0` | Sample audio data |

## Architecture

The entire application is a single file: `Dr_Sidekick.py`. There are no external modules beyond the standard library and Tkinter.

**Layer 1 — Binary format engine**
Parses and writes `PTNINFO0.SP0`, `PTNDATA0.SP0`, and `SMPINFO0.SP0`. Handles delta-tick serialization, rest-event chaining for gaps over 255 ticks, and slot-to-pattern pointer mapping.

**Layer 2 — Library and session utilities**
JSON catalog for indexing local sample and groove assets under `User-Library/`. Pure-Python MIDI format 0 parser (no third-party MIDI library). Card-prep orchestration for copying files to the SmartMedia directory structure.

**Layer 3 — GUI**
`PatternModel` owns the data and undo/redo stack. `PianoRollCanvas` is the visual editor (X = ticks, Y = 32 pad lanes across Banks A–D). `SP303PatternEditor` is the main window with all toolbar, menu, and dialog logic.

## User-Library

`User-Library/` holds local sample and groove assets:

```
User-Library/
  BOSS DATA_INCOMING/   # staging area for card reads
  BOSS DATA_OUTGOING/   # output for Quick Import WAV
  Songs/
```

The Library Manager indexes content here in `sp303_library_catalog.json`.

## License

© OneCoinOnePlay. All rights reserved.
