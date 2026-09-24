"""Wake Radio listener admin page, served at /users.

Caddy only lets the admin's Google account reach this service, so it
trusts the X-Auth-Request-Email header Caddy sets, and checks it anyway.

Edits the same allowlist file oauth2-proxy watches (it reloads on change).
On removal it forces a Caddy reload, which drops open live-feed connections
so a removed listener is cut off immediately; allowed listeners' browsers
reconnect by themselves.

Python standard library only.
"""

import glob
import html
import os
import re
import sys
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EMAILS = os.environ.get("EMAILS_FILE", "/data/auth/allowed-emails.txt")
LOG_DIR = os.environ.get("LOG_DIR", "/data/logs")
ADMIN = os.environ.get("ADMIN_EMAIL", "").strip().lower()
DOMAIN = os.environ.get("DOMAIN", "")
CADDY_ADMIN = os.environ.get("CADDY_ADMIN", "")  # e.g. http://172.29.0.2:2019
PORT = int(os.environ.get("PORT", "8080"))

EMAIL_RE = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
LOG_RE = re.compile(
    r"^(?P<ip>\S+) - \S+ - (?P<user>\S+) \[(?P<ts>[\d/: ]+)\] "
    r"\[(?P<status>AuthSuccess|AuthFailure)\] (?P<msg>.*)$")
lock = threading.Lock()


# ---------- allowlist ----------

def read_emails():
    try:
        with open(EMAILS) as f:
            out = []
            for line in f:
                e = line.strip().lower()
                if e and not e.startswith("#") and e not in out:
                    out.append(e)
            return out
    except FileNotFoundError:
        return []


def write_emails(emails):
    # Rewrite in place (same file) so oauth2-proxy's watcher sees the change.
    with open(EMAILS, "r+" if os.path.exists(EMAILS) else "w") as f:
        f.seek(0)
        f.write("".join(e + "\n" for e in emails))
        f.truncate()
        f.flush()
        os.fsync(f.fileno())


def drop_live_connections():
    """Force a Caddy config reload; Caddy closes open WebSockets on reload."""
    if not CADDY_ADMIN:
        return False
    try:
        get = urllib.request.Request(CADDY_ADMIN + "/config/")
        cfg = urllib.request.urlopen(get, timeout=10).read()
        load = urllib.request.Request(
            CADDY_ADMIN + "/load", data=cfg, method="POST",
            headers={"Content-Type": "application/json", "Cache-Control": "must-revalidate"})
        urllib.request.urlopen(load, timeout=15).read()
        return True
    except Exception as e:  # never block the page on this
        print("caddy reload failed:", e, file=sys.stderr)
        return False


# ---------- sign-in log ----------

