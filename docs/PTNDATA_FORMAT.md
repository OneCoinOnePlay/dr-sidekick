# SP-303 Pattern Format — PTNDATA0.SP0 / PTNINFO0.SP0

Status: AUTHORITATIVE
Consolidated: 2026-06-12 (from PTNDATA_CANONICAL.md 2026-04-05, the overdub
handover notes, and the v2.5 pattern-fix findings)

This document is the single source of truth for the verified pattern format.
Open research questions (Gate tuple semantics, overdub state transitions) live
in [PATTERN_RESEARCH.md](PATTERN_RESEARCH.md).

## Executive Summary

- All 16 pattern slots are stored in PTNDATA. Verified by cold-boot hardware
  testing.
- Storage is a linear array of 16 slots, each 1024 bytes, in reverse order.
- Event data is serialized as 6-byte records with delta-to-next timing.
- PTNINFO is a slot-to-pattern mapping layer carrying per-pattern loop length;
  it does not store quantize and does not reliably encode slot occupancy.
- Quantize (including mid-record changes) is recorded in-stream in PTNDATA,
  not in PTNINFO.
- Hardware-shaped Gate patterns use additional tuple families beyond the
  onset-only model; see the Gate section below.
- Duplicate same-pad hits are real overdub data and must be preserved.
- Some metadata and checksum formulas remain unknown, but the core layout is
  stable and verified.
- C/D patterns persist after device initialization while A/B patterns are
  cleared, confirming card-resident pattern storage.

## PTNINFO0.SP0 (64 bytes)

Each of the 16 slots uses 4 bytes. The hardware-verified mapping form is:

```
B0 04 <bars> <pattern_index>
```

- `<bars>` — per-pattern loop length in bars (1–99).
- `<pattern_index>` — mapped PTNDATA storage slot (1–16). The default mapping
  is identity (`slot_index + 1`).

Notes:

- **Quantize is not stored here.** An earlier reading of byte 2 as a quantize
  field (`04 B0 QQ 00` "active" entries) is superseded; quantize changes are
  recorded in the PTNDATA event stream. The reader in
  `dr_sidekick/engine/core.py` (`PatternSlot.from_bytes`) still accepts the
  legacy `04 B0` byte order for old app-authored captures.
- **Occupancy is not encoded here.** Hardware-authored cards use the same
  mapping form for empty and populated slots; PTNDATA holds the pattern body.
  Occupancy must be determined from the tuple stream.

### Mapping and pattern exchange (verified 2026-02-04)

Hardware exchange tests show that swapping patterns between pads updates
PTNINFO but does not move any PTNDATA slot data:

- Exchanging patterns changes only the per-slot 4-byte entries (the final
  `<pattern_index>` bytes are swapped) plus a small PTNDATA metadata delta.
- PTNDATA slot regions remain byte-identical during pattern exchange.

Implication: PTNDATA holds pattern bodies by index (1–16); PTNINFO maps
pad selection to a pattern index via the final byte.

## PTNDATA0.SP0 file layout (65,536 bytes)

```
0x0000-0x0003: Header — 8a b1 07 03 on the init template. NEVER modify it;
               pattern presence is indicated by the event data, not the header.
0x0004-0xB18F: Unknown/reserved
0xB190-0xF18F: 16 slots × 1024 bytes, REVERSE order (slot 15 first)
0xF190-0xFFFF: Global metadata
```

### Slot offset formula

```
slot_offset = 0xED90 - (slot_index * 0x400)
```

- Slot 0: 0xED90
- Slot 8: 0xCD90
- Slot 15: 0xB190

## Per-slot structure (1024 bytes)

```
Offset +0x000: Event index table (6 bytes per entry, 0x70 bytes total)
Offset +0x070: Event data (header + 6-byte events)
```

### Event index entry (6 bytes)

```
ff 80 00 00 10 00
```

Repeated for the entire 0x70-byte index area. (Early notes used
`ff 80 04 03 16 00`; hardware dumps confirm `ff 80 00 00 10 00`.)

### Event data header (6 bytes)

```
[header_checksum] 80 04 03 16 00
```

The checksum formula is not fully solved; observed values vary by event
count/pad. Treat as checksum-like and preserve observed behavior.

## Serialized event tuples (6 bytes per event)

```
[delta_ticks] [pad] [velocity] [flags] [s1] [s2]
```

- Byte 0: Delta ticks to the NEXT event (0–255). For the last event this is a
  checksum-like value.
