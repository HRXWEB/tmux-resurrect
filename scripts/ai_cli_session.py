#!/usr/bin/env python3
"""Route a tmux pane to its foreground CLI session recorder."""
import sys

from codex_common import NotCodex, UnsafeSession, pane_agent, process_snapshot
import codex_session
import claude_session


def select_agent(pane_pid, processes):
    owners = []
    uncertain = False
    for name in ("codex", "claude"):
        try:
            pane_agent(pane_pid, processes, name)
            owners.append(name)
        except NotCodex:
            pass
        except UnsafeSession:
            uncertain = True
    if len(owners) == 1:
        return owners[0]
    if owners or uncertain:
        raise UnsafeSession("no unique foreground CLI process")
    return None


def main():
    try:
        if len(sys.argv) != 2 or not sys.argv[1].isdigit():
            raise UnsafeSession("invalid pane PID")
        name = select_agent(int(sys.argv[1]), process_snapshot())
        if name == "codex":
            return codex_session.main()
        if name == "claude":
            return claude_session.main()
        return 2
    except Exception as error:
        message = str(error) if isinstance(error, UnsafeSession) else "process inspection unavailable"
        print("tmux-resurrect AI CLI: save deferred: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
