"""Sample workflow UI and actions extracted from Pattern Manager."""

from __future__ import annotations

import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Protocol

from dr_sidekick.engine import (
    AssignmentSession,
    SLOT_COUNT,
    SMPINFO,
    SP303CardPrep,
    SourceType,
    VirtualCard,
    find_wav_files,
    parse_mpc1000_pgm,
    quick_import,
    sp303_decode_sp0,
    sp303_write_wav,
)
from dr_sidekick.ui.dialogs import show_text_dialog

try:
    from tkinterdnd2 import DND_FILES

    TKDND_AVAILABLE = True
except ImportError:
    DND_FILES = None
    TKDND_AVAILABLE = False

log = logging.getLogger("dr_sidekick")


class SampleManagerHost(Protocol):
    root: tk.Misc
    state: object

    def update_status(self, message: str) -> None: ...

    def set_loaded_card_context(self, loaded_card: str) -> None: ...


def ask_output_directory(initialdir: Optional[Path] = None) -> Optional[Path]:
    kwargs = {"title": "Select Output Directory"}
    if initialdir is not None:
        kwargs["initialdir"] = str(initialdir)
    output_dir = filedialog.askdirectory(**kwargs)
    return Path(output_dir) if output_dir else None


def show_prepare_results(
    parent: tk.Misc,
    results: dict,
    output_dir: Path,
    title: str = "Card Preparation Complete",
    extra_lines: Optional[List[str]] = None,
    include_counts: bool = True,
) -> None:
    lines = [f"Output directory: {output_dir}"]
    if include_counts:
        if results.get("wav_prepared"):
            lines.append(f"WAV files prepared: {len(results['wav_prepared'])}")
        if results.get("archived_sp0_copied"):
            lines.append(f".SP0 files copied: {len(results['archived_sp0_copied'])}")
        if results.get("smpinfo_created"):
            lines.append("SMPINFO0.SP0 created")
    if extra_lines:
        lines.extend(extra_lines)
    show_text_dialog(parent, title, "\n".join(lines), geometry="1024x640")


def archive_existing_outgoing_wavs(output_dir: Path) -> Optional[Path]:
    wav_files = sorted(
        [
            path
            for path in output_dir.rglob("*")
            if path.is_file()
            and path.suffix.lower() == ".wav"
            and not any(part.startswith("wav_archive_") for part in path.parts)
        ]
    )
    if not wav_files:
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = output_dir / f"wav_archive_{timestamp}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    for wav_file in wav_files:
        relative_parent = wav_file.parent.relative_to(output_dir)
        target_parent = archive_dir / relative_parent
        target_parent.mkdir(parents=True, exist_ok=True)
        target = target_parent / wav_file.name
        suffix = 1
        while target.exists():
            target = target_parent / f"{wav_file.stem}_{suffix}{wav_file.suffix}"
            suffix += 1
        wav_file.rename(target)

    return archive_dir


def run_quick_import(host: SampleManagerHost) -> None:
    wav_file = filedialog.askopenfilename(
        title="Select Any WAV In The Target Folder (Cancel To Pick Folder)",
        filetypes=[("WAV Files", "*.wav *.WAV"), ("All Files", "*.*")],
    )
    if wav_file:
        wav_dir_path = Path(wav_file).parent
    else:
        wav_dir = filedialog.askdirectory(title="Select WAV Folder")
        if not wav_dir:
            return
        wav_dir_path = Path(wav_dir)

    output_dir = host.state.get_library_paths()["outgoing"]
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        archive_dir = archive_existing_outgoing_wavs(output_dir)
        payload = quick_import(wav_dir_path, output_dir, None)
        summary_lines = [f"WAV files processed: {payload['imported_count']}"]
        if archive_dir is not None:
            summary_lines.append(
                f"Archived existing WAV files in BOSS DATA_OUTGOING to Cards/BOSS DATA_OUTGOING/{archive_dir.name}"
            )
        if payload.get("batch_count", 1) > 1:
            summary_lines.append(
                f"Prepared {payload['batch_count']} bank-load folders: {', '.join(payload.get('batch_dirs', []))}"
            )
            summary_lines.append(
                "Import one folder at a time: copy its SMPL0001.WAV-SMPL0008.WAV to card, import to chosen bank, then repeat with the next folder."
            )
        conversion_lines = [
            item["conversion_summary"]
            for item in payload["results"].get("wav_prepared", [])
            if item.get("conversion_summary")
        ]
        if conversion_lines:
            summary_lines.extend(conversion_lines)
        else:
            for item in payload["results"].get("wav_prepared", []):
                source_name = item.get("source_file")
                target_name = item.get("file")
                if source_name and target_name:
                    summary_lines.append(f"Converted {source_name} -> {target_name}")
        show_prepare_results(
            host.root,
            payload["results"],
            output_dir,
            "Quick Import Complete",
            summary_lines,
            include_counts=False,
        )
        host.update_status(
            f"Quick import complete: processed {payload['imported_count']} of {payload['total_found']} WAV files"
        )
    except Exception as exc:
        messagebox.showerror("Quick Import Error", str(exc), parent=host.root)


