"""The long-running prompter loop."""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta

from . import prompt, schedule
from .store import Entry, Store, parse_items


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def sleep_until(target: datetime, max_chunk: float = 30.0) -> None:
    """Sleep in short chunks so laptop sleep / clock changes are noticed."""
    while True:
        remaining = (target - schedule.now_local()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, max_chunk))


def maybe_archive(cfg: dict, store: Store) -> None:
    keep = int(cfg["storage"]["archive_after_days"])
    if keep < 0:
        return
    written = store.archive(before=date.today() - timedelta(days=max(keep - 1, 0)))
    for p in written:
        log(f"archived -> {p}")


def check_in(cfg: dict, store: Store, slot: datetime, lock: threading.Lock | None = None) -> str:
    """Prompt once for the period ending at ``slot``; handles snooze. Returns action."""
    lock = lock or threading.Lock()
    snooze = timedelta(minutes=cfg["schedule"]["snooze_minutes"])
    mode = prompt.resolve_mode(cfg["prompt"]["mode"])
    while True:
        last = store.last_entry(slot.date())
        if last and last.end >= slot - timedelta(minutes=1):
            return "already-logged"  # e.g. logged from phone/web meanwhile
        start = schedule.period_start(slot, last.end if last else None, cfg)
        if mode == "notify":
            probs = prompt.notify(
                cfg,
                "WhatIDid",
                f"What did you do {start:%H:%M}–{slot:%H:%M}?",
                {"WHATIDID_START": start.isoformat(), "WHATIDID_END": slot.isoformat()},
            )
            for p in probs:
                log(f"notify problem: {p}")
            return "notified"
        if cfg["notify"]["ntfy_url"] or cfg["notify"]["command"]:
            prompt.notify(dict(cfg, notify=dict(cfg["notify"], desktop=False)), "WhatIDid", f"What did you do {start:%H:%M}–{slot:%H:%M}?")
        res = prompt.ask(cfg, start, slot, store.last_items())
        if res.action == "save":
            items = parse_items(res.text)
            if items:
                with lock:
                    # if the user took a while to answer, the entry still ends at the slot
                    store.append(Entry(start=start, end=slot, items=items, raw=res.text, source=mode))
                log(f"logged {len(items)} item(s) for {start:%H:%M}–{slot:%H:%M}")
            return "save"
        if res.action == "snooze":
            log(f"snoozed {cfg['schedule']['snooze_minutes']} min")
            sleep_until(schedule.now_local() + snooze)
            continue
        log(res.action)
        return res.action


def run(cfg: dict, store: Store, once: bool = False, lock: threading.Lock | None = None) -> None:
    step = timedelta(minutes=cfg["schedule"]["interval_minutes"])
    maybe_archive(cfg, store)
    last_day = date.today()
    mode = prompt.resolve_mode(cfg["prompt"]["mode"])
    log(f"WhatIDid running: every {cfg['schedule']['interval_minutes']} min, mode={mode}, data={store.root}")
    if once:
        now = schedule.now_local().replace(second=0, microsecond=0)
        check_in(cfg, store, now, lock)
        return
    while True:
        now = schedule.now_local()
        nxt = schedule.next_prompt_time(now, cfg)
        if nxt is None:
            log("No working hours configured in the next month; sleeping a day.")
            time.sleep(86400)
            continue
        log(f"next prompt at {nxt:%a %Y-%m-%d %H:%M}")
        sleep_until(nxt)
        woke = schedule.now_local()
        if woke.date() != last_day:
            maybe_archive(cfg, store)
            last_day = woke.date()
        if woke - nxt > step and not schedule.in_working_hours(woke, cfg):
            log(f"missed {nxt:%H:%M} prompt (computer asleep?); skipping")
            continue
        check_in(cfg, store, nxt, lock)
