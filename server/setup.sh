#!/usr/bin/env bash
# First-time setup (and safe re-run) for Wake Radio on the Lightsail server.
#
#   sudo /opt/wakeradio/setup.sh
#
# Asks for the Google sign-in credentials, writes .env, starts the
# containers, configures Rdio Scanner, and prints what SDRTrunk needs.
#
# Once configured, re-running it only applies updates (no questions).
#   sudo /opt/wakeradio/setup.sh --reconfigure   to change the answers
set -euo pipefail

DIR=/opt/wakeradio
ENV_FILE="$DIR/.env"
EMAILS="$DIR/auth/allowed-emails.txt"

[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo $0" >&2; exit 1; }
cd "$DIR"

get() { [[ -f $ENV_FILE ]] && grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }

ask() {  # ask VAR "Prompt" default [secret]
	local var=$1 prompt=$2 def=$3 secret=${4:-} val
	if [[ -n ${!var:-} ]]; then return; fi
	if [[ -n $secret ]]; then
		read -r -s -p "$prompt${def:+ [keep current]}: " val; echo
	else
		read -r -p "$prompt${def:+ [$def]}: " val
	fi
	printf -v "$var" '%s' "${val:-$def}"
}

DOMAIN=${DOMAIN:-}; ADMIN_EMAIL=${ADMIN_EMAIL:-}
GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-}; GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET:-}

# Already configured? Reuse the saved answers unless asked to reconfigure.
if [[ ${1:-} != --reconfigure ]]; then
	for v in DOMAIN ADMIN_EMAIL GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET; do
		[[ -n ${!v} ]] || printf -v "$v" '%s' "$(get "$v")"
	done
fi

cur=$(get DOMAIN); ask DOMAIN "Site address" "${cur:-wakeradio.joezambon.com}"
cur=$(get ADMIN_EMAIL); ask ADMIN_EMAIL "Your Google account (the admin)" "${cur:-joseph.zambon@gmail.com}"
ask GOOGLE_CLIENT_ID "Google OAuth Client ID" "$(get GOOGLE_CLIENT_ID)"
ask GOOGLE_CLIENT_SECRET "Google OAuth Client secret (hidden)" "$(get GOOGLE_CLIENT_SECRET)" secret

ADMIN_EMAIL=$(tr '[:upper:]' '[:lower:]' <<<"$ADMIN_EMAIL")
for v in DOMAIN ADMIN_EMAIL GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET; do
	[[ -n ${!v} ]] || { echo "Missing $v" >&2; exit 1; }
done
[[ $GOOGLE_CLIENT_ID == *.apps.googleusercontent.com ]] ||
	echo "Warning: that Client ID doesn't end in .apps.googleusercontent.com; double-check it." >&2

COOKIE_SECRET=$(get COOKIE_SECRET)
[[ -n $COOKIE_SECRET ]] || COOKIE_SECRET=$(openssl rand -base64 32 | tr -- '+/' '-_')

# Keep anything rdio-admin.py already stored (admin password, upload key).
RDIO_ADMIN_PASSWORD=$(get RDIO_ADMIN_PASSWORD)
RDIO_UPLOAD_KEY=$(get RDIO_UPLOAD_KEY)

umask 077
cat >"$ENV_FILE" <<EOF
DOMAIN=$DOMAIN
ADMIN_EMAIL=$ADMIN_EMAIL
GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET=$GOOGLE_CLIENT_SECRET
COOKIE_SECRET=$COOKIE_SECRET
EOF
[[ -n $RDIO_ADMIN_PASSWORD ]] && echo "RDIO_ADMIN_PASSWORD=$RDIO_ADMIN_PASSWORD" >>"$ENV_FILE"
[[ -n $RDIO_UPLOAD_KEY ]] && echo "RDIO_UPLOAD_KEY=$RDIO_UPLOAD_KEY" >>"$ENV_FILE"
umask 022

mkdir -p auth logs data/rdio data/caddy data/caddy-config
touch "$EMAILS"
grep -qxF "$ADMIN_EMAIL" "$EMAILS" || echo "$ADMIN_EMAIL" >>"$EMAILS"
chmod 644 "$EMAILS"
# oauth2-proxy and the /users page run as uid 65532; Rdio as 1000.
chown -R 65532:65532 auth logs
chown -R 1000:1000 data/rdio

# Let's Encrypt fails if DNS doesn't point here yet, so check first.
MY_IP=$(curl -fsS --max-time 5 https://checkip.amazonaws.com 2>/dev/null || true)
DNS_IP=$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}' || true)
if [[ -n $MY_IP && $MY_IP != "$DNS_IP" ]]; then
	echo
	echo "Heads up: $DOMAIN resolves to '${DNS_IP:-nothing}', but this server is $MY_IP."
	echo "Add/fix the GoDaddy A record, wait a few minutes, then re-run this script."
	echo "Starting anyway; Caddy will keep retrying the certificate."
	echo
fi

docker compose pull -q
docker compose up -d --remove-orphans

# `up -d` leaves running containers alone when only a mounted file changed,
# and a single-file mount keeps showing the old copy (even across a restart)
# once the installer replaces the file. Recreate the container whose file
# changed since the last run so it mounts the new one.
for pair in caddy:Caddyfile users-admin:users-admin/app.py; do
	svc=${pair%%:*}; file=${pair#*:}
	stamp="data/.applied-$svc.sha256"
	sum=$(sha256sum "$file" | cut -d' ' -f1)
	if [[ ! -f $stamp || $(cat "$stamp") != "$sum" ]]; then
		docker compose up -d --no-deps --force-recreate "$svc" >/dev/null 2>&1 &&
			echo "Applied new $file"
		echo "$sum" >"$stamp"
	fi
done

python3 "$DIR/rdio-admin.py" bootstrap

KEY=$(get RDIO_UPLOAD_KEY)
cat <<EOF

==================================================================
 Wake Radio is up:  https://$DOMAIN
 Signed-in Google accounts allowed:  $(paste -sd, "$EMAILS" | sed 's/,/, /g')

 SDRTrunk > Streaming > Rdio Scanner:
   Host     https://$DOMAIN
   API Key  $KEY
   System   1

 Manage listeners:  https://$DOMAIN/users.html
 Rdio admin page:   https://$DOMAIN/admin
   password:        sudo grep RDIO_ADMIN_PASSWORD /opt/wakeradio/.env
==================================================================
EOF
