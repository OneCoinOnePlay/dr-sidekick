"""SmartMedia Library window extracted from the legacy monolith."""

from __future__ import annotations

import json
import logging
import shutil
import threading
import tkinter as tk
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

from dr_sidekick import APP_VERSION
from dr_sidekick.engine import PROJECT_ROOT, SMPINFO, SP303_PADS, VirtualCard
from dr_sidekick.ui.dialogs import show_text_dialog

if TYPE_CHECKING:
    from dr_sidekick.app_state import AppState

log = logging.getLogger("dr_sidekick")
_LOG_PATH = PROJECT_ROOT / "logs" / "Dr_Sidekick.log"

class SmartMediaLibraryWindow:
    """SmartMedia Library — the application's true root window."""

    def __init__(
        self,
        root,
        state: 'AppState',
        *,
        on_open_sample_manager: Callable[[Optional[Path]], None],
        on_open_pattern_sequencer: Callable[[], None],
    ):
        self.root = root
        self.state = state
        self._on_open_sample_manager = on_open_sample_manager
        self._on_open_pattern_sequencer = on_open_pattern_sequencer
        self.root.title("Dr. Sidekick — SmartMedia Library")
        self.root.geometry("1200x720")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._setup_styles()

        self._build_ui()

    # ── Styles ───────────────────────────────────────────────────────────

    def _setup_styles(self):
        """Configure global ttk dark-theme styles."""
        style = ttk.Style()
        style.theme_use('clam')
        self.root.configure(bg="#000000")
        style.configure(".", background="#000000", foreground="#ffffff")
        style.configure("TFrame", background="#000000")
        style.configure("TLabel", background="#000000", foreground="#ffffff")
        style.configure("TButton", background="#111111", foreground="#ffffff", borderwidth=1)
        style.map(
            "TButton",
            background=[("active", "#1a1a1a"), ("pressed", "#222222")],
            foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
        )
        style.configure(
            "TCombobox",
            fieldbackground="#000000",
            background="#111111",
            foreground="#ffffff",
            arrowcolor="#ffffff",
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#000000")],
            selectbackground=[("readonly", "#000000")],
            selectforeground=[("readonly", "#ffffff")],
            foreground=[("readonly", "#ffffff")],
        )
        style.configure(
            "Treeview",
            background="#000000",
            fieldbackground="#000000",
            foreground="#ffffff",
            bordercolor="#222222",
            lightcolor="#222222",
            darkcolor="#222222",
        )
        style.map("Treeview", background=[("selected", "#1f1f1f")], foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background="#111111", foreground="#ffffff")
        style.map(
            "Treeview.Heading",
            background=[("active", "#111111"), ("pressed", "#111111")],
            foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
        )
        style.configure("TRadiobutton", background="#000000", foreground="#ffffff", indicatorcolor="#111111")
        style.map(
            "TRadiobutton",
            background=[("active", "#000000"), ("focus", "#000000"), ("selected", "#000000")],
            foreground=[("active", "#ffffff"), ("focus", "#ffffff"), ("selected", "#ffffff")],
            indicatorcolor=[("selected", "#00aa55"), ("active", "#111111"), ("!selected", "#111111")],
        )
        style.configure("TCheckbutton", background="#000000", foreground="#ffffff")
        style.configure("TEntry", fieldbackground="#000000", foreground="#ffffff")
        style.configure("TSpinbox", fieldbackground="#000000", foreground="#ffffff")
        style.configure("Toolbar.TLabel", background="#000000", foreground="#ffffff", font=("", 10, "bold"))
        style.configure("Toolbar.TButton", background="#111111", foreground="#ffffff", font=("", 10, "bold"))
        style.configure(
            "Toolbar.TRadiobutton",
            background="#000000",
            foreground="#ffffff",
            font=("", 10, "bold"),
            indicatorcolor="#111111",
        )
        style.map(
            "Toolbar.TRadiobutton",
            background=[("active", "#000000"), ("focus", "#000000"), ("selected", "#000000")],
            foreground=[("active", "#ffffff"), ("focus", "#ffffff"), ("selected", "#ffffff")],
            indicatorcolor=[("selected", "#00aa55"), ("active", "#111111"), ("!selected", "#111111")],
        )
        style.configure(
            "Toolbar.TCombobox",
            fieldbackground="#000000",
            background="#111111",
            foreground="#ffffff",
            arrowcolor="#ffffff",
            font=("", 10, "bold"),
        )
        style.configure("Toolbar.TSpinbox", fieldbackground="#000000", foreground="#ffffff", font=("", 10, "bold"))


    # ── Library menu bar ─────────────────────────────────────────────────

    def _create_menu(self, *, open_card, backup_card, new_card, delete_card,
                     save_current_card, restore_to_card, open_in_manager,
                     create_virtual_card_from_physical):
        """Build and attach the library window menu bar."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # File
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(
            label="Open Sample Manager",
            command=self.open_sample_manager,
            accelerator="Ctrl+Shift+M",
        )
        file_menu.add_command(label="Open Pattern Sequencer",
                              command=self.open_pattern_sequencer, accelerator="Ctrl+Shift+L")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)

        # Card
        card_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Card", menu=card_menu)
        card_menu.add_command(label="Open Card...", command=open_card)
        card_menu.add_command(label="Quick Backup", command=backup_card)
        card_menu.add_separator()
        card_menu.add_command(label="New Card...", command=new_card)
        card_menu.add_command(label="Delete Card", command=delete_card)
        card_menu.add_separator()
        card_menu.add_command(label="Save Card Changes", command=save_current_card)
        card_menu.add_command(label="Restore to Card", command=restore_to_card)
        card_menu.add_command(label="Open in Sample Manager", command=open_in_manager)
        card_menu.add_separator()
        card_menu.add_command(label="Create Virtual Card from Physical",
                              command=create_virtual_card_from_physical)

        # Help
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Quick Start", command=self.on_help_quick_start)
        help_menu.add_command(label="Workflow Examples", command=self.on_help_workflow_examples)
        help_menu.add_command(label="FAQ / Troubleshooting", command=self.on_help_faq)
        help_menu.add_separator()
        help_menu.add_command(label="Check for Update...", command=self.on_check_for_update)
        help_menu.add_command(label="About", command=self.on_about)
        help_menu.add_separator()
        help_menu.add_command(label="View Session Log...", command=self.on_view_log)

        # Keyboard shortcuts
        self.root.bind("<Control-Shift-M>", lambda e: self.open_sample_manager())
        self.root.bind("<Control-Shift-m>", lambda e: self.open_sample_manager())
        self.root.bind("<Control-Shift-L>", lambda e: self.open_pattern_sequencer())
        self.root.bind("<Control-Shift-l>", lambda e: self.open_pattern_sequencer())

    # ── Pattern Sequencer ────────────────────────────────────────────────

    def open_sample_manager(self, smpinfo_path: Optional[Path] = None) -> None:
        """Delegate Sample Manager launch to the application controller."""
        self._on_open_sample_manager(smpinfo_path)

    def open_pattern_sequencer(self) -> None:
        """Delegate Pattern Sequencer launch to the application controller."""
        self._on_open_pattern_sequencer()

    def _on_close(self):
        """Quit the application."""
        self.root.destroy()

    # ── Library UI ───────────────────────────────────────────────────────

    def on_help_quick_start(self):
        """Show quick-start guide for beta users."""
        quick_start = """Dr. Sidekick — Quick Start

