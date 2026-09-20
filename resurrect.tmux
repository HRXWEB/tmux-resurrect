#!/usr/bin/env bash

CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

source "$CURRENT_DIR/scripts/variables.sh"
source "$CURRENT_DIR/scripts/helpers.sh"

set_save_bindings() {
	local key_bindings=$(get_tmux_option "$save_option" "$default_save_key")
	local key
	for key in $key_bindings; do
		tmux bind-key "$key" run-shell "$CURRENT_DIR/scripts/save.sh"
	done
}

set_restore_bindings() {
	local key_bindings=$(get_tmux_option "$restore_option" "$default_restore_key")
	local key
	for key in $key_bindings; do
		tmux bind-key "$key" run-shell "$CURRENT_DIR/scripts/restore.sh"
	done
}

set_default_strategies() {
	tmux set-option -gq "${restore_process_strategy_option}irb" "default_strategy"
	tmux set-option -gq "${restore_process_strategy_option}mosh-client" "default_strategy"
}

set_script_path_options() {
	tmux set-option -gq "$save_path_option" "$CURRENT_DIR/scripts/save.sh"
	tmux set-option -gq "$restore_path_option" "$CURRENT_DIR/scripts/restore.sh"
}

setup_codex_hooks() {
	if [ "$(get_tmux_option "$save_command_strategy_option" "$default_save_command_strategy")" != "codex" ]; then
		return 0
	fi
	local output
	if output=$(python3 "$CURRENT_DIR/scripts/codex_hook_setup.py" install 2>&1); then
		# Registration does not imply that Codex has trusted the hook definitions.
		tmux set-option -gq @resurrect-codex-hooks-status registered
		if [[ "$output" == "Resurrect Codex hooks installed."* ]]; then
			display_message "tmux-resurrect: Codex hooks registered. Review/trust them in Codex /hooks."
		fi
	else
		tmux set-option -gq @resurrect-codex-hooks-status error
		display_message "tmux-resurrect: Codex hook setup failed. Check python3 (3.9+) and hooks.json, then reload tmux."
		printf '%s\n' "$output" >&2
	fi
	# A setup failure must not prevent other tmux plugins from loading.
	return 0
}

main() {
	set_save_bindings
	set_restore_bindings
	set_default_strategies
	set_script_path_options
	setup_codex_hooks
}
main
