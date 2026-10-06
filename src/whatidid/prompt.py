"""Ways of asking "what did you do?" and of nudging the user."""
from __future__ import annotations

import os
import platform
import select
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime

HINT = "One item per line (or separate with ';'). Use #tags for projects."


@dataclass
class PromptResult:
    action: str  # save | skip | snooze | timeout | notified
    text: str = ""


def _question(start: datetime, end: datetime) -> str:
    return f"What did you do {start:%H:%M}–{end:%H:%M}?"


def resolve_mode(mode: str) -> str:
    if mode != "auto":
        return mode
    if gui_available():
        return "gui"
    if platform.system() == "Darwin" and shutil.which("osascript"):
        return "osascript"
    if sys.stdin and sys.stdin.isatty():
        return "terminal"
    return "notify"


def gui_available() -> bool:
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    if platform.system() in ("Darwin", "Windows"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def ask(cfg: dict, start: datetime, end: datetime, last_items: list[str] | None = None) -> PromptResult:
    mode = resolve_mode(cfg["prompt"]["mode"])
    timeout = float(cfg["prompt"]["timeout_minutes"]) * 60
    if mode == "gui":
        return ask_gui(cfg, start, end, last_items, timeout)
    if mode == "osascript":
        return ask_osascript(cfg, start, end, timeout)
    if mode == "terminal":
        return ask_terminal(cfg, start, end, last_items, timeout)
    return PromptResult("notified")


# ------------------------------------------------------------------ tkinter
def ask_gui(cfg, start, end, last_items, timeout) -> PromptResult:
    """Show the Tk dialog in a child process (see dialog.py for why)."""
    import json

    args = json.dumps({
        "question": _question(start, end),
        "hint": HINT,
        "snooze_minutes": cfg["schedule"]["snooze_minutes"],
        "last_items": last_items or [],
        "timeout_s": timeout,
        "sound": cfg["prompt"]["sound"],
    })
    try:
        out = subprocess.run(
            [sys.executable, "-m", "whatidid.dialog", args],
            capture_output=True, text=True,
            timeout=(timeout + 60) if timeout > 0 else None,
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        return PromptResult("timeout")
    except OSError as e:
        print(f"whatidid: could not start dialog ({e})", file=sys.stderr)
        return PromptResult("timeout")
    lines = [ln for ln in out.stdout.splitlines() if ln.strip().startswith("{")]
    if out.returncode != 0 or not lines:
        print(f"whatidid: dialog failed: {out.stderr.strip()[-400:]}", file=sys.stderr)
        return PromptResult("timeout")
    res = json.loads(lines[-1])
    return PromptResult(res.get("action", "timeout"), res.get("text", ""))


def _child_env() -> dict:
    """Make sure the child can import this package even when run from a source tree."""
    import whatidid

    env = dict(os.environ)
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(whatidid.__file__)))
    env["PYTHONPATH"] = pkg_parent + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


# ------------------------------------------------------------- macOS dialog
def _as_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def ask_osascript(cfg, start, end, timeout) -> PromptResult:
    snooze = cfg["schedule"]["snooze_minutes"]
    script = f"""
set r to display dialog {_as_str(_question(start, end) + chr(10) + "Separate items with ';'. Use #tags.")} ¬
  default answer "" with title "WhatIDid" ¬
  buttons {{"Skip", "Snooze {snooze}m", "Save"}} default button "Save" ¬
  giving up after {int(timeout) if timeout > 0 else 86400}
if gave up of r then return "TIMEOUT"
return (button returned of r) & linefeed & (text returned of r)
"""
    try:
        out = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=timeout + 30 if timeout else None)
    except (OSError, subprocess.TimeoutExpired):
        return PromptResult("timeout")
    if out.returncode != 0:  # user hit Cmd-. / cancel
        return PromptResult("skip")
    button, _, text = out.stdout.rstrip("\n").partition("\n")
    if button == "TIMEOUT":
        return PromptResult("timeout")
    if button.startswith("Snooze"):
        return PromptResult("snooze")
    if button == "Save" and text.strip():
        return PromptResult("save", text)
    return PromptResult("skip")


# ----------------------------------------------------------------- terminal
def ask_terminal(cfg, start, end, last_items, timeout) -> PromptResult:
    if cfg["prompt"]["sound"]:
        sys.stdout.write("\a")
    print(f"\n{_question(start, end)}\n  {HINT}\n  Finish with an empty line.  Commands: !skip  !snooze  !same")
    lines: list[str] = []
    while True:
        sys.stdout.write("> ")
        sys.stdout.flush()
        line = _readline(timeout if not lines else 0)
        if line is None:
            print("\n(timed out)")
            return PromptResult("timeout")
        line = line.rstrip("\n")
        if not lines and line.strip() in ("!skip", "!s"):
            return PromptResult("skip")
        if not lines and line.strip() in ("!snooze", "!z"):
            return PromptResult("snooze")
        if not lines and line.strip() == "!same":
            return PromptResult("save", "\n".join(last_items or [])) if last_items else PromptResult("skip")
        if not line.strip():
            return PromptResult("save", "\n".join(lines)) if lines else PromptResult("skip")
        lines.append(line)


def _readline(timeout: float) -> str | None:
    if timeout and os.name == "posix":
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return None
    line = sys.stdin.readline()
    return line if line else None


# -------------------------------------------------------------- notifications
def notify(cfg: dict, title: str, message: str, extra_env: dict | None = None) -> list[str]:
    """Fire every configured notifier; return a list of problems (never raises)."""
    problems = []
    n = cfg["notify"]
    if n["desktop"]:
        try:
            desktop_notification(title, message)
        except Exception as e:  # pragma: no cover
            problems.append(f"desktop: {e}")
    if n["ntfy_url"]:
        try:
            headers = {"Title": title, "Tags": "memo"}
            if n["click_url"]:
                headers["Click"] = n["click_url"]
            req = urllib.request.Request(n["ntfy_url"], data=message.encode(), headers=headers, method="POST")
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:
            problems.append(f"ntfy: {e}")
    if n["command"]:
        env = dict(os.environ, WHATIDID_TITLE=title, WHATIDID_MESSAGE=message, **(extra_env or {}))
        try:
            subprocess.run(n["command"], shell=True, env=env, timeout=30)
        except Exception as e:
            problems.append(f"command: {e}")
    return problems


def desktop_notification(title: str, message: str) -> None:
    system = platform.system()
    if system == "Darwin" and shutil.which("osascript"):
        subprocess.run(
            ["osascript", "-e", f"display notification {_as_str(message)} with title {_as_str(title)}"],
            capture_output=True,
            timeout=10,
        )
    elif system == "Linux" and shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "WhatIDid", title, message], capture_output=True, timeout=10)