Welcome! Here's everything you need to get going.


THE SMARTMEDIA LIBRARY — Your starting point
─────────────────────────────────────────────
Dr. Sidekick opens with the SmartMedia Library. This is your personal library
of virtual SP-303 cards — one card per project, kit, or physical card.

Virtual cards are the heart of the workflow. They let you:

  • Back up your physical card — Card -> Quick Backup preserves everything
    (patterns + samples) before you make any changes.

  • Build a project library — create as many virtual cards as you like, each
    with its own name, samples and patterns. Your work is never stuck on one
    physical card.

  • Restore to your physical card — when a virtual card is ready to perform,
    Card -> Restore to Card writes it straight back to the SP-303.

  • Import a physical card — Card -> Create Virtual Card from Physical reads
    a mounted SP-303 card and brings it into the library.

From the library you can branch into two main areas:


WORK ON PATTERNS
─────────────────────────────────────────────
Open the Pattern Sequencer: File -> Open Pattern Sequencer (or Ctrl+Shift+L).

  • Select a pattern slot (C1–D8), switch to Draw mode, and click the pad
    rows to place hits. Drag to move them, right-click to delete.
    Set bar length with the Pattern Length spinner.
    Adjust velocity by selecting notes and using the [ / ] keys.
    Save with Ctrl+S.

  • Import a MIDI file: Patterns -> Import MIDI File...

  • Apply a groove: Patterns -> Add Groove Pattern...

  • Copy or exchange patterns between slots:
    Edit -> Copy Pattern / Paste Pattern, or Patterns -> Exchange Patterns...


WORK ON SAMPLES
─────────────────────────────────────────────
Open Sample Manager from File -> Open Sample Manager or
Card -> Open in Sample Manager in the Library.

  • Quick Import WAV Folder — point at a folder of WAVs. Dr. Sidekick
    converts and prepares everything in BOSS DATA_OUTGOING, ready to copy
    to the SP-303. More than 8 files? They split into BANK_LOAD_01,
    BANK_LOAD_02 etc. — load one bank at a time on the device.

  • Sample Manager — view and reassign which sample lives on which pad.
    Load a card setup, edit the table, then Write Changes to Card.

  • Convert MPC1000 Program (.pgm) — select a .pgm file and Dr. Sidekick
    maps all 64 pads to SP-303 banks, creates a new virtual card named
    after the program, and prepares BANK_LOAD folders for the device.


First time? Back up before you touch anything.
   Card -> Quick Backup, then explore. If anything goes wrong,
   Card -> Restore to Card gets you back to where you started.
"""
        show_text_dialog(self.root, "Quick Start", quick_start, geometry="980x700")

    def on_help_workflow_examples(self):
        """Show real-world workflow examples."""
        examples = """WORKFLOW EXAMPLES


─────────────────────────────────────────────────────────────
Example 1: Load a Kit and Program a Pattern from Scratch
─────────────────────────────────────────────────────────────

