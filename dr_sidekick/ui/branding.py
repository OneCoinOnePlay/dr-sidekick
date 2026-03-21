"""Shared branding helpers for top-level Dr. Sidekick windows."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Iterable, Optional, Tuple


HeaderAction = Tuple[str, Callable[[], None]]


def create_brand_header(
    parent,
    *,
    device_name: str,
    mode_label: Optional[str] = None,
    actions: Optional[Iterable[HeaderAction]] = None,
    pack: bool = True,
) -> ttk.Frame:
    """Create a consistent Dr. Sidekick header row."""
    header_frame = ttk.Frame(parent)
    if pack:
        header_frame.pack(fill=tk.X, pady=(0, 6))

    ttk.Label(
        header_frame,
        text="Dr. Sidekick",
        font=("Courier", 18, "bold"),
    ).pack(side=tk.LEFT, pady=2)

    subtitle_frame = tk.Frame(header_frame, bg="#000000")
    subtitle_frame.pack(side=tk.LEFT, padx=(12, 0), pady=2)
    tk.Label(
        subtitle_frame,
        text=f"{device_name} Edition",
        font=("Courier", 10, "bold"),
        bg="#000000",
        fg="#ffffff",
    ).pack(side=tk.LEFT)

    if mode_label:
        tk.Label(
            subtitle_frame,
            text=f"  {mode_label}",
            font=("Courier", 10),
            bg="#000000",
            fg="#cccccc",
        ).pack(side=tk.LEFT)

    if actions:
        for label, command in reversed(list(actions)):
            ttk.Button(header_frame, text=label, command=command).pack(side=tk.RIGHT, padx=(0, 6))

    return header_frame
