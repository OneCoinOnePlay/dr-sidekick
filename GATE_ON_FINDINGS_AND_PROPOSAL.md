# Gate On Findings And Proposal

## Purpose

This note captures the current hardware-backed findings around SP-303 Gate On pattern behavior, plus the proposed UI / decoder direction for follow-up validation and implementation.

This document is intended as a handover brief for:

1. a validation-focused agent
2. then an implementation-focused agent

## Current Conclusion

The earlier onset-only model is no longer sufficient.

Hardware testing now supports the conclusion that some PTNDATA tuple structures can encode playable sustained durations, not just onset blips.

Specifically:

- app-authored onset-only patterns replay as blips
- but raw tuple experiments produced sustained playback on hardware
- therefore the current app writer is incomplete for Gate-relevant behavior
- the pattern stream itself can influence duration behavior

## Important Distinction

Do not confuse these two cases:

- app-authored patterns written with the current writer
- hardware-authored or raw-crafted tuple streams tested directly on device

The current writer only knows how to serialize onset-oriented note events plus rests. That is not enough to recreate the sustained Gate On behavior discovered in testing.

So:

- playback failure from app-authored patterns is a writer limitation
- it is not evidence that the SP-303 pattern format is onset-only

## Real Hardware Reference

Reference backup:

- [`Backup/gate_on_pattern_backup_2026-04-05`](/Volumes/2TB/Audio/Boss%20SP-303/Dr_Sidekick/Backup/gate_on_pattern_backup_2026-04-05)

From that capture:

- active pattern was slot `C1`
- loop length was `8` bars
- sample slot `0` had `Gate = On`, `Loop = Off`
- the tuple stream contained same-pad edges and nontrivial `pad=0x80` control structure

This backup should remain the main reality anchor when validating future hypotheses.

## Raw Hypothesis Testing Summary

Later raw-crafted banks on the mounted card established:

- some tuple combinations remained silent or blip-like
- some produced sustained playback
- some produced different sustained lengths
- some appeared to latch or sustain almost to bar end

The decisive result came from replacing the long piano sample with:

- [`SMPL0001 Bass Junos B2.wav`](/Volumes/BOSS%20DATA/SMPL0001%20Bass%20Junos%20B2.wav)

and then recording the output of the existing test patterns:

- [`Recorded Sample Slot 0.wav`](/Volumes/BOSS%20DATA/Recorded%20Sample%20Slot%200.wav)
- [`Recorded Sample Slot 1.wav`](/Volumes/BOSS%20DATA/Recorded%20Sample%20Slot%201.wav)
- [`Recorded Sample Slot 2.wav`](/Volumes/BOSS%20DATA/Recorded%20Sample%20Slot%202.wav)
- [`Recorded Sample Slot 3.wav`](/Volumes/BOSS%20DATA/Recorded%20Sample%20Slot%203.wav)
- [`Recorded Sample Slot 4.wav`](/Volumes/BOSS%20DATA/Recorded%20Sample%20Slot%204.wav)
- [`Recorded Sample Slot 5.wav`](/Volumes/BOSS%20DATA/Recorded%20Sample%20Slot%205.wav)
- [`Recorded Sample Slot 6.wav`](/Volumes/BOSS%20DATA/Recorded%20Sample%20Slot%206.wav)
- [`Recorded Sample Slot 7.wav`](/Volumes/BOSS%20DATA/Recorded%20Sample%20Slot%207.wav)

## Caveat On Recorded Outputs

Loop endpoints in the recorded WAVs were trimmed by ear.

That means:

- exact tail alignment near the loop boundary is not fully trustworthy
- bar-end behavior for the longest sustains should be treated cautiously

But this does not materially weaken the main result, because the onset-to-drop timing inside each bar still showed consistent and meaningful differences.

## Strong Findings

### 1. `A -> B/C/D` same-pad edge pairs can encode variable sustain

Using the working shorthand from testing:

- `A = ff030000`
- `B = 7f001b00`
- `C = 7f002e00`
- `D = 7f002000`

Patterns using:

- `A -> B`
- `A -> C`
- `A -> D`

produced bar-by-bar sustained durations that tracked the programmed hold values.

This is the strongest current evidence that:

- same-pad edge pairs can represent a derived span
- the second edge timing matters
- the stream is not merely switching a global sustain state

### 2. `E/F/G`-type `pad=0x80` controls can force or latch sustained behavior

Working shorthand:

