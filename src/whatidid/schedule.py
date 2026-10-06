"""Working-hours logic and prompt-time computation."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta


def now_local() -> datetime:
    return datetime.now().astimezone()


def _at(d: date, minutes: int, tz) -> datetime:
    if minutes >= 24 * 60:
        return datetime.combine(d + timedelta(days=1), time(0), tz)
    return datetime.combine(d, time(minutes // 60, minutes % 60), tz)


def is_workday(d: date, cfg: dict) -> bool:
    s = cfg["schedule"]
    return d.weekday() in s["_days"] and d.isoformat() not in s["holidays"]


def windows_for(d: date, cfg: dict, tz=None) -> list[tuple[datetime, datetime]]:
    """Working windows on day ``d`` as aware datetimes (empty on non-work days)."""
    if not is_workday(d, cfg):
        return []
    tz = tz or now_local().tzinfo
    return [(_at(d, a, tz), _at(d, b, tz)) for a, b in cfg["schedule"]["_windows"]]


def in_working_hours(t: datetime, cfg: dict) -> bool:
    return any(a <= t < b for a, b in windows_for(t.date(), cfg, t.tzinfo))


def prompt_times_for_day(d: date, cfg: dict, tz=None) -> list[datetime]:
    """All scheduled prompt times on day ``d``.

    A prompt asks about the interval that just ended, so the first prompt in a window
    comes one interval after it opens (or at the first clock-aligned slot after it
    opens when ``align_to_clock``).  With ``prompt_at_window_end`` a final prompt is
    added when each window closes so the tail end is never lost.
    """
    s = cfg["schedule"]
    step = timedelta(minutes=s["interval_minutes"])
    out: set[datetime] = set()
    for ws, we in windows_for(d, cfg, tz):
        if s["align_to_clock"]:
            midnight = datetime.combine(d, time(0), ws.tzinfo)
            k = int((ws - midnight) / step) + 1
            t = midnight + k * step
        else:
            t = ws + step
        while t <= we:
            out.add(t)
            t += step
        if s["prompt_at_window_end"]:
            out.add(we)
    return sorted(out)


def next_prompt_time(after: datetime, cfg: dict, horizon_days: int = 31) -> datetime | None:
    """First prompt time strictly after ``after``."""
    for i in range(horizon_days + 1):
        for t in prompt_times_for_day(after.date() + timedelta(days=i), cfg, after.tzinfo):
            if t > after:
                return t
    return None


def prev_prompt_time(before: datetime, cfg: dict) -> datetime | None:
    """Most recent prompt time at or before ``before`` (today only)."""
    times = [t for t in prompt_times_for_day(before.date(), cfg, before.tzinfo) if t <= before]
    return times[-1] if times else None


def period_start(end: datetime, last_end: datetime | None, cfg: dict) -> datetime:
    """Start of the period a check-in at ``end`` covers.

    Normally ``end - interval``.  If earlier prompts were missed today, extend back to
    the previous check-in, but never across the start of the current working window
    (so a gap between windows isn't counted).
    """
    step = timedelta(minutes=cfg["schedule"]["interval_minutes"])
    start = end - step
    if last_end and last_end.date() == end.date() and last_end < start:
        start = last_end
    for ws, we in windows_for(end.date(), cfg, end.tzinfo):
        if ws <= end <= we + step:
            start = max(start, ws)
            break
    if last_end and last_end > start and last_end < end:
        start = last_end  # don't double-count overlap with the previous check-in
    return start
