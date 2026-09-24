#!/usr/bin/env python3
"""Wake Radio talker-alias learner.

Rdio Scanner 6.6.3 ignores the P25 talker alias SDRTrunk sends with each call,
so radios show as bare UID numbers. This tiny service sits in front of the
upload endpoint: it forwards every upload to Rdio unchanged (so recording is
never affected), and on the side it remembers each radio's talker alias and
writes it into Rdio's unit labels. A radio is learned the first time it keys
up with an alias; after that Rdio shows the name on every call by itself.

Forwarding is the priority and always runs; learning is best-effort and can
never break an upload. Names are flushed to Rdio in small batches (not once
per call) so the config isn't rewritten constantly.

Env:
  RDIO_URL             where Rdio Scanner listens (http://rdio-scanner:3000)
  RDIO_ADMIN_PASSWORD  Rdio admin password (from .env); needed to write labels
  PORT                 port to listen on (default 8090)
  STATE_FILE           where to remember learned names (default /data/aliases.json)
  DEBOUNCE_SECONDS     how long to gather new names before writing (default 5)
"""

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RDIO_URL = os.environ.get("RDIO_URL", "http://rdio-scanner:3000").rstrip("/")
ADMIN_PW = os.environ.get("RDIO_ADMIN_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8090"))
STATE_FILE = os.environ.get("STATE_FILE", "/data/aliases.json")
DEBOUNCE = float(os.environ.get("DEBOUNCE_SECONDS", "5"))

# Strip a leading UID number an alias might carry, e.g. "1838270 GFL1 Driver".
_LEADING_NUM = re.compile(r"^\s*\d+\s*[-:_.]?\s+(?=\S)")


def clean(name):
    return _LEADING_NUM.sub("", (name or "").strip()).strip()


# ---- learned state: {system_id(int): {uid(int): name}} --------------------
_lock = threading.Lock()
_learned = {}
_dirty = threading.Event()


def load_state():
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        for sys_id, units in data.items():
            _learned[int(sys_id)] = {int(u): n for u, n in units.items()}
        log(f"loaded {sum(len(u) for u in _learned.values())} learned names")
    except (OSError, ValueError):
        pass


def save_state():
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({str(s): {str(u): n for u, n in units.items()}
                       for s, units in _learned.items()}, f)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        log(f"could not save state: {e}")


def log(msg):
    print(f"[alias-learner] {msg}", flush=True)


# ---- multipart: pull just the small text fields we need --------------------
def text_fields(body, content_type):
    """Return {name: value} for non-file parts. Ignores the audio part."""
    m = re.search(r"boundary=([^;]+)", content_type or "")
    if not m:
        return {}
    boundary = ("--" + m.group(1).strip().strip('"')).encode()
    fields = {}
    for part in body.split(boundary):
        if not part or part in (b"--\r\n", b"--", b"\r\n"):
            continue
        head, _, value = part.partition(b"\r\n\r\n")
        if not _:
            continue
        head_l = head.lower()
        if b"filename=" in head_l:      # skip the audio file part
            continue
        nm = re.search(rb'name="([^"]*)"', head)
        if not nm:
            continue
        name = nm.group(1).decode(errors="replace")
        # value ends with the trailing CRLF before the next boundary
        val = value
        if val.endswith(b"\r\n"):
            val = val[:-2]
        fields[name] = val.decode(errors="replace")
    return fields


def note_call(body, content_type):
    f = text_fields(body, content_type)
    alias = clean(f.get("talkerAlias", ""))
    src = f.get("source", "")
    sysid = f.get("system", "")
    if not alias or not src.isdigit() or not sysid.isdigit():
        return
    sysid, src = int(sysid), int(src)
    with _lock:
        cur = _learned.setdefault(sysid, {})
        if cur.get(src) != alias:
            cur[src] = alias
            _dirty.set()
            log(f"learned system {sysid} unit {src} = {alias!r}")


# ---- forwarding to Rdio (the part that must never fail) --------------------
def forward(path, body, content_type, user_agent):
    req = urllib.request.Request(RDIO_URL + path, data=body, method="POST")
    if content_type:
        req.add_header("Content-Type", content_type)
    req.add_header("User-Agent", user_agent or "sdrtrunk")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(), r.headers.get_content_type() or "text/plain"
    except urllib.error.HTTPError as e:
        return e.code, e.read(), (e.headers.get_content_type() if e.headers else "text/plain")


# ---- writing learned names into Rdio's unit labels ------------------------
def api(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(RDIO_URL + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def flush():
    """Write any learned names Rdio doesn't have yet. Returns True if fully applied."""
    if not ADMIN_PW:
        log("no admin password set yet; will retry")
        return False
    with _lock:
        snapshot = {s: dict(u) for s, u in _learned.items()}
    if not snapshot:
        return True

    st, data = api("POST", "/api/admin/login", body={"password": ADMIN_PW})
    if st != 200 or not data or not data.get("token"):
        log("admin login failed; will retry")
        return False
    token = data["token"]
    st, data = api("GET", "/api/admin/config", token=token)
    if st != 200 or not data or "config" not in data:
        log("could not read config; will retry")
        return False
    cfg = data["config"]

    systems = {str(s.get("id")): s for s in cfg.get("systems", [])}
    changed = False
    pending = False
    for sysid, units in snapshot.items():
        system = systems.get(str(sysid))
        if system is None:
            pending = True          # Rdio hasn't created this system yet; retry later
            continue
        lst = system.setdefault("units", [])
        by_id = {u.get("id"): u for u in lst}
        for uid, name in units.items():
            u = by_id.get(uid)
            if u is None:
                lst.append({"id": uid, "label": name})
                changed = True
            elif u.get("label") != name:
                u["label"] = name
                changed = True

    if changed:
        st, _ = api("PUT", "/api/admin/config", token=token, body=cfg)
        if st != 200:
            log(f"config save failed (HTTP {st}); will retry")
            return False
        n = sum(len(u) for u in snapshot.values())
        log(f"applied names to Rdio ({n} known)")
        save_state()
    return not pending


def flusher():
    while True:
        _dirty.wait()
        time.sleep(DEBOUNCE)        # gather a burst into one write
        _dirty.clear()
        try:
            ok = flush()
        except Exception as e:
            ok = False
            log(f"flush error: {e}")
        if not ok:
            # something pending (system not created yet, or Rdio busy): retry soon
            threading.Timer(30, _dirty.set).start()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "")
        # 1) Forward to Rdio and relay its exact answer. This is what matters.
        status, resp, resp_ctype = forward(
            self.path, body, ctype, self.headers.get("User-Agent", "sdrtrunk"))
        self.send_response(status)
        self.send_header("Content-Type", resp_ctype)
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        try:
            self.wfile.write(resp)
        except Exception:
            pass
        # 2) Learn the talker alias (best-effort; never affects the upload).
        try:
            note_call(body, ctype)
        except Exception as e:
            log(f"note error: {e}")

    def do_GET(self):
        # health check
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "3")
        self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, *a):
        pass


def main():
    load_state()
    if _learned:
        _dirty.set()                # push what we know to a fresh Rdio on boot
    threading.Thread(target=flusher, daemon=True).start()
    log(f"forwarding uploads to {RDIO_URL}, listening on :{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