def us_eastern(dt_utc):
    """UTC -> US Eastern without needing tzdata in the container."""
    y = dt_utc.year

    def nth_sunday(month, n):
        d = datetime(y, month, 1)
        d += timedelta(days=(6 - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    dst_start = nth_sunday(3, 2) + timedelta(hours=7)   # 2:00 EST = 07:00 UTC
    dst_end = nth_sunday(11, 1) + timedelta(hours=6)    # 2:00 EDT = 06:00 UTC
    naive = dt_utc.replace(tzinfo=None)
    return dt_utc + timedelta(hours=-4 if dst_start <= naive < dst_end else -5)


def read_auth_events(limit=2000):
    events = []
    paths = sorted(glob.glob(os.path.join(LOG_DIR, "oauth2-proxy*.log")), key=os.path.getmtime)
    for path in paths[-2:]:
        try:
            with open(path, errors="replace") as f:
                lines = f.readlines()[-limit:]
        except (FileNotFoundError, PermissionError):
            continue
        for line in lines:
            m = LOG_RE.match(line.strip())
            if not m:
                continue
            try:
                ts = datetime.strptime(m["ts"], "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            user = m["user"].lower()
            events.append({
                "when": ts,
                "email": "" if user == "-" else user,
                "ok": m["status"] == "AuthSuccess",
                "ip": m["ip"],
            })
    events.sort(key=lambda e: e["when"])
    return events


def fmt(ts):
    return us_eastern(ts).strftime("%b %-d, %-I:%M %p")


# ---------- page ----------

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#1d2330;--muted:#667085;--line:#e4e7ec;--accent:#1f6feb;
--ok:#16794c;--bad:#b42318;--okbg:#e7f6ee;--badbg:#fdecea}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--card:#1a1f26;--ink:#e6e9ee;--muted:#98a2b3;
--line:#2a313b;--accent:#4c8dff;--ok:#4ade80;--bad:#f87171;--okbg:#12291d;--badbg:#2d1616}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0 0 12px}
.sub{color:var(--muted);margin:0 0 20px}.sub a{color:var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
form.add{display:flex;gap:8px;flex-wrap:wrap}
form.add input{flex:1 1 260px;min-width:0;padding:10px 12px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--ink);font:inherit}
button{font:inherit;border-radius:8px;padding:9px 14px;cursor:pointer;border:1px solid var(--line);
background:var(--card);color:var(--ink)}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.danger{color:var(--bad)}button.small{padding:5px 10px;font-size:13px}
table{width:100%;border-collapse:collapse}td,th{padding:9px 6px;border-top:1px solid var(--line);
text-align:left;vertical-align:middle}th{font-size:12px;color:var(--muted);font-weight:600;border-top:0}
td.r{text-align:right}.muted{color:var(--muted);font-size:13px}.email{word-break:break-all}
.flash{padding:10px 12px;border-radius:8px;margin-bottom:16px}
.flash.ok{background:var(--okbg);color:var(--ok)}.flash.bad{background:var(--badbg);color:var(--bad)}
.tag{font-size:12px;padding:2px 8px;border-radius:99px;white-space:nowrap}.tag.ok{background:var(--okbg);color:var(--ok)}
.tag.bad{background:var(--badbg);color:var(--bad)}
@media (max-width:520px){.hide-sm{display:none}}
"""


def page(viewer, flash=None, flash_ok=True):
    emails = read_emails()
    events = read_auth_events()
    last_ok = {}
    for e in events:
        if e["ok"] and e["email"]:
            last_ok[e["email"]] = e["when"]
    denied = {}
    for e in events:
        if not e["ok"] and e["email"] and e["email"] not in emails:
            denied[e["email"]] = e["when"]

    esc = html.escape
    rows = []
    for em in sorted(emails, key=lambda x: (x != ADMIN, x)):
        seen = fmt(last_ok[em]) if em in last_ok else "never signed in"
        if em == ADMIN:
            action = '<span class="muted">admin</span>'
        else:
            action = (f'<form method="post" action="/users/remove" '
                      f'onsubmit="return confirm(\'Remove {esc(em)}? They are cut off immediately.\')">'
                      f'<input type="hidden" name="email" value="{esc(em)}">'
                      f'<button class="small danger">Remove</button></form>')
        rows.append(f'<tr><td class="email">{esc(em)}</td>'
                    f'<td class="muted hide-sm">{esc(seen)}</td><td class="r">{action}</td></tr>')

    denied_html = ""
    if denied:
        drows = "".join(
            f'<tr><td class="email">{esc(em)}</td><td class="muted hide-sm">{esc(fmt(ts))}</td>'
            f'<td class="r"><form method="post" action="/users/add">'
            f'<input type="hidden" name="emails" value="{esc(em)}">'
            f'<button class="small">Add</button></form></td></tr>'
            for em, ts in sorted(denied.items(), key=lambda kv: kv[1], reverse=True)[:15])
        denied_html = (
            '<section class="card"><h2>Tried to sign in, not on the list</h2>'
            '<p class="muted" style="margin:-6px 0 8px">Anyone you send the link to shows up here after '
            'their first try. Click Add to let them in.</p>'
            f'<table><tr><th>Google account</th><th class="hide-sm">Last try</th><th></th></tr>{drows}</table></section>')

    recent = "".join(
        f'<tr><td><div class="email">{esc(e["email"] or "unknown")}</div>'
        f'<div class="muted">{esc(fmt(e["when"]))}</div></td>'
        f'<td class="r"><span class="tag {"ok" if e["ok"] else "bad"}">{"signed in" if e["ok"] else "refused"}</span></td></tr>'
        for e in reversed(events[-15:]))
    recent_html = (f'<table><tr><th>Account</th><th></th></tr>{recent}</table>'
                   if recent else '<p class="muted">No sign-ins recorded yet.</p>')

    flash_html = (f'<div class="flash {"ok" if flash_ok else "bad"}">{esc(flash)}</div>' if flash else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wake Radio listeners</title><style>{CSS}</style></head><body><main>
<h1>Wake Radio listeners</h1>
<p class="sub">Signed in as {esc(viewer)} &middot; <a href="/">Back to the scanner</a></p>
{flash_html}
<section class="card"><h2>Add listeners</h2>
<form class="add" method="post" action="/users/add">
<input name="emails" type="text" inputmode="email" autocomplete="off"
 placeholder="friend@gmail.com, another@gmail.com" aria-label="Google account email addresses" required>
<button class="primary">Add</button></form>
<p class="muted" style="margin:8px 0 0">Use the Google account they sign in with. Then send them
https://{esc(DOMAIN)}</p></section>
{denied_html}
<section class="card"><h2>Allowed ({len(emails)})</h2>
<table><tr><th>Google account</th><th class="hide-sm">Last sign-in</th><th></th></tr>{"".join(rows)}</table></section>
<section class="card"><h2>Recent sign-ins</h2>{recent_html}</section>
</main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "wakeradio-users"

    def viewer(self):
        return (self.headers.get("X-Auth-Request-Email") or "").strip().lower()

    def deny(self):
        self.send_response(403)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Admin only")

    def send_html(self, body, status=200):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, msg, ok=True):
        q = urllib.parse.urlencode({"msg": msg, "ok": "1" if ok else "0"})
        self.send_response(303)
        self.send_header("Location", "/users?" + q)
        self.end_headers()

    def do_GET(self):
        if self.viewer() != ADMIN:
            return self.deny()
        u = urllib.parse.urlparse(self.path)
        if u.path.rstrip("/") != "/users":
            self.send_response(404)
            self.end_headers()
            return
        q = urllib.parse.parse_qs(u.query)
        msg = q.get("msg", [None])[0]
        self.send_html(page(self.viewer(), msg, q.get("ok", ["1"])[0] == "1"))

    def same_origin(self):
        want = f"https://{DOMAIN}"
        origin = self.headers.get("Origin")
        if origin:
            return origin == want
        ref = self.headers.get("Referer") or ""
        return ref == want or ref.startswith(want + "/")

    def do_POST(self):
        if self.viewer() != ADMIN:
            return self.deny()
        if not self.same_origin():
            return self.deny()
        length = min(int(self.headers.get("Content-Length") or 0), 20000)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode(errors="replace"))
        path = urllib.parse.urlparse(self.path).path

        if path == "/users/add":
            raw = " ".join(form.get("emails", []))
            wanted = [e.strip().lower() for e in re.split(r"[\s,;]+", raw) if e.strip()]
            bad = [e for e in wanted if not EMAIL_RE.match(e)]
            good = [e for e in wanted if EMAIL_RE.match(e)]
            with lock:
                emails = read_emails()
                added = [e for e in good if e not in emails]
                if added:
                    write_emails(emails + added)
            parts = []
            if added:
                parts.append("Added " + ", ".join(added) + ". They can sign in now.")
            if good and not added:
                parts.append("Already on the list.")
            if bad:
                parts.append("Not an email address: " + ", ".join(bad))
            return self.redirect(" ".join(parts) or "Nothing to add.", ok=not bad)

        if path == "/users/remove":
            em = (form.get("email", [""])[0]).strip().lower()
            if em == ADMIN:
                return self.redirect("The admin account can't be removed here.", ok=False)
            with lock:
                emails = read_emails()
                if em not in emails:
                    return self.redirect(f"{em} wasn't on the list.", ok=False)
                write_emails([e for e in emails if e != em])
            cut = drop_live_connections()
            return self.redirect(
                f"Removed {em}." + (" Their live feed was cut off." if cut else
                                    " They're blocked on their next page load."))

        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt_, *args):
        pass


if __name__ == "__main__":
    if not ADMIN or not DOMAIN:
        sys.exit("ADMIN_EMAIL and DOMAIN must be set")
    print(f"wakeradio users page on :{PORT} (admin {ADMIN})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