- `E = 7f003b00`
- `F = 7f008301`
- `G = 7f00a904`
- `H = 7f000e00`

Patterns using `A` plus these control tuples on `pad=0x80` often produced near-full-bar sustain rather than proportional lengths.

Current interpretation:

- these controls are meaningful playback-state instructions
- they are not equivalent to the simple edge-pair duration rule

### 3. The current app writer is insufficient

The writer path in [`dr_sidekick/engine/core.py`](/Volumes/2TB/Audio/Boss%20SP-303/Dr_Sidekick/dr_sidekick/engine/core.py) currently serializes onset-oriented note tuples and rests.

That is not enough to recreate the sustained behavior found by raw tuple experiments.

Implication:

- decoding/display can move ahead provisionally
- but authoring true Gate-relevant spans will require writer changes later

## Provisional Decode / Display Rules

These are provisional and require validation before implementation.

### Rule A

For hardware-shaped streams, when a pad event with prefix `A` is followed by a same-pad event with prefix `B`, `C`, or `D`, treat the pair as a candidate span:

- span start = first edge tick
- span end = second edge tick

### Rule B

`pad=0x80` tuples using `E`, `F`, `G`, `H` prefixes should be preserved and surfaced as meaningful control/state tuples.

Do not collapse them into generic filler.

### Rule C

Do not yet assume exact semantics for `E/F/G/H`.

What is safe to say now:

- they materially affect playback
- they are not mere noise
- they likely participate in state changes or latched sustain behavior

## Piano Roll Proposal

The current piano roll model is too narrow.

We intend to revise the piano roll into a step occupier model, or a hybrid model, instead of a fixed-width onset marker only.

### Why

The current fixed `12`-tick width offers little value for this problem:

- it does not reflect the active display step
- it does not reflect derived span behavior
- it does not help assess decoder quality for Gate-relevant patterns

### Proposed Direction

Preferred direction:

- use a step occupier model for normal sequencer display
- and support a hybrid mode for validated span-bearing patterns

Practical interpretation:

- onset-only patterns can still render as one occupied step cell
- patterns that match validated span rules can render as a derived occupied span
- spans should be understood as decoder-derived structure, not necessarily explicit note-off objects stored in the file

### Minimal UI Policy

Until broader semantics are validated:

- onset-only tuples: render as step occupiers
- validated `A -> B/C/D` edge pairs: render as spans
- unresolved `E/F/G/H` controls: preserve internally, and optionally expose in debug/inspection UI, but do not overstate their meaning

## Validation Tasks For The Next Agent

The next agent should validate before any implementation work.

Primary tasks:

1. Reconcile the current raw hypothesis banks with the real backup capture.
2. Confirm which slot/bar recipes map to:
   - proportional sustain
   - near-full-bar sustain
   - silence / blip
3. Check whether `A -> B`, `A -> C`, and `A -> D` are functionally equivalent except for prefix family, or whether they diverge in edge cases.
4. Test whether `E/F/G/H` controls behave as:
   - sustain latch
   - state transition
   - release modifier
   - or another mode-like instruction
5. Keep exact line-by-line notes of which raw tuple sequences are promoted into provisional rules.

Validation should stay conservative:

- preserve structure first
- only promote hardware-backed rules
- do not simplify ambiguous tuple families prematurely

## Implementation Tasks For The Following Agent

Only after validation.

Primary implementation targets:

1. Decoder support for provisional `A -> B/C/D` derived spans.
2. Piano roll rendering update toward step occupier / hybrid display.
3. Optional debug visualization for unresolved control tuples.
4. Keep existing raw-event preservation intact so future validation is not blocked.

## Final Position

The important architectural change is this:

- Dr Sidekick can no longer assume all meaningful pattern events are fixed-width onset markers

The best current direction is:

- preserve raw tuples
- validate derived span rules conservatively
- move the piano roll toward a step occupier or hybrid model that can display validated spans without pretending to know more than the hardware evidence supports

## Validation Addendum

The following validation outcomes supersede the broader provisional claims above.

Validation sources:

- real hardware anchor:
  [`Backup/gate_on_pattern_backup_2026-04-05/PTNDATA0.SP0`](/Volumes/2TB/Audio/Boss%20SP-303/Dr_Sidekick/Backup/gate_on_pattern_backup_2026-04-05/PTNDATA0.SP0)
