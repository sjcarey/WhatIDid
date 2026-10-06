"""Rule-based daily and weekly summaries (no network needed)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher

from .store import Entry

_HASHTAG = re.compile(r"(?<![\w&/])#([A-Za-z0-9][\w./-]*)")
_WS = re.compile(r"\s+")
_NORM = re.compile(r"[^\w\s]")


def item_tags(text: str, cfg: dict) -> list[str]:
    """Explicit #tags first (alias-resolved), then keyword-rule tags, de-duplicated."""
    aliases = cfg["tags"]["aliases"]
    tags = [aliases.get(t.lower().rstrip("."), t.lower().rstrip(".")) for t in _HASHTAG.findall(text)]
    low = text.lower()
    for tag, kws in cfg["tags"]["keywords"].items():
        for kw in kws:
            if re.search(r"(?<!\w)" + re.escape(kw.lower()) + r"(?!\w)", low):
                tags.append(aliases.get(tag, tag))
                break
    out: list[str] = []
    for t in tags:
        if t not in out:
            out.append(t)
    return out


def clean_text(text: str, keep_empty: bool = False) -> str:
    """Drop #tags from display text (they become section headings)."""
    t = _HASHTAG.sub("", text)
    t = _WS.sub(" ", t).strip(" -,;:.")
    return t if (t or keep_empty) else text.strip()


def _norm(text: str) -> str:
    return _WS.sub(" ", _NORM.sub(" ", text.lower())).strip()


@dataclass
class Activity:
    text: str
    tag: str
    minutes: float = 0.0
    count: int = 0
    days: list[date] = field(default_factory=list)
    key: str = ""

    def add(self, minutes: float, d: date) -> None:
        self.minutes += minutes
        self.count += 1
        if d not in self.days:
            self.days.append(d)


def build_activities(entries: list[Entry], cfg: dict) -> list[Activity]:
    """Flatten entries to items, attribute time evenly, merge near-duplicates."""
    thr = float(cfg["summary"]["fuzzy_threshold"])
    other = cfg["summary"]["untagged_label"]
    acts: list[Activity] = []
    for e in entries:
        if not e.items:
            continue
        # An item that is only #tags (e.g. "#neos") tags every other item in the entry.
        entry_tags = [t for i in e.items if not clean_text(i, keep_empty=True) for t in item_tags(i, cfg)]
        items = [i for i in e.items if clean_text(i, keep_empty=True)]
        if not items:
            continue
        share = e.minutes / len(items)
        for raw in items:
            tags = item_tags(raw, cfg) or entry_tags
            tag = tags[0] if tags else other
            text = clean_text(raw)
            key = _norm(text)
            match = None
            for a in acts:
                if a.tag != tag:
                    continue
                if a.key == key or (thr < 1 and SequenceMatcher(None, a.key, key).ratio() >= thr):
                    match = a
                    break
            if match is None:
                match = Activity(text=text, tag=tag, key=key)
                acts.append(match)
            match.add(share, e.end.date())
    return acts


def group(acts: list[Activity], cfg: dict) -> list[tuple[str, float, list[Activity]]]:
    """[(tag, total_minutes, activities sorted by time)], biggest tag first, Other last."""
    other = cfg["summary"]["untagged_label"]
    groups: dict[str, list[Activity]] = {}
    for a in acts:
        groups.setdefault(a.tag, []).append(a)
    out = [(t, sum(a.minutes for a in v), sorted(v, key=lambda a: -a.minutes)) for t, v in groups.items()]
    out.sort(key=lambda g: (g[0] == other, -g[1], g[0]))
    return out


def fmt_minutes(m: float) -> str:
    m = int(round(m / 5.0) * 5)  # report to the nearest 5 minutes
    h, mm = divmod(m, 60)
    if h and mm:
        return f"{h}h {mm:02d}m"
    return f"{h}h" if h else f"{mm}m"


def _day_label(d: date) -> str:
    return d.strftime("%a %Y-%m-%d")


def week_bounds(d: date) -> tuple[date, date]:
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


