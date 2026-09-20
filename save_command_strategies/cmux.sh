#!/usr/bin/env bash

CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

if ! command -v python3 >/dev/null 2>&1; then
	printf '%s\n' 'tmux-resurrect cmux: Python 3.9+ is required; skipping process save' >&2
	exit 0
fi

command="$(python3 "$CURRENT_DIR/../scripts/cmux_codex.py" "$1")"
status=$?
case "$status" in
	0) printf '%s\n' "$command" ;;
	2) "$CURRENT_DIR/ps.sh" "$1" ;;
	*) : ;; # An unverified Codex must not silently restart a new conversation.
esac
