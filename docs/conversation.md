# Restore Codex and Claude Code conversations

The opt-in `conversation` save strategy records the exact session ID of each
foreground Codex or Claude Code process in a tmux pane. It writes `codex resume <UUID>` or
`claude --resume <UUID>` into the tmux-resurrect snapshot. The conversations
remain in each CLI's own local storage.

Requirements: macOS or Linux, tmux 3.2+, Bash and Python 3.9+ on the tmux
host. The CLI's own conversation files must be available there at restore time.

## Configuration

Put these settings before TPM initialization:

```tmux
set -g @resurrect-save-command-strategy 'conversation'
set -g @resurrect-processes '"~codex resume" "~claude --resume"'
```

Keep any other restore-list entries you already use. The save strategy chooses
how to capture commands; the process list allows the resulting commands to run
at restore time. This is the only hook-backed conversation strategy.

Loading the plugin with `conversation` registers two hooks in the selected Codex
home's `hooks.json` and two hooks in Claude Code's `settings.json`:
`SessionStart` and `UserPromptSubmit`. The latter refreshes the binding after
an in-process conversation switch. Codex hook trust still requires review in
Codex `/hooks`. Claude's hook settings are loaded by Claude Code when a session
starts; restart already-running Claude processes after initial installation.

Custom homes are read from the tmux server's `CODEX_HOME` and
`CLAUDE_CONFIG_DIR` environment variables. Set them with `set-environment -g`
before plugin initialization. A value exported in one pane does not configure
the server's hook installer. For additional Claude homes, run:

```sh
python3 <plugin-directory>/scripts/conversation_hook_setup.py install --provider claude --config-dir /absolute/path
```

`@resurrect-codex-hooks-status` and `@resurrect-claude-hooks-status` report
`registered` or `error`. Registration does not prove a hook has fired.

### Codex shared daemon

Codex 0.157 and later can run interactive sessions through a shared app-server
daemon. Hooks launched by that daemon inherit its environment, which may contain
the tmux pane of an earlier Codex client. The daemon's hook input does not
identify the current client pane, so this strategy cannot safely save those
sessions. To use pane-specific Codex restoration, disable automatic daemon
startup in `~/.codex/config.toml`:

```toml
[features]
daemon_auto_start = false
```

This setting does not disconnect an existing daemon. After finishing any Codex
sessions using it, run `codex app-server daemon stop` and then start a new Codex
session. `codex app-server daemon version` reports a connection error when no
daemon is running.

## Save and restore

When saving, the strategy verifies the hook's tmux pane, foreground process,
PID generation, root transcript and session ID. A failed check defers the
entire snapshot and retains the previous `last` file. This protects a known
resume command if a newly restored process has not run its hook yet. After
restoring, a `SessionStart` hook can bind the new process without a user prompt.
Failure messages name the tmux pane as `session:window.pane (%pane_id)`.

Claude's `-c` / `--continue` is deliberately not used: it selects the most
recent session for a directory and can confuse two panes in the same project.
Only interactive `claude` processes with simple launch options are supported.
Preserved options: `--model`, `--permission-mode`, `--effort`, `--agent`, and
the two explicit permission bypass flags. Other flags, noninteractive commands,
worktree launches and initial prompts defer saving rather than being silently
dropped. An absolute `CLAUDE_CONFIG_DIR` is preserved. The resume command runs
in the pane's restored working directory.

The Claude transcript must remain available under
`<CLAUDE_CONFIG_DIR>/projects/<project>/<UUID>.jsonl`. Claude Code can clean
up old transcripts according to its retention setting; tmux-resurrect stores
only the ID, not a copy of the conversation.

To remove only this plugin's Claude hooks, run:

```sh
python3 <plugin-directory>/scripts/conversation_hook_setup.py uninstall --provider claude
```

Switch back to `ps` before reloading the plugin if you no longer want
conversation hooks registered. See [Codex details](codex.md) for the Codex
provider rules.
