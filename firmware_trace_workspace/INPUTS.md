# Inputs

## Authoritative documents

- Handover doc:
  [`/Volumes/2TB/Audio/Boss SP-303/dr_sidekick docs/RDAC_HANDOVER.md`](/Volumes/2TB/Audio/Boss%20SP-303/dr_sidekick%20docs/RDAC_HANDOVER.md)

## Firmware and sample data

- Local mirrored firmware binary:
  [`/Volumes/2TB/Audio/Boss SP-303/Dr_Sidekick/firmware_trace_workspace/support/sp303.prg`](/Volumes/2TB/Audio/Boss%20SP-303/Dr_Sidekick/firmware_trace_workspace/support/sp303.prg)
- Local mirrored test signal directory:
  [`/Volumes/2TB/Audio/Boss SP-303/Dr_Sidekick/firmware_trace_workspace/support/Test Signals`](/Volumes/2TB/Audio/Boss%20SP-303/Dr_Sidekick/firmware_trace_workspace/support/Test%20Signals)
- Local mirrored REF1 RDAC data:
  [`/Volumes/2TB/Audio/Boss SP-303/Dr_Sidekick/firmware_trace_workspace/support/Test Signals/SMP0000L.SP0`](/Volumes/2TB/Audio/Boss%20SP-303/Dr_Sidekick/firmware_trace_workspace/support/Test%20Signals/SMP0000L.SP0)

Use the mirrored copies above by default for tracer development inside this workspace.

Original source locations:
- Firmware binary:
  [`/Volumes/2TB/Audio/Boss SP-303/dr_sidekick docs/Firmware Analysis/sp303.prg`](/Volumes/2TB/Audio/Boss%20SP-303/dr_sidekick%20docs/Firmware%20Analysis/sp303.prg)
- Test signal directory:
  [`/Volumes/2TB/Audio/Boss SP-303/Dr_Sidekick/SmartMedia-Library/Cards/Test Signals`](/Volumes/2TB/Audio/Boss%20SP-303/Dr_Sidekick/SmartMedia-Library/Cards/Test%20Signals)
- REF1 RDAC data:
  [`/Volumes/2TB/Audio/Boss SP-303/Dr_Sidekick/SmartMedia-Library/Cards/Test Signals/SMP0000L.SP0`](/Volumes/2TB/Audio/Boss%20SP-303/Dr_Sidekick/SmartMedia-Library/Cards/Test%20Signals/SMP0000L.SP0)

### What the test-signal files are

The `Test Signals` directory is the ground-truth fixture set for RDAC work.

File types used there:
- `SMPxxxxL.SP0`
  SP-303 RDAC sample data captured in the device's native sample-file format.
  This is the compressed data that the firmware decoder actually consumes block-by-block.
- `SMPxxxxR.SP0`
  Right-channel companion file for stereo samples when present.
- `SMPLxxxx.WAV` or similarly named WAV references
  Reference PCM audio used to understand what the decoded result should resemble.
  These are useful for validation, but the tracer must still ground behavior from firmware execution.
- `SP0 to WAV/`
  Output folder used by local diagnostics and decoder experiments. Treat these as generated artifacts, not source truth.

### Why these files matter

- `sp303.prg` tells us how the SP-303 firmware executes the decode path.
- `SMPxxxxL/R.SP0` gives the exact input bytes the firmware consumes.
- Reference WAVs help check whether a firmware-grounded decode model produces sensible output, but they do not replace trace evidence.

### Initial trace targets inside Test Signals

Start with:
- `support/Test Signals/SMP0000L.SP0` (REF1)
  First target for block-level tracing.
  Use block 0 first, then select one standard-anchor block and one non-standard-anchor block if available.

## Existing software-side instrumentation

- Decoder scaffold:
  [`/Volumes/2TB/Audio/Boss SP-303/Dr_Sidekick/dr_sidekick/engine/core.py`](/Volumes/2TB/Audio/Boss%20SP-303/Dr_Sidekick/dr_sidekick/engine/core.py)
- Trace utility:
  [`/Volumes/2TB/Audio/Boss SP-303/Dr_Sidekick/diag_dispatch_trace.py`](/Volumes/2TB/Audio/Boss%20SP-303/Dr_Sidekick/diag_dispatch_trace.py)

## Existing validation script

- Current smoke test:
  [`/Volumes/2TB/Audio/Boss SP-303/Dr_Sidekick/test_rdac.py`](/Volumes/2TB/Audio/Boss%20SP-303/Dr_Sidekick/test_rdac.py)

## Focus firmware regions

- Dispatcher: `0x5D18..0x6084`
- Jump table: `0xBCD0..0xBCEF`
- Target routines:
  - `0x6D00`
  - `0x6100`
  - `0x1900`
  - `0x2900`
  - `0x3808`
- Hotspot ranges:
  - `0x69xx`
  - `0x6Cxx`

## Known solid facts to start from

- Selector 6 jump-table entry bytes: `00 00 00 00`
- Current software trace for REF1 block 0 reports selector bits `110000001110000`
- Current software trace for REF1 block 0 reports selectors `[6, 0, 1, 6, 0]`
- `Record[8]` remains unused in playback until tracer evidence proves otherwise
