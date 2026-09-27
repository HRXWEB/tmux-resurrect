#!/usr/bin/env python3
"""Print a verified resume command, 2 for ordinary panes, 1 to defer save."""
import json
import os
import sys

from conversation_common import (NotAgent, UnsafeSession, foreground_agent,
                                 process_snapshot, select_record, tmux)
from conversation_hook import OPTION
from conversation_providers import PROVIDERS


def main():
    try:
        if len(sys.argv) != 2 or not sys.argv[1].isdigit():
            raise UnsafeSession("invalid pane PID")
        pane_pid = int(sys.argv[1])
        processes = process_snapshot()
        name, owner = foreground_agent(pane_pid, processes, PROVIDERS)
        socket = os.environ["TMUX"].rsplit(",", 2)[0]
        panes = tmux(socket, "list-panes", "-a", "-F", "#{pane_pid} #{pane_id}").splitlines()
        matches = {row.split()[1] for row in panes if row.split()[0] == str(pane_pid)}
        if len(matches) != 1:
            raise UnsafeSession("pane ownership is ambiguous")
        pane = matches.pop()
        raw = tmux(socket, "show-options", "-pqv", "-t", pane, OPTION)
        if not raw:
            raise UnsafeSession("waiting for this process's recording hook")
        record = json.loads(raw)
        if (record.get("version") != 1 or record.get("provider") != name
                or record.get("paneId") != pane or record.get("panePID") != pane_pid):
            raise UnsafeSession("stale pane recording")
        record = select_record(pane_pid, processes, [record], name)
        command = PROVIDERS[name].resume_command(record)
        if (foreground_agent(pane_pid, process_snapshot(), PROVIDERS) != (name, owner)
                or tmux(socket, "show-options", "-pqv", "-t", pane, OPTION) != raw):
            raise UnsafeSession("conversation changed during save")
        print(command)
        return 0
    except NotAgent:
        return 2
    except Exception as error:
        message = str(error) if isinstance(error, UnsafeSession) else "recording unavailable"
        print("tmux-resurrect conversation: save deferred: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
