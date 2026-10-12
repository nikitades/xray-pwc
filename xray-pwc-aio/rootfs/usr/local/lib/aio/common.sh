# Sourced by the service scripts. State lives in /app/state, written by the
# join token on the first start and by Ansible after that; the code of the
# services is the release in /app/current.
STATE=/app/state
CURRENT=/app/current
set -a
# shellcheck source=/dev/null
[[ -f $STATE/aio.env ]] && . "$STATE/aio.env"
set +a

# say LEVEL event [key=value...]: one log line of this service.
say() { echo "$*"; }

# quoted "text": a logfmt value.
quoted() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; printf '"%s"' "$s"; }

# has_links FILE: the file has at least one line that is not blank or a comment.
has_links() { [[ -f $1 ]] && grep -qvE '^[[:space:]]*(#|$)' "$1"; }

# idle REASON: nothing to run; s6 starts the service again after a while, and
# the reason is logged once per run.
idle() {
	say INFO idle reason="$(quoted "$1")"
	exec sleep "${2:-300}"
}

# release_bin NAME: a binary of the current release, else the image's own.
release_bin() {
	if [[ -x $CURRENT/bin/$1 ]]; then
		echo "$CURRENT/bin/$1"
	elif command -v "$1" >/dev/null; then
		command -v "$1"
	fi
}
