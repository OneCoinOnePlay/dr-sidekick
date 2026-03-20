"""Pack discovery and loading for Dr. Sidekick content packs."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("dr_sidekick")


@dataclass
class Pack:
    """A content pack containing grooves, samples, or both."""

    path: Path
    title: str
    description: str
    attribution: Dict[str, str]
    content: dict
    card: Optional[dict] = None

    @property
    def has_grooves(self) -> bool:
        return "grooves_dir" in self.content

    @property
    def has_samples(self) -> bool:
        return "banks" in self.content

    @property
    def grooves_path(self) -> Optional[Path]:
        if self.has_grooves:
            return self.path / self.content["grooves_dir"]
        return None


def discover_packs(packs_dir: Path) -> List[Pack]:
    """Scan packs_dir for folders containing pack.json, return loaded Pack objects."""
    packs: List[Pack] = []
    if not packs_dir.is_dir():
        log.warning("Packs directory not found: %s", packs_dir)
        return packs
    for pack_dir in sorted(packs_dir.iterdir()):
        manifest = pack_dir / "pack.json"
        if not manifest.is_file():
            continue
        try:
            with open(manifest, "r") as f:
                data = json.load(f)
            packs.append(Pack(
                path=pack_dir,
                title=data.get("title", pack_dir.name),
                description=data.get("description", ""),
                attribution=data.get("attribution", {}),
                content=data.get("content", {}),
                card=data.get("card"),
            ))
        except Exception:
            log.error("Failed to load pack manifest %s", manifest, exc_info=True)
    return packs


def promote_card_to_pack(
    card_dir: Path,
    packs_dir: Path,
    description: str = "",
    url: str = "",
    license_text: str = "",
) -> Path:
    """Copy a SmartMedia card into packs/ as a sample pack.

    Reads the card's pack.json (or legacy card.json), scans SMPINFO0.SP0 for
    sample layout, checks for patterns, copies all SP0 files, and writes a
    pack.json with the content block populated.

    Returns the path to the new pack directory.
    """
    from .core import SMPINFO, VirtualCard

    manifest_path = card_dir / "pack.json"
    if not manifest_path.exists():
        raise ValueError(f"No pack.json found in {card_dir}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    card = VirtualCard.from_dict(raw_data, card_dir)
    safe_name = card.name.lower().replace(" ", "-").replace("/", "-")
    pack_dir = packs_dir / safe_name
    pack_dir.mkdir(parents=True, exist_ok=True)

    # Copy SP0 files
    for sp0_file in sorted(card_dir.glob("*.SP0")):
        shutil.copy2(sp0_file, pack_dir / sp0_file.name)

    # Scan SMPINFO for bank layout
    banks: Dict[str, dict] = {}
    smpinfo_path = card_dir / "SMPINFO0.SP0"
    if smpinfo_path.exists():
        smpinfo = SMPINFO.from_file(smpinfo_path)
        for slot in smpinfo.slots:
            if slot.is_empty:
                continue
            bank = "A" if slot.slot_index < 8 else "B"
            pad = (slot.slot_index % 8) + 1
            sample_entry = {
                "pad": pad,
                "file": slot.sample_filenames[0],
                "stereo": slot.is_stereo,
            }
            pad_key = f"{bank}{pad}"
            if pad_key in card.pad_notes:
                sample_entry["note"] = card.pad_notes[pad_key]
            banks.setdefault(bank, {"samples": []})
            banks[bank]["samples"].append(sample_entry)

    # Check for patterns
    has_patterns = (
        (card_dir / "PTNINFO0.SP0").exists()
        and (card_dir / "PTNDATA0.SP0").exists()
    )

    content: dict = {}
    if banks:
        content["banks"] = banks
    if has_patterns:
        content["patterns"] = {"files": ["PTNINFO0.SP0", "PTNDATA0.SP0"]}

    # Build pack.json — override description/url/license from dialog inputs
    pack_data = card.to_dict()
    pack_data["description"] = description or f"Sample pack from {card.name}"
    pack_data["attribution"]["url"] = url
    pack_data["attribution"]["license"] = license_text
    pack_data["content"] = content
    pack_data["card"]["modified"] = datetime.now().isoformat(timespec="seconds")

    with open(pack_dir / "pack.json", "w", encoding="utf-8") as f:
        json.dump(pack_data, f, indent=2)
        f.write("\n")

    log.info("Promoted card '%s' to pack at %s", card.name, pack_dir)
    return pack_dir
