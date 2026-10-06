"""Command-line interface."""
from __future__ import annotations

import argparse
import re
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from . import __version__, schedule, summarize
from .config import ConfigError, default_config_path, dump_effective, load_config, parse_scalar
from .store import Entry, Store, parse_items

EXAMPLE_CONFIG = Path(__file__).with_name("example_config.toml")


# ----------------------------------------------------------------- parsing
def parse_day(s: str | None) -> date:
    today = date.today()
    if not s or s == "today":
        return today
    if s == "yesterday":
        return today - timedelta(days=1)
    if re.fullmatch(r"-\d+", s):
        return today + timedelta(days=int(s))
    return date.fromisoformat(s)


def parse_week(s: str | None) -> date:
    """'this', 'last', '-2', '2026-W40', or any date in the week -> a date in that week."""
    today = date.today()
    if not s or s == "this":
        return today
    if s == "last":
        return today - timedelta(days=7)
    if re.fullmatch(r"-\d+", s):
        return today + timedelta(weeks=int(s))
    m = re.fullmatch(r"(\d{4})-?W(\d{1,2})", s, re.I)
    if m:
        return date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    return parse_day(s)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    g = common.add_argument_group("configuration (override the parameter file)")
    g.add_argument("-c", "--config", help=f"parameter file (default: {default_config_path()})")
    g.add_argument("-i", "--interval", type=float, help="minutes between prompts (default 30)")
    g.add_argument("--hours", help="working windows, e.g. '09:00-12:00,13:00-17:30'")
    g.add_argument("--days", help="working days, e.g. 'Mon-Fri' or 'Mon,Tue,Thu'")
    g.add_argument("--mode", choices=["auto", "gui", "osascript", "terminal", "notify"], help="how to prompt")
    g.add_argument("--align", dest="align", action="store_true", help="prompt on clock multiples (:00, :30)")
    g.add_argument("--no-align", dest="align", action="store_false", help="prompt every N min from window start")
    g.add_argument("--data-dir", help="where entries and archives are kept")
    g.add_argument(
        "-o", "--set", dest="set", action="append", metavar="SECTION.KEY=VALUE",
        help="override any parameter, e.g. -o summary.show_time=false (repeatable)",
    )
    g.add_argument("-v", "--verbose", action="store_true")

    p = argparse.ArgumentParser(
        prog="whatidid",
        description="Periodically ask what you did; produce daily and weekly summaries.",
        parents=[common],
    )
    p.add_argument("--version", action="version", version=f"whatidid {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    r = sub.add_parser("run", parents=[common], help="run the prompter during working hours")
    r.add_argument("--once", action="store_true", help="prompt once now and exit")
    r.add_argument("--serve", action="store_true", help="also run the web UI in the background")
    r.add_argument("--host"), r.add_argument("--port", type=int)

    lg = sub.add_parser("log", parents=[common], help="log an entry now (text, or stdin if omitted / '-')")
    lg.add_argument("text", nargs="*")
    lg.add_argument("-m", "--minutes", type=float, help="period length (default: since last entry / interval)")
    lg.add_argument("--at", help="end time HH:MM today (default now)")

    sumopts = argparse.ArgumentParser(add_help=False)
    sumopts.add_argument("-f", "--format", choices=["markdown", "md", "text", "json"])
    sumopts.add_argument("--timeline", action="store_true", help="daily: include the raw timeline")
    sumopts.add_argument("--llm", action="store_true", help="polish with Claude (needs ANTHROPIC_API_KEY)")
    sumopts.add_argument("--no-llm", action="store_true")
    sumopts.add_argument("-O", "--output", help="write to file instead of stdout")

    s = sub.add_parser("summary", parents=[common, sumopts], help="daily or weekly summary")
    when = s.add_mutually_exclusive_group()
    when.add_argument("-d", "--day", nargs="?", const="today", help="today|yesterday|-N|YYYY-MM-DD (default)")
    when.add_argument("-w", "--week", nargs="?", const="this", help="this|last|-N|YYYY-Www|date")
    sub.add_parser("today", parents=[common, sumopts], help="shortcut for 'summary --day today'")
    sub.add_parser("week", parents=[common, sumopts], help="shortcut for 'summary --week this'")

    sh = sub.add_parser("show", parents=[common], help="list raw entries")
    sh.add_argument("--from", dest="first", default="today")
    sh.add_argument("--to", dest="last")

    sv = sub.add_parser("serve", parents=[common], help="web UI for phone/browser entry")
    sv.add_argument("--host", help="bind address (0.0.0.0 for LAN access; a token is then required)")
    sv.add_argument("--port", type=int)
    sv.add_argument("--prompter", action="store_true", help="also run the prompter loop")

    a = sub.add_parser("archive", parents=[common], help="compress older raw files now")
    a.add_argument("--all-hosts", action="store_true", help="also archive other machines' files")
    a.add_argument("--include-today", action="store_true")

    cf = sub.add_parser("config", parents=[common], help="show / create the parameter file")
    cf.add_argument("--init", action="store_true", help="write a commented parameter file")
    cf.add_argument("--force", action="store_true")
    cf.add_argument("--path", action="store_true", help="print the default parameter-file path")

    n = sub.add_parser("next", parents=[common], help="show upcoming prompt times")
    n.add_argument("-n", type=int, default=10)
    return p


def overrides_from(args: argparse.Namespace) -> dict:
    o: dict = {}
    if hasattr(args, "interval"):
        o["schedule.interval_minutes"] = args.interval
    if hasattr(args, "hours"):
        o["schedule.work_hours"] = args.hours
    if hasattr(args, "days"):
        o["schedule.work_days"] = args.days
    if hasattr(args, "mode"):
        o["prompt.mode"] = args.mode
    if hasattr(args, "align"):
        o["schedule.align_to_clock"] = args.align
    if hasattr(args, "data_dir"):
        o["storage.data_dir"] = args.data_dir
    for item in getattr(args, "set", None) or []:
        if "=" not in item:
            raise ConfigError(f"-o expects SECTION.KEY=VALUE, got {item!r}")
        k, v = item.split("=", 1)
        o[k.strip()] = parse_scalar(v.strip())
    return o


# ------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.cmd or "run"

    if cmd == "config" and getattr(args, "path", False):
        print(default_config_path())
        return 0
    if cmd == "config" and getattr(args, "init", False):
        dest = Path(getattr(args, "config", None) or default_config_path()).expanduser()
        if dest.exists() and not args.force:
            print(f"{dest} exists (use --force to overwrite)", file=sys.stderr)
            return 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(EXAMPLE_CONFIG.read_text())
        print(f"wrote {dest}")
        return 0

    try:
        cfg = load_config(getattr(args, "config", None), overrides_from(args))
    except ConfigError as e:
        print(f"whatidid: config error: {e}", file=sys.stderr)
        return 2
    cfg["_verbose"] = getattr(args, "verbose", False)
    store = Store.from_config(cfg)

    if cmd == "config":
        sys.stdout.write(dump_effective(cfg))
        return 0

    if cmd == "run":
        from . import runner

        lock = threading.Lock()
        if args.serve:
            from . import web

            web.serve(cfg, store, getattr(args, "host", None), getattr(args, "port", None), lock, background=True)
        try:
            runner.run(cfg, store, once=args.once, lock=lock)
        except KeyboardInterrupt:
            print()
        return 0

    if cmd == "serve":
        from . import runner, web

        lock = threading.Lock()
        if args.prompter:
            threading.Thread(target=runner.run, args=(dict(cfg, prompt=dict(cfg["prompt"], mode="notify")), store), kwargs={"lock": lock}, daemon=True).start()
        web.serve(cfg, store, getattr(args, "host", None), getattr(args, "port", None), lock)
        return 0

    if cmd == "log":
        text = " ".join(args.text)
        if not text or text == "-":
            text = sys.stdin.read()
        items = parse_items(text)
        if not items:
            print("nothing to log", file=sys.stderr)
            return 1
        end = schedule.now_local().replace(microsecond=0)
        if args.at:
            hh, mm = map(int, args.at.split(":"))
            end = end.replace(hour=hh, minute=mm, second=0)
        last = store.last_entry(end.date())
        prior = last.end if last and last.end <= end else None
        start = end - timedelta(minutes=args.minutes) if args.minutes else schedule.period_start(end, prior, cfg)
        e = store.append(Entry(start=start, end=end, items=items, raw=text, source="cli"))
        print(f"logged {len(items)} item(s) for {e.start:%H:%M}–{e.end:%H:%M}")
        return 0

    if cmd in ("summary", "today", "week"):
        fmt = getattr(args, "format", None)
        fmt = "markdown" if fmt == "md" else fmt
        is_week = cmd == "week" or getattr(args, "week", None) is not None
        if is_week:
            d = parse_week(getattr(args, "week", None) or "this")
            a, b = summarize.week_bounds(d)
            entries = store.entries(a, b)
            out = summarize.weekly(entries, d, cfg, fmt)
            label = f"week of {a:%Y-%m-%d}"
        else:
            d = parse_day(getattr(args, "day", None))
            entries = store.entries(d)
            out = summarize.daily(entries, d, cfg, fmt, getattr(args, "timeline", None) or None)
            label = f"{d:%A %Y-%m-%d}"
        use_llm = (getattr(args, "llm", False) or cfg["summary"]["llm"]) and not getattr(args, "no_llm", False)
        if use_llm and (fmt or cfg["summary"]["format"]) != "json" and entries:
            from .llm import LLMError, polish

            try:
                out = polish(out, label, cfg, [i for e in entries for i in e.items])
            except LLMError as e:
                print(f"whatidid: LLM polish skipped ({e}); showing rule-based summary", file=sys.stderr)
        if getattr(args, "output", None):
            Path(args.output).expanduser().write_text(out)
            print(f"wrote {args.output}")
        else:
            sys.stdout.write(out)
        return 0

    if cmd == "show":
        a = parse_day(args.first)
        b = parse_day(args.last) if args.last else a
        for e in store.entries(a, b):
            print(f"{e.end:%Y-%m-%d} {e.start:%H:%M}–{e.end:%H:%M} [{e.host}/{e.source}]  " + "; ".join(e.items))
        return 0

    if cmd == "archive":
        before = date.today() + timedelta(days=1 if args.include_today else 0)
        written = store.archive(before=before, all_hosts=args.all_hosts)
        for p in written:
            print(p)
        if not written:
            print("nothing to archive")
        return 0

    if cmd == "next":
        t = schedule.now_local()
        for _ in range(args.n):
            t = schedule.next_prompt_time(t, cfg)
            if t is None:
                break
            print(f"{t:%a %Y-%m-%d %H:%M}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
