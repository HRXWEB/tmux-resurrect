# Resume Codex conversations recorded by cmux

This fork adds the opt-in `cmux` save-command strategy. It reads the session IDs
already recorded by `cmux hooks setup --agent codex`, and saves commands such as:

```sh
codex resume 01950000-0000-7000-8000-000000000001 --model example --cd '/my project'
```

The pane is a **tmux pane**, not a cmux surface. Matching uses the foreground
Codex process descended from that pane, plus its process start time. Two panes
may have the same directory and even the same inherited cmux surface ID without
being assigned the same conversation. Codex child agents cannot replace their
parent's conversation.

**Development status:** the save/restore consumer works with verified hook
records. The live checks below found gaps in unconfigured `ssh-tmux` routing
and in capture immediately after resume. Do not enable unattended continuum
saves until those checks pass in your launch environment.

## Requirements and scope

- Python 3.9+ on PATH when saving; Bash and tmux as required by upstream.
- macOS or Linux. Process inspection uses BSD/GNU `ps`.
- A native interactive `codex` process, with an existing cmux hook record for
  its PID and `pidStartSeconds`. Old cmux records lacking process-generation
  identity are skipped.
- The hook store and tmux processes must belong to the **same machine and user**.
  The default store is `~/.cmuxterm/codex-hook-sessions.json`; a save process can
  override its directory with `CMUX_AGENT_HOOK_STATE_DIR`.
- The original Codex session files and project directory must remain available
  when restoring. This plugin does not back up conversation contents.

Installing cmux hooks alone does not guarantee a record exists for a Codex
process inside tmux. cmux's current hook dispatch requires its routing context.
Codex started outside that context may have no record, including a process
started before hooks were installed. Attaching later through `cmux ssh-tmux`
does not establish that the process has emitted a usable hook record.

In the running Codex conversation, `/hooks` shows whether the configured hooks
are loaded and trusted. Current Codex versions skip new or changed non-managed
hooks until their exact definitions are trusted. A hooks file on disk and
`features.hooks = true` alone do not prove that callbacks ran. See the
[official hook trust documentation](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks).

This fork consumes existing records; it does not install replacement hooks,
invent cmux surface IDs, or discover conversations by choosing the latest one
in a directory. A Mac-side record cannot be matched to a remote Linux PID.
For `ssh-tmux`, verify that records are present on the host running tmux before
enabling automatic saves. A cmux record-capture/routing gap is a separate issue
from this plugin's save/restore integration.

## Configuration

For a manual checkout of this development branch:

```sh
git clone --branch cmux-codex-resume https://github.com/HRXWEB/tmux-resurrect.git ~/src/tmux-resurrect-cmux
```

Use that checkout in your tmux configuration in place of the original resurrect
plugin loader:

```tmux
set -g @resurrect-save-command-strategy 'cmux'

# Example: keep your other programs here, and match the generated resume command.
set -g @resurrect-processes 'yazi ssh claude lazygit "~codex resume"'

run-shell ~/src/tmux-resurrect-cmux/resurrect.tmux
```

Load exactly one copy of resurrect. The existing TPM entry for
`tmux-plugins/tmux-resurrect` must not also load when using the manual checkout.
Your existing non-Codex restore list can be retained; replace a plain `codex`
entry with `"~codex resume"`. The substring match also supports a generated
`env CODEX_HOME=... codex resume ...` command.

Do not configure a conflicting inline rule such as `codex->codex resume --last`:
that would replace the exact saved session ID during restore.

Both ordinary saves (`prefix + Ctrl-s`) and continuum saves use this strategy.
The saved snapshot contains the full resume command, so restoration does not
need the cmux app, the hook store, or Python. **Subsequent saves do need a fresh
record matching the newly launched Codex process.**

## Verify before relying on a snapshot

Inside a tmux pane whose Codex you intend to save, obtain its pane PID from
another terminal or a tmux command prompt. Run:

```sh
python3 ~/src/tmux-resurrect-cmux/scripts/cmux_codex.py <pane-pid>
```

