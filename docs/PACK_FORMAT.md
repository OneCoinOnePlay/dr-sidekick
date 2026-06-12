# Dr. Sidekick Pack Format

Consolidated: 2026-06-12. Describes the `pack.json` format actually
implemented in `dr_sidekick/engine/packs.py`. The legacy toolkit's pack v2.0
specification (`Archive/SP303_PACK_FORMAT.md`) described a richer
banks/build/arrangement schema for the retired Wizard CLI and is superseded —
see [LEGACY_TOOLKIT.md](LEGACY_TOOLKIT.md).

## Overview

A pack is a folder under `packs/` containing a `pack.json` manifest plus
content. `discover_packs()` scans `packs/` for folders with a `pack.json` and
loads them. Packs can carry grooves, samples (SP0 card content), or both.

## pack.json

```json
{
  "format": "sp303-pack",
  "title": "Grooves From Mars",
  "description": "Timing templates from 19 classic drum machines",
  "attribution": {
    "author": "Samples From Mars",
    "url": "https://samplesfrommars.com",
    "license": "Used with permission from Samples From Mars"
  },
  "content": {
    "grooves_dir": "grooves"
  }
}
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `format` | yes | `"sp303-pack"` |
| `title` | no | Display name (falls back to folder name) |
| `description` | no | Display description |
| `attribution` | no | `author` / `url` / `license` strings |
| `content` | no | What the pack contains (see below) |
| `card` | no | Card metadata block, present on packs promoted from virtual cards |

### content block

- `content.grooves_dir` — relative directory of groove JSON files
  (one per machine; e.g. `grooves/TR-808.json`). Presence makes
  `Pack.has_grooves` true.
- `content.banks` — sample layout scanned from SMPINFO, keyed by bank with
  `samples` lists (`pad`, `file`, `stereo`, optional `note`). Presence makes
  `Pack.has_samples` true.
- `content.patterns.files` — pattern files included with the pack
  (`PTNINFO0.SP0`, `PTNDATA0.SP0`).

## Promoting a card to a pack

`promote_card_to_pack()` copies a SmartMedia virtual card into `packs/` as a
sample pack: it reads the card's `pack.json`/legacy `card.json`, copies all
`*.SP0` files, scans `SMPINFO0.SP0` for the bank/pad layout, records pattern
files if present, and writes the populated `pack.json` (description, url, and
license come from the promotion dialog).

## Bundled pack

`packs/grooves-from-mars/` — timing templates from 19 classic drum machines
(CR-78 through TR-909), used with permission from Samples From Mars.