- Byte 1: Pad number `0x10–0x1F`, or `0x80` for control/fill tuples.
- Byte 2: Velocity (typically `0x7F`).
- Byte 3: Flags — `0x00` normal/last, `0x01` first-of-three marker (observed),
  `0x03` more events follow (observed).
- Bytes 4–5: Side bytes, normally `0x00 0x00`; nonzero values appear in
  hardware-shaped streams and are meaningful (see Gate section and
  [PATTERN_RESEARCH.md](PATTERN_RESEARCH.md)).

### Timing encoding (solved)

The timing byte is the delta to the next event in ticks at 96 PPQN
(384 ticks per 4/4 bar). Example (ticks 0, 48, 96):

```
Event 1 delta: 0x30 (48)
Event 2 delta: 0x30 (48)
Event 3 delta: [last-event checksum]
```

### Stream-family specifics

- **`07031100` family:** note tuples with `delta == 0` mean "repeat the
  previous nonzero note step," not "same tick." Hardware-validated; fixes a
  major under-length decode error (a two-bar 16+16 pattern previously decoded
  as ending at tick 504 now decodes across 0–744).
- **`04031600` family:** appears in hardware-correct patterns too — the marker
  family alone does not prove app vs. hardware provenance.
- **`07030600` family:** observed in quantize-Off and quantize-transition
  patterns. Mid-stream `pad=0x80` control tuples with `0703xxxx` side bytes
  mark recording-state transitions, likely including quantize changes
  (e.g. `818007030600`, `428007030600`, `848007030600`, `348007030600`).
  Preserve them; exact user-facing quantize values are not needed for decode.
- **Overdub duplicates:** hardware loop recording stores repeated same-pad
  hits as distinct events. Preserve duplicates by default.

### App-authored loop tail (hardware-verified)

The working tail shape for the app writer is:

- final note tuple with `delta 00`
- explicit loop-closing rest tuple
- immediate fill tuple `ff 80 00 00 10 00`

The final delta must point exactly to the end of the calculated bar length or
the hardware loop hiccups (see
[DEVELOPMENT_MANDATES.md](DEVELOPMENT_MANDATES.md)).

## Global metadata (0xF190–0xFFFF, partially understood)

Known fields:

- Event count at 0xFA3C (value increments with event count)
- Active flag at 0xFABE

Other fields change per pattern but formulas are unknown. **The app writer
leaves metadata at the init-template values** — hardware-working captures were
byte-identical to the official init template here, and modifying these fields
broke hardware compatibility in earlier toolkit versions.

## Gate tuple families (validated 2026-04-05)

The onset-only model is too narrow for hardware-shaped Gate patterns: some
tuple structures encode playable sustained durations.

Working shorthand from validation:

```
A = ff030000    B = 7f001b00    C = 7f002e00    D = 7f002000
E = 7f003b00    F = 7f008301    G = 7f00a904    H = 7f000e00
```

Safe canonical constraints:

- Preserve non-fill tuple families; do not collapse them into generic timing
  filler.
- Same-pad `A -> B` pairs act as derived spans (span start = first edge tick,
  span end = second edge tick).
- Same-pad `A -> C` pairs act as derived spans.

Not yet canonical (see [PATTERN_RESEARCH.md](PATTERN_RESEARCH.md)):

- `A -> D` as a stable clean span family
- exact semantics for `E/F/G/H` `pad=0x80` controls (meaningful and
  preserved, but unresolved)
- authored Gate-span writing rules — the current onset/rest writer cannot
  faithfully re-author hardware Gate structure and must not guess

## Verified vs unknown

Verified:

- 16-slot linear storage and slot offset formula
- Event index table layout
- 6-byte event layout; pad encoding 0x10–0x1F
- Timing byte as delta-to-next at 96 PPQN
- PTNINFO bars + mapping semantics
- `07031100` zero-delta step-repeat semantics
- Same-pad `A -> B` / `A -> C` derived spans

Unknown / partial:

- Header checksum formula
- Last-event delta checksum formula
- Full meaning of global metadata fields
- `A -> D`, `E/F/G/H` semantics
- Note side-bytes with `flags = 3`, `s1 = 1`

## Implementation notes

- Write events in chronological order; use delta-to-next for all but the last
  event.
- The last-event delta and header checksum should preserve observed behavior;
  treat as checksum-like values until fully solved.
- Never modify the file header or global metadata template.
- Decode conservatively: promote only validated structure, preserve the rest
  (see [ARCHITECTURE.md](ARCHITECTURE.md)).
