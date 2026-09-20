# Restore Codex conversations in tmux

This fork adds an opt-in `codex` save strategy and a small, independent Codex
hook. It works on the machine running tmux and Codex, regardless of whether you
view it through a local terminal, SSH, or a cmux ssh-tmux mirror. It does not
require cmux, its environment variables, a socket relay, or a cmux session store.

The hook associates a native foreground Codex process with its **tmux pane**.
A snapshot contains an exact command such as `codex resume UUID --cd '/project'`.
Conversation contents remain in Codex's own storage; this plugin does not back
them up or move them between machines.

## Install on each machine running Codex

Requirements: macOS or Linux, tmux 3.2+, Bash, and Python 3.9+ on PATH. Native
interactive Codex is supported. Recording was tested with Codex CLI 0.155.1.

Clone the development branch into a permanent location:

```sh
git clone --branch cmux-codex-resume https://github.com/HRXWEB/tmux-resurrect.git ~/src/tmux-resurrect-codex
python3 ~/src/tmux-resurrect-codex/scripts/codex_hooks.py install
```

The installer adds two synchronous handlers, for `SessionStart` and
`UserPromptSubmit`, to `~/.codex/hooks.json`. It preserves existing cmux and other
hooks, backs up changed files, and is idempotent. Symlinked configurations keep
their symlink. An invalid existing file is never overwritten.

For a custom Codex home, run the installer for that home:

```sh
python3 ~/src/tmux-resurrect-codex/scripts/codex_hooks.py install --codex-home /path/to/codex-home
```

`CODEX_HOME` is also honored. Keep it absolute when launching Codex. Use the same
home for installation and execution. The configured hook points at this
checkout, so rerun installation if you move the checkout.

