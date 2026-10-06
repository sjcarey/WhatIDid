import json
import lzma
from datetime import date, datetime, timedelta, timezone

import pytest

from whatidid import cli, schedule, summarize
from whatidid.config import ConfigError, load_config, parse_days, parse_hours
from whatidid.store import Entry, parse_items

TZ = timezone(timedelta(hours=-7))


def dt(d, hm):
    h, m = map(int, hm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


MON = date(2026, 10, 5)


# ------------------------------------------------------------------ config
def test_parse_days_and_hours():
    assert parse_days("Mon-Fri") == [0, 1, 2, 3, 4]
    assert parse_days(["Sat", "sun"]) == [5, 6]
    assert parse_days("Fri-Mon") == [0, 4, 5, 6]
    assert parse_hours("09:00-12:00, 13:00-17:30") == [(540, 720), (780, 1050)]
    with pytest.raises(ConfigError):
        parse_hours(["17:00-09:00"])


def test_file_then_cli_precedence(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[schedule]\ninterval_minutes = 20\nwork_days = "Mon-Thu"\n')
    cfg = load_config(f, {"schedule.interval_minutes": 15})
    assert cfg["schedule"]["interval_minutes"] == 15
    assert cfg["schedule"]["_days"] == [0, 1, 2, 3]
    with pytest.raises(ConfigError):
        load_config(f, {"schedule.interval_minutes": 0})


# ---------------------------------------------------------------- schedule
def test_default_is_one_continuous_window(cfg):
    times = [t.strftime("%H:%M") for t in schedule.prompt_times_for_day(MON, cfg, TZ)]
    assert times[0] == "09:30" and "12:30" in times and "13:00" in times and times[-1] == "17:30"
    assert schedule.next_prompt_time(dt(MON, "12:36"), cfg) == dt(MON, "13:00")


def test_prompt_times_aligned(cfg):
    cfg["schedule"]["_windows"] = [(540, 720), (780, 1050)]  # 09:00-12:00, 13:00-17:30
    times = [t.strftime("%H:%M") for t in schedule.prompt_times_for_day(MON, cfg, TZ)]
    assert times[0] == "09:30" and "12:00" in times and "12:30" not in times
    assert "13:30" in times and times[-1] == "17:30"
    assert schedule.prompt_times_for_day(date(2026, 10, 4), cfg, TZ) == []  # Sunday


def test_prompt_times_unaligned(cfg):
    cfg["schedule"]["align_to_clock"] = False
    cfg["schedule"]["interval_minutes"] = 45
    cfg["schedule"]["_windows"] = [(9 * 60 + 10, 11 * 60)]
    times = [t.strftime("%H:%M") for t in schedule.prompt_times_for_day(MON, cfg, TZ)]
    assert times == ["09:55", "10:40", "11:00"]


def test_next_prompt_rolls_over_weekend(cfg):
    fri_evening = dt(date(2026, 10, 9), "18:00")
    assert schedule.next_prompt_time(fri_evening, cfg) == dt(date(2026, 10, 12), "09:30")


def test_period_start(cfg):
    cfg["schedule"]["_windows"] = [(540, 720), (780, 1050)]
    end = dt(MON, "11:00")
    assert schedule.period_start(end, None, cfg) == dt(MON, "10:30")
    assert schedule.period_start(end, dt(MON, "09:30"), cfg) == dt(MON, "09:30")  # missed prompts
    # after lunch: don't reach back over the break
    assert schedule.period_start(dt(MON, "13:30"), dt(MON, "12:00"), cfg) == dt(MON, "13:00")


# ------------------------------------------------------------------ store
def test_parse_items():
    text = "- fixed FITS header bug #neos\n* telecon with JPL; reviewed PR 42\n\n1. emails"
    assert parse_items(text) == ["fixed FITS header bug #neos", "telecon with JPL", "reviewed PR 42", "emails"]


def test_append_read_archive_roundtrip(store):
    e1 = store.append(Entry(dt(MON, "09:00"), dt(MON, "09:30"), ["a #x"]))
    tue = MON + timedelta(days=1)
    store.append(Entry(dt(tue, "09:00"), dt(tue, "09:30"), ["b"]))
    assert [e.id for e in store.entries(MON)] == [e1.id]
    written = store.archive(before=tue)
    assert len(written) == 1 and written[0].name == "2026-W41.testhost.jsonl.xz"
    assert not store.raw_path(MON).exists() and store.raw_path(tue).exists()
    with lzma.open(written[0], "rt") as fh:
        assert json.loads(fh.readline())["id"] == e1.id
    # reading transparently merges archive + raw, and re-archiving is idempotent
    assert len(store.entries(MON, tue)) == 2
    store.archive(before=tue + timedelta(days=1))
    assert len(store.entries(MON, tue)) == 2
    assert not list(store.raw_dir.glob("*.jsonl"))


def test_other_host_files_merged_not_archived(store):
    other = store.raw_path(MON, host="laptop")
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text(Entry(dt(MON, "10:00"), dt(MON, "10:30"), ["from laptop"]).to_json() + "\n")
    store.append(Entry(dt(MON, "09:00"), dt(MON, "09:30"), ["from desktop"]))
    assert len(store.entries(MON)) == 2
    store.archive(before=MON + timedelta(days=1))
    assert other.exists()


# ---------------------------------------------------------------- summary
def _fill(store):
    store.append(Entry(dt(MON, "09:00"), dt(MON, "09:30"), ["Reviewed FITS pipeline #neos", "email"]))
    store.append(Entry(dt(MON, "09:30"), dt(MON, "10:00"), ["reviewed FITS pipeline. #NEOS"]))
    store.append(Entry(dt(MON, "10:00"), dt(MON, "11:00"), ["Telecon with detector team"]))
    wed = MON + timedelta(days=2)
    store.append(Entry(dt(wed, "13:00"), dt(wed, "13:30"), ["Reviewed FITS pipeline #neos"]))


def test_tags_and_dedupe(cfg, store):
    _fill(store)
    acts = summarize.build_activities(store.entries(MON), cfg)
    by = {(a.tag, a.text): a for a in acts}
    fits = by[("neo-surveyor", "Reviewed FITS pipeline")]
    assert fits.count == 2 and fits.minutes == pytest.approx(45)
    assert ("meetings", "Telecon with detector team") in by
    assert ("Other", "email") in by


def test_daily_and_weekly_text(cfg, store):
    _fill(store)
    d = summarize.daily(store.entries(MON), MON, cfg)
    assert "## neo-surveyor (45m)" in d and "×2" in d and d.index("neo-surveyor") < d.index("## Other")
    a, b = summarize.week_bounds(MON)
    w = summarize.weekly(store.entries(a, b), MON, cfg)
    assert "2026-W41" in w and "Mon/Wed" in w and "## Day by day" in w
    j = json.loads(summarize.weekly(store.entries(a, b), MON, cfg, "json"))
    assert j["total_minutes"] == 150
    t = summarize.daily(store.entries(MON), MON, cfg, "text")
    assert "#" not in t.splitlines()[0]


def test_fmt_minutes():
    assert summarize.fmt_minutes(44) == "45m"
    assert summarize.fmt_minutes(125) == "2h 05m"
    assert summarize.fmt_minutes(120) == "2h"


# --------------------------------------------------------------------- cli
def test_cli_log_and_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WHATIDID_CONFIG", str(tmp_path / "none.toml"))
    base = ["--data-dir", str(tmp_path / "d"), "-o", 'storage.host="h"']
    assert cli.main(["log", *base, "-m", "30", "wrote docs #whatidid; lunch prep"]) == 0
    assert "logged 2 item(s)" in capsys.readouterr().out
    assert cli.main(["today", *base, "-o", "summary.show_time=false"]) == 0
    out = capsys.readouterr().out
    assert "## whatidid" in out and "- wrote docs" in out and "(30m)" not in out
    assert cli.main(["next", "-i", "15", "--hours", "08:00-09:00", "--days", "Mon-Sun", "-n", "3"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 3


def test_cli_week_parse():
    assert cli.parse_week("2026-W41") == date(2026, 10, 5)
    assert cli.parse_day("2026-10-05") == MON


def test_tag_only_item_applies_to_entry(cfg, store):
    store.append(Entry(dt(MON, "09:00"), dt(MON, "10:00"), ["#neos", "standup", "wrote plan #other"]))
    acts = {a.text: a.tag for a in summarize.build_activities(store.entries(MON), cfg)}
    assert acts == {"standup": "neo-surveyor", "wrote plan": "other"}


@pytest.mark.parametrize("lunch", ["lunch", "Lunch.", "#lunch", "sandwich #lunch"])
def test_lunch_excluded(cfg, store, lunch):
    store.append(Entry(dt(MON, "11:30"), dt(MON, "12:00"), ["telecon with partners"]))
    store.append(Entry(dt(MON, "12:00"), dt(MON, "12:30"), [lunch]))
    d = summarize.daily(store.entries(MON), MON, cfg)
    assert d.count("unch") == 1 and "_Not counted:" in d and "30m logged across 2 check-ins" in d
    a, b = summarize.week_bounds(MON)
    w = summarize.weekly(store.entries(a, b), MON, cfg)
    assert "30m logged over 1 day_" in w and "(30m): telecon with partners" in w


def test_gui_dialog_runs_in_child_process(cfg, monkeypatch):
    import subprocess as sp
    from whatidid import prompt

    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["args"] = json.loads(cmd[-1])
        return sp.CompletedProcess(cmd, 0, stdout='{"action": "save", "text": "a; b"}\n', stderr="")

    monkeypatch.setattr(prompt.subprocess, "run", fake_run)
    now = datetime.now(TZ)
    res = prompt.ask_gui(cfg, now - timedelta(minutes=30), now, ["prev"], 600)
    assert (res.action, res.text) == ("save", "a; b")
    assert seen["cmd"][1:3] == ["-m", "whatidid.dialog"] and seen["args"]["last_items"] == ["prev"]
