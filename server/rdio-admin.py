#!/usr/bin/env python3
"""Configure Rdio Scanner (v6.6.3) over its local admin API.

Run on the server. Talks to Rdio Scanner on 127.0.0.1:3000, which is only
published on the loopback interface, never to the internet.

  rdio-admin.py bootstrap   first-run setup: replace the default admin
                            password, create the SDRTrunk upload key, and
                            apply the options below. Safe to re-run.
  rdio-admin.py show-key    print the SDRTrunk upload key
  rdio-admin.py new-key     replace the upload key (then update SDRTrunk)
  rdio-admin.py import-units PLAYLIST.xml [--system N] [--dry-run]
                            name radios from an SDRTrunk playlist: each radio
                            alias becomes a unit label (so calls show
                            "GFL1 Driver" instead of a bare UID number). The
                            leading UID digits SDRTrunk shows are stripped, so
                            only the meaningful name is used. Re-run any time
                            to pick up new aliases.
  rdio-admin.py clean-units [--system N] [--dry-run]
                            strip a leading UID number from unit labels that
                            already exist (e.g. "1838270 GFL1 Driver" ->
                            "GFL1 Driver").
  rdio-admin.py list-talkgroups [--system N]
                            list talkgroups (id, group, label) so you can see
                            what to bundle.
  rdio-admin.py set-group "LABEL" [--match TEXT]... [TGID]... [--system N] [--dry-run]
                            put talkgroups into a group you can toggle on/off
                            in the player's SELECT TG panel. Pick talkgroups by
                            id and/or by --match (case-insensitive substring of
                            the label). Creates the group if it doesn't exist.
                            Example:
                              set-group "Fire Pre-Alert" --match "Cary FD Disp" \
                                --match "WC FD Disp" --match "WC FD Alert" \
                                --match "RFD HQ Disp" --match "RFD Alert"

Secrets live in /opt/wakeradio/.env (root-only).
"""

import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET

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


# A leading UID number that SDRTrunk (or an imported list) glued onto the front
# of a name: "1838270 GFL1 Driver" or "01838270 - GFL1 Driver" -> "GFL1 Driver".
# Only strips when real text follows, so a name that is only a number is left be.
_LEADING_NUM = re.compile(r"^\s*\d+\s*[-:_.]?\s+(?=\S)")


def clean_label(name):
    return _LEADING_NUM.sub("", (name or "").strip()).strip()


