"""Configuration: built-in defaults < TOML parameter file < CLI overrides."""
from __future__ import annotations

import copy
import os
import re
import socket
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

DEFAULTS: dict[str, Any] = {
    "schedule": {
        "interval_minutes": 30,
        "work_hours": ["09:00-17:30"],
        "work_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
        "align_to_clock": True,
        "prompt_at_window_end": True,
        "snooze_minutes": 5,
        "holidays": [],
    },
    "prompt": {
        "mode": "auto",  # auto | gui | osascript | terminal | notify
        "timeout_minutes": 10,
        "sound": True,
    },
    "storage": {
        "data_dir": "",  # empty -> platform default
        "compression": "xz",  # xz | gz | bz2
        "archive_after_days": 1,
        "host": "",  # empty -> short hostname
    },
    "summary": {
        "format": "markdown",  # markdown | text | json
        "show_time": True,
        "timeline": False,
        "fuzzy_threshold": 0.85,
        "top_per_day": 5,
        "untagged_label": "Other",
        "exclude_tags": ["lunch"],  # not counted in totals or listed in summaries
        "llm": False,
        "llm_model": "claude-sonnet-4-5",
        "llm_max_tokens": 1500,
        "llm_style": (
            "Rewrite as a concise weekly status update for my team: short bullet "
            "points grouped by project, past tense, no time estimates unless notable."
        ),
    },
    "tags": {
        "aliases": {},  # {"neos": "neo-surveyor"}
        "keywords": {},  # {"meetings": ["meeting", "telecon", "standup"]}
    },
    "web": {
        "host": "127.0.0.1",
        "port": 8765,
        "token": "",
    },
    "notify": {
        "desktop": True,
        "ntfy_url": "",  # e.g. https://ntfy.sh/my-secret-topic
        "click_url": "",  # link opened from the push notification
        "command": "",  # shell hook, gets WHATIDID_* env vars
    },
}


class ConfigError(ValueError):
    pass


def default_config_path() -> Path:
    env = os.environ.get("WHATIDID_CONFIG")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "whatidid" / "config.toml"


def default_data_dir() -> Path:
    env = os.environ.get("WHATIDID_DATA")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "whatidid"


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("aliases", "keywords"):
            out[k] = deep_merge(out[k], v)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = copy.deepcopy(v)
    return out


