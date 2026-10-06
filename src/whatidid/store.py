"""Raw entry storage (JSON Lines) with compressed weekly archives.

Layout under ``data_dir``::

    raw/2026-10-05.<host>.jsonl              # active days, plain text, append-only
    archive/2026/2026-W41.<host>.jsonl.xz    # older days, compressed per ISO week

Files are partitioned by host so several machines can share one synced data folder
(Dropbox, iCloud, Box ...) without write conflicts; readers merge all hosts.
"""
from __future__ import annotations

import bz2
import gzip
import json
import lzma
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator

OPENERS = {"xz": lzma.open, "gz": gzip.open, "bz2": bz2.open}

_SPLIT = re.compile(r"\s*(?:\r?\n|;)\s*")
_BULLET = re.compile(r"^\s*(?:[-*•·+>]|\d+[.)]|\[[ xX]?\])\s+")


def parse_items(text: str) -> list[str]:
    """Split free text into items: one per line or ';'-separated; bullets stripped."""
    items = []
    for part in _SPLIT.split(text or ""):
        part = _BULLET.sub("", part).strip().rstrip(",")
        if part:
            items.append(part)
    return items


@dataclass
class Entry:
    start: datetime
    end: datetime
    items: list[str]
    raw: str = ""
    source: str = "cli"
    host: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def minutes(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds() / 60.0)

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "start": self.start.isoformat(timespec="seconds"),
                "end": self.end.isoformat(timespec="seconds"),
                "items": self.items,
                "raw": self.raw,
                "source": self.source,
                "host": self.host,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, line: str) -> "Entry":
        d = json.loads(line)
        return cls(
            start=datetime.fromisoformat(d["start"]),
            end=datetime.fromisoformat(d["end"]),
            items=list(d.get("items", [])),
            raw=d.get("raw", ""),
            source=d.get("source", ""),
            host=d.get("host", ""),
            id=d.get("id") or uuid.uuid4().hex[:12],
        )


def iso_week_label(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


class Store:
    def __init__(self, data_dir: str | Path, host: str, compression: str = "xz"):
        self.root = Path(data_dir).expanduser()
        self.host = host
        self.compression = compression
        self.raw_dir = self.root / "raw"
        self.archive_dir = self.root / "archive"

    @classmethod
    def from_config(cls, cfg: dict) -> "Store":
        st = cfg["storage"]
        return cls(st["data_dir"], st["host"], st["compression"])

    # ---------------------------------------------------------------- writing
    def raw_path(self, d: date, host: str | None = None) -> Path:
        return self.raw_dir / f"{d.isoformat()}.{host or self.host}.jsonl"

    def append(self, entry: Entry) -> Entry:
        entry.host = entry.host or self.host
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        line = (entry.to_json() + "\n").encode("utf-8")
        # O_APPEND + a single write keeps concurrent writers (web + prompter) safe.
        fd = os.open(self.raw_path(entry.end.date()), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        return entry

    # ---------------------------------------------------------------- reading
    def _read_lines(self, path: Path) -> Iterator[str]:
        ext = path.suffix.lstrip(".")
        opener = OPENERS.get(ext)
        try:
            fh = opener(path, "rt", encoding="utf-8") if opener else open(path, encoding="utf-8")
        except FileNotFoundError:
            return
        with fh:
            for line in fh:
                if line.strip():
                    yield line

    def _parse(self, paths: Iterable[Path]) -> Iterator[Entry]:
        for p in paths:
            for line in self._read_lines(p):
                try:
                    yield Entry.from_json(line)
                except (ValueError, KeyError):
                    continue  # tolerate a torn/corrupt line

    def entries(self, first: date, last: date | None = None) -> list[Entry]:
        """All entries whose end time falls on [first, last], sorted, de-duplicated."""
        last = last or first
        paths: list[Path] = []
        d = first
        weeks = set()
        while d <= last:
            paths += sorted(self.raw_dir.glob(f"{d.isoformat()}.*.jsonl"))
            weeks.add((d.isocalendar()[0], iso_week_label(d)))
            d += timedelta(days=1)
        for year, label in sorted(weeks):
            paths += sorted((self.archive_dir / str(year)).glob(f"{label}.*.jsonl.*"))
        seen: set[str] = set()
        out = []
        for e in self._parse(paths):
            if e.id in seen or not (first <= e.end.date() <= last):
                continue
            seen.add(e.id)
            out.append(e)
        out.sort(key=lambda e: e.end)
        return out

    def last_entry(self, on: date | None = None) -> Entry | None:
        on = on or date.today()
        es = self.entries(on)
        return es[-1] if es else None

    def last_items(self, lookback_days: int = 7) -> list[str]:
        today = date.today()
        es = self.entries(today - timedelta(days=lookback_days), today)
        return es[-1].items if es else []

    # -------------------------------------------------------------- archiving
    def archive(self, before: date | None = None, all_hosts: bool = False) -> list[Path]:
        """Compress raw day files dated before ``before`` into weekly archives.

        Only this host's files are touched unless ``all_hosts`` (another machine may
        still be appending to its own file in a shared folder).  Idempotent: entries
        are merged by id, written atomically, and the raw file removed last.
        """
        before = before or date.today()
        pattern = "*.jsonl" if all_hosts else f"*.{self.host}.jsonl"
        by_target: dict[Path, list[Path]] = {}
        for p in sorted(self.raw_dir.glob(pattern)):
            m = re.match(r"(\d{4}-\d{2}-\d{2})\.(.+)\.jsonl$", p.name)
            if not m:
                continue
            d = date.fromisoformat(m.group(1))
            if d >= before:
                continue
            host = m.group(2)
            target = self.archive_dir / str(d.isocalendar()[0]) / f"{iso_week_label(d)}.{host}.jsonl.{self.compression}"
            by_target.setdefault(target, []).append(p)

        written = []
        for target, sources in by_target.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            existing, stale = self._existing_archive(target)
            merged: dict[str, str] = {}
            for line in existing:
                merged.setdefault(_line_id(line), line.rstrip("\n"))
            for src in sources:
                for line in self._read_lines(src):
                    merged.setdefault(_line_id(line), line.rstrip("\n"))
            lines = sorted(merged.values(), key=_line_end)
            tmp = target.with_name(target.name + ".tmp")
            with OPENERS[self.compression](tmp, "wt", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            os.replace(tmp, target)
            for src in stale + sources:
                src.unlink()
            written.append(target)
        return written

    def _existing_archive(self, target: Path) -> tuple[list[str], list[Path]]:
        # The same week may already exist with another compression; fold it in and
        # report it as stale so it is removed only after the new file is in place.
        lines: list[str] = []
        stale: list[Path] = []
        stem = target.name.rsplit(".", 1)[0]
        for ext in OPENERS:
            p = target.with_name(f"{stem}.{ext}")
            if p.exists():
                lines += list(self._read_lines(p))
                if p != target:
                    stale.append(p)
        return lines, stale


def _line_id(line: str) -> str:
    try:
        return json.loads(line).get("id") or line
    except ValueError:
        return line


def _line_end(line: str) -> str:
    try:
        return json.loads(line).get("end", "")
    except ValueError:
        return ""
