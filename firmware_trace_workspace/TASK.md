# Task

Build a bit-accurate SH-DSP firmware tracer for the SP-303 RDAC path.

Primary objective:
- Produce executed-state evidence from `sp303.prg` and `SMP0000L.SP0` that resolves the decoder ambiguities recorded in `RDAC_HANDOVER.md`.

Immediate first milestone:
1. Trace REF1 block 0 from `SMP0000L.SP0`
2. Show exact selector assembly
3. Show jump-table dispatch behavior at runtime
4. Show post-`movx.w` / post-`movy.w` register state
5. Show 2-4 successive `pshl` steps from a real loaded state
6. Show observed cursor movement and residual writes

Do not spend time on:
- broad repo cleanup
- UI work
- decoder “improvements” without trace evidence
- speculative architecture summaries

Expected deliverables:
- tracer implementation
- machine-readable trace output
- short grounded writeup of findings
- updates back into `RDAC_HANDOVER.md` after evidence exists