Goal: Get your own samples onto the SP-303 and program a beat
      ready to play back on the hardware.

Step 1 — Open the Pattern Sequencer.
  In the SmartMedia Library window: File -> Open Pattern Sequencer (Ctrl+Shift+L).

Step 2 — Load your samples onto the card.
  Open Sample Manager, then Samples -> Quick Import WAV Folder.
  Select your kit folder.
  Files are prepared in SmartMedia-Library/Cards/BOSS DATA_OUTGOING.
  If more than 8 WAVs, load BANK_LOAD_01 first, then BANK_LOAD_02
  on the device. Samples land on pads A1–D8 in file order.

Step 3 — Program the pattern.
  Select a pattern slot (C1–D8). Switch to Draw mode.
  Click pad rows to place hits. Drag to move. Right-click to delete.
  Set bar length with the Pattern Length spinner.
  Adjust velocity by selecting notes and using [ / ] keys.
  Keep device limits in mind: the SP-303 has a nominal 112-event cap
  per pattern slot, but dense timing can fit fewer once encoded.

  Alternatively, import your own MIDI file:
  Patterns -> Import MIDI File... -> select your file.
  Review events in the editor and adjust as needed.

  Optionally apply a groove:
  Patterns -> Add Groove Pattern... -> select your groove file.

Step 4 — Save and load onto the SP-303.
  File -> Save (Ctrl+S).
  Copy the PTNINFO0.SP0 and PTNDATA0.SP0 files to your card.
  Eject safely, insert into SP-303, and play.

Note: A library of example MIDI patterns and grooves is planned for a future release.


─────────────────────────────────────────────────────────────
Example 2: Convert an MPC1000 Kit to SP-303
─────────────────────────────────────────────────────────────

Goal: Bring an MPC1000 drum program straight onto the SP-303,
      preserving the original pad layout as closely as possible.

Step 1 — Open Sample Manager from the Library.
  In Sample Manager, choose Samples -> Convert MPC1000 Program (.pgm).
  Select the .pgm file. If the WAV samples are in the same folder
  (or a subfolder), no further prompt appears.
  If WAVs live elsewhere, a folder picker opens.

Step 2 — Review the results dialog.
  Each bank (A–H) shows which WAV landed on which SMPL slot.
  NOT FOUND entries mean the .pgm referenced a sample name that
  wasn't matched in the WAV folder — check spelling or relocate.

Step 3 — Load onto the SP-303.
  Open SmartMedia-Library/Cards/<pgm name> in Finder.
  Copy BANK_LOAD_01 contents (SMPL0001–SMPL0008.WAV) to your card.
  On the SP-303 select the target bank and run Import.
  Repeat for each BANK_LOAD folder.

Note: Each .pgm gets its own card named after the program file.
  Re-running with the same .pgm overwrites that card only.


─────────────────────────────────────────────────────────────
Example 3: Reorganize a Card Without Losing Anything
─────────────────────────────────────────────────────────────

Goal: Safely reassign pads and shuffle patterns on an existing card.

Step 1 — Back up first.
  In the SmartMedia Library window: Card -> Quick Backup.
  A backup is created in Backup/ next to SmartMedia-Library.

Step 2 — Load the current card setup.
  Open Sample Manager, then click Load Card Setup.
  All current pad assignments appear in the table.

Step 3 — Reassign pads.
  Select a pad row, then use Assign WAV/SP0 to swap samples.
  The status bar confirms every change.

Step 4 — Remap or exchange patterns.
  In the Pattern Sequencer use Edit -> Copy Pattern / Paste Pattern
  to move patterns between slots without re-programming.

Step 5 — Write changes.
  In Sample Manager: Write Changes to Card.
  Eject safely and verify on device.
  If anything is wrong: SmartMedia Library -> Card -> Restore to Card.


─────────────────────────────────────────────────────────────
Example 4: Build and Refine a Sample Kit
─────────────────────────────────────────────────────────────

Goal: Load a folder of WAVs onto the SP-303, then fine-tune
      which sample sits on which pad before committing to the card.

Step 1 — Back up your current card first.
  In the SmartMedia Library window: Card -> Quick Backup.
  A backup is stored in Backup/ next to SmartMedia-Library.

Step 2 — Quick Import your WAVs.
  Open Sample Manager and choose Samples -> Quick Import WAV Folder.
  Select your kit folder. Dr. Sidekick converts and prepares
  the files in SmartMedia-Library/Cards/BOSS DATA_OUTGOING.
  If there are more than 8 WAVs they split into BANK_LOAD_01,
  BANK_LOAD_02 etc. — load one bank at a time on the device.
  Samples are assigned to pads in file order (A1 upwards).

Step 3 — Review and reassign pads.
  In Sample Manager, click Load Card Setup.
  The table shows every pad and its current assignment.
  To move a sample: select its row, click Assign WAV/SP0,
  and pick the replacement file. Repeat for any pad you want
  to change. The status bar confirms each reassignment.

Step 4 — Write to card.
  Click Write Changes to Card in the Sample Manager.
  Copy the output files to your physical SP-303 card.
  Eject safely, insert into SP-303, and verify on device.

