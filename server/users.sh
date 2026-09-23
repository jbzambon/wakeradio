#!/usr/bin/env bash
# Manage who can sign in to Wake Radio (Google accounts).
#
#   sudo /opt/wakeradio/users.sh list
#   sudo /opt/wakeradio/users.sh add someone@gmail.com [another@gmail.com ...]
#   sudo /opt/wakeradio/users.sh remove someone@gmail.com
#   sudo /opt/wakeradio/users.sh log        # recent sign-ins
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
	docker compose restart oauth2-proxy >/dev/null
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
	# Blocks their next page load; restarting Rdio also drops any live feed they have open.
	docker compose restart oauth2-proxy rdio-scanner >/dev/null
	echo "Done. Anyone removed is cut off now."
	;;
log)
	docker compose logs --since 168h oauth2-proxy 2>/dev/null |
		grep -E 'AuthSuccess|AuthFailure' | tail -40
	;;
*)
	sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
	exit 1
	;;
esac
