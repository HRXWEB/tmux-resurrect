#!/usr/bin/env python3
"""Capture root Codex hook identity in the owning tmux pane, without cmux."""
import ctypes
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import uuid

from codex_common import (UnsafeSession, ancestors, launch_options, pane_codex,
                          process_snapshot, single_line)

OPTION = "@resurrect-codex-session"
MAX_INPUT = 1024 * 1024


class ChildSession(Exception):
    """Known non-root sessions do not own this pane binding."""



def tmux_context():
    raw, pane = os.environ.get("TMUX", ""), os.environ.get("TMUX_PANE", "")
    if not raw or not pane:
        return None
    try:
        socket, server, _ = raw.rsplit(",", 2)
        if not os.path.isabs(socket) or not server.isdigit() or not pane.startswith("%") or not pane[1:].isdigit():
            raise ValueError()
    except ValueError:
        raise UnsafeSession("invalid tmux context") from None
    return socket, int(server), pane


def tmux(socket, *args):
    return subprocess.run(["tmux", "-S", socket, *args], capture_output=True,
                          text=True, check=True, timeout=2).stdout.strip()


def process_arguments(pid):
    """Read argv directly, never reconstruct a shell command from ps output."""
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
        size = ctypes.c_size_t()
        if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or not 4 < size.value <= 4 * MAX_INPUT:
            raise UnsafeSession("cannot inspect Codex arguments")
        buffer = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
            raise UnsafeSession("Codex exited during inspection")
        raw = buffer.raw[:size.value]
        argc = struct.unpack("i", raw[:4])[0]
        offset = raw.index(b"\0", 4)
        while offset < len(raw) and raw[offset] == 0:
            offset += 1
        if not 0 < argc < 65536:
            raise UnsafeSession("invalid process argument count")
        parts = raw[offset:].split(b"\0")
        argv, entries = parts[:argc], parts[argc:]
    elif sys.platform.startswith("linux"):
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    else:
        raise UnsafeSession("process inspection requires macOS or Linux")
    args = [v.decode("utf-8") for v in argv]
    # Do not retain or print the rest of the process environment.
    home = next((v.split(b"=", 1)[1].decode("utf-8") for v in entries
                 if v.startswith(b"CODEX_HOME=")), None)
    return args, home


def root_session_metadata(payload, home):
    """Bounded provenance check: same-process child hooks cannot replace root."""
    try:
        session = str(uuid.UUID(payload["session_id"]))
        path = Path(single_line(payload["transcript_path"])).resolve(strict=True)
        root = Path(home).expanduser().resolve(strict=True)
        if not any(path.is_relative_to(root / folder) for folder in ("sessions", "archived_sessions")):
            raise UnsafeSession("transcript is outside the Codex session directory")
        with path.open("rb") as stream:
            line = stream.readline(65537)
        if len(line) > 65536:
            raise UnsafeSession("session metadata is too large")
        header = json.loads(line)
        meta = header["payload"]
        if header.get("type") != "session_meta" or meta.get("id") != session:
            raise UnsafeSession("session metadata identity mismatch")
        source = meta.get("source")
        if ((isinstance(source, dict) and "subagent" in source)
                or meta.get("thread_source") == "subagent" or source in ("exec", "review")):
            raise ChildSession()
        if source not in ("cli", "tui"):
            raise UnsafeSession("session is not a verified root CLI conversation")
        return session
    except (KeyError, ValueError, TypeError, AttributeError):
        raise UnsafeSession("unsupported Codex session metadata") from None


def capture(payload, context):
    socket, server, pane = context
    details = tmux(socket, "display-message", "-p", "-t", pane, "#{pid} #{pane_pid} #{pane_id}").split()
    if len(details) != 3 or details[0] != str(server) or details[2] != pane:
        raise UnsafeSession("tmux pane ownership changed")
    pane_pid = int(details[1])
    processes = process_snapshot()
    owner = pane_codex(pane_pid, processes)
    # A different shell with TMUX_PANE copied into its env is not an agent hook.
    # Nor may a nested Codex executable publish on behalf of the outer Codex.
    lineage = [processes[p] for p in ancestors(os.getpid(), processes) if processes[p].is_codex]
    if len(lineage) != 1 or lineage[0].pid != owner.pid:
        raise UnsafeSession("hook is not owned by the foreground Codex")
    base = {"version": 1, "pid": owner.pid, "pidStartSeconds": owner.started,
            "paneId": pane, "panePID": pane_pid}
    try:
        args, captured_home = process_arguments(owner.pid)
        if not args or Path(args[0]).name != "codex":
            raise UnsafeSession("unsupported Codex launcher")
        home = captured_home or str(Path.home() / ".codex")
        session = root_session_metadata(payload, home)
        cwd = single_line(payload["cwd"])
        if not os.path.isabs(cwd):
            raise UnsafeSession("session directory is not absolute")
        # Sanitize before persistence, so initial prompts never enter pane options.
        launch = {"arguments": ["codex"] + launch_options(args[1:]), "workingDirectory": cwd}
        if captured_home:
            if not os.path.isabs(captured_home):
                raise UnsafeSession("CODEX_HOME must be absolute")
            launch["environment"] = {"CODEX_HOME": single_line(captured_home)}
        record = {**base, "sessionId": session, "updatedAt": time.time(),
                  "cwd": cwd, "launchCommand": launch}
        if pane_codex(pane_pid, process_snapshot()) != owner:
            raise UnsafeSession("Codex process changed during hook capture")
        tmux(socket, "set-option", "-p", "-t", pane, OPTION,
             json.dumps(record, separators=(",", ":")))
    except ChildSession:
        return
    except Exception:
        # This callback belongs to the foreground process, but its current
        # conversation could not be verified. Never silently reuse an earlier
        # conversation from the same still-running process after /new or /resume.
        if pane_codex(pane_pid, process_snapshot()) == owner:
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
        if not isinstance(payload, dict) or payload.get("hook_event_name") not in ("SessionStart", "UserPromptSubmit"):
            return 0
        capture(payload, context)
    except Exception as error:
        # Hooks must never block a user's turn; the save strategy fails closed.
        # Do not print payloads, paths, argv, or arbitrary exception messages.
        message = str(error) if isinstance(error, UnsafeSession) else "capture unavailable"
        print("tmux-resurrect Codex: " + message, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