Step 5 — Iterate.
  Not happy with the layout? Go back to Step 3 — the virtual
  card in the library holds your work between sessions.
  When you're satisfied, Card -> Quick Backup again to save
  the final state before loading it onto the hardware.
"""
        show_text_dialog(self.root, "Workflow Examples", examples, geometry="980x700")

    def on_help_faq(self):
        """Show FAQ and troubleshooting notes for beta users."""
        faq = """FAQ / Troubleshooting (Beta)

Q: Where do I start — the SmartMedia Library or the Pattern Sequencer?
A: The SmartMedia Library window opens first and is always present. Use it to
   manage your virtual cards. Open the Pattern Sequencer from File -> Open Pattern
   Sequencer (or Ctrl+Shift+L) when you need to edit patterns or work with samples.

Q: I selected a single WAV file in Quick Import. Is that valid?
A: Yes. The app uses that file's parent folder automatically.

Q: Why do I get BANK_LOAD_01 folders?
A: More than 8 WAV files were found. SP-303 loads one bank (8 samples) at a time.

Q: Where are Quick Import files written?
A: SmartMedia-Library/Cards/BOSS DATA_OUTGOING (or /Volumes/BOSS DATA if your
   physical card is mounted and write-to-card is enabled).

Q: Existing WAVs disappeared from BOSS DATA_OUTGOING.
A: They are archived into the subfolder wav_archive_YYYYMMDD_HHMMSS before
   each Quick Import run.

Q: Write Changes completed, but the device did not reflect changes.
A: Most common causes:
   - Card not ejected safely before inserting into SP-303.
   - Wrong output path: check SmartMedia-Library/Cards/BOSS DATA_OUTGOING or
     confirm write-to-card is enabled if targeting a mounted physical card.

Q: How do I back up and restore a card?
A: In the SmartMedia Library window, select the card and use Card -> Quick Backup.
   Backups are stored in Backup/ next to SmartMedia-Library. To restore, use
   Card -> Restore to Card.

Q: I used Convert MPC1000 Program and some pads say NOT FOUND.
A: The .pgm stores sample names without file extensions, and matching is
   done by filename stem. Check that your WAV filenames match the names
   stored in the .pgm (case-insensitive partial matches are tried too).
   If WAVs are in a different folder, re-run and point to the correct folder
   when the folder picker appears.

Q: Convert MPC1000 Program only shows a few banks — where are the rest?
A: Only banks that contain at least one matched sample are written.
   Empty banks are skipped. Pads with no assignment or unresolved samples
   are listed per-slot in the results dialog.

Q: I ran Convert MPC1000 twice with different programs and both are there.
A: Correct — each .pgm gets its own card named after the program file.
   Re-running with the same .pgm overwrites that card only.

Q: How do I import my physical SP-303 card into the library?
A: In the SmartMedia Library window: Card -> Create Virtual Card from Physical.
   This copies the SP0 files from the mounted card into a new virtual card entry.

Q: My imported samples are peaking / too loud on the SP-303.
A: Quick Import does not alter the audio level of your WAV files. If samples
   are peaking after import, use the LEVEL parameter on the SP-303 to reduce
   the amplitude for each pad.

