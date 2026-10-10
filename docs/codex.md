# Codex conversation provider

The [`conversation` save strategy](conversation.md) records Codex and Claude Code
sessions through one shared tmux recording pipeline. For Codex, its provider
validates the root CLI/TUI transcript metadata under `CODEX_HOME`, rejects
subagent and noninteractive sessions, and builds `codex resume <UUID> --cd <cwd>`.

The Codex hooks are installed in `CODEX_HOME/hooks.json` for `SessionStart` and
`UserPromptSubmit`. Review and trust them in Codex `/hooks`. A registration status
of `registered` does not prove that Codex has run the hooks.
An interactive `codex resume` picker is supported once the chosen conversation's
hook runs. Codex 0.162.1 may wait for the first prompt after resuming before
recording it; see [save and restore](conversation.md#save-and-restore).
For Codex 0.157+, disable shared daemon auto-start and stop any existing daemon
before launching a new session; see [Codex shared daemon](conversation.md#codex-shared-daemon).

The provider preserves supported explicit launch flags, validates the foreground
process and PID generation, and omits prompts. Unknown or unsafe launch forms
defer saving the entire snapshot. Conversation contents remain in Codex's own
storage. See [the combined guide](conversation.md) for setup and behavior.
