"""Tiny stdlib web UI so a phone (or any browser) can log entries and read summaries."""
from __future__ import annotations

import hmac
import html
import ipaddress
import json
import secrets
import threading
from datetime import date, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import schedule, summarize
from .store import Entry, Store, parse_items


def resolve_token(cfg: dict) -> str:
    """Configured token; or, when listening beyond localhost, a persisted random one."""
    tok = cfg["web"]["token"]
    if tok:
        return tok
    host = cfg["web"]["host"]
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if loopback:
        return ""
    p = Path(cfg["storage"]["data_dir"]) / "web_token"
    if p.exists():
        return p.read_text().strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(16)
    p.write_text(tok)
    p.chmod(0o600)
    return tok


def status(cfg: dict, store: Store) -> dict:
    now = schedule.now_local()
    last = store.last_entry(now.date())
    prev = schedule.prev_prompt_time(now, cfg)
    nxt = schedule.next_prompt_time(now, cfg)
    due = bool(prev and (last is None or last.end < prev - timedelta(minutes=1)))
    start = schedule.period_start(now, last.end if last else None, cfg)
    return {
        "now": now.isoformat(timespec="seconds"),
        "working": schedule.in_working_hours(now, cfg),
        "due": due,
        "period_start": start.strftime("%H:%M"),
        "next_prompt": nxt.isoformat(timespec="minutes") if nxt else None,
        "last_end": last.end.isoformat(timespec="minutes") if last else None,
        "interval_minutes": cfg["schedule"]["interval_minutes"],
    }


