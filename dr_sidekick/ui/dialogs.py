"""Shared dialog helpers for the Tk UI."""

import tkinter as tk
from tkinter import ttk


def show_text_dialog(
    parent: tk.Widget,
    title: str,
    content: str,
    geometry: str = "1024x640",
):
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.geometry(geometry)
    dialog.transient(parent)
    dialog.grab_set()
    dialog.configure(bg="#000000")

    frame = ttk.Frame(dialog, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)

    text_frame = ttk.Frame(frame)
    text_frame.pack(fill=tk.BOTH, expand=True)

    text = tk.Text(
        text_frame,
        wrap="none",
        font=("TkFixedFont", 11),
        bg="#000000",
        fg="#ffffff",
        insertbackground="#ffffff",
        relief=tk.FLAT,
        highlightthickness=0,
    )
    text.insert("1.0", content)
    text.configure(state=tk.DISABLED)
    text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    y_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text.yview)
    y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    text.configure(yscrollcommand=y_scroll.set)

    x_scroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=text.xview)
    x_scroll.pack(fill=tk.X)
    text.configure(xscrollcommand=x_scroll.set)