def run_mpc1000_import(host: SampleManagerHost) -> None:
    """Convert MPC1000 .pgm program + WAV folder into a SmartMedia Library card."""
    pgm_file = filedialog.askopenfilename(
        title="Select MPC1000 Program (.pgm)",
        filetypes=[("MPC1000 Program", "*.pgm *.PGM"), ("All Files", "*.*")],
    )
    if not pgm_file:
        return
    pgm_path = Path(pgm_file)

    wav_dir_path = pgm_path.parent
    if not find_wav_files(wav_dir_path, recursive=True):
        wav_dir = filedialog.askdirectory(
            title="No WAVs found next to .pgm — Select WAV Folder",
            initialdir=str(pgm_path.parent),
        )
        if not wav_dir:
            return
        wav_dir_path = Path(wav_dir)

    try:
        pads = parse_mpc1000_pgm(pgm_path)
    except ValueError as exc:
        messagebox.showerror("MPC1000 Import Error", str(exc), parent=host.root)
        return

    wav_files_in_dir = find_wav_files(wav_dir_path, recursive=True)

    def find_wav_for_name(sample_name):
        for wav_file in wav_files_in_dir:
            if wav_file.stem == sample_name:
                return wav_file
        for wav_file in wav_files_in_dir:
            if wav_file.stem.lower() == sample_name.lower():
                return wav_file
        for wav_file in wav_files_in_dir:
            if sample_name.lower() in wav_file.stem.lower():
                return wav_file
        return None

    host.state.smartmedia_lib.ensure_dirs()
    card_name = pgm_path.stem
    card_dir = host.state.smartmedia_lib.cards_dir / card_name
    if card_dir.exists():
        shutil.rmtree(card_dir)
    card_dir.mkdir(parents=True, exist_ok=True)
    card = VirtualCard(name=card_name, tags=["mpc1000"])
    host.state.smartmedia_lib.create_card(card)

    prep = SP303CardPrep()
    summary_lines = [f"Program: {pgm_path.name}", f"WAV folder: {wav_dir_path.name}", ""]
    total_written = 0
    not_found = []

    for bank_idx, bank_name in enumerate("ABCDEFGH"):
        bank_dir = card_dir / f"BANK_LOAD_{bank_idx + 1:02d}"
        bank_lines = []
        bank_has_samples = False

        for slot in range(8):
            pad_index = bank_idx * 8 + slot
            sample_name = pads.get(pad_index)
            smpl_name = f"SMPL{slot + 1:04d}.WAV"

            if not sample_name:
                bank_lines.append(f"  {smpl_name}: (empty)")
                continue

            wav_file = find_wav_for_name(sample_name)
            if not wav_file:
                not_found.append(f"Bank {bank_name} pad {slot + 1}: {sample_name}")
                bank_lines.append(f"  {smpl_name}: NOT FOUND ({sample_name})")
                continue

            bank_dir.mkdir(parents=True, exist_ok=True)
            target = bank_dir / smpl_name
            actions = prep._prepare_wav(wav_file, target)
            action_str = f" [{', '.join(actions)}]" if actions else ""
            bank_lines.append(f"  {smpl_name}: {wav_file.name}{action_str}")
            total_written += 1
            bank_has_samples = True

        if bank_has_samples:
            summary_lines.append(f"Bank {bank_name} (BANK_LOAD_{bank_idx + 1:02d}):")
            summary_lines.extend(bank_lines)
            summary_lines.append("")

    summary_lines.append(f"Total samples written: {total_written}")
    if not_found:
        summary_lines.append(f"\nNot found ({len(not_found)}):")
        summary_lines.extend(f"  {item}" for item in not_found)
    summary_lines.append(f"\nSaved to: {card_dir}")
    summary_lines.append("Load one BANK_LOAD folder at a time on the SP-303.")

    show_text_dialog(host.root, "MPC1000 Import Complete", "\n".join(summary_lines))
    host.update_status(f"MPC1000 import complete: {total_written} samples written to Cards/{card_name}")
    log.info("MPC1000 import: %s -> Cards/%s (%d samples)", pgm_path.name, card_name, total_written)


