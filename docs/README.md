# Dr. Sidekick Documentation

Consolidated project documentation, 2026-06-12. This set supersedes the
scattered notes that previously lived in `dr_sidekick docs/`, the repo root,
and the legacy toolkit `Archive/`.

## Contents

| Document | Covers |
|----------|--------|
| [DEVELOPMENT_MANDATES.md](DEVELOPMENT_MANDATES.md) | Non-negotiable timing/integrity rules for all pattern code |
| [PTNDATA_FORMAT.md](PTNDATA_FORMAT.md) | Canonical PTNDATA0.SP0 + PTNINFO0.SP0 format specification |
| [SMPINFO_FORMAT.md](SMPINFO_FORMAT.md) | Canonical SMPINFO0.SP0 format and RDAC sample-mode selection |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Implemented system architecture: decoder, codec, UI layers, restore-to-card |
| [PATTERN_RESEARCH.md](PATTERN_RESEARCH.md) | Open research: Gate-span tuple families, overdub semantics, next validation steps |
| [PACK_FORMAT.md](PACK_FORMAT.md) | Current `pack.json` content-pack format |
| [LEGACY_TOOLKIT.md](LEGACY_TOOLKIT.md) | The archived CLI toolkit, and which of its claims are superseded |

## Consolidation map

Where the previous documents went:

- `Foundational_Mandates.md` → [DEVELOPMENT_MANDATES.md](DEVELOPMENT_MANDATES.md)
- `PTNDATA_CANONICAL.md` → [PTNDATA_FORMAT.md](PTNDATA_FORMAT.md)
  (PTNINFO quantize claim corrected: byte 2 is the bar count; quantize is
  in-stream PTNDATA state)
- `SMPINFO.md` (repo root) → [SMPINFO_FORMAT.md](SMPINFO_FORMAT.md)
- `IMPLEMENTED_ARCHITECTURE_2026-04-05.md` → [ARCHITECTURE.md](ARCHITECTURE.md)
  (updated for the solved RDAC codec that replaced the experimental decoder)
- `GATE_ON_FINDINGS_AND_PROPOSAL.md`, `OVERDUB_HANDOVER.md` →
  [PATTERN_RESEARCH.md](PATTERN_RESEARCH.md) (validated findings promoted into
  PTNDATA_FORMAT.md; open questions kept here)
- `Archive/SP303_PACK_FORMAT.md`, `Archive/SP303_PACK_GUIDE.md` →
  [PACK_FORMAT.md](PACK_FORMAT.md) (rewritten for the current
  `dr_sidekick/engine/packs.py` format; the v2.0 spec described a pack system
  the app never shipped)
- `Archive/PROJECT_STATUS.md`, `Archive/PATTERN_FIX_v2.5_COMPLETE.md`,
  `Archive/QUICK_START_GUIDE.md`, `Archive/QUICKSTART_GUI.md` →
  [LEGACY_TOOLKIT.md](LEGACY_TOOLKIT.md) (historical record with corrections;
  the CLI toolkit itself remains in `Archive/`)

User-facing run/usage instructions live in the top-level [README.md](../README.md).
