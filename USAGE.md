# WhatIDid — usage guide

This is the detailed reference for running WhatIDid: every command, every
configuration key, the phone setup, and common ways to run it. For the design
rationale see [`DESIGN.md`](DESIGN.md).

- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Commands](#commands)
- [Global options](#global-options)
- [Configuration file](#configuration-file)
- [Writing entries](#writing-entries)
- [Using WhatIDid from a phone](#using-whatidid-from-a-phone)
- [Common setups](#common-setups)
- [Running in the background](#running-in-the-background)
- [Files and environment variables](#files-and-environment-variables)
- [Troubleshooting](#troubleshooting)

---

## Quick start

```bash
whatidid config --init        # write a commented parameter file
whatidid next                 # check upcoming prompt times
whatidid run                  # start prompting during working hours
```

At any time:

```bash
whatidid log "fixed FITS header bug #neos"   # log something right now
whatidid today                               # today's summary
whatidid week                                # this week's summary
```

---

## How it works

- During working hours, WhatIDid asks every *N* minutes (default 30) what you
  did since the last check-in.
- Each answer becomes one **entry** covering a time period. A check-in at time
  *t* covers *t − N* to *t*, extended back to the previous check-in if prompts
  were missed, but never earlier than the start of the current working window
  and never overlapping the previous entry.
- Entries are stored as plain JSON Lines, one file per day per machine, and are
  compressed into weekly archives after a day or so.
- Summaries group your items by **tag**, merge near-duplicate items, and total
  the time spent. Tags are worked out at summary time, so changing tag rules
  later re-groups old data too.

---

## Commands

Run `whatidid COMMAND --help` for the built-in help on any command.

### `run` — the prompter

```
whatidid run [--once] [--serve] [--host HOST] [--port PORT]
```

Long-running loop: waits for the next prompt time, asks what you did, saves the
answer, repeats. At each prompt it also sends any configured notifications
(ntfy push to your phone, desktop notification, shell hook). Outside working
hours it sleeps.

| Option | Meaning |
|---|---|
| `--once` | Prompt once right now and exit. Handy for testing your prompt and notification setup. |
| `--serve` | Also run the web UI in the same process, so one command gives you desktop prompts, phone pushes and the phone entry page. |
| `--host HOST`, `--port PORT` | Web UI bind address and port when `--serve` is used. |

Behaviour worth knowing:

- If the computer was asleep through a prompt and wakes outside working hours,
  that prompt is skipped.
- If an entry already covers the slot (for example you logged from the phone),
  the prompt is suppressed.
- If you don't answer within `prompt.timeout_minutes`, the dialog closes.
  The snooze option re-asks after `schedule.snooze_minutes`.
- When the day rolls over, older raw files for this machine are archived
  automatically.

### `log` — log an entry now

```
whatidid log [-m MINUTES] [--at HH:MM] [text ...]
```

Adds an entry ending now. Text can be given as arguments, or piped on stdin if
omitted or given as `-`.

| Option | Meaning |
|---|---|
| `-m`, `--minutes MINUTES` | Length of the period. Default: since the last entry, or one interval. |
| `--at HH:MM` | End time today, instead of now. Useful for filling in a missed slot. |

```bash
whatidid log "standup; reviewed PR #42 #neos"
whatidid log -m 90 "detector characterization run #neos"
whatidid log --at 11:00 "telecon with JPL"
printf -- "- item one\n- item two #neos\n" | whatidid log -
```

### `summary`, `today`, `week` — reports

```
whatidid summary [-d [DAY] | -w [WEEK]] [-f FORMAT] [--timeline] [--llm | --no-llm] [-O FILE]
whatidid today   [same options except -d/-w]
whatidid week    [same options except -d/-w]
```

`today` is a shortcut for `summary --day today`; `week` is a shortcut for
`summary --week this`. With no `-d` or `-w`, `summary` gives today.

| Option | Meaning |
|---|---|
| `-d`, `--day [DAY]` | `today`, `yesterday`, `-N` (N days ago) or `YYYY-MM-DD`. |
| `-w`, `--week [WEEK]` | `this`, `last`, `-N` (N weeks ago), an ISO week `YYYY-Www` (e.g. `2026-W41`), or any date in the week. |
| `-f`, `--format` | `markdown` (or `md`), `text` or `json`. Default from `summary.format`. |
| `--timeline` | Daily summaries: also list the raw entries in time order. |
| `--llm` / `--no-llm` | Turn the Claude rewrite on or off for this run (overrides `summary.llm`). Needs `ANTHROPIC_API_KEY`. |
| `-O`, `--output FILE` | Write to a file instead of the terminal. |

```bash
whatidid summary -d yesterday --timeline
whatidid summary -w last -O status-$(date +%F).md
whatidid week --llm                     # polished weekly status for the team
whatidid summary -w 2026-W40 -f json
```

### `show` — raw entries

```
whatidid show [--from FIRST] [--to LAST]
```

Lists entries exactly as stored, without grouping or merging. Use it to check
what was recorded. Give dates as `YYYY-MM-DD`.

### `serve` — web UI for phone or browser

```
whatidid serve [--host HOST] [--port PORT] [--prompter]
```

Serves an entry page and today's log, plus a small JSON API (see
[Web API](#web-api)).

| Option | Meaning |
|---|---|
| `--host HOST` | Bind address. Default `127.0.0.1` (this computer only, no token needed). Use `0.0.0.0`, a host name, or a Tailscale address to reach it from a phone; a token is then required. |
| `--port PORT` | Default `8765`. |
| `--prompter` | Also run the prompter loop in the same process (same as `run --serve`). |

On its own, `serve` does **not** send reminders; only the prompter does. Use
`run --serve` or `serve --prompter` if you want both from one process.

### `archive` — compress old raw files now

```
whatidid archive [--all-hosts] [--include-today]
```

Normally automatic. By default only this machine's files are archived.

| Option | Meaning |
|---|---|
| `--all-hosts` | Also archive other machines' files in a shared data folder. Run this from one machine only. |
| `--include-today` | Also archive today's file. |

### `config` — show or create the parameter file

```
whatidid config            # print the effective configuration and where it came from
whatidid config --path     # print the default parameter-file path
whatidid config --init     # write a commented parameter file
whatidid config --init --force   # overwrite an existing one
```

The printed configuration includes every default and every override, so it is
the quickest way to check that an edit took effect or to spot a misspelled key.

### `next` — upcoming prompt times

```
whatidid next [-n N]
```

Shows the next `N` prompt times given the current schedule.

---

## Global options

Every command accepts these. They override the parameter file for that run only.

| Option | Config key | Meaning |
|---|---|---|
| `-c`, `--config FILE` | | Use this parameter file (must exist). |
| `-i`, `--interval MIN` | `schedule.interval_minutes` | Minutes between prompts. |
| `--hours SPEC` | `schedule.work_hours` | Working windows, e.g. `'09:00-12:00,13:00-17:30'`. |
| `--days SPEC` | `schedule.work_days` | Working days, e.g. `'Mon-Fri'` or `'Mon,Tue,Thu'`. |
| `--mode MODE` | `prompt.mode` | `auto`, `gui`, `osascript`, `terminal` or `notify`. |
| `--align` / `--no-align` | `schedule.align_to_clock` | Prompt on clock multiples (:00, :30) or every N minutes from window start. |
| `--data-dir DIR` | `storage.data_dir` | Where entries and archives are kept. |
| `-o`, `--set SECTION.KEY=VALUE` | any | Override any key. Repeatable. |
| `-v`, `--verbose` | | More logging. |

**Precedence:** built-in defaults < parameter file < named flags < `-o` overrides.

`-o` values are read as TOML values, so lists and strings with special
characters need TOML syntax and shell quoting:

```bash
whatidid today -o summary.show_time=false
whatidid run -o 'schedule.work_hours=["08:00-12:00","13:00-16:30"]'
whatidid week -o 'summary.exclude_tags=["lunch","admin"]'
```

---

## Configuration file

Default location: `~/.config/whatidid/config.toml`, or `$XDG_CONFIG_HOME/whatidid/config.toml`,
or whatever `$WHATIDID_CONFIG` points to. Create a commented one with
`whatidid config --init`. You only need to include the keys you want to change.

**Changes are read at startup.** After editing, restart `run` and/or `serve`.

### `[schedule]`

| Key | Default | Meaning |
|---|---|---|
| `interval_minutes` | `30` | Minutes between prompts (decimals allowed). |
| `work_hours` | `["09:00-17:30"]` | Working windows, list or comma-separated string. Check-ins never count across a gap between windows. |
| `work_days` | `["Mon","Tue","Wed","Thu","Fri"]` | Weekdays; also accepts ranges like `"Mon-Fri"`. |
| `align_to_clock` | `true` | Prompt at multiples of the interval from midnight (09:00, 09:30, …) instead of window start + k·N. |
| `prompt_at_window_end` | `true` | Add a final prompt when each window closes. |
| `snooze_minutes` | `5` | Delay when you snooze a prompt. |
| `holidays` | `[]` | Dates with no prompts, as `"YYYY-MM-DD"` strings. |

### `[prompt]`

| Key | Default | Meaning |
|---|---|---|
| `mode` | `"auto"` | How to ask on this computer: `gui` (Tk dialog), `osascript` (macOS dialog), `terminal`, `notify` (no dialog, notifications only), or `auto` (first of gui → osascript → terminal → notify that works). |
| `timeout_minutes` | `10` | Close an unanswered dialog after this long. |
| `sound` | `true` | Play a sound when prompting. |

### `[storage]`

| Key | Default | Meaning |
|---|---|---|
| `data_dir` | `""` | Empty means `~/.local/share/whatidid` (or `$XDG_DATA_HOME/whatidid`, or `$WHATIDID_DATA`). Can be a synced folder such as Dropbox. |
| `compression` | `"xz"` | Archive format: `xz`, `gz` or `bz2`. |
| `archive_after_days` | `1` | Raw daily files older than this are compressed into weekly archives. |
| `host` | `""` | Name used in this machine's file names. Empty means the short host name. Set it explicitly if a machine's host name changes. |

### `[summary]`

| Key | Default | Meaning |
|---|---|---|
| `format` | `"markdown"` | `markdown`, `text` or `json`. |
| `show_time` | `true` | Show time totals per tag/item. |
| `timeline` | `false` | Include the raw timeline in daily summaries. |
| `fuzzy_threshold` | `0.85` | How similar two items must be (0–1) to merge them. Lower merges more aggressively. |
| `top_per_day` | `5` | How many top items per day to show in weekly summaries. |
| `untagged_label` | `"Other"` | Group name for items with no tag. |
| `exclude_tags` | `["lunch"]` | Tags left out of totals and listings. Log lunch as `#lunch` and it disappears from reports. |
| `llm` | `false` | Rewrite summaries with Claude by default. |
| `llm_model` | `"claude-sonnet-4-5"` | Model used for the rewrite. |
| `llm_max_tokens` | `1500` | Length limit for the rewrite. |
| `llm_style` | (team status instructions) | Instructions given to Claude for the rewrite. Edit to match your team's report style. |

The Claude rewrite sends your summary text to the Anthropic API and needs the
`ANTHROPIC_API_KEY` environment variable. Everything else works offline.

### `[tags]`

| Key | Default | Meaning |
|---|---|---|
| `aliases` | `{}` | Map short tags to canonical names, e.g. `{ neos = "neo-surveyor" }`. |
| `keywords` | `{}` | Tag items automatically by words they contain, e.g. `{ meetings = ["meeting", "telecon", "standup"] }`. |

```toml
[tags.aliases]
neos = "neo-surveyor"

[tags.keywords]
meetings = ["meeting", "telecon", "standup"]
email = ["email", "inbox"]
```

### `[web]`

| Key | Default | Meaning |
|---|---|---|
| `host` | `"127.0.0.1"` | Bind address for the web UI. Set to `"0.0.0.0"` or a Tailscale address for phone access. |
| `port` | `8765` | Port. |
| `token` | `""` | Access token for non-local connections. Empty means one is generated and saved in `data_dir/web_token`. Don't commit a real token to git. |

### `[notify]`

| Key | Default | Meaning |
|---|---|---|
| `desktop` | `true` | macOS Notification Center / Linux `notify-send` banner (used in `notify` mode). |
| `ntfy_url` | `""` | Push to your phone, e.g. `"https://ntfy.sh/<long-random-topic>"`. |
| `click_url` | `""` | Page opened when you tap the push, e.g. `"http://100.x.y.z:8765/"`. Leave the token out. |
| `command` | `""` | Optional shell hook run at each prompt. Receives `WHATIDID_TITLE`, `WHATIDID_MESSAGE`, `WHATIDID_START`, `WHATIDID_END` in its environment. |

---

## Writing entries

Free text, whichever way is quickest:

- A single item: `answered email`
- A list, one item per line (bullets like `-` or `*` are fine):
  ```
  - reviewed L1 FITS header changes #neos
  - answered email
  ```

**Tags** start with `#`, e.g. `#neos`. Items without a tag can still be grouped
by `[tags.keywords]`, otherwise they fall under `summary.untagged_label`.
Use `#lunch` (or any tag in `exclude_tags`) for things that shouldn't appear in
reports.

Entries from the desktop, the phone and `whatidid log` all end up in the same
log.

---

## Using WhatIDid from a phone

The phone does not run WhatIDid. A computer runs the web UI; the phone opens it
in a browser. Optionally, ntfy sends a push notification as a reminder.

### 1. Make the computer reachable

Phone and computer must be able to reach each other. On the same home Wi-Fi
this usually works directly. Elsewhere, use [Tailscale](https://tailscale.com)
on both devices and use the computer's Tailscale address (`100.x.y.z`).

The web UI uses plain HTTP, so keep it on a trusted network or VPN, not the open
internet.

### 2. Start the web UI

```bash
whatidid serve --host 0.0.0.0           # entry page only
whatidid run --serve --host 0.0.0.0     # entry page + prompts + phone pushes
```

Instead of `0.0.0.0` you can bind to one specific address, such as the Tailscale
address. Whatever you choose, use the same address in `click_url`.

The token is shown at startup and stored in `data_dir/web_token`.

### 3. Open it on the phone

1. Open `http://<address>:8765/?t=<token>` in the phone's browser. After the
   first visit a cookie remembers you, so the token isn't needed again.
2. Save it to the home screen:
   - **Android (Chrome):** ⋮ menu → *Add to Home screen*.
   - **iPhone (Safari):** Share → *Add to Home Screen*. Add it while the address
     still includes `?t=...`, because home-screen apps keep their own cookies.
3. Test by logging an item, then run `whatidid today` on the computer.

The token survives restarts. You only need to open the `?t=` link again if the
token changes, for example after moving `data_dir`.

### 4. Push reminders with ntfy (optional)

1. Install **ntfy** on the phone (Google Play, F-Droid or App Store).
2. Tap **+**, keep the server as `ntfy.sh`, and subscribe to a long, random
   topic name. On ntfy.sh, the topic name is the only protection.
3. Allow notifications. On Android also set Settings → Apps → ntfy → Battery →
   **Unrestricted**, or reminders may arrive late.
4. Configure the same topic on the computer:
   ```toml
   [notify]
   ntfy_url  = "https://ntfy.sh/<your-topic>"
   click_url = "http://<address>:8765/"
   ```
5. Test without WhatIDid:
   ```bash
   curl -H "Click: http://<address>:8765/" -d "WhatIDid test" https://ntfy.sh/<your-topic>
   ```
6. Restart `run` (pushes are sent by the prompter, not by `serve` alone).

**Security notes.** Anyone who knows the topic can read or send messages on it,
and ntfy.sh keeps recent messages for some hours. The push only says it's time
to log; your entries go directly from phone to computer. Never put the web
token in `click_url`.

### Web API

| Method | Path | Notes |
|---|---|---|
| GET | `/` | Entry form and today's log. |
| GET | `/api/status` | `{due, next_prompt, period_start, working, ...}`; the page polls this every 60 s. |
| POST | `/entry` (form) or `/api/entry` (JSON `{text, minutes?}`) | Add an entry ending now. |
| GET | `/api/entries?date=YYYY-MM-DD` | Raw entries for a day. |
| GET | `/summary?period=day\|week&date=...&format=markdown\|text\|json` | Summary page or JSON. |

---

## Common setups

### Desktop only

```bash
whatidid run
```

### Desktop prompts plus phone

```bash
whatidid run --serve --host 0.0.0.0
```

With `ntfy_url` set, each prompt shows the desktop dialog and pushes to the
phone. Answer whichever is convenient; once an entry covers the slot, later
prompts for it are suppressed.

### Phone reminders only, nothing popping up on the computer

```toml
[prompt]
mode = "notify"

[notify]
desktop = false   # true gives a brief, non-blocking banner on the computer
```

then `whatidid run --serve --host 0.0.0.0`.

### Several computers sharing one log (Dropbox or similar)

On each computer:

```toml
[storage]
data_dir = "~/Dropbox/whatidid"
```

Each machine writes its own per-day files, so synced writes never collide, and
summaries on any machine include entries from all of them. Each machine archives
only its own files unless you run `archive --all-hosts`; do that from one
machine only. The web token file also lives in this folder and is shared.

Run the prompter on one machine at a time, or accept that each running prompter
may ask, until sync catches up.

### Different schedule for one day

```bash
whatidid run --hours '07:30-12:00,13:00-15:00'
whatidid run -i 60
```

---

## Running in the background

Simplest:

```bash
# bash/zsh
nohup whatidid run --serve --host 0.0.0.0 > ~/whatidid.log 2>&1 &

# tcsh
nohup whatidid run --serve --host 0.0.0.0 >& ~/whatidid.log &
```

Or run it inside `tmux` / `screen`. It pauses while the computer sleeps.
To stop it: `pkill -f "whatidid run"`.

### Start automatically at login (macOS)

Save as `~/Library/LaunchAgents/org.whatidid.run.plist`, replacing the path with
the output of `which whatidid`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>org.whatidid.run</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/whatidid</string>
    <string>run</string>
    <string>--serve</string>
    <string>--host</string>
    <string>0.0.0.0</string>
  </array>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <true/>
  <key>StandardOutPath</key>   <string>/tmp/whatidid.log</string>
  <key>StandardErrorPath</key> <string>/tmp/whatidid.log</string>
</dict>
</plist>
```

```bash
launchctl bootstrap gui/`id -u` ~/Library/LaunchAgents/org.whatidid.run.plist   # start
launchctl bootout   gui/`id -u` ~/Library/LaunchAgents/org.whatidid.run.plist   # stop
```

After editing `config.toml`, restart with `bootout` then `bootstrap`.
If `ANTHROPIC_API_KEY` is needed by `run`, add it under an
`EnvironmentVariables` dictionary; launchd does not read your shell startup files.

---

## Files and environment variables

| What | Where |
|---|---|
| Parameter file | `~/.config/whatidid/config.toml` (see `whatidid config --path`) |
| Raw entries | `data_dir`, one JSON Lines file per day per host |
| Archives | `data_dir`, weekly compressed files (`*.jsonl.xz` by default; search with `xzgrep`) |
| Web token | `data_dir/web_token` |

| Variable | Effect |
|---|---|
| `WHATIDID_CONFIG` | Path of the parameter file. |
| `WHATIDID_DATA` | Default data directory. |
| `XDG_CONFIG_HOME`, `XDG_DATA_HOME` | Standard base directories, used if the above aren't set. |
| `ANTHROPIC_API_KEY` | Needed only for `--llm` / `summary.llm`. |

Each stored entry looks like:

```json
{"id": "a1b2c3d4e5f6", "start": "2026-10-05T09:30:00-07:00", "end": "2026-10-05T10:00:00-07:00",
 "items": ["Reviewed L1 FITS header changes #neos", "answered email"],
 "raw": "- Reviewed L1 FITS header changes #neos\n- answered email",
 "source": "gui", "host": "havnor"}
```

---

## Troubleshooting

**No prompt appeared.** Check `whatidid next` (is it a working day and time?)
and that `run` is still running. Try `whatidid run --once`.

**My config change did nothing.** Restart `run`/`serve`. Then run
`whatidid config`: it shows which file was read and the value actually in use,
which catches misspelled keys and wrong sections.

**No phone push.** Run the `curl` test above. If that fails, check the topic
spelling matches exactly on both sides, notification permission, Android
battery setting, and Do Not Disturb. If `curl` works but WhatIDid doesn't, make
sure `run` was restarted after setting `ntfy_url`, and check its log with `-v`.

**Tapping the push opens a page that won't load.** The phone can't reach the
computer: check Tailscale is connected on the phone, the server is running, and
`click_url` uses the address the server is bound to.

**The page asks for a token again.** The token changed (for example `data_dir`
moved). Open `http://<address>:8765/?t=<new token>` once.

**Reading a TOML file fails on older Python.** Python below 3.11 needs
`pip install tomli`.