def open_sample_manager(host: SampleManagerHost, smpinfo_path: Optional[Path] = None) -> None:
    session = AssignmentSession()
    dialog = tk.Toplevel(host.root)
    card_label = smpinfo_path.parent.name if smpinfo_path else "No card loaded"
    dialog.title(f"Sample Manager — {card_label}" if smpinfo_path else "Sample Manager")
    dialog.geometry("1180x680")
    dialog.transient(host.root)
    dialog.grab_set()
    dialog.configure(bg="#000000")

    frame = ttk.Frame(dialog, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)

    ttk.Label(frame, text="Sample Manager", font=("Courier", 13, "bold")).pack(anchor=tk.W, pady=(0, 2))
    ttk.Label(
        frame,
        text="Helps you manage the samples on your SmartMedia card. You can also reorganise the order by dragging them into new positions.",
    ).pack(anchor=tk.W, pady=(0, 4))
    ttk.Label(
        frame,
        text="Coming Soon: Long/Lo-Fi and DSP Effect editing.",
    ).pack(anchor=tk.W, pady=(0, 4))
    dialog_context = ttk.Label(frame, text="Card Setup: Not loaded | Pending pad changes: 0")
    dialog_context.pack(anchor=tk.W, pady=(0, 8))

    style = ttk.Style(dialog)
    style.configure(
        "CustomPad.Treeview",
        background="#000000",
        fieldbackground="#000000",
        foreground="#ffffff",
        rowheight=24,
    )
    style.map(
        "CustomPad.Treeview",
        background=[("selected", "#2a7fff")],
        foreground=[("selected", "#ffffff")],
    )

    columns = ("bank_pad", "source", "file", "long_lofi", "stereo", "length", "duration", "gate", "loop", "reverse")
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=14, style="CustomPad.Treeview")
    tree.heading("bank_pad", text="Pad")
    tree.heading("source", text="Source")
    tree.heading("file", text="File")
    tree.heading("long_lofi", text="Long/Lo-Fi")
    tree.heading("stereo", text="Stereo")
    tree.heading("length", text="File Length")
    tree.heading("duration", text="Duration")
    tree.heading("gate", text="Gate")
    tree.heading("loop", text="Loop")
    tree.heading("reverse", text="Reverse")
    tree.column("bank_pad", width=58, anchor=tk.CENTER, stretch=False)
    tree.column("source", width=82, anchor=tk.CENTER, stretch=False)
    tree.column("file", width=215, stretch=True)
    tree.column("long_lofi", width=80, anchor=tk.CENTER, stretch=False)
    tree.column("stereo", width=66, anchor=tk.CENTER, stretch=False)
    tree.column("length", width=88, anchor=tk.E, stretch=False)
    tree.column("duration", width=86, anchor=tk.E, stretch=False)
    tree.column("gate", width=64, anchor=tk.CENTER, stretch=False)
    tree.column("loop", width=96, anchor=tk.CENTER, stretch=False)
    tree.column("reverse", width=74, anchor=tk.CENTER, stretch=False)
    tree.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
    tree.tag_configure("drop_target", background="#1f3f66", foreground="#ffffff")
    tree.tag_configure("rearrange_target", background="#5a2a8a", foreground="#ffffff")

    ttk.Label(
        frame,
        text="Tip: Load SMPINFO0.SP0 to view slot metadata. Select a row to assign WAV/SP0 and re-map pads. Click Gate, Loop or Reverse to toggle.",
    ).pack(anchor=tk.W, pady=(8, 8))

    control_panel = tk.Frame(frame, bg="#050505", highlightbackground="#2a2a2a", highlightthickness=1, bd=0)
    control_panel.pack(fill=tk.X, pady=(2, 0))

    setup_section = tk.Frame(control_panel, bg="#050505", highlightbackground="#262626", highlightthickness=1, bd=0)
    setup_section.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)
    tk.Label(setup_section, text="CARD SETUP", bg="#050505", fg="#f0f0f0", font=("Courier", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 4))
    setup_row = ttk.Frame(setup_section)
    setup_row.pack(fill=tk.X, padx=6, pady=(0, 6))

    route_section = tk.Frame(control_panel, bg="#050505", highlightbackground="#262626", highlightthickness=1, bd=0)
    route_section.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)
    tk.Label(route_section, text="PAD ROUTING", bg="#050505", fg="#f0f0f0", font=("Courier", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 4))
    route_row = ttk.Frame(route_section)
    route_row.pack(fill=tk.X, padx=6, pady=(0, 6))

    write_section = tk.Frame(control_panel, bg="#050505", highlightbackground="#262626", highlightthickness=1, bd=0)
    write_section.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)
    tk.Label(write_section, text="WRITE", bg="#050505", fg="#f0f0f0", font=("Courier", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 4))
    write_row = ttk.Frame(write_section)
    write_row.pack(fill=tk.X, padx=6, pady=(0, 6))
    drop_target_iid: Optional[str] = None
    rearrange_target_iid: Optional[str] = None
    rearrange_source_iid: Optional[str] = None
    slot_metadata: Dict[int, Dict[str, str]] = {}
    gate_state: Dict[int, bool] = {}
    baseline_gate_state: Dict[int, bool] = {}
    loop_state: Dict[int, bool] = {}
    baseline_loop_state: Dict[int, bool] = {}
    reverse_state: Dict[int, bool] = {}
    baseline_reverse_state: Dict[int, bool] = {}
    loaded_smpinfo_bytes: Optional[bytes] = None
    loaded_smpinfo_path: Optional[Path] = None
    baseline_assignments: Dict[int, str] = {}
    playback: List[object] = [None, None]

    def slot_to_label(slot: int) -> str:
        bank = "C" if slot < 8 else "D"
        pad = (slot % 8) + 1
        return f"{bank}{pad}"

    def current_assignment_snapshot() -> Dict[int, str]:
        snapshot: Dict[int, str] = {}
        for slot, source in enumerate(session.prep.sources):
            snapshot[slot] = "-" if source.source_path is None else source.source_path.name
        return snapshot

    def build_change_lines() -> List[str]:
        current = current_assignment_snapshot()
        lines: List[str] = []
        for slot in range(SLOT_COUNT):
            before = baseline_assignments.get(slot, "-")
            after = current.get(slot, "-")
            if before != after:
                lines.append(f"{slot_to_label(slot)}: {before} -> {after}")
        for slot in range(SLOT_COUNT):
            if slot in baseline_gate_state:
                before_gate = baseline_gate_state[slot]
                after_gate = gate_state.get(slot, before_gate)
                if before_gate != after_gate:
                    lines.append(f"{slot_to_label(slot)}: Gate -> {'On' if after_gate else 'Off'}")
        for slot in range(SLOT_COUNT):
            if slot in baseline_loop_state:
                before_loop = baseline_loop_state[slot]
                after_loop = loop_state.get(slot, before_loop)
                if before_loop != after_loop:
                    lines.append(f"{slot_to_label(slot)}: Loop -> {'On' if after_loop else 'Off'}")
        for slot in range(SLOT_COUNT):
            if slot in baseline_reverse_state:
                before_reverse = baseline_reverse_state[slot]
                after_reverse = reverse_state.get(slot, before_reverse)
                if before_reverse != after_reverse:
                    lines.append(f"{slot_to_label(slot)}: Reverse -> {'On' if after_reverse else 'Off'}")
        return lines

    def refresh_dialog_context() -> None:
        loaded = str(loaded_smpinfo_path) if loaded_smpinfo_path is not None else "Not loaded"
        changes = len(build_change_lines()) if baseline_assignments else 0
        dialog_context.config(text=f"Card Setup: {loaded} | Pending pad changes: {changes}")

    def selected_slot() -> Optional[int]:
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Sample Manager", "Select a pad first.", parent=dialog)
            return None
        return int(selected[0])

    def refresh_tree() -> None:
        nonlocal drop_target_iid, rearrange_target_iid
        for item in tree.get_children():
            tree.delete(item)
        drop_target_iid = None
        rearrange_target_iid = None
        for slot, source in enumerate(session.prep.sources):
            bank = "C" if slot < 8 else "D"
            pad = (slot % 8) + 1
            source_name = "SP-303" if source.source_type.value == "archived" else source.source_type.value

            if source.source_path:
                filename = source.source_path.name
                if source.source_type.value == "archived" and source.is_stereo:
                    right_name = source.source_path.name.replace("L.SP0", "R.SP0")
                    if right_name != source.source_path.name:
                        filename = f"{source.source_path.name} + {right_name}"
            else:
                filename = "-"
            meta = slot_metadata.get(slot, {})
            tree.insert(
                "",
                tk.END,
                iid=str(slot),
                values=(
                    f"{bank}{pad}",
                    source_name,
                    filename,
                    meta.get("long_lofi", "-"),
                    meta.get("stereo", "-"),
                    meta.get("length", "-"),
                    meta.get("duration", "-"),
                    meta.get("gate", "-"),
                    meta.get("loop", "-"),
                    meta.get("reverse", "-"),
                ),
            )
        refresh_dialog_context()

    def set_drop_target(iid: Optional[str]) -> None:
        nonlocal drop_target_iid
        if drop_target_iid and tree.exists(drop_target_iid):
            tree.item(drop_target_iid, tags=())
        drop_target_iid = iid
        if drop_target_iid and tree.exists(drop_target_iid):
            tree.item(drop_target_iid, tags=("drop_target",))

    def set_rearrange_target(iid: Optional[str]) -> None:
        nonlocal rearrange_target_iid
        if rearrange_target_iid and tree.exists(rearrange_target_iid):
            tree.item(rearrange_target_iid, tags=())
        rearrange_target_iid = iid
        if rearrange_target_iid and tree.exists(rearrange_target_iid):
            tree.item(rearrange_target_iid, tags=("rearrange_target",))

    def assign_wav_to_slot(slot: int, wav_path: Path) -> None:
        session.assign_wav(slot, wav_path)
        refresh_tree()
        tree.selection_set(str(slot))
        tree.focus(str(slot))

    def assign_wav() -> None:
        slot = selected_slot()
        if slot is None:
            return
        wav_file = filedialog.askopenfilename(
            parent=dialog,
            title="Select WAV File",
            filetypes=[("WAV Files", "*.wav *.WAV"), ("All Files", "*.*")],
        )
        if not wav_file:
            return
        try:
            assign_wav_to_slot(slot, Path(wav_file))
            host.update_status(f"Assigned WAV to {slot_to_label(slot)}")
        except Exception as exc:
            messagebox.showerror("Assign WAV", str(exc), parent=dialog)

    def assign_sp0() -> None:
        slot = selected_slot()
        if slot is None:
            return
        sp0_file = filedialog.askopenfilename(
            parent=dialog,
            title="Select SP0 File",
            filetypes=[("SP0 Files", "*.SP0"), ("All Files", "*.*")],
        )
        if not sp0_file:
            return
        try:
            selected_path = Path(sp0_file)
            name_upper = selected_path.name.upper()

            if name_upper.endswith("R.SP0"):
                left_candidate = selected_path.with_name(selected_path.name[:-5] + "L.SP0")
                if not left_candidate.exists():
                    raise ValueError(f"Matching left file not found: {left_candidate.name}")
                selected_path = left_candidate
                name_upper = selected_path.name.upper()

            if name_upper.endswith("L.SP0"):
                right_candidate = selected_path.with_name(selected_path.name[:-5] + "R.SP0")
                stereo = right_candidate.exists()
            else:
                stereo = False

            session.assign_archived_sp0(slot, selected_path, stereo)
            refresh_tree()
            host.update_status(f"Assigned SP0 to {slot_to_label(slot)}")
        except Exception as exc:
            messagebox.showerror("Assign SP0", str(exc), parent=dialog)

    def load_smpinfo_from_path(path: Path) -> None:
        nonlocal loaded_smpinfo_bytes, loaded_smpinfo_path
        try:
            if path.name.upper() != "SMPINFO0.SP0":
                raise ValueError("Please select SMPINFO0.SP0")

            loaded_smpinfo_bytes = path.read_bytes()
            loaded_smpinfo_path = path
            smpinfo = SMPINFO.from_file(path)
            source_dir = path.parent

            for slot in range(SLOT_COUNT):
                session.clear_slot(slot)
            slot_metadata.clear()
            gate_state.clear()
            loop_state.clear()
            reverse_state.clear()
            missing_files: List[str] = []

            for slot in range(SLOT_COUNT):
                slot_record = smpinfo.slots[slot]
                if slot_record.is_empty:
                    continue

                seconds = slot_record.sample_length_bytes / 33075.0
                duration_text = f"{seconds:.2f}s" if seconds >= 1.0 else f"{seconds * 1000.0:.1f}ms"
                gate_state[slot] = slot_record.is_gate
                loop_state[slot] = slot_record.is_loop
                reverse_state[slot] = slot_record.is_reverse
                slot_metadata[slot] = {
                    "long_lofi": "-",
                    "stereo": "Stereo" if slot_record.is_stereo else "Mono",
                    "length": f"{slot_record.sample_length_bytes:,} B",
                    "duration": duration_text,
                    "loop": "Loop" if slot_record.is_loop else "Off",
                    "reverse": "Reverse" if slot_record.is_reverse else "Off",
                    "gate": "Gate" if slot_record.is_gate else "Off",
                }

                left_file = source_dir / f"SMP{slot:04X}L.SP0"
                right_file = source_dir / f"SMP{slot:04X}R.SP0"
                if not left_file.exists():
                    missing_files.append(left_file.name)
                    continue
                if slot_record.is_stereo and not right_file.exists():
                    missing_files.append(right_file.name)
                    continue
                session.assign_archived_sp0(slot, left_file, slot_record.is_stereo)

            baseline_assignments.clear()
            baseline_assignments.update(current_assignment_snapshot())
            baseline_gate_state.clear()
            baseline_gate_state.update(gate_state)
            baseline_loop_state.clear()
            baseline_loop_state.update(loop_state)
            baseline_reverse_state.clear()
            baseline_reverse_state.update(reverse_state)
            refresh_tree()
            host.set_loaded_card_context(str(source_dir))
            host.update_status(f"Loaded card setup: {path}")
            dialog.title(f"Sample Manager — {source_dir.name}")
            log.info("Card opened: %s (%d slots populated)", path, sum(1 for slot in smpinfo.slots if not slot.is_empty))
            if missing_files:
                messagebox.showwarning(
                    "Load SMPINFO0.SP0",
                    f"Metadata loaded. Missing sample files: {', '.join(sorted(set(missing_files)))}",
                    parent=dialog,
                )
        except Exception as exc:
            messagebox.showerror("Load SMPINFO0.SP0", str(exc), parent=dialog)

    def load_smpinfo_metadata() -> None:
        smpinfo_file = filedialog.askopenfilename(
            parent=dialog,
            title="Select SMPINFO0.SP0",
            initialdir=str(host.state.default_card_mount_dir()),
            filetypes=[("SMPINFO0.SP0", "SMPINFO0.SP0"), ("SP0 Files", "*.SP0"), ("All Files", "*.*")],
        )
        if not smpinfo_file:
            return
        load_smpinfo_from_path(Path(smpinfo_file))

    def clear_pad() -> None:
        slot = selected_slot()
        if slot is None:
            return
        session.clear_slot(slot)
        refresh_tree()
        host.update_status(f"Cleared {slot_to_label(slot)}")

    def prepare_card_now() -> None:
        preferred_output = Path("/Volumes/BOSS DATA")
        if not preferred_output.exists():
            preferred_output = host.state.get_library_paths()["outgoing"]
        output_dir = ask_output_directory(preferred_output)
        if output_dir is None:
            return
        change_lines = build_change_lines()

        summary_lines = [
            f"Target card/output: {output_dir}",
            f"Pending pad changes: {len(change_lines)}",
        ]
        if change_lines:
            summary_lines.extend(change_lines[:24])
            if len(change_lines) > 24:
                summary_lines.append(f"... and {len(change_lines) - 24} more")

        confirm_dialog = tk.Toplevel(dialog)
        confirm_dialog.title("Confirm Write Changes to Card")
        confirm_dialog.geometry("720x520")
        confirm_dialog.transient(dialog)
        confirm_dialog.grab_set()
        confirm_dialog.configure(bg="#000000")

        confirm_frame = ttk.Frame(confirm_dialog, padding=10)
        confirm_frame.pack(fill=tk.BOTH, expand=True)
        confirm_text = tk.Text(
            confirm_frame,
            wrap=tk.NONE,
            bg="#000000",
            fg="#ffffff",
            insertbackground="#ffffff",
            relief=tk.FLAT,
            highlightthickness=0,
        )
        confirm_text.insert("1.0", "\n".join(summary_lines))
        confirm_text.configure(state=tk.DISABLED)
        confirm_text.pack(fill=tk.BOTH, expand=True)
        action_row = ttk.Frame(confirm_frame)
        action_row.pack(fill=tk.X, pady=(8, 0))

        def do_write() -> None:
            for widget in action_row.winfo_children():
                widget.destroy()
            confirm_text.configure(state=tk.NORMAL)
            confirm_text.delete("1.0", tk.END)
            confirm_text.insert("1.0", "Writing...")
            confirm_text.configure(state=tk.DISABLED)
            confirm_dialog.update()
            log.info("Write to card started: %s", output_dir)
            try:
                results = session.prepare_card(output_dir)
                smpinfo_out = output_dir / "SMPINFO0.SP0"
                if loaded_smpinfo_bytes is not None and smpinfo_out.exists():
                    has_reassignment = False
                    for slot, source in enumerate(session.prep.sources):
                        if source.source_type.value != "archived" or source.source_path is None:
                            continue
                        expected_name = f"SMP{slot:04X}L.SP0"
                        if source.source_path.name.upper() != expected_name:
                            has_reassignment = True
                            break

                    generated = smpinfo_out.read_bytes()
                    if len(generated) == len(loaded_smpinfo_bytes) and len(generated) >= 0x400:
                        merged = bytearray(generated)
                        if not has_reassignment:
                            merged[0x400:] = loaded_smpinfo_bytes[0x400:]
                        else:
                            results.setdefault("warnings", []).append(
                                "Skipped SMPINFO tail preservation because pad reassignment was detected."
                            )
                        smpinfo_out.write_bytes(merged)
                if gate_state and smpinfo_out.exists():
                    patched = bytearray(smpinfo_out.read_bytes())
                    block_size = 0x400
                    num_blocks = len(patched) // block_size
                    for slot, is_gate in gate_state.items():
                        gate_byte = 0x01 if is_gate else 0x00
                        for blk in range(num_blocks):
                            blk_start = blk * block_size
                            if patched[blk_start:blk_start + 4] == b"\xff\xff\xff\xff":
                                break
                            byte_off = blk_start + slot * 48 + 37
                            if byte_off < len(patched):
                                patched[byte_off] = gate_byte
                    smpinfo_out.write_bytes(patched)
                if loop_state and smpinfo_out.exists():
                    patched = bytearray(smpinfo_out.read_bytes())
                    block_size = 0x400
                    num_blocks = len(patched) // block_size
                    for slot, is_loop in loop_state.items():
                        loop_byte = 0x01 if is_loop else 0x00
                        for blk in range(num_blocks):
                            blk_start = blk * block_size
                            if patched[blk_start:blk_start + 4] == b"\xff\xff\xff\xff":
                                break
                            byte_off = blk_start + slot * 48 + 38
                            if byte_off < len(patched):
                                patched[byte_off] = loop_byte
                    smpinfo_out.write_bytes(patched)
                if reverse_state and smpinfo_out.exists():
                    patched = bytearray(smpinfo_out.read_bytes())
                    block_size = 0x400
                    num_blocks = len(patched) // block_size
                    for slot, is_reverse in reverse_state.items():
                        rev_byte = 0x01 if is_reverse else 0x00
                        for blk in range(num_blocks):
                            blk_start = blk * block_size
                            if patched[blk_start:blk_start + 4] == b"\xff\xff\xff\xff":
                                break
                            byte_off = blk_start + slot * 48 + 39
                            if byte_off < len(patched):
                                patched[byte_off] = rev_byte
                    smpinfo_out.write_bytes(patched)

                result_lines = [f"Output directory: {output_dir}"]
                if results.get("wav_prepared"):
                    result_lines.append(f"WAV files prepared: {len(results['wav_prepared'])}")
                if results.get("archived_sp0_copied"):
                    result_lines.append(f".SP0 files copied: {len(results['archived_sp0_copied'])}")
                if results.get("smpinfo_created"):
                    result_lines.append("SMPINFO0.SP0 created.")
                if results.get("warnings"):
                    result_lines.extend(results["warnings"])
                confirm_text.configure(state=tk.NORMAL)
                confirm_text.delete("1.0", tk.END)
                confirm_text.insert("1.0", "\n".join(result_lines))
                confirm_text.configure(state=tk.DISABLED)
                confirm_dialog.title("Write Complete")
                host.update_status("Custom pad assignment complete")
                log.info("Write to card complete: %s", output_dir)
                baseline_assignments.clear()
                baseline_assignments.update(current_assignment_snapshot())
                baseline_gate_state.clear()
                baseline_gate_state.update(gate_state)
                baseline_loop_state.clear()
                baseline_loop_state.update(loop_state)
                baseline_reverse_state.clear()
                baseline_reverse_state.update(reverse_state)
                refresh_dialog_context()
            except Exception as exc:
                log.error("Write to card failed: %s", exc, exc_info=True)
                confirm_text.configure(state=tk.NORMAL)
                confirm_text.delete("1.0", tk.END)
                confirm_text.insert("1.0", f"Error: {exc}")
                confirm_text.configure(state=tk.DISABLED)
                confirm_dialog.title("Write Failed")
            ttk.Button(action_row, text="Close", command=confirm_dialog.destroy).pack(side=tk.RIGHT)

        ttk.Button(action_row, text="Cancel", command=confirm_dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(action_row, text="Write Changes", command=do_write).pack(side=tk.RIGHT, padx=(0, 6))

    def stop_playback() -> None:
        proc, tmp_path = playback[0], playback[1]
        playback[0] = None
        playback[1] = None
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def launch_playback(file_path: str, tmp_path: Optional[str] = None) -> None:
        if sys.platform == "darwin":
            cmd = ["afplay", file_path]
        elif sys.platform.startswith("linux"):
            cmd = ["aplay", file_path]
        else:
            messagebox.showinfo("Preview", "Audio preview is not supported on this platform.", parent=dialog)
            return
        try:
            proc = subprocess.Popen(cmd)
        except Exception as exc:
            messagebox.showerror("Preview", str(exc), parent=dialog)
            return
        playback[0] = proc
        playback[1] = tmp_path

        def cleanup(playback_proc, playback_tmp) -> None:
            try:
                playback_proc.wait(timeout=120)
            except Exception:
                pass
            if playback[0] is playback_proc:
                playback[0] = None
            if playback_tmp:
                if playback[1] == playback_tmp:
                    playback[1] = None
                try:
                    os.unlink(playback_tmp)
                except Exception:
                    pass

        threading.Thread(target=cleanup, args=(proc, tmp_path), daemon=True).start()

    def decode_sp0_to_pcm(left_path: Path, is_stereo: bool):
        samples_l = sp303_decode_sp0(str(left_path))
        if is_stereo:
            right_path = left_path.with_name(left_path.name[:-5] + "R.SP0")
            if right_path.exists():
                samples_r = sp303_decode_sp0(str(right_path))
                count = max(len(samples_l), len(samples_r))
                samples_l += [0] * (count - len(samples_l))
                samples_r += [0] * (count - len(samples_r))
                pcm = [value for pair in zip(samples_l, samples_r) for value in pair]
                return pcm, count, 2
        return samples_l, len(samples_l), 1

    def preview_pad() -> None:
        slot = selected_slot()
        if slot is None:
            return
        source = session.prep.sources[slot]
        if source.source_type == SourceType.EMPTY or source.source_path is None:
            messagebox.showinfo("Preview", "No sample assigned to this pad.", parent=dialog)
            return
        stop_playback()
        try:
            if source.source_type == SourceType.ARCHIVED_SP0:
                pcm, n_samples, channels = decode_sp0_to_pcm(source.source_path, source.is_stereo)
                fd, tmp_path = tempfile.mkstemp(suffix=".wav")
                with os.fdopen(fd, "wb") as wav_file:
                    sp303_write_wav(wav_file, n_samples, 32000, channels)
                    wav_file.write(struct.pack(f"<{len(pcm)}h", *pcm))
                launch_playback(tmp_path, tmp_path)
            else:
                launch_playback(str(source.source_path))
        except Exception as exc:
            messagebox.showerror("Preview", str(exc), parent=dialog)

    def convert_sp0_to_wav() -> None:
        left_path: Optional[Path] = None
        is_stereo = False
        selection = tree.selection()
        if selection:
            source = session.prep.sources[int(selection[0])]
            if source.source_type == SourceType.ARCHIVED_SP0 and source.source_path is not None:
                left_path = source.source_path
                is_stereo = source.is_stereo
        if left_path is None:
            sp0_file = filedialog.askopenfilename(
                parent=dialog,
                title="Select SP0 File to Convert",
                filetypes=[("SP0 Files", "*.SP0 *.sp0"), ("All Files", "*.*")],
            )
            if not sp0_file:
                return
            left_path = Path(sp0_file)
            if left_path.name.upper().endswith("L.SP0"):
                right_path = left_path.with_name(left_path.name[:-5] + "R.SP0")
                is_stereo = right_path.exists()
        stem = left_path.stem[:-1] if left_path.stem.upper().endswith("L") else left_path.stem
        out_file = filedialog.asksaveasfilename(
            parent=dialog,
            title="Save WAV As",
            initialfile=stem + ".wav",
            defaultextension=".wav",
            filetypes=[("WAV Files", "*.wav"), ("All Files", "*.*")],
        )
        if not out_file:
            return
        try:
            pcm, n_samples, channels = decode_sp0_to_pcm(left_path, is_stereo)
            with open(out_file, "wb") as wav_file:
                sp303_write_wav(wav_file, n_samples, 32000, channels)
                wav_file.write(struct.pack(f"<{len(pcm)}h", *pcm))
            duration = n_samples / 32000.0
            messagebox.showinfo(
                "Convert SP0 to WAV",
                f"Saved: {out_file}\n{n_samples:,} samples  {duration:.2f}s  {'Stereo' if channels == 2 else 'Mono'}  32 kHz",
                parent=dialog,
            )
            log.info(
                "SP0 → WAV: %s → %s (%.2fs, %s)",
                left_path.name,
                out_file,
                duration,
                "stereo" if channels == 2 else "mono",
            )
        except Exception as exc:
            messagebox.showerror("Convert SP0 to WAV", str(exc), parent=dialog)

    def on_dialog_close() -> None:
        stop_playback()
        dialog.destroy()

    ttk.Button(setup_row, text="Load Card Setup", command=load_smpinfo_metadata).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(route_row, text="Assign WAV", command=assign_wav).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(route_row, text="Assign SP0", command=assign_sp0).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(route_row, text="Clear Pad", command=clear_pad).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(route_row, text="Refresh", command=refresh_tree).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(route_row, text="Preview", command=preview_pad).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(write_row, text="Write Changes to Card", command=prepare_card_now).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(write_row, text="Close", command=on_dialog_close).pack(side=tk.RIGHT)

    sm_menubar = tk.Menu(dialog, tearoff=0)
    sm_tools = tk.Menu(sm_menubar, tearoff=0)
    sm_menubar.add_cascade(label="Samples", menu=sm_tools)
    sm_tools.add_command(label="Quick Import WAV Folder...", command=lambda: run_quick_import(host))
    sm_tools.add_command(label="Convert MPC1000 Program (.pgm)...", command=lambda: run_mpc1000_import(host))
    sm_tools.add_separator()
    sm_tools.add_command(label="Preview Selected Pad", command=preview_pad, accelerator="Space")
    sm_tools.add_command(label="Convert SP0 to WAV...", command=convert_sp0_to_wav)
    dialog.configure(menu=sm_menubar)
    tree.bind("<space>", lambda _event: preview_pad())
    dialog.protocol("WM_DELETE_WINDOW", on_dialog_close)

    if TKDND_AVAILABLE and hasattr(tree, "drop_target_register"):

        def on_tree_drop_position(event):
            row_id = tree.identify_row(event.y)
            col_id = tree.identify_column(event.x)
            if row_id and col_id == "#3":
                set_drop_target(row_id)
            else:
                set_drop_target(None)
            return "break"

        def on_tree_drop_leave(_event):
            set_drop_target(None)
            return "break"

        def on_tree_drop(event):
            row_id = tree.identify_row(event.y)
            col_id = tree.identify_column(event.x)
            if not row_id or col_id != "#3":
                set_drop_target(None)
                return "break"

            try:
                dropped_paths = list(dialog.tk.splitlist(event.data))
            except Exception:
                dropped_paths = [event.data]

            wav_path = None
            for raw_path in dropped_paths:
                candidate = Path(str(raw_path).strip().strip("{}"))
                if candidate.suffix.lower() == ".wav":
                    wav_path = candidate
                    break

            if wav_path is None:
                messagebox.showerror(
                    "Assign WAV",
                    "Drop a .wav file onto the File column for the target pad.",
                    parent=dialog,
                )
                return "break"

            if not wav_path.exists():
                messagebox.showerror("Assign WAV", f"File not found: {wav_path}", parent=dialog)
                return "break"

            try:
                assign_wav_to_slot(int(row_id), wav_path)
            except Exception as exc:
                messagebox.showerror("Assign WAV", str(exc), parent=dialog)
            set_drop_target(None)
            return "break"

        tree.drop_target_register(DND_FILES)
        tree.dnd_bind("<<DropPosition>>", on_tree_drop_position)
        tree.dnd_bind("<<DropLeave>>", on_tree_drop_leave)
        tree.dnd_bind("<<Drop>>", on_tree_drop)

    def on_tree_double_click(event) -> None:
        row_id = tree.identify_row(event.y)
        col_id = tree.identify_column(event.x)
        if not row_id or col_id != "#3":
            return

        wav_file = filedialog.askopenfilename(
            parent=dialog,
            title="Select WAV File",
            filetypes=[("WAV Files", "*.wav *.WAV"), ("All Files", "*.*")],
        )
        if not wav_file:
            return

        try:
            assign_wav_to_slot(int(row_id), Path(wav_file))
        except Exception as exc:
            messagebox.showerror("Assign WAV", str(exc), parent=dialog)

    def swap_assignments(slot_a: int, slot_b: int) -> None:
        if slot_a == slot_b:
            return
        session.prep.sources[slot_a], session.prep.sources[slot_b] = (
            session.prep.sources[slot_b],
            session.prep.sources[slot_a],
        )
        session.prep.sources[slot_a].slot_index = slot_a
        session.prep.sources[slot_b].slot_index = slot_b
        if slot_a in slot_metadata or slot_b in slot_metadata:
            meta_a = slot_metadata.get(slot_a)
            meta_b = slot_metadata.get(slot_b)
            if meta_b is None:
                slot_metadata.pop(slot_a, None)
            else:
                slot_metadata[slot_a] = meta_b
            if meta_a is None:
                slot_metadata.pop(slot_b, None)
            else:
                slot_metadata[slot_b] = meta_a
        gate_a = gate_state.pop(slot_a, False)
        gate_b = gate_state.pop(slot_b, False)
        if gate_b:
            gate_state[slot_a] = gate_b
        if gate_a:
            gate_state[slot_b] = gate_a
        loop_a = loop_state.pop(slot_a, False)
        loop_b = loop_state.pop(slot_b, False)
        if loop_b:
            loop_state[slot_a] = loop_b
        if loop_a:
            loop_state[slot_b] = loop_a
        reverse_a = reverse_state.pop(slot_a, False)
        reverse_b = reverse_state.pop(slot_b, False)
        if reverse_b:
            reverse_state[slot_a] = reverse_b
        if reverse_a:
            reverse_state[slot_b] = reverse_a
        refresh_tree()
        tree.selection_set(str(slot_b))
        tree.focus(str(slot_b))

    def on_tree_press(event) -> None:
        nonlocal rearrange_source_iid
        row_id = tree.identify_row(event.y)
        rearrange_source_iid = row_id if row_id else None

    def on_tree_drag(event) -> None:
        if not rearrange_source_iid:
            return
        row_id = tree.identify_row(event.y)
        if not row_id or row_id == rearrange_source_iid:
            set_rearrange_target(None)
            return
        set_rearrange_target(row_id)

    toggle_columns = {"#8": "gate", "#9": "loop", "#10": "reverse"}

    def handle_cell_toggle(slot: int, col_id: str) -> None:
        field = toggle_columns.get(col_id)
        if not field:
            return
        if loaded_smpinfo_path is None or slot not in slot_metadata:
            return
        if field == "gate":
            new_val = not gate_state.get(slot, False)
            gate_state[slot] = new_val
            slot_metadata[slot]["gate"] = "Gate" if new_val else "Off"
            refresh_tree()
            host.update_status(f"{slot_to_label(slot)} Gate: {'On' if new_val else 'Off'}")
        elif field == "loop":
            new_val = not loop_state.get(slot, False)
            loop_state[slot] = new_val
            slot_metadata[slot]["loop"] = "Loop" if new_val else "Off"
            refresh_tree()
            host.update_status(f"{slot_to_label(slot)} Loop: {'On' if new_val else 'Off'}")
        elif field == "reverse":
            new_val = not reverse_state.get(slot, False)
            reverse_state[slot] = new_val
            slot_metadata[slot]["reverse"] = "Reverse" if new_val else "Off"
            refresh_tree()
            host.update_status(f"{slot_to_label(slot)} Reverse: {'On' if new_val else 'Off'}")

    def on_tree_release(event) -> None:
        nonlocal rearrange_source_iid
        if rearrange_source_iid and rearrange_target_iid:
            try:
                swap_assignments(int(rearrange_source_iid), int(rearrange_target_iid))
            except Exception as exc:
                messagebox.showerror("Rearrange Pads", str(exc), parent=dialog)
        elif rearrange_source_iid:
            row_id = tree.identify_row(event.y)
            col_id = tree.identify_column(event.x)
            if row_id and row_id == rearrange_source_iid and col_id in toggle_columns:
                handle_cell_toggle(int(row_id), col_id)
        rearrange_source_iid = None
        set_rearrange_target(None)

    tree.bind("<Double-1>", on_tree_double_click)
    tree.bind("<ButtonPress-1>", on_tree_press, add="+")
    tree.bind("<B1-Motion>", on_tree_drag, add="+")
    tree.bind("<ButtonRelease-1>", on_tree_release, add="+")

    refresh_tree()
    tree.selection_set("0")
    tree.focus("0")
    if smpinfo_path is not None:
        dialog.after(50, lambda: load_smpinfo_from_path(smpinfo_path))
