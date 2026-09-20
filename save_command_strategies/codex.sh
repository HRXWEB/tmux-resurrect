#!/usr/bin/env bash
CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if ! command -v python3 >/dev/null 2>&1; then
	printf '%s\n' 'tmux-resurrect Codex: Python 3.9+ required; save deferred' >&2
	exit 1
fi
command="$(python3 "$CURRENT_DIR/../scripts/codex_session.py" "$1")"
status=$?
case "$status" in
	0) printf '%s\n' "$command" ;;
	2) "$CURRENT_DIR/ps.sh" "$1" ;;
	*) exit 1 ;;
esac
