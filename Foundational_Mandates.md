# Core Mandates: Musical & Timing Integrity

This project prioritizes musical timing over technical "best-fits." To prevent recurrence of timing drift and pattern corruption, all agents and developers MUST adhere to these architectural rules.

## 1. Zero-Truncation Policy
- **NO `int()` for Ticks:** Never use `int()` to convert MIDI or groove ticks. This causes cumulative timing drift.
- **MANDATORY `round()`:** Always use `round()` for coordinate and time-base conversions to ensure notes snap to the nearest valid tick rather than falling behind.
  - *Correct:* `new_tick = round(old_tick * scale_factor)`
  - *Incorrect:* `new_tick = int(old_tick * scale_factor)`

## 2. Deterministic Bar Boundaries
- **NO "Fuzzy Math":** Avoid using `+ 0.999` or other heuristics to determine pattern length.
- **EXPLICIT Calculation:** Pattern length (in bars) must be calculated based on a strict 96 PPQN (384 ticks per 4/4 bar). Any note at or beyond a bar boundary must be handled by an explicit check against fixed bar-lengths.

## 3. Preservation of Musical Performance
- **Category Distinction:**
    - **Grooves (Grid-Type):** Used for 1-to-1 quantization maps. If an offset exceeds 50% of the grid size (e.g., > 12 ticks on a 24-tick grid), it MUST be reclassified as a Pattern.
    - **Patterns (Compound-Type):** Used for raw musical performances (flams, rolls, ghost notes). NEVER attempt to force a Pattern into a Grid-Offset model.
- **Capacity Warnings:** The SP-303 has a strict event limit (approx. 112 events per slot). If an import or edit exceeds this capacity, the system MUST warn the user rather than silently truncating or dropping events.

## 4. Hardware Loop Fidelity
- **Loop Points:** The final delta in a pattern MUST point exactly to the end of the calculated bar length. Any discrepancy of even 1 tick will cause hardware loop "hiccups."