# --------------------------------------------------------------------- daily
def daily(entries: list[Entry], day: date, cfg: dict, fmt: str | None = None, timeline: bool | None = None) -> str:
    fmt = fmt or cfg["summary"]["format"]
    timeline = cfg["summary"]["timeline"] if timeline is None else timeline
    show_time = cfg["summary"]["show_time"]
    es = [e for e in entries if e.end.date() == day]
    acts = build_activities(es, cfg)
    groups = group(acts, cfg)
    total = sum(e.minutes for e in es)

    if fmt == "json":
        return json.dumps(
            {
                "date": day.isoformat(),
                "total_minutes": round(total, 1),
                "checkins": len(es),
                "groups": [
                    {"tag": t, "minutes": round(m, 1), "items": [_act_json(a) for a in v]} for t, m, v in groups
                ],
                "timeline": [
                    {"start": e.start.isoformat(), "end": e.end.isoformat(), "items": e.items} for e in es
                ],
            },
            indent=2,
        )

    L = [f"# What I did — {_day_label(day)}", ""]
    if not es:
        L.append("_No check-ins recorded._")
        return _finish(L, fmt)
    span = f"{es[0].start:%H:%M}–{es[-1].end:%H:%M}"
    L += [f"_{fmt_minutes(total)} logged across {len(es)} check-ins ({span})_", ""]
    for tag, mins, items in groups:
        L.append(f"## {tag}" + (f" ({fmt_minutes(mins)})" if show_time else ""))
        for a in items:
            extra = []
            if show_time:
                extra.append(fmt_minutes(a.minutes))
            if a.count > 1:
                extra.append(f"×{a.count}")
            L.append(f"- {a.text}" + (f" ({', '.join(extra)})" if extra else ""))
        L.append("")
    if timeline:
        L.append("## Timeline")
        for e in es:
            L.append(f"- {e.start:%H:%M}–{e.end:%H:%M}  " + "; ".join(e.items))
        L.append("")
    return _finish(L, fmt)


# -------------------------------------------------------------------- weekly
def weekly(entries: list[Entry], any_day: date, cfg: dict, fmt: str | None = None) -> str:
    fmt = fmt or cfg["summary"]["format"]
    show_time = cfg["summary"]["show_time"]
    top_n = int(cfg["summary"]["top_per_day"])
    first, last = week_bounds(any_day)
    es = [e for e in entries if first <= e.end.date() <= last]
    acts = build_activities(es, cfg)
    groups = group(acts, cfg)
    total = sum(e.minutes for e in es)
    days = sorted({e.end.date() for e in es})
    y, w, _ = first.isocalendar()

    if fmt == "json":
        return json.dumps(
            {
                "week": f"{y}-W{w:02d}",
                "from": first.isoformat(),
                "to": last.isoformat(),
                "total_minutes": round(total, 1),
                "days": [d.isoformat() for d in days],
                "groups": [
                    {"tag": t, "minutes": round(m, 1), "items": [_act_json(a) for a in v]} for t, m, v in groups
                ],
            },
            indent=2,
        )

    L = [f"# Weekly status — {y}-W{w:02d} ({first:%a %b %d} – {last:%a %b %d})", ""]
    if not es:
        L.append("_No check-ins recorded this week._")
        return _finish(L, fmt)
    L += [f"_{fmt_minutes(total)} logged over {len(days)} day(s)_", ""]
    for tag, mins, items in groups:
        L.append(f"## {tag}" + (f" ({fmt_minutes(mins)})" if show_time else ""))
        for a in items:
            extra = []
            if show_time:
                extra.append(fmt_minutes(a.minutes))
            if len(a.days) > 1:
                extra.append("/".join(d.strftime("%a") for d in sorted(a.days)))
            L.append(f"- {a.text}" + (f" ({', '.join(extra)})" if extra else ""))
        L.append("")
    L.append("## Day by day")
    for d in days:
        day_es = [e for e in es if e.end.date() == d]
        day_acts = sorted(build_activities(day_es, cfg), key=lambda a: -a.minutes)
        mins = sum(e.minutes for e in day_es)
        head = f"- **{d:%a %m-%d}**" + (f" ({fmt_minutes(mins)})" if show_time else "") + ": "
        L.append(head + "; ".join(a.text for a in day_acts[:top_n]) + (" …" if len(day_acts) > top_n else ""))
    L.append("")
    return _finish(L, fmt)


def _act_json(a: Activity) -> dict:
    return {
        "text": a.text,
        "minutes": round(a.minutes, 1),
        "count": a.count,
        "days": [d.isoformat() for d in a.days],
    }


def _finish(lines: list[str], fmt: str) -> str:
    text = "\n".join(lines).rstrip() + "\n"
    if fmt == "text":
        text = re.sub(r"^#+ ", "", text, flags=re.M)
        text = re.sub(r"(?m)^_(.*)_$", r"\1", text).replace("**", "")
    return text
