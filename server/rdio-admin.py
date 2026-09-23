#!/usr/bin/env python3
"""Configure Rdio Scanner (v6.6.3) over its local admin API.

Run on the server. Talks to Rdio Scanner on 127.0.0.1:3000, which is only
published on the loopback interface, never to the internet.

  rdio-admin.py bootstrap   first-run setup: replace the default admin
                            password, create the SDRTrunk upload key, and
                            apply the options below. Safe to re-run.
  rdio-admin.py show-key    print the SDRTrunk upload key
  rdio-admin.py new-key     replace the upload key (then update SDRTrunk)

Secrets live in /opt/wakeradio/.env (root-only).
"""

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("RDIO_URL", "http://127.0.0.1:3000")
ENV_FILE = os.environ.get("WAKERADIO_ENV", "/opt/wakeradio/.env")
DEFAULT_PASSWORD = "rdio-scanner"
KEY_IDENT = "sdrtrunk"

# Server options applied at bootstrap. Everything else keeps Rdio's defaults.
OPTIONS = {
    "autoPopulate": True,   # create systems/talkgroups from what SDRTrunk sends
    "branding": "Wake Radio",
    "pruneDays": 30,        # keep 30 days of audio (about 1-3 GB for a busy system)
    "time12hFormat": True,
}


def read_env():
    env = {}
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k] = v
    except FileNotFoundError:
        pass
    return env


def set_env(key, value):
    lines, found = [], False
    try:
        with open(ENV_FILE) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        pass
    for i, line in enumerate(lines):
        if line.startswith(key + "="):
            lines[i] = f"{key}={value}"
            found = True
    if not found:
        lines.append(f"{key}={value}")
    old = os.umask(0o077)
    try:
        with open(ENV_FILE, "w") as f:
            f.write("\n".join(lines) + "\n")
    finally:
        os.umask(old)


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", token)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def wait_for_server(seconds=90):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen(BASE + "/", timeout=3)
            return
        except Exception:
            time.sleep(2)
    sys.exit(f"Rdio Scanner is not answering on {BASE}. Check: docker compose logs rdio-scanner")


def login(password):
    status, data = call("POST", "/api/admin/login", {"password": password})
    if status == 200 and data and data.get("token"):
        return data["token"]
    return None


def get_token():
    env = read_env()
    pw = env.get("RDIO_ADMIN_PASSWORD")
    token = login(pw) if pw else None
    if token:
        return token, False
    token = login(DEFAULT_PASSWORD)
    if token:
        return token, True
    sys.exit("Could not log in to Rdio Scanner admin. If you changed the admin password "
             "in the web UI, put it in RDIO_ADMIN_PASSWORD in " + ENV_FILE)


def get_config(token):
    status, data = call("GET", "/api/admin/config", token=token)
    if status != 200 or not data or "config" not in data:
        sys.exit(f"Could not read Rdio Scanner config (HTTP {status})")
    return data["config"]


def put_config(token, cfg):
    status, _ = call("PUT", "/api/admin/config", cfg, token=token)
    if status != 200:
        sys.exit(f"Could not save Rdio Scanner config (HTTP {status})")


def upload_key(cfg):
    for k in cfg.get("apiKeys", []):
        if k.get("ident") == KEY_IDENT and not k.get("disabled"):
            return k.get("key")
    return None


def bootstrap():
    wait_for_server()
    token, is_default = get_token()

    if is_default:
        new_pw = secrets.token_urlsafe(18)
        status, _ = call("POST", "/api/admin/password",
                         {"currentPassword": DEFAULT_PASSWORD, "newPassword": new_pw}, token=token)
        if status != 200:
            sys.exit(f"Could not change the default admin password (HTTP {status})")
        set_env("RDIO_ADMIN_PASSWORD", new_pw)
        token = login(new_pw)
        if not token:
            sys.exit("Admin password was changed but logging in with it failed")
        print("Replaced the default Rdio Scanner admin password (saved in .env).")

    cfg = get_config(token)
    cfg.setdefault("options", {}).update(OPTIONS)
    if not upload_key(cfg):
        cfg.setdefault("apiKeys", []).append({
            "disabled": False,
            "ident": KEY_IDENT,
            "key": str(uuid.uuid4()),
            "systems": "*",
        })
        print("Created the SDRTrunk upload key.")
    put_config(token, cfg)

    key = upload_key(get_config(token))
    if not key:
        sys.exit("Upload key did not persist; check docker compose logs rdio-scanner")
    set_env("RDIO_UPLOAD_KEY", key)
    print("Rdio Scanner is configured.")


def show_key():
    token, _ = get_token()
    key = upload_key(get_config(token))
    print(key or "No upload key yet. Run: sudo /opt/wakeradio/rdio-admin.py bootstrap")


def new_key():
    token, _ = get_token()
    cfg = get_config(token)
    # Change the key in place: Rdio 6.6.3 won't delete a key row while the
    # list contains a new (id-less) entry, so remove-and-add leaves the old key live.
    keys = [k for k in cfg.get("apiKeys", []) if k.get("ident") == KEY_IDENT]
    if not keys:
        sys.exit("No upload key yet. Run: sudo /opt/wakeradio/rdio-admin.py bootstrap")
    fresh = str(uuid.uuid4())
    for k in keys:
        k["key"] = fresh
        k["disabled"] = False
    put_config(token, cfg)
    key = upload_key(get_config(token))
    set_env("RDIO_UPLOAD_KEY", key)
    print(key)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"bootstrap": bootstrap, "show-key": show_key, "new-key": new_key}.get(
        cmd, lambda: sys.exit(__doc__))()
