#!/usr/bin/env python3
"""Record the foreground Claude Code conversation in its tmux pane."""
import json
import os
from pathlib import Path
import sys
import time
import uuid

from codex_common import UnsafeSession, ancestors, pane_agent, process_snapshot, single_line
from codex_session_recorder import MAX_INPUT, process_arguments, tmux, tmux_context
from claude_common import launch_options

OPTION = "@resurrect-claude-session"


def root_session(payload, config_dir):
    try:
        session = str(uuid.UUID(payload["session_id"]))
        path = Path(single_line(payload["transcript_path"])).resolve(strict=True)
        root = Path(config_dir).expanduser().resolve(strict=True) / "projects"
        if path.name != session + ".jsonl" or path.parent.parent != root:
            raise UnsafeSession("Claude transcript is not a root session")
        if not path.is_file():
            raise UnsafeSession("Claude transcript is missing")
        return session
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        raise UnsafeSession("Claude transcript identity unavailable") from None


def capture(payload, context):
    socket, server, pane = context
    details = tmux(socket, "display-message", "-p", "-t", pane, "#{pid} #{pane_pid} #{pane_id}").split()
    if len(details) != 3 or details[0] != str(server) or details[2] != pane:
        raise UnsafeSession("tmux pane ownership changed")
    pane_pid = int(details[1])
    processes = process_snapshot()
    owner = pane_agent(pane_pid, processes, "claude")
    lineage = [processes[p] for p in ancestors(os.getpid(), processes)
               if processes[p].is_claude]
    if len(lineage) != 1 or lineage[0].pid != owner.pid:
        raise UnsafeSession("hook is not owned by foreground Claude")
    base = {"version": 1, "pid": owner.pid, "pidStartSeconds": owner.started,
            "paneId": pane, "panePID": pane_pid}
    try:
        args, captured_dir = process_arguments(owner.pid, "CLAUDE_CONFIG_DIR")
        if not args or Path(args[0]).name != "claude":
            raise UnsafeSession("unsupported Claude launcher")
        config_dir = captured_dir or str(Path.home() / ".claude")
        if not os.path.isabs(config_dir):
            raise UnsafeSession("CLAUDE_CONFIG_DIR must be absolute")
        session = root_session(payload, config_dir)
        cwd = single_line(payload["cwd"])
        if not os.path.isabs(cwd):
            raise UnsafeSession("Claude working directory must be absolute")
        launch = {"arguments": ["claude"] + launch_options(args[1:])}
        if captured_dir:
            launch["environment"] = {"CLAUDE_CONFIG_DIR": single_line(captured_dir)}
        record = {**base, "sessionId": session, "updatedAt": time.time(),
                  "cwd": cwd, "launchCommand": launch}
        if pane_agent(pane_pid, process_snapshot(), "claude") != owner:
            raise UnsafeSession("Claude process changed during capture")
        tmux(socket, "set-option", "-p", "-t", pane, OPTION,
             json.dumps(record, separators=(",", ":")))
    except Exception:
        if pane_agent(pane_pid, process_snapshot(), "claude") == owner:
            tmux(socket, "set-option", "-p", "-t", pane, OPTION,
                 json.dumps({**base, "updatedAt": time.time(), "isRestorable": False}))
        raise


def main():
    try:
        context = tmux_context()
        if context is None:
            return 0
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise UnsafeSession("hook input is too large")
        payload = json.loads(raw)
        if isinstance(payload, dict) and payload.get("hook_event_name") in ("SessionStart", "UserPromptSubmit"):
            capture(payload, context)
    except Exception as error:
        message = str(error) if isinstance(error, UnsafeSession) else "capture unavailable"
        print("tmux-resurrect Claude: " + message, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
