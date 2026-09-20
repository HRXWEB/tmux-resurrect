#!/usr/bin/env python3
"""Print a verified Codex resume command, 2 for non-Codex, 1 to defer save."""
import json
import os
import sys

from codex_common import (NotCodex, UnsafeSession, pane_codex, process_snapshot,
                          resume_command, select_record)
from codex_hook import OPTION, tmux


def main():
    try:
        if len(sys.argv) != 2 or not sys.argv[1].isdigit():
            raise UnsafeSession("invalid pane PID")
        pane_pid = int(sys.argv[1])
        processes = process_snapshot()
        owner = pane_codex(pane_pid, processes)
        socket = os.environ["TMUX"].rsplit(",", 2)[0]
        panes = tmux(socket, "list-panes", "-a", "-F", "#{pane_pid} #{pane_id}").splitlines()
        matches = {row.split()[1] for row in panes if row.split()[0] == str(pane_pid)}
        if len(matches) != 1:
            raise UnsafeSession("pane ownership is ambiguous")
        pane = matches.pop()
        raw = tmux(socket, "show-options", "-pqv", "-t", pane, OPTION)
        if not raw:
            raise UnsafeSession("waiting for this Codex process's recording hook")
        record = json.loads(raw)
        if record.get("version") != 1 or record.get("paneId") != pane or record.get("panePID") != pane_pid:
            raise UnsafeSession("stale pane recording")
        record = select_record(pane_pid, processes, [record])
        command = resume_command(record)
        if (pane_codex(pane_pid, process_snapshot()) != owner
                or tmux(socket, "show-options", "-pqv", "-t", pane, OPTION) != raw):
            raise UnsafeSession("Codex changed during save")
        print(command)
        return 0
    except NotCodex:
        return 2
    except Exception as error:
        message = str(error) if isinstance(error, UnsafeSession) else "recording unavailable"
        print("tmux-resurrect Codex: save deferred: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