- real hardware anchor:
  [`Backup/gate_on_pattern_backup_2026-04-05/PTNINFO0.SP0`](/Volumes/2TB/Audio/Boss%20SP-303/Dr_Sidekick/Backup/gate_on_pattern_backup_2026-04-05/PTNINFO0.SP0)
- raw hypothesis bank:
  [`Backup/gate_on_hypothesis_bank2_20260405_215909/PTNDATA0.SP0`](/Volumes/2TB/Audio/Boss%20SP-303/Dr_Sidekick/Backup/gate_on_hypothesis_bank2_20260405_215909/PTNDATA0.SP0)
- supportive recordings:
  [`Recorded Sample Slot 0.wav`](/Volumes/BOSS%20DATA/Recorded%20Sample%20Slot%200.wav) through [`Recorded Sample Slot 7.wav`](/Volumes/BOSS%20DATA/Recorded%20Sample%20Slot%207.wav)

### Narrowed Rule Set

#### `A -> B/C/D` same-pad edge pairs

This claim is partially rejected in its broad form.

What is safe to promote:

- same-pad `A -> B` can be treated as a derived span
- same-pad `A -> C` can be treated as a derived span

What is not yet safe to promote:

- `A -> D` as an equivalent clean span family

Current validated interpretation:

- some same-pad two-edge families derive spans
- `B` and `C` are hardware-backed proportional-span cases
- `D` remains unresolved / unstable

#### `E/F/G/H` `pad=0x80` tuples

This claim is only partially confirmed.

What is safe to say:

- these families are present in the real hardware anchor
- they are meaningful and must be preserved
- they should not be collapsed into generic filler

What is not yet safe to say:

- that their exact semantics are already known
- that every member of the family has the same role

#### Writer sufficiency

Confirmed:

- the current writer is insufficient for true Gate-relevant authoring
- it only emits onset tuples plus generic `pad=0x80` timing rests / fill
- it cannot author the validated sustained behaviors

### Validated Mapping

Ignore the earlier same-day backups `214248`, `214315`, and `214346`.

Their PTNDATA is empty and they are not the real tested bank.

The meaningful tested mapping is the bank2 image.

- Slot `0`: `A -> A`, offsets `24,48,72,96,120,144,192,288`
  Outcome: proportional sustain
  Status: supportive only
- Slot `1`: `A -> B`, same offsets
  Outcome: proportional sustain
  Status: promotable
- Slot `2`: `A -> C`, same offsets
  Outcome: proportional sustain
  Status: promotable
- Slot `3`: `A -> D`, same offsets
  Outcome: unstable / effectively bar-filling
  Status: unresolved
- Slot `4`: `B -> A`, same offsets
  Outcome: near-full-bar sustain
  Status: unresolved but meaningful
- Slot `5`: `B -> C`, same offsets
  Outcome: unstable
  Status: unresolved
- Slot `6`: `B -> D`, same offsets
  Outcome: near-full-bar sustain
  Status: unresolved but meaningful
- Slot `7`, bar `1`: `E -> A @24`
  Outcome: blip / short proportional
- Slot `7`, bar `2`: `F -> A @48`
  Outcome: short proportional
- Slot `7`, bar `3`: `G -> A @72`
  Outcome: short proportional
- Slot `7`, bar `4`: `H -> A @96`
  Outcome: near-full-bar sustain
- Slot `7`, bars `5-8`: `C -> B @120`, `D -> B @144`, `C -> D @192`, `D -> C @288`
  Outcome: near-full-bar sustain after the bar-4 transition

### Updated Implementation Guidance

The implementation agent should:

- promote only same-pad `A -> B` and same-pad `A -> C` to derived spans
- preserve all non-fill prefix families as opaque hardware tuples
- especially preserve:
  `7f001b00`, `7f002e00`, `7f002000`, `7f003b00`, `7f008301`, `7f00a904`, `7f000e00`
- surface unresolved tuples in debug / inspection UI instead of collapsing them into generic rests / filler
- avoid authored Gate-span support for now

The implementation agent should not:

- generalize `A -> D` as a clean span family
- assign fixed semantics to `E/F/G/H`
- retrofit the existing onset/rest writer with guessed Gate behavior

### Remaining Unknowns

- whether `A -> D` is a stable family or an unstable / latching edge case
- whether `E/F/G` have individual semantics beyond “behavior-affecting, non-noise tuples”
- exact bar-end sustain behavior for the longest cases, because recorded loop ends were trimmed by ear
