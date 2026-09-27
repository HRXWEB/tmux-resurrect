#!/usr/bin/env python3
"""Shared process, tmux and record validation for conversation providers."""
import ctypes
import datetime
from dataclasses import dataclass
import math
import os
from pathlib import Path
import struct
import subprocess
import sys


class NotAgent(Exception):
    pass


class UnsafeSession(Exception):
    pass


class IgnoredSession(Exception):
    """A nested conversation event must not replace the pane's root binding."""


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    pgid: int
    tpgid: int
    tty: str
    started: int
    executable: str


def process_snapshot():
    # comm contains only the executable name, never a prompt or token in argv.
    result = subprocess.run(
        ["ps", "-ww", "-ax", "-o", "pid=,ppid=,pgid=,tpgid=,tty=,lstart=,comm="],
        capture_output=True, text=True, check=True, timeout=5,
        env={**os.environ, "LC_ALL": "C"},
    )
    processes = {}
    for line in result.stdout.splitlines():
        fields = line.split(None, 10)
        if len(fields) != 11:
            continue
        try:
            started = int(datetime.datetime.strptime(
                " ".join(fields[5:10]), "%a %b %d %H:%M:%S %Y").timestamp())
            process = Process(*(int(f) for f in fields[:4]), fields[4], started, fields[10])
        except (ValueError, OverflowError):
            continue
        processes[process.pid] = process
    return processes


def ancestors(pid, processes):
    seen = set()
    while pid in processes and pid not in seen:
        seen.add(pid)
        yield pid
        pid = processes[pid].ppid


def pane_agent(pane_pid, processes, name):
    pane = processes.get(pane_pid)
    if pane is None:
        raise UnsafeSession("pane process disappeared")
    agents = [p for p in processes.values()
              if Path(p.executable).name == name and pane_pid in ancestors(p.pid, processes)]
    if not agents:
        raise NotAgent()
    # Nested same-name executables do not own the pane's conversation.
    agent_ids = {p.pid for p in agents}
    roots = [p for p in agents if not any(
        parent in agent_ids for parent in ancestors(p.ppid, processes))]
    foreground = [p for p in roots if p.tty == pane.tty
                  and p.tty not in ("?", "??", "-")
                  and p.pgid > 0 and p.pgid == p.tpgid == pane.tpgid]
    if len(foreground) != 1:
        raise UnsafeSession("no unique foreground " + name + " process")
    return foreground[0]


def foreground_agent(pane_pid, processes, names):
    owners, uncertain = [], False
    for name in names:
        try:
            owners.append((name, pane_agent(pane_pid, processes, name)))
        except NotAgent:
            pass
        except UnsafeSession:
            uncertain = True
    if len(owners) == 1:
        return owners[0]
    if owners or uncertain:
        raise UnsafeSession("no unique foreground conversation process")
    raise NotAgent()


def verify_hook_owner(hook_pid, owner, processes, provider):
    """Allow only known runtime helpers between a hook and its pane owner."""
    lineage = [processes[pid] for pid in ancestors(hook_pid, processes)
               if Path(processes[pid].executable).name == provider.NAME]
    if not lineage or lineage[-1].pid != owner.pid:
        raise UnsafeSession("hook is not owned by the foreground " + provider.NAME)
    for child in lineage[:-1]:
        args, _ = process_arguments(child.pid, provider.ENV_NAME)
        if len(args) < 2 or args[1] not in provider.HOOK_CHILD_SUBCOMMANDS:
            raise UnsafeSession("hook came from an unsupported nested " + provider.NAME)


def select_record(pane_pid, processes, records, name):
    process = pane_agent(pane_pid, processes, name)
    matches = []
    for record in records:
        if not isinstance(record, dict):
            continue
        # Validate process generation, not just a PID that can be recycled.
        if (type(record.get("pid")) is not int or record["pid"] != process.pid
                or type(record.get("pidStartSeconds")) is not int
                or record["pidStartSeconds"] != process.started):
            continue
        updated = record.get("updatedAt")
        if not isinstance(updated, (int, float)) or not math.isfinite(updated):
            continue
        if updated < process.started:
            continue
        matches.append(record)
    if not matches:
        raise UnsafeSession("no " + name + " hook record for this process generation")
    # /new and /resume can switch conversations without changing the process.
    latest = max(r["updatedAt"] for r in matches)
    matches = [r for r in matches if r["updatedAt"] == latest]
    if any(r.get("isRestorable") is False for r in matches):
        raise UnsafeSession("hook marked the current conversation non-restorable")
    if len({r.get("sessionId") for r in matches if isinstance(r.get("sessionId"), str)}) != 1:
        raise UnsafeSession("ambiguous conversation hook records")
    return matches[0]


def single_line(value):
    if (not isinstance(value, str) or not value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise UnsafeSession("invalid or multiline launch data")
    return value


MAX_INPUT = 1024 * 1024


def process_arguments(pid, env_name):
    """Read argv and one named environment value without exposing the rest."""
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, pid)
        size = ctypes.c_size_t()
        if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or not 4 < size.value <= 4 * MAX_INPUT:
            raise UnsafeSession("cannot inspect process arguments")
        buffer = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
            raise UnsafeSession("process exited during inspection")
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
    prefix = env_name.encode("ascii") + b"="
    value = next((item.split(b"=", 1)[1].decode("utf-8") for item in entries
                  if item.startswith(prefix)), None)
    return [item.decode("utf-8") for item in argv], value


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