"""
        show_text_dialog(self.root, "FAQ / Troubleshooting", faq, geometry="1024x680")

    def on_check_for_update(self):
        """Check GitHub for the latest release."""
        api_url = "https://api.github.com/repos/OneCoinOnePlay/dr-sidekick/releases/latest"
        current_version = APP_VERSION

        def parse_version(raw: str) -> Tuple[int, ...]:
            raw = raw.strip().lstrip("vV")
            parts: List[int] = []
            for token in raw.split("."):
                digits = "".join(ch for ch in token if ch.isdigit())
                if not digits:
                    break
                parts.append(int(digits))
            return tuple(parts) if parts else (0,)

        def show_result(title: str, msg: str):
            self.root.after(0, lambda: messagebox.showinfo(title, msg))

        def do_check():
            try:
                req = urllib.request.Request(
                    api_url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "Dr-Sidekick-Update-Check",
                    },
                )
                with urllib.request.urlopen(req, timeout=6) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                latest_tag = str(payload.get("tag_name", "")).strip()
                latest_name = str(payload.get("name", "")).strip()
                latest_version_label = latest_tag or latest_name or "unknown"
                release_url = str(payload.get("html_url", "https://github.com/OneCoinOnePlay/dr-sidekick/releases"))

                if parse_version(latest_version_label) > parse_version(current_version):
                    show_result(
                        "Update Available",
                        f"Current version: {current_version}\n"
                        f"Latest version: {latest_version_label}\n\n"
                        f"Download:\n{release_url}",
                    )
                else:
                    show_result(
                        "Up To Date",
                        f"Dr. Sidekick is up to date.\n\nCurrent version: {current_version}",
                    )
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
                show_result(
                    "Check for Update",
                    "Unable to check updates right now.\n\n"
                    f"Current version: {current_version}\n"
                    "Manual check:\nhttps://github.com/OneCoinOnePlay/dr-sidekick/releases",
                )

        threading.Thread(target=do_check, daemon=True).start()

    def on_view_log(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Session Log")
        dialog.geometry("900x540")
        dialog.transient(self.root)
        dialog.configure(bg="#000000")

        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        text = tk.Text(
            frame, wrap=tk.NONE, bg="#000000", fg="#cccccc",
            insertbackground="#ffffff", relief=tk.FLAT, highlightthickness=0,
            font=("Courier", 10),
        )
        scroll_y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        scroll_x = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=text.xview)
        text.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        text.pack(fill=tk.BOTH, expand=True)

        try:
            content = _LOG_PATH.read_text(encoding="utf-8") if _LOG_PATH.exists() else "(no log file yet)"
        except Exception as exc:
            content = f"(could not read log: {exc})"

        text.insert("1.0", content)
        text.configure(state=tk.DISABLED)
        text.see(tk.END)

        bottom = ttk.Frame(frame)
        bottom.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(bottom, text=str(_LOG_PATH), font=("Courier", 9)).pack(side=tk.LEFT)

        def clear_log():
            if not messagebox.askyesno("Clear Log", "Clear the session log file?", parent=dialog):
                return
            try:
                _LOG_PATH.write_text("", encoding="utf-8")
            except Exception as exc:
                messagebox.showerror("Clear Log", str(exc), parent=dialog)
                return
            log.info("Session log cleared by user.")
            try:
                new_content = _LOG_PATH.read_text(encoding="utf-8")
            except Exception:
                new_content = ""
            text.configure(state=tk.NORMAL)
            text.delete("1.0", tk.END)
            text.insert("1.0", new_content)
            text.configure(state=tk.DISABLED)
            text.see(tk.END)

        ttk.Button(bottom, text="Clear Log", command=clear_log).pack(side=tk.RIGHT)

    def on_about(self):
        """Show about dialog"""
        about = tk.Toplevel(self.root)
        about.title("About Dr. Sidekick")
        about.geometry("620x340")
        about.resizable(False, False)
        about.transient(self.root)
        about.grab_set()
        about.configure(bg="#000000")

        container = tk.Frame(about, bg="#000000", padx=16, pady=16)
        container.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            container,
            text=f"Dr. Sidekick v{APP_VERSION}",
            font=("", 14, "bold"),
            bg="#000000",
            fg="#ffffff",
            anchor="w",
            justify=tk.LEFT,
        ).pack(anchor=tk.W)

        tk.Label(
            container,
            text="Standalone graphical pattern editor and SmartMedia librarian for the BOSS Dr. Sample SP-303",
            wraplength=580,
            justify=tk.LEFT,
            bg="#000000",
            fg="#ffffff",
            anchor="w",
        ).pack(anchor=tk.W, pady=(8, 10))

        contacts = (
            "Author: One Coin One Play\n\n"
            "github.com/OneCoinOnePlay\n"
            "soundcloud.com/one_coin_one_play\n"
            "instagram.com/one_coin_one_play\n"
            "linkedin.com/in/onecoinoneplay\n"
            "x.com/OneCoinOnePlay\n"
            "youtube.com/@1coin1play"
        )
        tk.Label(
            container,
            text=contacts,
            justify=tk.LEFT,
            bg="#000000",
            fg="#ffffff",
            anchor="w",
        ).pack(anchor=tk.W, pady=(0, 14))

        tk.Label(
            container,
            text="Disclaimer: Dr. Sidekick is an independent community project and is not affiliated with, endorsed by, or supported by Roland Corporation or BOSS.",
            wraplength=580,
            justify=tk.LEFT,
            bg="#000000",
            fg="#cccccc",
            anchor="w",
        ).pack(anchor=tk.W)

    def _build_ui(self):
        """Build the library window UI directly into the root window."""
        self.state.smartmedia_lib.ensure_dirs()

        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        # ── Branding header ─────────────────────────────────────────────────
        header_frame = ttk.Frame(frame)
        header_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(header_frame, text="Dr. Sidekick", font=("Courier", 18, "bold")).pack(side=tk.LEFT)
        ttk.Label(header_frame, text="Pattern editor and SmartMedia librarian for the Boss Dr. Sample SP-303.",
                  font=("Courier", 9)).pack(side=tk.LEFT, padx=(12, 0), anchor=tk.S, pady=(0, 3))
        ttk.Button(header_frame, text="Open Pattern Sequencer",
                   command=self.open_pattern_sequencer).pack(side=tk.RIGHT)
        ttk.Button(header_frame, text="Open Sample Manager",
                   command=self.open_sample_manager).pack(side=tk.RIGHT, padx=(0, 6))

        # ── Top status bar ──────────────────────────────────────────────────
        top_bar = ttk.Frame(frame)
        top_bar.pack(fill=tk.X, pady=(0, 8))

        card_status_var = tk.StringVar(value="Checking physical card...")
        card_status_lbl = ttk.Label(top_bar, textvariable=card_status_var, font=("Courier", 10))
        card_status_lbl.pack(side=tk.LEFT)

        write_to_card_var = tk.BooleanVar(value=self.state.config.get("write_to_card", True))
        def on_write_toggle():
            self.state.config["write_to_card"] = write_to_card_var.get()
            self.state.save_config()
        ttk.Checkbutton(top_bar, text="Write to Card", variable=write_to_card_var,
                        command=on_write_toggle).pack(side=tk.RIGHT)

        open_card_status_var = tk.StringVar(value="No card open")
        ttk.Label(top_bar, textvariable=open_card_status_var, font=("Courier", 10)).pack(side=tk.RIGHT, padx=(0, 16))

        def refresh_card_status():
            preferred = Path("/Volumes/BOSS DATA")
            if preferred.exists():
                card_status_var.set(f"● BOSS DATA mounted: {preferred}")
            else:
                card_status_var.set("○ No physical card mounted")
            self.root.after(2000, refresh_card_status)
        refresh_card_status()

        auto_backup_var = tk.BooleanVar(value=self.state.config.get("auto_backup_on_open", False))
        def on_auto_backup_toggle():
            self.state.config["auto_backup_on_open"] = auto_backup_var.get()
            self.state.save_config()

        def open_card():
            preferred = Path("/Volumes/BOSS DATA") / "SMPINFO0.SP0"
            if preferred.exists():
                path = preferred
            else:
                chosen = filedialog.askopenfilename(
                    parent=self.root,
                    title="Select SMPINFO0.SP0",
                    initialdir=str(self.state.default_card_mount_dir()),
                    filetypes=[("SMPINFO0.SP0", "SMPINFO0.SP0"), ("SP0 Files", "*.SP0"), ("All Files", "*.*")],
                )
                if not chosen:
                    return
                path = Path(chosen)
            active_smpinfo[0] = path
            open_card_status_var.set(f"Open: {path.parent.name}")
            if auto_backup_var.get():
                try:
                    source = path.parent
                    dest = self.state.smartmedia_lib.backup_dir / source.name
                    dest.mkdir(parents=True, exist_ok=True)
                    for f in sorted(source.glob("*.SP0")):
                        shutil.copy(f, dest / f.name)
                except Exception:
                    pass

        def backup_card():
            card_dir = Path("/Volumes/BOSS DATA")
            if not card_dir.exists():
                if active_smpinfo[0] is not None:
                    card_dir = active_smpinfo[0].parent
                else:
                    messagebox.showwarning("Quick Backup", "No physical card mounted or open.", parent=self.root)
                    return
            sp0_files = sorted(card_dir.glob("*.SP0"))
            if not sp0_files:
                messagebox.showwarning("Quick Backup", "No .SP0 files found on card.", parent=self.root)
                return
            try:
                dest = self.state.smartmedia_lib.backup_dir / card_dir.name
                existing_backup_files = sorted(dest.glob("*.SP0")) if dest.exists() else []
                if existing_backup_files:
                    overwrite = messagebox.askyesno(
                        "Quick Backup",
                        (
                            f"An existing backup is already present at Backup/{card_dir.name}/.\n\n"
                            "Continuing will overwrite files in that backup folder with the current card contents.\n\n"
                            "Do you want to continue?"
                        ),
                        parent=self.root,
                    )
                    if not overwrite:
                        return
                dest.mkdir(parents=True, exist_ok=True)
                for f in sp0_files:
                    shutil.copy(f, dest / f.name)
                messagebox.showinfo("Quick Backup", f"Backed up {len(sp0_files)} file(s) to Backup/{card_dir.name}/", parent=self.root)
            except Exception as exc:
                messagebox.showerror("Quick Backup", str(exc), parent=self.root)

        ttk.Button(top_bar, text="Open Card", command=open_card).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(top_bar, text="Create Virtual Card", command=lambda: create_virtual_card_from_physical()).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(top_bar, text="Quick Backup", command=backup_card).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Checkbutton(top_bar, text="Auto-backup on Open", variable=auto_backup_var,
                        command=on_auto_backup_toggle).pack(side=tk.LEFT, padx=(12, 0))

        # ── Main two-panel layout ────────────────────────────────────────────
        paned = ttk.PanedWindow(frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # ── Left panel: card browser ─────────────────────────────────────────
        left_frame = ttk.Frame(paned, padding=4)
        paned.add(left_frame, weight=1)

        ttk.Label(left_frame, text="VIRTUAL CARDS", font=("Courier", 11, "bold")).pack(anchor=tk.W, pady=(0, 4))

        filter_row = ttk.Frame(left_frame)
        filter_row.pack(fill=tk.X, pady=(0, 4))
        search_var = tk.StringVar()
        search_entry = ttk.Entry(filter_row, textvariable=search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        card_tree_cols = ("name", "author", "ptn")
        style = ttk.Style(self.root)
        style.configure("Library.Treeview", background="#000000", fieldbackground="#000000",
                        foreground="#ffffff", rowheight=22)
        style.map("Library.Treeview", background=[("selected", "#2a7fff")],
                  foreground=[("selected", "#ffffff")])
        card_tree = ttk.Treeview(left_frame, columns=card_tree_cols, show="headings",
                                 height=20, style="Library.Treeview")
        card_tree.heading("name", text="Name")
        card_tree.heading("author", text="Author")
        card_tree.heading("ptn", text="PTN")
        card_tree.column("name", width=160)
        card_tree.column("author", width=120)
        card_tree.column("ptn", width=40, anchor=tk.CENTER)
        card_tree.tag_configure("active", background="#2a7fff", foreground="#ffffff")
        card_tree.pack(fill=tk.BOTH, expand=True)

        left_btn_row = ttk.Frame(left_frame)
        left_btn_row.pack(fill=tk.X, pady=(6, 0))

        # ── Right panel: card detail ─────────────────────────────────────────
        right_frame = ttk.Frame(paned, padding=4)
        paned.add(right_frame, weight=2)

        detail_title = ttk.Label(right_frame, text="CARD DETAIL", font=("Courier", 11, "bold"))
        detail_title.pack(anchor=tk.W, pady=(0, 6))

        # Card detail fields
        detail_frame = ttk.Frame(right_frame)
        detail_frame.pack(fill=tk.X)

        def make_field(parent, label, row):
            ttk.Label(parent, text=label, width=12, anchor=tk.E).grid(row=row, column=0, sticky=tk.E, padx=(0, 6), pady=2)
            var = tk.StringVar()
            entry = ttk.Entry(parent, textvariable=var)
            entry.grid(row=row, column=1, sticky=tk.EW, pady=2)
            parent.columnconfigure(1, weight=1)
            return var, entry

        name_var, name_entry = make_field(detail_frame, "Name:", 0)
        author_var, author_entry = make_field(detail_frame, "Author:", 1)
        categories_var, categories_entry = make_field(detail_frame, "Categories:", 2)
        tags_var, tags_entry = make_field(detail_frame, "Tags:", 3)

        wp_var = tk.BooleanVar(value=False)
        wp_btn = ttk.Checkbutton(detail_frame, text="Write Protect", variable=wp_var)
        wp_btn.grid(row=4, column=1, sticky=tk.W, pady=4)

        # Pre-fill author from last used
        author_var.set(self.state.config.get("last_author", ""))

        # Pad notes
        pad_notes_frame = ttk.LabelFrame(right_frame, text="PAD NOTES", padding=4)
        pad_notes_frame.pack(fill=tk.X, pady=(8, 0))

        pad_note_vars: Dict[str, tk.StringVar] = {}
        for i, pad in enumerate(SP303_PADS):
            col = i % 8
            base_row = (i // 8) * 2
            ttk.Label(pad_notes_frame, text=pad, anchor=tk.CENTER, width=6).grid(
                row=base_row, column=col, padx=2, sticky=tk.EW)
            var = tk.StringVar()
            ttk.Entry(pad_notes_frame, textvariable=var, width=8).grid(
                row=base_row + 1, column=col, padx=2, pady=(0, 4), sticky=tk.EW)
            pad_note_vars[pad] = var
            pad_notes_frame.columnconfigure(col, weight=1)

        detail_btn_row = ttk.Frame(right_frame)
        detail_btn_row.pack(fill=tk.X, pady=(6, 0))

        # ── State ─────────────────────────────────────────────────────────────
        current_card: list = [None]
        active_smpinfo: list = [None]

        def get_all_cards():
            query = search_var.get().strip().lower()
            cards = self.state.smartmedia_lib.list_cards()
            if query:
                cards = [c for c in cards if query in c.name.lower() or query in c.author.lower()
                         or any(query in cat.lower() for cat in c.categories)]
            return cards

        def refresh_card_list():
            active_name = current_card[0].name if current_card[0] else None
            existing = set(card_tree.get_children())
            seen = set()
            for card in get_all_cards():
                if card.name in seen:
                    continue  # skip duplicate names (two dirs with same name in card.json)
                seen.add(card.name)
                tag = ("active",) if card.name == active_name else ()
                ptn_dot = "●" if self.state.smartmedia_lib.card_has_patterns(card.name) else "○"
                if card_tree.exists(card.name):
                    card_tree.item(card.name, values=(card.name, card.author, ptn_dot), tags=tag)
                else:
                    card_tree.insert("", tk.END, iid=card.name, values=(card.name, card.author, ptn_dot), tags=tag)
            for stale in existing - seen:
                card_tree.delete(stale)

        def on_card_select(event=None):
            sel = card_tree.selection()
            for item in card_tree.get_children():
                card_tree.item(item, tags=())
            if not sel:
                current_card[0] = None
                return
            card_tree.item(sel[0], tags=("active",))
            card = self.state.smartmedia_lib.get_card(sel[0])
            if card is None:
                return
            current_card[0] = card
            name_var.set(card.name)
            author_var.set(card.author)
            categories_var.set(", ".join(card.categories))
            tags_var.set(", ".join(card.tags))
            wp_var.set(card.write_protect)
            for pad, var in pad_note_vars.items():
                var.set(card.pad_notes.get(pad, ""))

        card_tree.bind("<<TreeviewSelect>>", on_card_select)
        search_var.trace_add("write", lambda *_: refresh_card_list())

        def save_current_card():
            card = current_card[0]
            if card is None:
                return
            new_name = name_var.get().strip()
            if new_name != card.name:
                try:
                    self.state.smartmedia_lib.rename_card(card, new_name)
                except ValueError as exc:
                    messagebox.showerror("Rename Card", str(exc), parent=self.root)
                    return
            card.author = author_var.get().strip()
            card.categories = [c.strip() for c in categories_var.get().split(",") if c.strip()]
            card.tags = [t.strip() for t in tags_var.get().split(",") if t.strip()]
            card.pad_notes = {pad: var.get().strip() for pad, var in pad_note_vars.items() if var.get().strip()}
            card.write_protect = wp_var.get()
            if card.author:
                self.state.config["last_author"] = card.author
                self.state.save_config()
            self.state.smartmedia_lib.save_card(card)
            refresh_card_list()

        def new_card():
            new_name = simpledialog.askstring("New Virtual Card", "Card name:", parent=self.root)
            if not new_name or not new_name.strip():
                return
            new_name = new_name.strip()
            if self.state.smartmedia_lib.get_card(new_name):
                messagebox.showwarning("New Card", f"A card named '{new_name}' already exists.", parent=self.root)
                return
            card = VirtualCard(name=new_name)
            self.state.smartmedia_lib.create_card(card)
            refresh_card_list()
            card_tree.selection_set(new_name)
            on_card_select()

        def delete_card():
            card = current_card[0]
            if card is None:
                messagebox.showinfo("Delete Card", "Select a card first.", parent=self.root)
                return
            if card.write_protect:
                messagebox.showwarning("Delete Card", "Card is write-protected.", parent=self.root)
                return
            if messagebox.askyesno("Delete Card", f"Delete '{card.name}'? This cannot be undone.", parent=self.root):
                self.state.smartmedia_lib.delete_card(card.name)
                current_card[0] = None
                refresh_card_list()

        def restore_to_card():
            card = current_card[0]
            if card is None:
                messagebox.showinfo("Restore to Card", "Select a virtual card first.", parent=self.root)
                return
            sp0_files = list((self.state.smartmedia_lib.cards_dir / card.name).glob("*.SP0"))
            if not sp0_files:
                messagebox.showwarning("Restore to Card", f"'{card.name}' has no SP0 files to restore.", parent=self.root)
                return
            preferred = Path("/Volumes/BOSS DATA")
            target = preferred if preferred.exists() else self.state.get_library_paths()["outgoing"]
            if not messagebox.askyesno("Restore to Card",
                                       f"Restore '{card.name}' to:\n{target}\n\nThis will overwrite files. Continue?",
                                       parent=self.root):
                return
            try:
                self.state.smartmedia_lib.restore_card(card.name, target)
                messagebox.showinfo("Restore to Card", f"Restored to {target}", parent=self.root)
            except Exception as exc:
                messagebox.showerror("Restore to Card", str(exc), parent=self.root)

        def open_in_manager():
            smpinfo = active_smpinfo[0]
            if smpinfo is None and current_card[0] is not None:
                candidate = self.state.smartmedia_lib.cards_dir / current_card[0].name / "SMPINFO0.SP0"
                if candidate.exists():
                    smpinfo = candidate
            if smpinfo is None:
                messagebox.showinfo(
                    "Sample Manager",
                    "Select a virtual card or open a physical card first.",
                    parent=self.root,
                )
                return
            self.open_sample_manager(smpinfo_path=smpinfo)

        def create_virtual_card_from_physical():
            if active_smpinfo[0] is None:
                messagebox.showinfo(
                    "Create Virtual Card",
                    "Open a physical card first using the Open Card button.",
                    parent=self.root,
                )
                return
            source_dir = active_smpinfo[0].parent
            suggested = source_dir.name if source_dir.name != "BOSS DATA" else ""
            name = simpledialog.askstring(
                "Create Virtual Card", "Name for this virtual card:", initialvalue=suggested, parent=self.root
            )
            if not name or not name.strip():
                return
            name = name.strip()
            if self.state.smartmedia_lib.get_card(name):
                messagebox.showwarning("Create Virtual Card", f"A card named '{name}' already exists.", parent=self.root)
                return
            card = VirtualCard(name=name, author=author_var.get().strip())
            self.state.smartmedia_lib.create_card(card)
            sp0_files = sorted(source_dir.glob("*.SP0"))
            self.state.smartmedia_lib.import_sp0_files(name, source_dir, auto_backup=False)
            card_dir = self.state.smartmedia_lib.cards_dir / name
            active_smpinfo[0] = card_dir / "SMPINFO0.SP0"
            open_card_status_var.set(f"Open: {name}")
            refresh_card_list()
            card_tree.selection_set(name)
            on_card_select()
            messagebox.showinfo(
                "Create Virtual Card",
                f"Created '{name}' with {len(sp0_files)} file(s) imported from {source_dir.name}.",
                parent=self.root,
            )

        # Wire up buttons
        ttk.Button(left_btn_row, text="New Card", command=new_card).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(left_btn_row, text="Delete Card", command=delete_card).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(detail_btn_row, text="Save Changes", command=save_current_card).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(detail_btn_row, text="Restore to Card", command=restore_to_card).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(detail_btn_row, text="Open in Sample Manager", command=open_in_manager).pack(side=tk.LEFT, padx=(0, 6))

        refresh_card_list()

        self._create_menu(
            open_card=open_card,
            backup_card=backup_card,
            new_card=new_card,
            delete_card=delete_card,
            save_current_card=save_current_card,
            restore_to_card=restore_to_card,
            open_in_manager=open_in_manager,
            create_virtual_card_from_physical=create_virtual_card_from_physical,
        )
