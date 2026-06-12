# Implemented Architecture

Consolidated: 2026-06-12 (from IMPLEMENTED_ARCHITECTURE_2026-04-05.md, updated
for the solved RDAC codec that replaced the experimental decoder)

This document describes the system behavior and boundaries that actually exist
in code. It is not a research note; open questions live in
[PATTERN_RESEARCH.md](PATTERN_RESEARCH.md).

## Core Design

The pattern subsystem separates three concerns:

1. raw hardware tuple preservation
2. conservative derived interpretation
3. display behavior based only on validated rules

The key architectural rule is: **preserve more than you interpret**. The
decoder keeps hardware tuple identity intact and only promotes a narrow
validated subset into higher-level span behavior.

## Pattern Decoder Layer

Primary file: `dr_sidekick/engine/core.py`

- Non-fill hardware tuple families (`7f001b00`, `7f002e00`, `7f002000`,
  `7f003b00`, `7f008301`, `7f00a904`, `7f000e00`) are preserved as meaningful
  structure, never flattened into generic timing filler.
- Only the validated subset is promoted into derived spans: same-pad `A -> B`
  and same-pad `A -> C` (see [PTNDATA_FORMAT.md](PTNDATA_FORMAT.md)). `A -> D`
  and the `E/F/G/H` controls remain opaque and inspectable.
- The decoder conceptually has two simultaneous outputs: raw preserved
  hardware structure, and a conservative interpreted layer for validated
  spans. UI and inspection tooling benefit from validated rules without
  losing unresolved hardware detail.
- `07031100`-family zero-delta note tuples decode as repeated prior steps,
  preserving overdub timing.

## Pattern Model Layer

Primary file: `dr_sidekick/engine/patterns.py`

- The model warns before saving hardware-derived Gate patterns whose structure
  cannot be faithfully re-authored by the current writer, preventing silent
  loss of meaning.
- The model may display and inspect validated Gate structures; it is not yet
  allowed to author them. That boundary is explicit.

## Writer Layer

Primary file: `dr_sidekick/engine/core.py`

The writer remains onset/rest based. It can write conventional app-authored
onset streams and rest/fill timing structure. It cannot author validated Gate
spans or emit the hardware-shaped tuple families used for sustained Gate
behavior — that is a separate future subsystem, not a small extension of the
onset serializer.

## Piano Roll Layer

Primary file: `dr_sidekick/ui/piano_roll.py`

Hybrid occupier model:

- onset-only tuples render as occupied steps based on the active display grid
- validated `A -> B/C` pairs render as spans
- unresolved tuples are preserved, inspectable, and not over-interpreted

## Debug / Inspection UI

Primary file: `dr_sidekick/ui/pattern_window.py`

A tuple inspector exposes preserved hardware tuple structure to support
validation of unresolved families without forcing hidden assumptions into the
main editor model.

## RDAC Sample Codec Layer

Primary files: `dr_sidekick/engine/sp303_codec.py`, `dr_sidekick/engine/rdac.py`

Sample audio decoding uses the solved codec ported from the Roland SP RDAC
Toolkit, replacing the earlier experimental decoder. All three modes decode at
native rates, with the mode selected per pad from the SMPINFO rate field
(see [SMPINFO_FORMAT.md](SMPINFO_FORMAT.md)):

- STANDARD (44.1 kHz) — 12-byte unit-6 blocks in 512-byte pages
- LONG (22.05 kHz) — identical framing, half playback rate
- LO-FI (11.025 kHz) — raw 8-byte unit-4 blocks, no page framing

SMPINFO metadata is authoritative for decode: the rate field selects the
framing and the length field counts audio bytes only (page trailers excluded).
Every producer of SMPINFO metadata must maintain those invariants.
`rdac.py` provides the shared control/window machinery; `sp303_codec.py`
provides the SP-303-specific families, page-trailer handling, and stream
decoders. The codec is validated byte-exact against hardware-written
reference cards.

## Restore-To-Card

Primary files: `dr_sidekick/engine/core.py`, `dr_sidekick/ui/library_window.py`

- stale sample-related `*.SP0` files on the destination are removed
- source sample-related `*.SP0` files are copied in
- destination `PTNINFO0.SP0` / `PTNDATA0.SP0` are preserved unless the virtual
  card explicitly includes them

Contract: replace sample/card audio content from the virtual card; preserve
destination patterns by default. Backed by regression coverage.

## Test Coverage

Primary tests:

- `tests/test_hardware_ptndata_decode.py` — validated hardware decode
  regressions, overdub decode behavior
- `tests/test_restore_to_card.py` — stale SP0 cleanup / pattern preservation
- `tests/test_pattern_capacity.py` — event-capacity warnings
- `tests/test_quick_import.py` — WAV quick-import validation

Still weak or missing: authored Gate-span writing, complete semantics for
unresolved tuple families, exhaustive UI-level hybrid-rendering behavior.

## Intentional Unknowns

Left unresolved by design (the architecture tolerates them by preserving
unresolved structure instead of guessing):

- whether `A -> D` is a stable span family
- exact semantics of `E/F/G/H`
- complete bar-end sustain semantics for the longest hardware cases
- any writer-side representation for true Gate-span authoring

## Working Principle

- Validated structure may be promoted for display.
- Unresolved structure must be preserved for inspection.

That principle remains in place until additional hardware validation justifies
promoting more tuple families into higher-level editing semantics.
