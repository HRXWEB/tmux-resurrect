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

setup_conversation_hooks() {
	if [ "$(get_tmux_option "$save_command_strategy_option" "$default_save_command_strategy")" != "conversation" ]; then
		return 0
	fi
	local provider output
	for provider in codex claude; do
		if output=$(python3 "$CURRENT_DIR/scripts/conversation_hook_setup.py" install --provider "$provider" 2>&1); then
			tmux set-option -gq "@resurrect-${provider}-hooks-status" registered
		else
			tmux set-option -gq "@resurrect-${provider}-hooks-status" error
			display_message "tmux-resurrect: ${provider} hook setup failed. Check Python and settings."
			printf '%s\n' "$output" >&2
		fi
	done
	return 0
}

main() {
	set_save_bindings
	set_restore_bindings
	set_default_strategies
	set_script_path_options
	setup_conversation_hooks
}
main
