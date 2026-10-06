# WhatIDid

Every *N* minutes during working hours (default 30), WhatIDid asks **"What did you do 10:00–10:30?"**.
Type an item or a few — fragments are fine. At the end of the day or week it produces a grouped
summary you can paste into your status report. Raw entries are kept as JSON Lines and rolled into
compressed weekly archives.

Pure Python standard library (3.9+; `tomli` is pulled in automatically on 3.9/3.10 to read the parameter file).

## Install

```bash
cd WhatIDid
python3 -m pip install -e .          # provides the `whatidid` command
whatidid config --init               # writes ~/.config/whatidid/config.toml (commented)
```

Or without installing: `PYTHONPATH=src python3 -m whatidid ...`

## Everyday use

```bash
whatidid run                     # prompt during working hours (Tk dialog / macOS dialog / terminal)
whatidid run --serve             # ...and serve the web UI for your phone
whatidid log "fixed L1 header bug #neos; telecon w/ JPL"     # log right now from a shell
whatidid today                   # today's summary (markdown)
whatidid week                    # this week's status report
whatidid summary --week last --llm -O status.md   # last week, polished by Claude, to a file
whatidid show --from -7          # raw entries for the past week
whatidid next                    # upcoming prompt times under the current settings
```

### Writing entries

* One item per line, or separate items with `;`. Leading `-`, `*`, `1.` bullets are stripped.
* `#tag` an item to group it in summaries (`#neos`, `#meetings`, `#admin`). An item that is *only*
  tags — e.g. a line containing just `#neos` — applies to every other item in that check-in.
* Untagged items can be auto-tagged by keyword rules and tags can be aliased (see `[tags]`).
* In the dialog: **Save** (⌘/Ctrl+Enter), **Same as last**, **Snooze**, **Skip** (Esc).
  If you skip or a prompt times out, the next check-in automatically covers the gap
  (but never reaches back across a break between working windows).
* **Lunch/breaks:** the default is one continuous working window, so just log `lunch` (or tag it
  `#lunch`) when it happens. Anything in `summary.exclude_tags` (default `["lunch"]`) is left out of
  the summary lists and totals and shown on a single "Not counted" line. If your lunch is fixed,
  use two windows instead, e.g. `--hours 09:00-12:00,13:00-17:30`.

## Controlling behaviour

Precedence: **built-in defaults < parameter file < command-line flags**.

| Setting | Parameter file | CLI |
|---|---|---|
| Minutes between prompts | `schedule.interval_minutes = 30` | `-i 20` |
| Working windows | `schedule.work_hours = ["09:00-17:30"]` | `--hours 08:30-12:00,13:00-17:00` |
| Working days | `schedule.work_days = "Mon-Fri"` | `--days Mon-Thu` |
| Clock-aligned prompts (:00/:30) | `schedule.align_to_clock = true` | `--align` / `--no-align` |
| Prompt style | `prompt.mode = "auto"` | `--mode gui\|osascript\|terminal\|notify` |
| Data location | `storage.data_dir` | `--data-dir PATH` |
| Anything else | | `-o section.key=value` (repeatable), e.g. `-o summary.show_time=false` |

Use another parameter file with `-c FILE` or `$WHATIDID_CONFIG`. `whatidid config` prints the
effective settings after all overrides. See `src/whatidid/example_config.toml` for every option.

## Phone / other devices

`whatidid serve --host 0.0.0.0` (or `run --serve`) starts a small mobile-friendly page: an entry box,
today's log, and one-tap daily/weekly summaries with a *Copy Markdown* button. When bound beyond
localhost it requires a token; the printed URL includes it (`?t=...`) and sets a cookie, so add that
URL to your phone's home screen once. Reach it over the LAN or VPN.

For a phone **nudge** at prompt time, set `notify.ntfy_url` to a private [ntfy](https://ntfy.sh) topic and
`notify.click_url` to the web-UI URL; the prompter posts to it on every check-in and the phone app shows
a push notification that opens the page. `prompt.mode = "notify"` sends only notifications (no dialog)
— handy for a headless machine. `notify.command` runs any shell hook instead.

Entries already logged from the phone suppress the corresponding desktop prompt.

**Multiple computers:** files are partitioned by host (`raw/2026-10-05.<host>.jsonl`), so pointing
`storage.data_dir` at a synced folder (Box/Dropbox/iCloud) on each machine merges everything without
write conflicts; each host archives only its own files.

## Storage

```
<data_dir>/raw/2026-10-05.havnor.jsonl            # today's entries (append-only, one JSON per line)
<data_dir>/archive/2026/2026-W41.havnor.jsonl.xz  # previous days, compressed weekly (xz | gz | bz2)
```

Each entry: `{"id","start","end","items":[...],"raw","source","host"}`. Archiving happens
automatically when the prompter starts and at each day rollover (`storage.archive_after_days`), or
manually with `whatidid archive`. It is atomic and idempotent (merge by id, temp file + rename,
raw file removed last). Summaries read raw and archived data transparently.

## Summaries

Rule-based and offline by default: items are grouped by tag, near-duplicates merged
(`summary.fuzzy_threshold`), and each check-in's time is split evenly across its items to estimate
time per item and tag. Weekly reports add the days each item appeared and a *Day by day* section.
Formats: `markdown` (default), `text`, `json`.

`--llm` (or `summary.llm = true`) sends the rule-based summary plus raw items to the Anthropic API
and returns a polished status update in the style given by `summary.llm_style`.
Needs `ANTHROPIC_API_KEY`; on any error it falls back to the rule-based output.

## Start automatically

* **macOS:** edit and copy `contrib/edu.caltech.ipac.whatidid.plist` to `~/Library/LaunchAgents/`, then
  `launchctl load ~/Library/LaunchAgents/edu.caltech.ipac.whatidid.plist`.
* **Linux:** `contrib/whatidid.service` → `~/.config/systemd/user/`, then
  `systemctl --user enable --now whatidid`.

## Development

```bash
python3 -m pip install -e '.[test]'
python3 -m pytest
```

See `DESIGN.md` for the architecture.
