# Sourced by the service scripts of a release, which s6 runs as abc through
# aio-run. The state lives in /app/state: written by the join token on the
# first start and by Ansible after that. The release is /app/current.
STATE=/app/state
CURRENT=/app/current
IFACE=xray-pwc-net
set -a
# shellcheck source=/dev/null
[[ -f $STATE/aio.env ]] && . "$STATE/aio.env"
# VERSION, MIN_IMAGE, ALLOY_VERSION, CADDY_VERSION, PLAYWRIGHT_VERSION.
# shellcheck source=/dev/null
. "$CURRENT/release.env"
set +a

# say LEVEL event [key=value...]: one log line of this service.
say() { echo "$*"; }

# quoted "text": a logfmt value.
quoted() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; printf '"%s"' "$s"; }

# has_links FILE: the file has at least one line that is not blank or a comment.
has_links() { [[ -f $1 ]] && grep -qvE '^[[:space:]]*(#|$)' "$1"; }

# idle REASON [SECONDS]: nothing to run; s6 starts the service again after a
# while, and the reason is logged once per run.
idle() {
	say INFO idle reason="$(quoted "$1")"
	exec sleep "${2:-300}"
}

# tool NAME: alloy or caddy of the version this release needs: Ansible's in
# /app/opt, else the image's in /opt.
tool() {
	local version dir
	case $1 in
	alloy) version=$ALLOY_VERSION ;;
	caddy) version=$CADDY_VERSION ;;
	esac
	for dir in "/app/opt/$1-$version" "/opt/$1-$version"; do
		[[ -x $dir/$1 ]] && { echo "$dir/$1"; return; }
	done
}

# browsers: Chromium for this release's Playwright: the image's, or Ansible's.
browsers() {
	if [[ $(cat /etc/aio-playwright-version 2>/dev/null) == "$PLAYWRIGHT_VERSION" ]]; then
		echo /ms-playwright
	else
		echo "/app/opt/ms-playwright-$PLAYWRIGHT_VERSION"
	fi
}
