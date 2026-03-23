# Agent Notes

Use this workspace as a boundary.

Preferred sequence:
1. Re-read the relevant `RDAC_HANDOVER.md` sections only
2. Focus on one real block
3. Get executed-state evidence
4. Compare the trace against the current software-side instrumentation
5. Only then recommend decoder changes

Do not assume:
- jump-table `param` equals residual count
- generic SH-DSP docs prove the SP-303 path
- a plausible selector/anchor interpretation is correct without runtime trace evidence

If blocked, produce:
- what exact trace is still missing
- what instruction/address range needs better decoding
- what minimal next experiment would resolve the ambiguity