In Codex, use `/hooks` to review and trust the two added handlers. Installing the
file does not grant trust; the installer never edits trust hashes or disables
trust checks. If you explicitly disabled hooks in Codex configuration, enable
them again. Start a new Codex process after setup and submit a test message.
See the [Codex hook documentation](https://learn.chatgpt.com/docs/hooks).

Configure tmux to load this checkout instead of upstream resurrect:

```tmux
set -g @resurrect-save-command-strategy 'codex'
set -g @resurrect-processes '"~codex resume"'
run-shell ~/src/tmux-resurrect-codex/resurrect.tmux
```

Load exactly one copy of resurrect: remove the old upstream TPM entry when
switching to this manual loader. If you already set `@resurrect-processes`, add
`"~codex resume"` to that existing value instead of replacing your other entries.
The example adds only Codex to resurrect's built-in default restore list.

The three lines have separate roles:

- `@resurrect-save-command-strategy 'codex'` selects how commands are captured
  **when saving**. For a verified Codex pane, it writes the exact resume command;
  ordinary panes use upstream process capture. It does not change which saved
  commands are allowed to run during restore.
- `@resurrect-processes '"~codex resume"'` adds that command to the allowlist
  **when restoring**. Without a matching rule (or an existing restore-all setting),
  the command can be saved but will not be launched on restore. The `~` means
  match anywhere in the saved command, including after an `env CODEX_HOME=...`
  prefix. The inner quotes keep `codex resume` together as one matching rule.
- `run-shell .../resurrect.tmux` loads this fork and its save/restore key bindings;
  it does not install the Codex hooks. Run the hook installer above separately.

Do not replace the exact command with an inline `codex->codex resume --last`
rule: each pane must retain its own saved session ID.

No configuration changes are made just by cloning the repository or running its
tests. The strategy is opt-in; upstream `ps` behavior remains the default.

## Recording and saving

The hook uses `TMUX` and `TMUX_PANE`, validates the server/pane and foreground
Codex ancestry, and atomically writes one JSON value to the pane option
`@resurrect-codex-session`. It records the UUID, PID/start time, cwd and supported
launch options. It does not store prompts or conversation contents.

To prevent same-process subagent events from replacing the main conversation,
it reads only the bounded first metadata line of the hook-provided transcript.
The ID must match and the source must identify a root CLI/TUI session within
that Codex home's session directories. It does not choose the latest transcript
in a directory. Unknown metadata formats defer recording/saving rather than
guessing. A later valid hook can recover an unverified binding.

Both manual saves (`prefix + Ctrl-s`) and continuum use this strategy. Saving
checks that the record still belongs to the current foreground process. Two
panes can share a cwd without sharing a conversation. PID reuse, a different
foreground process, nested Codex executables and known subagent sessions cannot
supply another pane's identity.

The read-only inspection command is:

```sh
python3 ~/src/tmux-resurrect-codex/scripts/codex_session.py <pane-pid>
```

Run it with `TMUX` pointing at the server being inspected. Get the pane's root
PID with `tmux display-message -p -t <pane> '#{pane_pid}'`.
Exit 0 prints the proposed resume command, 2 means an ordinary non-Codex pane,
and 1 means the Codex identity cannot yet be saved. The output includes local
paths and launch configuration; treat it like the snapshot itself.

## Restore and save without a new prompt

The full resume command is already in the snapshot. Restore needs neither a
running GUI nor a new hook event to launch the saved conversation. Codex's
session files, the project directory and the executable must still exist on
that machine. `SessionStart`/`UserPromptSubmit` subsequently bind the new process.

**If any foreground Codex pane is unverified, the entire new save is deferred.**
The incomplete staged snapshot is discarded and the previous `last` snapshot
is retained. This is important when hooks have not run yet after restore: an
automatic save must never replace a known resume command with an empty command
or a bare `codex` launch. Repeated restarts can still restore the last valid
snapshot, even if no prompt has been submitted since the previous restore.

The tradeoff is explicit: other newly changed panes/layouts are not saved while
a Codex pane is unverified. Review `/hooks`, submit a prompt, or exit the
unrecorded Codex and retry. Saves report a nonzero status and a diagnostic;
interactive saves also show a tmux message. The plugin does not silently fall
back to old conversation IDs after a failed `/new` or `/resume` capture.

Successful Codex-mode snapshots use unique filenames and staged publication, so
rapid saves in the same second cannot delete the file referenced by `last`.

## Preserved and unsupported launch forms

Preserved: exact UUID, cwd, supported explicit model/profile/sandbox/approval,
search, feature, additional-directory and config flags, plus an absolute captured
`CODEX_HOME`. Arguments are shell-quoted. Other environment variables and
credentials are not copied. Initial prompts and old resume selectors are omitted.
Restore resolves the installed `codex` on PATH on the restoring machine.

Unknown options, attached short-option values (`-mfoo`), custom/node launchers,
inline hook overrides, noninteractive commands, fork launches, image/worktree
flags, remote app-server sessions and multiline launch data defer saving.
This first version intentionally supports a narrow, verified interactive path.
Shell config aliases/functions that rewrite `codex` during restore remain the
user's responsibility, as with upstream process restoration.

## tmux-continuum

Continuum needs no source changes. Load this fork before continuum, retain your
existing save interval and `@continuum-restore 'on'` configuration, and keep
continuum last among plugins that modify the status line.

Its periodic saving still depends on status-line refresh. Control-mode-only
clients such as ssh-tmux may not drive that scheduling; verify snapshot times
advance in your setup. This plugin fixes recording and snapshot behavior, not
continuum's timer. Automatic restore runs at tmux server startup, not every
attach. See [continuum's documentation](https://github.com/tmux-plugins/tmux-continuum).

## Uninstall the recording hooks

```sh
python3 ~/src/tmux-resurrect-codex/scripts/codex_hooks.py uninstall
```

Use the same `--codex-home` if you installed into a custom home. This removes only
this plugin's handlers. Switch tmux's save strategy back to `ps` when disabling
recording. Existing Codex conversations and saved snapshots are not deleted.

## Development checks

```sh
python3 -m unittest discover -s tests -p 'test_codex*.py' -v
```

Integration tests compile a tiny native Codex fixture with `cc`, invoke the real
recording hook through its child process, and exercise actual tmux save/restore
scripts on private sockets. Temporary Codex metadata is synthetic; the tests do
not contact a model or require cmux. Coverage includes same-directory panes,
subagent and unrelated-process rejection, conversation switches, stale process
identity, preserved homes/options, installer preservation and deferred saves.
GitHub Actions runs the same suite on macOS and Linux.

## Live verification (September 20, 2026)

On macOS with native Codex CLI 0.155.1, a private tmux server and isolated
CODEX_HOME were started with every CMUX variable removed. Only this plugin's
recording hooks were installed in that home. A first prompt produced a verified
pane record and an exact resume snapshot.

Two tmux server restarts restored the same conversation and the earlier test
exchange. Between the restarts, no new prompt was submitted: no fresh hook record
was observed, the attempted save returned 1, and the prior snapshot/link remained
unchanged. After the second restore, a prompt recalled the original marker,
updated the binding to the new PID, and enabled another successful save.

The isolated test invocation bypassed hook trust only for its reviewed test
hooks; production installation still requires /hooks review. Production Codex
configuration, the installed tmux plugin and cmux source were not changed.
