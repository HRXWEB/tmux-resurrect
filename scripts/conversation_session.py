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
    pane_label = "unknown pane"
    try:
        if len(sys.argv) != 2 or not sys.argv[1].isdigit():
            raise UnsafeSession("invalid pane PID")
        pane_pid = int(sys.argv[1])
        pane_label = f"pane PID {pane_pid}"
        socket = os.environ["TMUX"].rsplit(",", 2)[0]
        panes = tmux(socket, "list-panes", "-a", "-F",
                     "#{pane_pid}\t#{pane_id}\t#{session_name}:#{window_index}.#{pane_index}").splitlines()
        matches = [fields for row in panes if len(fields := row.split("\t", 2)) == 3
                   and fields[0] == str(pane_pid)]
        if len(matches) != 1:
            raise UnsafeSession("pane ownership is ambiguous")
        _, pane, location = matches[0]
        pane_label = f"{location} ({pane})"
        processes = process_snapshot()
        name, owner = foreground_agent(pane_pid, processes, PROVIDERS)
        raw = tmux(socket, "show-options", "-pqv", "-t", pane, OPTION)
        if not raw:
            hint = getattr(PROVIDERS[name], "MISSING_RECORD_HINT", "")
            raise UnsafeSession("waiting for this process's recording hook"
                                + ("; " + hint if hint else ""))
        record = json.loads(raw)
        if (record.get("version") != 1 or record.get("provider") != name
                or record.get("paneId") != pane or record.get("panePID") != pane_pid):
            raise UnsafeSession("stale pane recording")
        try:
            record = select_record(pane_pid, processes, [record], name)
        except UnsafeSession as error:
            hint = getattr(PROVIDERS[name], "MISSING_RECORD_HINT", "")
            if hint and str(error).startswith("no " + name + " hook record"):
                raise UnsafeSession(str(error) + "; " + hint) from None
            raise
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
        print(f"tmux-resurrect conversation: {pane_label}: save deferred: {message}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
