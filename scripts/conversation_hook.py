#!/usr/bin/env python3
"""Bind one verified CLI conversation to its foreground tmux pane."""
import json
import os
from pathlib import Path
import sys
import time

from conversation_common import (IgnoredSession, MAX_INPUT, UnsafeSession, ancestors, pane_agent,
                                 process_arguments, process_snapshot, single_line,
                                 tmux, tmux_context)
from conversation_providers import PROVIDERS

OPTION = "@resurrect-conversation"


def capture(payload, context, provider):
    socket, server, pane = context
    details = tmux(socket, "display-message", "-p", "-t", pane, "#{pid} #{pane_pid} #{pane_id}").split()
    if len(details) != 3 or details[0] != str(server) or details[2] != pane:
        raise UnsafeSession("tmux pane ownership changed")
    pane_pid = int(details[1])
    processes = process_snapshot()
    owner = pane_agent(pane_pid, processes, provider.NAME)
    lineage = [processes[pid] for pid in ancestors(os.getpid(), processes)
               if Path(processes[pid].executable).name == provider.NAME]
    if len(lineage) != 1 or lineage[0].pid != owner.pid:
        raise UnsafeSession("hook is not owned by the foreground " + provider.NAME)
    base = {"version": 1, "provider": provider.NAME, "pid": owner.pid,
            "pidStartSeconds": owner.started, "paneId": pane, "panePID": pane_pid}
    try:
        args, captured_dir = process_arguments(owner.pid, provider.ENV_NAME)
        if not args or Path(args[0]).name != provider.NAME:
            raise UnsafeSession("unsupported " + provider.NAME + " launcher")
        config_dir = captured_dir or str(Path(provider.DEFAULT_DIR).expanduser())
        if not os.path.isabs(config_dir):
            raise UnsafeSession(provider.ENV_NAME + " must be absolute")
        session = provider.root_session(payload, config_dir)
        cwd = single_line(payload["cwd"])
        if not os.path.isabs(cwd):
            raise UnsafeSession("working directory must be absolute")
        launch = {"arguments": [provider.NAME] + provider.launch_options(args[1:])}
        if captured_dir:
            launch["environment"] = {provider.ENV_NAME: single_line(captured_dir)}
        record = {**base, "sessionId": session, "updatedAt": time.time(),
                  "cwd": cwd, "launchCommand": launch}
        if pane_agent(pane_pid, process_snapshot(), provider.NAME) != owner:
            raise UnsafeSession("process changed during hook capture")
        tmux(socket, "set-option", "-p", "-t", pane, OPTION,
             json.dumps(record, separators=(",", ":")))
    except IgnoredSession:
        return
    except Exception:
        if pane_agent(pane_pid, process_snapshot(), provider.NAME) == owner:
            tmux(socket, "set-option", "-p", "-t", pane, OPTION,
                 json.dumps({**base, "updatedAt": time.time(), "isRestorable": False}))
        raise


def main():
    try:
        if len(sys.argv) < 2 or sys.argv[1] not in PROVIDERS:
            raise UnsafeSession("invalid conversation provider")
        context = tmux_context()
        if context is None:
            return 0
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise UnsafeSession("hook input is too large")
        payload = json.loads(raw)
        if isinstance(payload, dict) and payload.get("hook_event_name") in ("SessionStart", "UserPromptSubmit"):
            capture(payload, context, PROVIDERS[sys.argv[1]])
    except Exception as error:
        message = str(error) if isinstance(error, UnsafeSession) else "capture unavailable"
        print("tmux-resurrect conversation: " + message, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
