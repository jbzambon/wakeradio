# Wake Radio

Private web scanner for the Cary/Wake P25 system (RadioReference SID 7976).
SDRTrunk decodes on the Windows PC at home and uploads each finished call to
a small AWS Lightsail server. People sign in with Google at
**https://wakeradio.joezambon.com** and listen in the browser. Only Google
accounts on the allowlist get in.

```
 Airspy R2 ──► SDRTrunk (Windows PC)
                  │  HTTPS upload per call, API key
                  ▼
 ┌─────────── Lightsail (Ubuntu 24.04, Docker) ────────────┐
 │  Caddy :443  (Let's Encrypt HTTPS)                        │
 │    ├─ /api/call-upload ─────────────► Rdio Scanner 6.6.3  │
 │    └─ everything else ─► oauth2-proxy ─► Rdio Scanner     │
 │                          (Google sign-in + allowlist)     │
 └───────────────────────────────────────────────────────────┘
                  ▲
   Listeners' browsers (Google account on the allowlist)
```

The home PC only makes outbound HTTPS requests, so there is no port
forwarding and nothing at home is exposed.

## What's in here

| Path | What it is |
|---|---|
| `lightsail-launch-script.sh` | Paste into Lightsail when creating the instance. Installs Docker and puts the server files in `/opt/wakeradio`. No secrets. |
| `server/docker-compose.yml` | The three containers: Caddy, oauth2-proxy, Rdio Scanner. |
| `server/Caddyfile` | HTTPS, Google sign-in on every page, upload endpoint left to Rdio's API key, `/admin` limited to your account. |
| `server/setup.sh` | Run once on the server. Asks for the Google credentials, starts everything, configures Rdio, prints the SDRTrunk settings. Safe to re-run. |
| `server/users.sh` | Add, remove and list who can sign in. |
| `server/rdio-admin.py` | Configures Rdio Scanner through its local admin API (called by setup.sh). |
| `windows/Add-RdioStreamToAliases.ps1` | Tags every talkgroup alias in your SDRTrunk playlist with the stream, instead of 326 clicks. |
| `tools/` | `build-launch-script.sh` regenerates the launch script after editing `server/`. `fake-sdrtrunk-upload.py` sends a call exactly like SDRTrunk does, for testing. |

---

## Setup

Allow about 45 minutes. Do the steps in order; each one needs something
from the one before.

### 1. Google sign-in credential (about 5 minutes)

This is what lets the site offer "Sign in with Google". It does not give
the site access to anyone's Gmail or Drive, only their name and email
address.

1. Go to <https://console.cloud.google.com/> and sign in with your Google account.
2. Project picker (top left) → **New project** → name it `wakeradio` → **Create**, then select it.
3. Menu → **Google Auth Platform** (older consoles call this **APIs & Services → OAuth consent screen**). Click **Get started**.
   - App name: `Wake Radio`. User support email: yours.
   - Audience: **External**.
   - Contact email: yours. Agree and **Create**.