def add_entry(cfg: dict, store: Store, text: str, source: str = "web", minutes: float | None = None) -> Entry | None:
    items = parse_items(text)
    if not items:
        return None
    end = schedule.now_local().replace(microsecond=0)
    last = store.last_entry(end.date())
    if minutes:
        start = end - timedelta(minutes=minutes)
    else:
        start = schedule.period_start(end, last.end if last else None, cfg)
    return store.append(Entry(start=start, end=end, items=items, raw=text, source=source))


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#1f2937">
<title>WhatIDid</title>
<style>
:root{--bg:#f7f7f5;--fg:#1f2937;--mut:#6b7280;--card:#fff;--acc:#2563eb;--bd:#e5e7eb}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--fg:#e5e7eb;--mut:#9ca3af;--card:#1b1e25;--acc:#60a5fa;--bd:#2b2f38}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 -apple-system,system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:4px 0 12px}h2{font-size:15px;color:var(--mut);margin:20px 0 8px;text-transform:uppercase;letter-spacing:.04em}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px}
textarea{width:100%;min-height:120px;font:inherit;padding:10px;border-radius:8px;border:1px solid var(--bd);background:var(--bg);color:var(--fg)}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}
button,.btn{font:inherit;padding:9px 14px;border-radius:8px;border:1px solid var(--bd);background:var(--card);color:var(--fg);cursor:pointer;text-decoration:none}
button.primary{background:var(--acc);border-color:var(--acc);color:#fff}
.mut{color:var(--mut);font-size:14px}.due{color:#dc2626;font-weight:600}
ul.log{list-style:none;padding:0;margin:0}ul.log li{padding:8px 0;border-top:1px solid var(--bd)}ul.log li:first-child{border-top:0}
.t{font-variant-numeric:tabular-nums;color:var(--mut);font-size:14px;margin-right:6px}
pre{white-space:pre-wrap;word-wrap:break-word;font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0}
input[type=number]{width:5em;font:inherit;padding:6px;border-radius:8px;border:1px solid var(--bd);background:var(--bg);color:var(--fg)}
</style></head><body><main>
<h1>What did you do?</h1>
<div class="card">
 <div id="st" class="mut">__STATUS__</div>
 <form method="post" action="__BASE__/entry__Q__" id="f">
  <textarea name="text" id="text" placeholder="One item per line, or separate with ';'.  #tags group items in summaries." autofocus></textarea>
  <div class="row">
   <button class="primary" type="submit">Save</button>
   <button type="button" id="same">Same as last</button>
   <span class="mut">covers <input type="number" name="minutes" min="1" step="1" placeholder="auto"> min</span>
  </div>
 </form>
</div>
<h2>Today</h2><div class="card">__TODAY__</div>
<h2>Summaries</h2>
<div class="row">
 <a class="btn" href="__BASE__/summary__Q__&period=day">Today</a>
 <a class="btn" href="__BASE__/summary__Q__&period=day&date=__YESTERDAY__">Yesterday</a>
 <a class="btn" href="__BASE__/summary__Q__&period=week">This week</a>
 <a class="btn" href="__BASE__/summary__Q__&period=week&date=__LASTWEEK__">Last week</a>
</div>
</main>
<script>
const LAST=__LAST__;
document.getElementById('same').onclick=()=>{document.getElementById('text').value=LAST.join('\\n')};
document.getElementById('text').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){document.getElementById('f').submit()}});
async function poll(){try{const r=await fetch('__BASE__/api/status__Q__');const s=await r.json();
 const el=document.getElementById('st');
 el.innerHTML=(s.due?'<span class="due">Check-in due</span> · ':'')+'Next prompt '+(s.next_prompt?s.next_prompt.slice(11,16)+(s.next_prompt.slice(0,10)!==s.now.slice(0,10)?' ('+s.next_prompt.slice(0,10)+')':''):'—')+' · this entry covers '+s.period_start+'–now';
 document.title=(s.due?'● ':'')+'WhatIDid';
 if(s.due&&window.Notification&&Notification.permission==='granted'&&!window._n){window._n=new Notification('WhatIDid',{body:'What did you do since '+s.period_start+'?'})}
 if(!s.due)window._n=null;
}catch(e){}}
if(window.Notification&&Notification.permission==='default'){document.body.addEventListener('click',()=>Notification.requestPermission(),{once:true})}
poll();setInterval(poll,60000);
</script></body></html>"""

SUMMARY_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>WhatIDid summary</title>
<style>:root{--bg:#f7f7f5;--fg:#1f2937;--card:#fff;--bd:#e5e7eb}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--fg:#e5e7eb;--card:#1b1e25;--bd:#2b2f38}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 -apple-system,system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px}
pre{white-space:pre-wrap;word-wrap:break-word;font:14px/1.5 ui-monospace,Menlo,monospace;margin:0}
.row{display:flex;gap:8px;margin:0 0 12px}button,a{font:inherit;padding:8px 12px;border-radius:8px;border:1px solid var(--bd);background:var(--card);color:var(--fg);text-decoration:none;cursor:pointer}
</style></head><body><main>
<div class="row"><a href="__BASE__/__Q__">← Back</a><button onclick="navigator.clipboard.writeText(document.getElementById('md').innerText).then(()=>this.textContent='Copied')">Copy Markdown</button></div>
<div class="card"><pre id="md">__BODY__</pre></div></main></body></html>"""