def parse_scalar(text: str) -> Any:
    """Parse a CLI override value: TOML literal if possible, else a bare string."""
    if tomllib is not None:
        try:
            return tomllib.loads(f"v = {text}")["v"]
        except Exception:
            pass
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def set_path(cfg: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = cfg
    for p in parts[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):
            raise ConfigError(f"'{dotted}': '{p}' is not a section")
    node[parts[-1]] = value


def read_toml(path: Path) -> dict:
    if tomllib is None:
        raise ConfigError(
            "Reading a TOML parameter file needs Python 3.11+ or the 'tomli' package "
            "(pip install tomli)."
        )
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def load_config(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> dict:
    """Return the effective configuration dict.

    ``path``: explicit parameter file (must exist). If None, the default location is
    used when present. ``overrides``: {"schedule.interval_minutes": 15, ...}.
    """
    cfg = copy.deepcopy(DEFAULTS)
    src = None
    if path is not None:
        src = Path(path).expanduser()
        if not src.exists():
            raise ConfigError(f"Parameter file not found: {src}")
    elif default_config_path().exists():
        src = default_config_path()
    if src is not None:
        cfg = deep_merge(cfg, read_toml(src))
    for key, val in (overrides or {}).items():
        set_path(cfg, key, val)
    cfg["_source"] = str(src) if src else None
    return validate(cfg)


_HHMM = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def parse_days(spec: Any) -> list[int]:
    """'Mon-Fri', 'Mon,Wed,Fri', ['Mon', 'Tue'] or [0, 1] -> sorted weekday ints."""
    if isinstance(spec, str):
        spec = [s for s in re.split(r"[,\s]+", spec) if s]
    days: set[int] = set()
    for item in spec:
        if isinstance(item, int):
            days.add(item % 7)
            continue
        item = str(item).strip().lower()
        if "-" in item:
            a, b = (DAY_NAMES.index(x[:3]) for x in item.split("-", 1))
            i = a
            while True:
                days.add(i)
                if i == b:
                    break
                i = (i + 1) % 7
        else:
            if item[:3] not in DAY_NAMES:
                raise ConfigError(f"Unknown weekday: {item!r}")
            days.add(DAY_NAMES.index(item[:3]))
    return sorted(days)


def parse_hours(spec: Any) -> list[tuple[int, int]]:
    """['09:00-12:00', ...] or '09:00-12:00,13:00-17:00' -> [(start_min, end_min), ...]."""
    if isinstance(spec, str):
        spec = [s for s in spec.split(",") if s.strip()]
    out = []
    for w in spec:
        m = _HHMM.match(str(w))
        if not m:
            raise ConfigError(f"Bad work_hours window {w!r}; expected 'HH:MM-HH:MM'")
        h1, m1, h2, m2 = map(int, m.groups())
        a, b = h1 * 60 + m1, h2 * 60 + m2
        if not (0 <= a < b <= 24 * 60):
            raise ConfigError(f"Bad work_hours window {w!r}")
        out.append((a, b))
    return sorted(out)


def validate(cfg: dict) -> dict:
    s = cfg["schedule"]
    try:
        s["interval_minutes"] = float(s["interval_minutes"])
    except (TypeError, ValueError):
        raise ConfigError("schedule.interval_minutes must be a number")
    if s["interval_minutes"] <= 0:
        raise ConfigError("schedule.interval_minutes must be > 0")
    if s["interval_minutes"].is_integer():
        s["interval_minutes"] = int(s["interval_minutes"])
    s["_windows"] = parse_hours(s["work_hours"])
    s["_days"] = parse_days(s["work_days"])
    s["holidays"] = [str(h) for h in s.get("holidays", [])]

    if cfg["prompt"]["mode"] not in ("auto", "gui", "osascript", "terminal", "notify"):
        raise ConfigError("prompt.mode must be auto|gui|osascript|terminal|notify")
    st = cfg["storage"]
    if st["compression"] not in ("xz", "gz", "bz2"):
        raise ConfigError("storage.compression must be xz|gz|bz2")
    st["data_dir"] = str(Path(st["data_dir"]).expanduser()) if st["data_dir"] else str(default_data_dir())
    st["host"] = re.sub(r"[^\w-]", "_", st["host"] or socket.gethostname().split(".")[0]) or "host"
    if cfg["summary"]["format"] in ("md",):
        cfg["summary"]["format"] = "markdown"
    if cfg["summary"]["format"] not in ("markdown", "text", "json"):
        raise ConfigError("summary.format must be markdown|text|json")
    ex = cfg["summary"]["exclude_tags"]
    cfg["summary"]["exclude_tags"] = [str(t).lower().lstrip("#") for t in ([ex] if isinstance(ex, str) else ex)]
    cfg["tags"]["aliases"] = {str(k).lower(): str(v).lower() for k, v in cfg["tags"]["aliases"].items()}
    cfg["tags"]["keywords"] = {
        str(k).lower(): [kw if isinstance(kw, str) else str(kw) for kw in (v if isinstance(v, list) else [v])]
        for k, v in cfg["tags"]["keywords"].items()
    }
    return cfg


def dump_effective(cfg: dict) -> str:
    """Human-readable TOML-ish dump of the effective configuration."""
    lines = [f"# source: {cfg.get('_source') or '(built-in defaults)'}"]
    for section, body in cfg.items():
        if section.startswith("_") or not isinstance(body, dict):
            continue
        lines.append(f"\n[{section}]")
        for k, v in body.items():
            if k.startswith("_"):
                continue
            lines.append(f"{k} = {_toml_value(v)}")
    return "\n".join(lines) + "\n"


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f'"{k}" = {_toml_value(x)}' for k, x in v.items()) + " }"
    return repr(v)
