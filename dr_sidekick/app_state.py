"""Shared application state and filesystem helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dr_sidekick.engine import PROJECT_ROOT, SmartMediaLibrary, VirtualCard


class AppState:
    """Shared application state for config, library, and filesystem defaults."""

    def __init__(self):
        self.smartmedia_library_root = PROJECT_ROOT / "SmartMedia-Library"
        self.smartmedia_library_root.mkdir(parents=True, exist_ok=True)
        self.smartmedia_lib = SmartMediaLibrary(self.smartmedia_library_root)
        self.load_config()
        self.ensure_library_dirs()

    def load_config(self):
        """Load app config from JSON, migrating old recent files if present."""
        self.config: dict = {
            "device": "BOSS Dr. Sample SP-303",
            "card_mount_path": "",
            "write_to_card": True,
            "recent_files": [],
        }
        config_path = PROJECT_ROOT / "dr_sidekick_config.json"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as handle:
                    saved = json.load(handle)
                self.config.update(saved)
            except Exception:
                pass
        old_path = Path.home() / ".dr_sidekick_recent"
        if old_path.exists() and not self.config.get("recent_files"):
            try:
                lines = old_path.read_text(encoding="utf-8").splitlines()
                self.config["recent_files"] = [line.strip() for line in lines if line.strip()]
                self.save_config()
                old_path.unlink(missing_ok=True)
            except Exception:
                pass

    def save_config(self):
        """Save app config to JSON."""
        config_path = PROJECT_ROOT / "dr_sidekick_config.json"
        try:
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump(self.config, handle, indent=2)
        except Exception:
            pass

    def get_library_paths(self) -> dict:
        return {
            "root": self.smartmedia_library_root,
            "cards": self.smartmedia_library_root / "Cards",
            "incoming": self.smartmedia_library_root / "Cards" / "BOSS DATA_INCOMING",
            "outgoing": self.smartmedia_library_root / "Cards" / "BOSS DATA_OUTGOING",
        }

    def ensure_library_dirs(self):
        self.smartmedia_lib.ensure_dirs()
        for name in ("BOSS DATA_INCOMING", "BOSS DATA_OUTGOING"):
            if self.smartmedia_lib.get_card(name) is None:
                self.smartmedia_lib.create_card(VirtualCard(name=name, author="Dr. Sample"))

    def default_card_mount_dir(self) -> Path:
        if sys.platform == "darwin":
            preferred = Path("/Volumes/BOSS DATA")
            if preferred.exists():
                return preferred
            incoming = self.get_library_paths()["incoming"]
            return incoming if incoming.exists() else Path("/Volumes")

        config_path = self.config.get("card_mount_path", "")
        if config_path:
            candidate = Path(config_path)
            if candidate.exists():
                return candidate

        outgoing = self.get_library_paths()["outgoing"]
        return outgoing if outgoing.exists() else Path.cwd()

    def default_pattern_open_dir(self) -> Path:
        preferred = Path("/Volumes/BOSS DATA")
        if preferred.exists():
            return preferred
        incoming = self.get_library_paths()["incoming"]
        if incoming.exists():
            return incoming
        return self.smartmedia_library_root

    def default_pattern_save_dir(self) -> Path:
        preferred = Path("/Volumes/BOSS DATA")
        if preferred.exists():
            return preferred
        outgoing = self.get_library_paths()["outgoing"]
        if outgoing.exists():
            return outgoing
        return self.smartmedia_library_root