The helper is read-only. Exit status 0 prints the exact proposed resume command;
2 means this pane has no Codex and uses upstream's `ps` strategy; 1 reports an
unverified/unsupported Codex and saves no command. For a tmux command prompt,
`display-message -p '#{pane_pid}'` identifies the selected pane's root process.

Save with `prefix + Ctrl-s` while Codex is still running, then inspect the saved
command for that pane. It should contain its specific `codex resume UUID`.
Missing records produce a diagnostic on stderr and an empty process command;
layout restoration still works, but the conversation needs manual resumption.
Diagnostics deliberately omit prompts, session IDs, and launch configuration.

No changes to existing tmux sessions or the installed plugin are made by the
test suite. Do not kill a working server merely to test this configuration.

## What is preserved

- Exact conversation UUID and its hook-observed working directory.
- Supported explicit model, profile, sandbox, approval, search, feature, config,
  additional-directory, and terminal-display options.
- A captured absolute `CODEX_HOME`, when present. Other environment variables
  and credentials are not copied into the tmux snapshot.

Initial prompts, old `resume` selectors, and `--last`/`--all` are removed. The
current `codex` on PATH is used instead of a potentially temporary cmux shim.
This continues to use persistent hooks configured by `cmux hooks setup`.

The first version skips unknown options, attached short-option values (`-mfoo`),
custom/node launchers, inline hook overrides, noninteractive commands, fork
launches, image/worktree flags, remote app-server sessions, and multiline launch
data. It also skips ambiguous/background-only Codex processes, stale PIDs,
missing launch capture, corrupt stores, or records marked non-restorable by
cmux. It never falls back to a new Codex conversation for these cases.

## tmux-continuum

Continuum needs no source changes. It invokes the same resurrect scripts:

```tmux
set -g @continuum-restore 'on'
```

Ensure the fork's `resurrect.tmux` has registered its script paths before
continuum loads, and keep continuum last among plugins that modify the status
line. Its periodic saving depends on `status-right` updates. When using only
`ssh-tmux` control-mode clients, check that snapshots actually advance; this
integration does not add an independent timer or claim to fix that scheduling
limitation. Automatic restore runs on tmux server startup, not every attach.

See [continuum's documentation](https://github.com/tmux-plugins/tmux-continuum).

## Development checks

### Live check: September 20, 2026

Tested with cmux 0.64.25 (106) and Codex CLI 0.155.1, using existing persistent
hooks and a disposable test directory. No hook definitions were changed.

| Scenario | Observed result |
| --- | --- |
| New cmux terminal, private tmux server, first Codex prompt | A hook record matched the live Codex PID and process start time. |
| Save with this strategy, restart that private server, restore | The exact conversation ID and earlier test exchange returned. |
| Submit another prompt after restore, save again | The record updated to the new PID; saving succeeded again. |
| Restore, but submit no new prompt | No record matched the new PID during the check. |
| New session created over SSH, attached with `cmux ssh-tmux` on the same Mac | The Codex process lacked `CMUX_SURFACE_ID`; hooks did not record it even after a prompt. |
| Relaunch only that SSH test process with its actual mirror surface/workspace IDs | Existing hooks recorded the new process after a prompt; the helper resolved its exact resume command. |

The final row is a diagnostic control, not an automatic routing fix. Hardcoding
those IDs is not a durable configuration: cmux can recreate surfaces. The hook
script's missing-surface guard and the successful control identify a routing
precondition that this save strategy cannot supply to an already running
process.

The idle-after-resume case also matters for continuum: saving before a fresh
hook record exists produces an empty command for that pane in the new snapshot.
An older snapshot can still contain the resume command, but `last` may no longer
contain it. The strategy does not substitute a stale PID record or guess which
conversation is active. Reliable unattended repeated restore/save remains an
open integration requirement.

### Automated checks

```sh
python3 -m unittest discover -s tests -p 'test_cmux*.py' -v
```

Integration tests require `tmux` and a C compiler. They create private sockets,
temporary cmux-format hook fixtures, and a small fake Codex executable that
records argv. They exercise the real upstream save/restore scripts across a
server restart without contacting an AI service. They validate consumption of
hook records, not cmux's production hook delivery into arbitrary tmux panes.
GitHub Actions runs these checks on macOS and Linux.
