"""Tk check-in dialog, run in its own short-lived process.

On macOS a Tk window that is destroyed while the main program goes back to
sleeping is never actually removed from screen: nothing services the Cocoa event
loop any more, so the window lingers with a spinning beach ball.  Running the
dialog in a child process that exits as soon as the user answers guarantees the
window (and the Dock icon) disappear immediately.

Protocol: ``python -m whatidid.dialog '<json args>'`` prints one JSON line
``{"action": "save|skip|snooze|timeout", "text": "..."}`` and exits.
"""
from __future__ import annotations

import json
import sys


def run_dialog(question: str, hint: str, snooze_minutes: float, last_items: list[str],
               timeout_s: float, sound: bool) -> dict:
    import tkinter as tk

    result = {"action": "timeout", "text": ""}
    root = tk.Tk()
    root.title("WhatIDid")
    root.attributes("-topmost", True)
    frm = tk.Frame(root, padx=12, pady=10)
    frm.pack(fill="both", expand=True)
    tk.Label(frm, text=question, font=("TkDefaultFont", 13, "bold")).pack(anchor="w")
    tk.Label(frm, text=hint, fg="#666").pack(anchor="w", pady=(0, 6))
    txt = tk.Text(frm, width=60, height=7, wrap="word", undo=True)
    txt.pack(fill="both", expand=True)
    btns = tk.Frame(frm, pady=8)
    btns.pack(fill="x")

    def finish(action: str, text: str = "") -> None:
        result["action"], result["text"] = action, text
        root.withdraw()  # hide at once, before anything else happens
        root.quit()

    def save(_=None):
        text = txt.get("1.0", "end").strip()
        if text:
            finish("save", text)
        return "break"

    tk.Button(btns, text="Save  (⌘/Ctrl+Enter)", command=save, default="active").pack(side="right")
    tk.Button(btns, text=f"Snooze {snooze_minutes:g}m", command=lambda: finish("snooze")).pack(side="right", padx=4)
    tk.Button(btns, text="Skip", command=lambda: finish("skip")).pack(side="right")
    if last_items:
        def same():
            txt.delete("1.0", "end")
            txt.insert("1.0", "\n".join(last_items))
        tk.Button(btns, text="Same as last", command=same).pack(side="left")
    for seq in ("<Control-Return>", "<Command-Return>"):
        try:
            root.bind(seq, save)
        except tk.TclError:
            pass
    root.bind("<Escape>", lambda _e: finish("skip"))
    root.protocol("WM_DELETE_WINDOW", lambda: finish("skip"))
    if timeout_s > 0:
        root.after(int(timeout_s * 1000), lambda: finish("timeout"))
    if sound:
        root.bell()
    root.lift()
    root.focus_force()
    txt.focus_set()
    root.mainloop()
    try:
        root.destroy()
    except tk.TclError:
        pass
    return result


def main(argv: list[str] | None = None) -> int:
    args = json.loads((argv or sys.argv[1:])[0])
    res = run_dialog(
        args["question"], args["hint"], args["snooze_minutes"],
        args.get("last_items") or [], float(args.get("timeout_s", 0)), bool(args.get("sound", True)),
    )
    sys.stdout.write(json.dumps(res) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
