# Pattern Research Notes — Gate Spans & Overdub Semantics

Consolidated: 2026-06-12 (from GATE_ON_FINDINGS_AND_PROPOSAL.md and
OVERDUB_HANDOVER.md)

Validated findings have been promoted into
[PTNDATA_FORMAT.md](PTNDATA_FORMAT.md) and implemented per
[ARCHITECTURE.md](ARCHITECTURE.md). This note keeps the evidence trail and the
open questions for future hardware-validation sessions.

## Working assumptions (keep active)

- Duplicate hits are meaningful overdub data, not noise. Do not deduplicate
  same-pad / same-tick events on import.
- PTNINFO stores pattern length and mapping, not quantize.
- Pattern tempo is device-global, not stored per-pattern in PTNINFO/PTNDATA.
- Mid-record quantize changes are recorded in-stream, not in PTNINFO.
- Preserve structure first, interpret later. Promote only hardware-validated
  rules into the decoder.

## Gate-span validation (2026-04-05)

### Evidence anchors

- Real hardware capture: `Backup/gate_on_pattern_backup_2026-04-05/`
  (active pattern C1, 8-bar loop, slot 0 Gate=On Loop=Off; tuple stream
  contains same-pad edges and nontrivial `pad=0x80` control structure).
- Raw hypothesis bank: `Backup/gate_on_hypothesis_bank2_20260405_215909/`.
  Ignore the earlier same-day backups `214248`, `214315`, `214346` — their
  PTNDATA is empty; they are not the real tested bank.
- Recorded outputs: `Recorded Sample Slot 0–7.wav` (on the BOSS DATA card),
  captured after replacing the long piano sample with
  `SMPL0001 Bass Junos B2.wav`. Loop endpoints were trimmed by ear, so exact
  tail/bar-end alignment for the longest sustains is approximate; the
  onset-to-drop timing inside each bar is trustworthy.

Tuple shorthand: `A=ff030000 B=7f001b00 C=7f002e00 D=7f002000 E=7f003b00
F=7f008301 G=7f00a904 H=7f000e00`.

### Validated bank2 mapping

| Slot | Recipe (offsets 24,48,72,96,120,144,192,288) | Outcome | Status |
|------|----------------------------------------------|---------|--------|
| 0 | `A -> A` | proportional sustain | supportive only |
| 1 | `A -> B` | proportional sustain | **promoted** |
| 2 | `A -> C` | proportional sustain | **promoted** |
| 3 | `A -> D` | unstable / effectively bar-filling | unresolved |
| 4 | `B -> A` | near-full-bar sustain | unresolved but meaningful |
| 5 | `B -> C` | unstable | unresolved |
| 6 | `B -> D` | near-full-bar sustain | unresolved but meaningful |
| 7 bar 1 | `E -> A @24` | blip / short proportional | unresolved |
| 7 bar 2 | `F -> A @48` | short proportional | unresolved |
| 7 bar 3 | `G -> A @72` | short proportional | unresolved |
| 7 bar 4 | `H -> A @96` | near-full-bar sustain | unresolved |
| 7 bars 5–8 | `C->B @120`, `D->B @144`, `C->D @192`, `D->C @288` | near-full-bar sustain after the bar-4 transition | unresolved |

### Conclusions

- Same-pad `A -> B` and `A -> C` edge pairs are hardware-backed proportional
  spans (promoted to canonical; decoded and rendered as spans).
- `A -> D` remains unresolved/unstable; not promoted.
- `E/F/G/H` `pad=0x80` controls are meaningful playback-state instructions
  present in the real hardware anchor — preserved as opaque tuples, semantics
  unknown. Do not assign fixed semantics or collapse them into filler.
- The current app writer is insufficient for Gate authoring: it serializes
  onset tuples plus generic rests/fill only. Playback failure of app-authored
  Gate patterns is a writer limitation, not evidence the format is onset-only.
  Do not retrofit the onset/rest writer with guessed Gate behavior.

### Remaining unknowns

- Whether `A -> D` is a stable family or a latching edge case.
- Whether `E/F/G` have individual semantics beyond "behavior-affecting,
  non-noise tuples" (`H` looked latch-like in bar 4).
- Exact bar-end sustain behavior for the longest cases (recordings trimmed by
  ear).

## Overdub / quantize research

### Confirmed (promoted to PTNDATA_FORMAT.md)

- PTNINFO entry `b0 04 <bars> <pattern_index>`; quantize not stored there.
- Overdub duplicates are real stored events.
- `07031100` zero-delta note tuples mean "repeat the previous nonzero note
  step" (validated on hardware; fixed the slot-6 under-length decode,
  0–744 ticks for a 16+16 two-bar structure).
- App-authored loop tail: final note with `delta 00`, explicit loop-closing
  rest, immediate fill `ff8000001000`.
- Quantize transitions stay within the `07030600` stream family; the signal is
  in-stream `pad=0x80` control tuples (e.g. `818007030600`, `428007030600`,
  `848007030600`, `348007030600`), not the top-level slot marker.
- `04031600` appears in hardware-correct patterns; marker family alone does
  not prove provenance.

### Open questions / highest-value next steps

1. **Characterize overdub state transitions** — for `07030600` and `07031100`
   patterns, map positions of `pad=0x80` control tuples against known user
   actions: loop recording, overdub layering, mid-record quantize changes,
   erase while recording.
2. **Investigate note side-bytes** — do note tuples with `flags = 3`, `s1 = 1`
   mark pass boundaries, overdub state, erase state, or quantize transitions?
3. **Controlled erase-during-overdub captures** — same base pattern, overdub
   added, then erase one pad during loop recording. Does the stream store
   destructive edits as new event/control structure rather than note removal?
4. **Gate follow-ups** — writer support for authored spans; further validation
   of `A -> D` and `E/F/G/H`; duplicate-hit visualization polish.

### Practical reminder

Do not restart from generic decoder archaeology. Take small hardware-recorded
references, compare raw tuples around action boundaries, and promote only
hardware-validated rules. Files worth reading first:
`dr_sidekick/engine/core.py`, `dr_sidekick/engine/patterns.py`,
`tests/test_hardware_ptndata_decode.py`.