4. **Branding** page (left sidebar). Google won't publish without these, even for name-and-email sign-in:
   - Application home page: `https://wakeradio.joezambon.com`
   - Application privacy policy link: `https://wakeradio.joezambon.com/privacy` (a short public page the server provides)
   - Authorized domains → **Add domain** → `joezambon.com`
   - Leave the logo empty (a logo triggers Google's review) and terms of service blank. **Save**.
   The links don't have to work yet; Google only checks them if the app goes through verification, which it won't.
5. **Audience** page → **Publish app** → confirm. The status should read **In production**.
   Sign-in only asks for name and email, which Google doesn't review, so there is no verification step. Who actually gets in is decided by your allowlist on the server, not by Google.
6. **Clients** → **Create client**:
   - Application type: **Web application**. Name: `wakeradio`.
   - **Authorized redirect URIs** → Add URI: `https://wakeradio.joezambon.com/oauth2/callback`
   - **Create**.
7. Copy the **Client ID** (ends in `.apps.googleusercontent.com`) and the **Client secret** somewhere temporary. You'll paste them in step 4, then you can delete your copy.

### 2. Lightsail instance (about 10 minutes)

1. Go to <https://lightsail.aws.amazon.com/> → **Create instance**.
2. Region: **Virginia (us-east-1)**, any zone.
3. Platform **Linux/Unix** → **Operating system (OS) only** → **Ubuntu 24.04 LTS**.
4. **+ Add launch script**. Open `lightsail-launch-script.sh` from this repo (on GitHub, the **Raw** button, then select all and copy) and paste the whole thing into the box.
5. Plan: **General purpose, $7/month (1 GB RAM, 2 vCPUs, 40 GB SSD)**, with the public IPv4 option. The $5 plan (512 MB) would probably run it, but 1 GB leaves headroom.
6. Name it `wakeradio` → **Create instance**.
7. When it shows **Running**, open it and go to the **Networking** tab:
   - **Attach static IP** → create one named `wakeradio-ip` and attach it. Write the address down. (Free while attached to a running instance.)
   - Under **IPv4 Firewall** → **Add rule** → Application **HTTPS** (TCP 443) → **Create**. HTTP (80) should already be there; keep it, since Let's Encrypt uses it to issue the certificate.
   - Optional: edit the **SSH** rule → **Restrict to IP address** → your home IP, and keep **Allow Lightsail browser SSH/RDP** checked so the browser button still works.
8. Give the launch script about 3 minutes to finish. To check, go to the **Connect** tab → **Connect using SSH**, and run:
   ```
   tail -3 /var/log/wakeradio-launch.log
   ```
   The last line should say `Wake Radio files installed`.

### 3. GoDaddy DNS (about 2 minutes, then a short wait)

1. GoDaddy → **My Products** → joezambon.com → **DNS** (Manage DNS).
2. **Add New Record**: Type **A**, Name `wakeradio`, Value = the static IP from step 2, TTL **1/2 hour** → **Save**.
3. Wait until it resolves. On the Windows PC:
   ```
   nslookup wakeradio.joezambon.com 8.8.8.8
   ```
   It should return the static IP. This usually takes a few minutes, occasionally longer.

### 4. Start the server (about 3 minutes)

In the Lightsail browser SSH window:

```
sudo /opt/wakeradio/setup.sh
```

Press Enter to accept the site address and your Google account, then paste
the Client ID and Client secret from step 1 (the secret doesn't echo). It
starts the containers, replaces Rdio Scanner's default admin password with a
random one, creates the upload key, and ends with a box like this:

```
 SDRTrunk > Streaming > Rdio Scanner:
   Host     https://wakeradio.joezambon.com
   API Key  8c1e...-....-....-............
   System   1
```

Keep that window open for step 5. Then open https://wakeradio.joezambon.com
in a browser. You should get Google's sign-in, then the scanner page (empty
until calls arrive). If the browser says the certificate isn't valid, wait a
minute and reload; Caddy gets it on the first visit after DNS is right.

### 5. SDRTrunk streaming (about 10 minutes)

1. In SDRTrunk: **View → Playlist Editor → Streaming** tab → **New** → **Rdio Scanner**.
2. Fill in:
   - Name: `wakeradio` (lowercase; the PowerShell script looks for this name)
   - Host: `https://wakeradio.joezambon.com`
   - API Key: from the box in step 4
   - System ID: `1`
   - Enabled: checked
3. **Save**. Don't rely on the **Test** button: Rdio 6.6.3 predates SDRTrunk's test message and always answers `Incomplete call data: no talkgroup`, even when everything is right. Real calls are the test.
4. Now assign every talkgroup to the stream. **Quit SDRTrunk completely** (File → Exit), then in PowerShell, from wherever you saved the repo's `windows` folder:
   ```
   powershell -ExecutionPolicy Bypass -File .\Add-RdioStreamToAliases.ps1 -WhatIf
   powershell -ExecutionPolicy Bypass -File .\Add-RdioStreamToAliases.ps1
   ```
   The first line only reports; the second makes the change and saves a backup of `default.xml` next to it. Expect about 326 aliases tagged.
5. Start SDRTrunk. In the Streaming tab the `wakeradio` row should show **Connected**, and the **Streamed** count climbs as calls finish. On the website, talkgroups appear by themselves as each one is first heard (Rdio's auto-populate), labeled with your SDRTrunk alias names.

To stream only some talkgroups instead, skip the script and add the stream
to those aliases by hand: Playlist Editor → Aliases → select an alias → in its
streaming section, check `wakeradio` → Save.

### 6. Add listeners

In the Lightsail SSH window:

```
sudo wakeradio-users add friend@gmail.com another@gmail.com
sudo wakeradio-users list
sudo wakeradio-users remove friend@gmail.com
sudo wakeradio-users log          # recent sign-ins and refusals
```

Send them the link. Anyone not on the list gets Google's sign-in and then a
"403 Forbidden" page. Removing someone cuts them off immediately, including
a live feed they have open. Sign-ins last 30 days before Google asks again.

Any Google account works, including Google Workspace accounts and Google
accounts made with a non-Gmail address. Use the exact address they sign in
with.

---

## Using it

- Press **LIVE FEED** to start listening. Browsers won't play audio until the page has been clicked once.
- **SELECT TG** picks which talkgroups to hear. **HOLD SYS / HOLD TG** locks onto one.
- **SEARCH CALL** plays back anything from the last 30 days.
- On a phone, leave the tab in front with the screen on; mobile browsers pause background tabs.

## Running it

| Task | Command (Lightsail SSH) |
|---|---|
| Status | `cd /opt/wakeradio && sudo docker compose ps` |
| Logs | `sudo docker compose -f /opt/wakeradio/docker-compose.yml logs --tail 50` |
| Restart everything | `cd /opt/wakeradio && sudo docker compose restart` |
| Show the SDRTrunk key | `sudo /opt/wakeradio/rdio-admin.py show-key` |
| Replace the SDRTrunk key | `sudo /opt/wakeradio/rdio-admin.py new-key`, then paste it into SDRTrunk |
| Rdio admin password | `sudo grep RDIO_ADMIN_PASSWORD /opt/wakeradio/.env` |
| Disk space | `df -h /` |

- **Rdio admin page** (`/admin`): only your Google account can open it, and it also asks for the Rdio admin password above. You rarely need it: talkgroups fill themselves in. Use it to rename talkgroups, change groups or tags, or change how long audio is kept (Options → Prune Days, set to 30).
- **Updates**: Ubuntu security updates install automatically. `cd /opt/wakeradio && sudo docker compose pull && sudo docker compose up -d` picks up new Caddy 2.x releases. oauth2-proxy is pinned to v7.15.4; to update it, change the version in `docker-compose.yml` and run the same command. Rdio Scanner is pinned and never changes (see below).
- **Backups**: Lightsail → instance → **Snapshots** → enable **Automatic snapshots** (about $0.05 per GB-month). Everything that matters (settings, audio, allowlist, `.env`) lives in `/opt/wakeradio`.
- **Cost**: $7/month for the instance. The static IP is free while attached. Data transfer is far under the 2 TB included.
- **Storage**: 30 days of P25 audio for a system this busy should be a few GB, well within 40 GB. Check `df -h /` after the first month.

## Security model

- Every page, the live feed and the call search require a signed-in Google account on the allowlist. That check happens in oauth2-proxy before anything reaches Rdio Scanner.
- The only unauthenticated paths are `/api/call-upload`, which Rdio accepts only with the upload key (uploads with a wrong key are refused), and `/privacy`, a static page Caddy serves itself.
- `/admin` requires your Google account **and** the Rdio admin password.
- Rdio Scanner is only reachable through Caddy. Its own port is bound to the server's loopback address.
- Secrets live in `/opt/wakeradio/.env` (root-only), never in this repo.

## Why Rdio Scanner 6.6.3

On January 3, 2026, Rdio Scanner's author (Saubeo Solutions) made the
WebSocket API in new releases proprietary. That's the connection the web
player uses for the live feed, and self-hosting it now needs a written
license ([API_ACCESS_POLICY.md](https://github.com/chuot/rdio-scanner/blob/main/API_ACCESS_POLICY.md)).
Version 6.6.3 (November 2022) was released entirely under the GPL before
that change, so it can be self-hosted freely. It is pinned by image digest
in `docker-compose.yml`.

It's unmaintained, so it is kept behind the Google sign-in and never
exposed on its own. If Saubeo offers reasonable terms for a small private
feed (rdio-scanner@saubeo.solutions), switching is a one-line image change
plus their license key.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Browser: certificate error or "can't connect" | DNS not pointing at the static IP yet (step 3), or port 443 missing from the Lightsail firewall (step 2.7). Then `sudo docker compose -f /opt/wakeradio/docker-compose.yml logs caddy`. |
| Google: `Error 400: redirect_uri_mismatch` | The redirect URI in step 1.6 must be exactly `https://wakeradio.joezambon.com/oauth2/callback`. |
| Google: "Access blocked: app is in testing" | Step 1.5: the app must be **In production**. |
| "403 Forbidden" after signing in | That Google account isn't on the list. `sudo wakeradio-users list` / `add`. |
| SDRTrunk stream shows errors, nothing arrives | Check Host has `https://` and no trailing path, the API key matches `show-key`, and System ID is `1`. SDRTrunk's log (`SDRTrunk\logs\sdrtrunk_app.log`) shows Rdio's exact reply. |
| Stream connected but Streamed count stays 0 | Aliases aren't assigned to the stream. Re-run the PowerShell script with SDRTrunk closed. |
| Page loads but no sound | Click the page once, press **LIVE FEED**, and check **SELECT TG** has talkgroups on. |
| Launch script didn't run | `sudo cat /var/log/wakeradio-launch.log`. You can re-run it: `sudo bash lightsail-launch-script.sh` from a copy of the file. |

## Changing the server files

Edit `server/`, run `tools/build-launch-script.sh`, commit both. On an
existing server, copy the changed file into `/opt/wakeradio/` and run
`sudo docker compose up -d` there.

Tested before first deploy (September 2026): the same versions of Caddy,
oauth2-proxy and Rdio Scanner 6.6.3 run locally with this Caddyfile. Signed-out
requests redirect to Google, non-admins get 403 on `/admin` and the admin
API even with a forged email header, the live-feed WebSocket upgrades for
signed-in users, and an upload built byte-for-byte like SDRTrunk's is
accepted (wrong key refused) and auto-creates the system and talkgroups.
The Google round trip itself can only be tested once the real site is up.