def radios_from_playlist(path):
    """Return {uid_int: label} for every radio alias in an SDRTrunk playlist."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        sys.exit(f"Could not read playlist {path}: {e}")
    units = {}
    for alias in root.iter("alias"):
        name = clean_label(alias.get("name", ""))
        if not name:
            continue
        for ident in alias.findall("id"):
            if (ident.get("type") or "").lower() == "radio":
                val = ident.get("value")
                if val and val.isdigit():
                    units[int(val)] = name
    return units


def _pick_system(cfg, system_id):
    systems = cfg.get("systems", [])
    if not systems:
        sys.exit("No systems in Rdio yet. Let SDRTrunk upload a few calls first.")
    for s in systems:
        if str(s.get("id")) == str(system_id):
            return s
    ids = ", ".join(str(s.get("id")) for s in systems)
    sys.exit(f"No system with id {system_id}. Systems present: {ids}. "
             f"Pass --system N.")


def _args(rest):
    system_id, dry = "1", False
    i = 0
    extra = None
    while i < len(rest):
        a = rest[i]
        if a == "--system" and i + 1 < len(rest):
            system_id = rest[i + 1]; i += 2; continue
        if a == "--dry-run":
            dry = True; i += 1; continue
        extra = a; i += 1
    return extra, system_id, dry


def import_units(rest):
    path, system_id, dry = _args(rest)
    if not path:
        sys.exit("Usage: rdio-admin.py import-units PLAYLIST.xml [--system N] [--dry-run]")
    wanted = radios_from_playlist(path)
    if not wanted:
        sys.exit(f"No radio aliases found in {path}. (Aliases need an "
                 f"<id type=\"radio\" value=\"...\"/> entry.)")
    token, _ = get_token()
    cfg = get_config(token)
    system = _pick_system(cfg, system_id)
    units = system.setdefault("units", [])
    by_id = {u.get("id"): u for u in units}

    added = changed = 0
    for uid, label in sorted(wanted.items()):
        u = by_id.get(uid)
        if u is None:
            units.append({"id": uid, "label": label})
            added += 1
        elif u.get("label") != label:
            u["label"] = label
            changed += 1

    print(f"system {system_id} ({system.get('label')}): "
          f"{len(wanted)} radio aliases in playlist -> "
          f"{added} new, {changed} relabeled, "
          f"{len(wanted) - added - changed} already current.")
    for uid, label in list(sorted(wanted.items()))[:5]:
        print(f"  {uid} -> {label}")
    if len(wanted) > 5:
        print(f"  ... and {len(wanted) - 5} more")
    if dry:
        print("(dry run: nothing saved)")
        return
    if added or changed:
        put_config(token, cfg)
        print("Saved. New calls will show these names; past calls update too.")
    else:
        print("Nothing to change.")


def clean_units(rest):
    _, system_id, dry = _args(rest)
    token, _ = get_token()
    cfg = get_config(token)
    system = _pick_system(cfg, system_id)
    units = system.get("units", [])
    changed = 0
    for u in units:
        new = clean_label(u.get("label"))
        if new and new != u.get("label"):
            print(f"  {u.get('id')}: {u.get('label')!r} -> {new!r}")
            u["label"] = new
            changed += 1
    if not changed:
        print(f"system {system_id}: no unit labels needed cleaning.")
        return
    if dry:
        print(f"(dry run: {changed} would change, nothing saved)")
        return
    put_config(token, cfg)
    print(f"Cleaned {changed} unit labels.")


def _group_label(cfg, gid):
    for g in cfg.get("groups", []):
        if g.get("_id") == gid:
            return g.get("label")
    return None


def list_talkgroups(rest):
    _, system_id, _ = _args(rest)
    token, _ = get_token()
    cfg = get_config(token)
    system = _pick_system(cfg, system_id)
    tgs = sorted(system.get("talkgroups", []), key=lambda t: t.get("id", 0))
    if not tgs:
        print("No talkgroups yet. Let SDRTrunk upload some calls first.")
        return
    print(f"system {system_id} ({system.get('label')}): {len(tgs)} talkgroups")
    print(f"  {'TGID':>7}  {'group':<16} label")
    for t in tgs:
        g = _group_label(cfg, t.get("groupId")) or "-"
        print(f"  {t.get('id'):>7}  {g:<16} {t.get('label')}")


def set_group(rest):
    # First non-flag arg is the group label; remaining digit args are TGIDs.
    label = None
    tgids = set()
    matches = []
    system_id, dry = "1", False
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--system" and i + 1 < len(rest):
            system_id = rest[i + 1]; i += 2; continue
        if a == "--match" and i + 1 < len(rest):
            matches.append(rest[i + 1].lower()); i += 2; continue
        if a == "--dry-run":
            dry = True; i += 1; continue
        if label is None:
            label = a
        elif a.isdigit():
            tgids.add(int(a))
        else:
            sys.exit(f"Unexpected argument {a!r}. TGIDs must be numbers; "
                     f"use --match for names.")
        i += 1

    if not label:
        sys.exit('Usage: rdio-admin.py set-group "LABEL" [--match TEXT]... [TGID]...')
    if not tgids and not matches:
        sys.exit("Give at least one TGID or --match to choose talkgroups.")

    token, _ = get_token()
    cfg = get_config(token)
    system = _pick_system(cfg, system_id)
    tgs = system.get("talkgroups", [])

    # Resolve which talkgroups to move.
    chosen = {}
    for t in tgs:
        tid = t.get("id")
        lab = (t.get("label") or "").lower()
        if tid in tgids or any(m in lab for m in matches):
            chosen[tid] = t
    missing = tgids - set(chosen)
    if missing:
        print("Warning: no talkgroup with id " +
              ", ".join(str(m) for m in sorted(missing)))
    unmatched = [m for m in matches
                 if not any(m in (t.get("label") or "").lower() for t in tgs)]
    if unmatched:
        print("Warning: --match found nothing for: " +
              ", ".join(repr(m) for m in unmatched))
    if not chosen:
        sys.exit("Nothing matched. Try: rdio-admin.py list-talkgroups")

    # Find or create the group.
    groups = cfg.setdefault("groups", [])
    grp = next((g for g in groups if (g.get("label") or "").lower() == label.lower()), None)
    if grp is None:
        new_id = max((g.get("_id", 0) for g in groups), default=0) + 1
        grp = {"_id": new_id, "label": label}
        groups.append(grp)
        print(f"Creating group {new_id} \"{label}\".")
    gid = grp["_id"]

    print(f"Putting {len(chosen)} talkgroups into \"{label}\":")
    changed = 0
    for tid, t in sorted(chosen.items()):
        was = _group_label(cfg, t.get("groupId")) or "-"
        if t.get("groupId") != gid:
            changed += 1
        print(f"  {tid:>7}  {t.get('label')}   ({was} -> {label})")
        t["groupId"] = gid

    if dry:
        print("(dry run: nothing saved)")
        return
    put_config(token, cfg)
    print(f"Saved. In the player, open SELECT TG and toggle \"{label}\". "
          f"Your choice is remembered per browser.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    rest = sys.argv[2:]
    dispatch = {
        "import-units": import_units,
        "clean-units": clean_units,
        "list-talkgroups": list_talkgroups,
        "set-group": set_group,
    }
    if cmd in dispatch:
        dispatch[cmd](rest)
    else:
        {"bootstrap": bootstrap, "show-key": show_key, "new-key": new_key}.get(
            cmd, lambda: sys.exit(__doc__))()
