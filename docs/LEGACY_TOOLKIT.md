# Legacy SP-303 Toolkit (Archive) — History and Superseded Claims

Consolidated: 2026-06-12 (from Archive/PROJECT_STATUS.md,
Archive/PATTERN_FIX_v2.5_COMPLETE.md, Archive/QUICK_START_GUIDE.md,
Archive/QUICKSTART_GUI.md, Archive/SP303_PACK_GUIDE.md)

Before Dr. Sidekick, this project shipped as a CLI toolkit (card prep scripts,
`sp303_midi_patternsV2.py`, a standalone pattern-editor GUI, a pack builder,
and an interactive wizard). It lives in `/Volumes/2TB/Boss SP-303/Archive/`
(its own git repository) and is no longer maintained. Dr. Sidekick replaces
all of its workflows.

## What the toolkit got right (carried forward)

These hardware-verified findings from the v2.5 pattern fix remain canonical
and are folded into [PTNDATA_FORMAT.md](PTNDATA_FORMAT.md):

- **Never modify the PTNDATA header** (`8a b1 07 03`). Pattern presence is
  indicated by event data, not a header change.
- **Event index table is `ff 80 00 00 10 00` repeating** for the 0x70-byte
  index area (not `ff 80 04 03 16 00` as earlier notes had it).
- **Global metadata stays at init-template values.** Working hardware captures
  were byte-identical to the official init template; modifying 0xF4D0/0xFA3C/
  0xFABE/0xFDA0 broke compatibility.
- **Copy hardware dumps byte-for-byte** rather than reverse-engineering
  formulas — checksum "formulas" were really observed-value tables.
- Core PTNDATA layout: 16 × 1 KB slots in reverse order
  (`0xED90 - slot × 0x400`), 6-byte delta-encoded events.

## Superseded claims (do not trust the Archive docs on these)

| Archive claim | Current understanding |
|---------------|----------------------|
| SMPINFO has "16 × 512-byte slot records" | Slot records are **48 bytes** at `slot × 48`; see [SMPINFO_FORMAT.md](SMPINFO_FORMAT.md) |
| SMPINFO / PTNINFO / PTNDATA "FULLY DECODED" | Core layouts verified, but checksums, global metadata, Gate tuple families, and `E/F/G/H` controls remain partially understood |
| PTNINFO active entries `04 b0 QQ 00` store quantize | PTNINFO is `b0 04 <bars> <pattern_index>`; quantize is in-stream PTNDATA state |
| PTNINFO is static `b0 04 02 [slot+1]` | Byte 2 is the per-pattern bar count (1–99); byte 3 is a mapping pointer that hardware swaps on pattern exchange |
| Patterns are onset-only 6-byte events | Hardware Gate patterns carry additional tuple families; same-pad `A -> B`/`A -> C` pairs encode spans |
| Checksum lookup (1 event → 0x00, 2 → 0x06, 3 → 0x09) | Observed values only; formula still unsolved — preserve observed behavior |
| "No GUI (CLI only)" | Dr. Sidekick is a full Tk GUI (pattern sequencer, sample manager, library) |
| Pack format v2.0 (banks/build/arrangement schema) | Replaced by the simpler implemented format; see [PACK_FORMAT.md](PACK_FORMAT.md) |
| Velocity fixed at 0x7F, no editing | Dr. Sidekick supports velocity editing in the sequencer |

## Still-true hardware workflow facts (from the old quick-start guides)

- WAV import onto the device: copy files to the card, press **BANK C** (or D)
  then **REC**, wait for conversion. One bank per import operation (hardware
  limitation). Dr. Sidekick's *Quick Import WAV Folder* prepares
  `BOSS DATA_OUTGOING/` sets for exactly this flow.
- Minimum sample duration ~110 ms (Dr. Sidekick auto-pads).
- Banks A & B are ROM; user samples live in Banks C & D (16 slots).
- Files 1–8 map to Bank C pads 1–8, files 9–16 to Bank D pads 1–8.

For current usage instructions, see the top-level [README.md](../README.md).
