#!/usr/bin/env bash
# Manage who can sign in to Wake Radio (Google accounts).
#
#   sudo /opt/wakeradio/users.sh list
#   sudo /opt/wakeradio/users.sh add someone@gmail.com [another@gmail.com ...]
#   sudo /opt/wakeradio/users.sh remove someone@gmail.com
#   sudo /opt/wakeradio/users.sh log        # recent sign-ins
#
# The same thing is available in a browser at https://<your site>/users
set -euo pipefail

DIR=/opt/wakeradio
EMAILS="$DIR/auth/allowed-emails.txt"
cd "$DIR"

need_root() { [[ $EUID -eq 0 ]] || { echo "Run with sudo" >&2; exit 1; }; }
lower() { tr '[:upper:]' '[:lower:]' <<<"$1"; }
admin() { grep -E '^ADMIN_EMAIL=' .env | cut -d= -f2-; }

case "${1:-}" in
list)
	sort -u "$EMAILS"
	;;
add)
	need_root; shift
	[[ $# -gt 0 ]] || { echo "Usage: $0 add email@example.com" >&2; exit 1; }
	for e in "$@"; do
		e=$(lower "$e")
		[[ $e == *@*.* ]] || { echo "Skipping '$e' (not an email address)"; continue; }
		if grep -qxF "$e" "$EMAILS"; then echo "$e is already allowed"
		else echo "$e" >>"$EMAILS"; echo "Added $e"; fi
	done
	# oauth2-proxy notices the change by itself.
	echo "They can sign in now at https://$(grep -E '^DOMAIN=' .env | cut -d= -f2-)"
	;;
remove)
	need_root; shift
	[[ $# -gt 0 ]] || { echo "Usage: $0 remove email@example.com" >&2; exit 1; }
	for e in "$@"; do
		e=$(lower "$e")
		if [[ $e == "$(admin)" ]]; then echo "Not removing the admin account ($e)"; continue; fi
		if grep -qxF "$e" "$EMAILS"; then
			grep -vxF "$e" "$EMAILS" >"$EMAILS.tmp" || true
			cat "$EMAILS.tmp" >"$EMAILS" && rm -f "$EMAILS.tmp"
			echo "Removed $e"
		else
			echo "$e was not on the list"
		fi
	done
	# oauth2-proxy blocks their next request by itself; a forced Caddy reload
	# also drops any live feed they have open (allowed listeners reconnect).
	docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile \
		--adapter caddyfile --force --address 172.29.0.2:2019 >/dev/null 2>&1 ||
		docker compose restart rdio-scanner >/dev/null
	echo "Done. Anyone removed is cut off now."
	;;
log)
	cat "$DIR"/logs/oauth2-proxy*.log 2>/dev/null |
		grep -E 'AuthSuccess|AuthFailure' | tail -40
	;;
*)
	sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
	exit 1
	;;
esac