def make_handler(cfg: dict, store: Store, token: str, lock: threading.Lock):
    class Handler(BaseHTTPRequestHandler):
        server_version = "WhatIDid/1"

        def log_message(self, fmt, *args):  # quieter logs
            if cfg.get("_verbose"):
                super().log_message(fmt, *args)

        # ---------------------------------------------------------- helpers
        def _authorized(self, qs: dict) -> bool:
            if not token:
                return True
            supplied = (qs.get("t") or [""])[0]
            if not supplied:
                c = SimpleCookie(self.headers.get("Cookie", ""))
                supplied = c["wid_t"].value if "wid_t" in c else ""
            return hmac.compare_digest(supplied, token)

        def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8", extra=None):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if token:
                self.send_header("Set-Cookie", f"wid_t={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _q(self) -> str:
            return "?"  # token travels in the cookie after the first visit

        # ------------------------------------------------------------ routes
        def do_GET(self):
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            if not self._authorized(qs):
                return self._send(403, "Forbidden: add ?t=<token> to the URL once.", "text/plain")
            if u.path == "/":
                return self._send(200, self._index())
            if u.path == "/api/status":
                return self._send(200, json.dumps(status(cfg, store)), "application/json")
            if u.path == "/api/entries":
                d = _date(qs, date.today())
                es = store.entries(d)
                return self._send(200, json.dumps([json.loads(e.to_json()) for e in es]), "application/json")
            if u.path == "/summary":
                period = (qs.get("period") or ["day"])[0]
                fmt = (qs.get("format") or ["markdown"])[0]
                d = _date(qs, date.today())
                if period == "week":
                    a, b = summarize.week_bounds(d)
                    body = summarize.weekly(store.entries(a, b), d, cfg, fmt)
                else:
                    body = summarize.daily(store.entries(d), d, cfg, fmt)
                if fmt == "json":
                    return self._send(200, body, "application/json")
                page = SUMMARY_PAGE.replace("__BODY__", html.escape(body)).replace("__BASE__", "").replace("__Q__", "")
                return self._send(200, page)
            return self._send(404, "Not found", "text/plain")

        def do_POST(self):
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            if not self._authorized(qs):
                return self._send(403, "Forbidden", "text/plain")
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100_000:
                return self._send(413, "Too large", "text/plain")
            raw = self.rfile.read(n).decode("utf-8", "replace")
            if "json" in (self.headers.get("Content-Type") or ""):
                try:
                    payload = json.loads(raw or "{}")
                except ValueError:
                    return self._send(400, "Bad JSON", "text/plain")
            else:
                payload = {k: v[0] for k, v in parse_qs(raw).items()}
            if u.path in ("/entry", "/api/entry"):
                minutes = payload.get("minutes")
                try:
                    minutes = float(minutes) if minutes not in (None, "") else None
                except ValueError:
                    minutes = None
                with lock:
                    e = add_entry(cfg, store, str(payload.get("text", "")), "web", minutes)
                if u.path == "/api/entry":
                    return self._send(200 if e else 400, json.dumps(json.loads(e.to_json()) if e else {"error": "empty"}), "application/json")
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._send(404, "Not found", "text/plain")

        def _index(self) -> str:
            today = date.today()
            es = store.entries(today)
            if es:
                lis = "".join(
                    f'<li><span class="t">{e.start:%H:%M}–{e.end:%H:%M}</span>{html.escape("; ".join(e.items))}</li>'
                    for e in reversed(es)
                )
                today_html = f'<ul class="log">{lis}</ul>'
            else:
                today_html = '<span class="mut">Nothing logged yet today.</span>'
            last = store.last_items()
            return (
                PAGE.replace("__STATUS__", "&nbsp;")
                .replace("__TODAY__", today_html)
                .replace("__LAST__", json.dumps(last).replace("</", "<\\/"))
                .replace("__YESTERDAY__", (today - timedelta(days=1)).isoformat())
                .replace("__LASTWEEK__", (today - timedelta(days=7)).isoformat())
                .replace("__BASE__", "")
                .replace("__Q__", self._q())
            )

    return Handler


def _date(qs: dict, default: date) -> date:
    v = (qs.get("date") or [""])[0]
    try:
        return date.fromisoformat(v) if v else default
    except ValueError:
        return default


def make_server(cfg: dict, store: Store, host: str | None = None, port: int | None = None, lock=None):
    host = host or cfg["web"]["host"]
    port = int(port or cfg["web"]["port"])
    cfg = dict(cfg, web=dict(cfg["web"], host=host, port=port))
    token = resolve_token(cfg)
    httpd = ThreadingHTTPServer((host, port), make_handler(cfg, store, token, lock or threading.Lock()))
    return httpd, token


def serve(cfg: dict, store: Store, host: str | None = None, port: int | None = None, lock=None, background=False):
    httpd, token = make_server(cfg, store, host, port, lock)
    h, p = httpd.server_address[:2]
    shown = "localhost" if h in ("127.0.0.1", "::1") else (h if h != "0.0.0.0" else _lan_ip())
    url = f"http://{shown}:{p}/" + (f"?t={token}" if token else "")
    print(f"WhatIDid web UI: {url}", flush=True)
    if background:
        t = threading.Thread(target=httpd.serve_forever, daemon=True, name="whatidid-web")
        t.start()
        return httpd
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return httpd


def _lan_ip() -> str:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
