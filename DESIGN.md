# WhatIDid — design

## Goals (from the project brief)

1. Prompt during working hours, every N minutes (default 30), for what was done in the last N minutes.
2. Run on desktop, laptop and phone.
3. Input is free text: one item or a list, sentences optional.
4. Daily and weekly summaries to support a weekly team status report.
5. Archive the raw input in a compressed format.
6. Behaviour controllable from the CLI and a parameter file.

## Decisions

| Concern | Choice | Why |
|---|---|---|
| Language / deps | Python ≥3.9, stdlib only (+`tomli` on <3.11) | Runs anywhere Python does; nothing to maintain |
| Parameter file | TOML, `~/.config/whatidid/config.toml` | Readable, comments, stdlib `tomllib` |
| Precedence | defaults < file < CLI flags < `-o key=val` | Predictable; any key overridable ad hoc |
| Desktop prompt | Tk dialog → macOS `osascript` dialog → terminal → notify-only (`auto` picks the first that works) | Tk is in the stdlib; osascript covers Macs whose Python lacks Tk |
| Phone | Built-in web UI (`http.server`) + optional ntfy push | No app store / native build; works on any phone browser |
| Raw storage | JSON Lines, one file per day **per host** | Append-only, human-readable, crash-tolerant; safe in a synced folder |
| Archive | Weekly `*.jsonl.xz` (xz/gz/bz2 selectable) | Strong compression, stdlib, still greppable via `xzgrep` |
| Summaries | Rule-based (tags, fuzzy de-dup, time split) + optional Claude polish | Works offline; LLM only when asked |

## Modules (`src/whatidid/`)

```
config.py     defaults, TOML loading, CLI overrides, validation (hours/days parsing)
schedule.py   working windows, prompt times (aligned or not), period covered by a check-in
store.py      Entry model, item parsing, append/read, compressed weekly archiving
prompt.py     Tk / osascript / terminal prompts; desktop, ntfy and shell-hook notifications
runner.py     long-running loop: sleep → prompt → save; snooze; archive on day rollover
summarize.py  activities (tag, fuzzy merge, time), daily & weekly markdown/text/json
llm.py        optional Anthropic Messages API polish (urllib, no SDK)
web.py        mobile web UI + JSON API (/api/status, /api/entry, /api/entries, /summary)
cli.py        argparse front end: run, log, today, week, summary, show, serve, archive, config, next
```

## Scheduling rules

* Prompt times for a day are generated per working window. Aligned mode uses multiples of N from
  midnight that fall inside the window; unaligned mode uses window-start + k·N. With
  `prompt_at_window_end` a final prompt is added at each window close.
* A check-in at time *t* covers `[t − N, t]`, extended back to the previous check-in if prompts
  were missed, but clamped to the start of the current window (no counting across a break between windows), and
  never overlapping the previous entry.
* Default is a single 09:00–17:30 window; lunch is logged like any other item and dropped from
  summaries via `summary.exclude_tags` (default `["lunch"]`), because midday meetings with
  partners in other time zones make a fixed lunch gap unreliable.
* If the machine slept through a prompt and wakes outside working hours, that prompt is skipped.
* If an entry covering a slot already exists (e.g. logged from the phone), the desktop prompt is
  suppressed.

## Data model

```json
{"id": "a1b2c3d4e5f6", "start": "2026-10-05T09:30:00-07:00", "end": "2026-10-05T10:00:00-07:00",
 "items": ["Reviewed L1 FITS header changes #neos", "answered email"],
 "raw": "- Reviewed L1 FITS header changes #neos\n- answered email",
 "source": "gui", "host": "havnor"}
```

Tags are **not** stored; they are derived at summary time so changing aliases or keyword rules
re-groups historical data.

## Web API

| Method | Path | Notes |
|---|---|---|
| GET | `/` | entry form + today's log |
| GET | `/api/status` | `{due, next_prompt, period_start, working, ...}` (page polls every 60 s) |
| POST | `/entry` (form) or `/api/entry` (JSON `{text, minutes?}`) | add an entry ending now |
| GET | `/api/entries?date=YYYY-MM-DD` | raw entries |
| GET | `/summary?period=day\|week&date=...&format=markdown\|text\|json` | summary page / JSON |

Auth: none on loopback; otherwise a token (`web.token` or auto-generated `data_dir/web_token`)
via `?t=` once, then an HttpOnly cookie. Plain HTTP — use on a trusted LAN/VPN.

## Possible next steps

* Menu-bar/tray icon (e.g. `rumps` on macOS, `pystray`) with "log now" and pause.
* Idle detection to skip prompts while away from the keyboard.
* Export of weekly report straight to email/Slack/Confluence.
* HTTPS for the web UI (self-signed or via Tailscale serve).
