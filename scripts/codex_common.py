#!/usr/bin/env python3
"""Process ownership and safe resume commands shared by Codex hooks and saves."""
import datetime
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid


class NotCodex(Exception):
    pass


class UnsafeSession(Exception):
    pass


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    pgid: int
    tpgid: int
    tty: str
    started: int
    executable: str

    @property
    def is_codex(self):
        return Path(self.executable).name == "codex"


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


def pane_codex(pane_pid, processes):
    pane = processes.get(pane_pid)
    if pane is None:
        raise UnsafeSession("pane process disappeared")
    codex = [p for p in processes.values()
             if p.is_codex and pane_pid in ancestors(p.pid, processes)]
    if not codex:
        raise NotCodex()
    # A subagent may be another codex executable with the same cwd/TTY/group.
    # Only the top-level Codex process can own this pane's conversation.
    codex_ids = {p.pid for p in codex}
    roots = [p for p in codex if not any(
        parent in codex_ids for parent in ancestors(p.ppid, processes))]
    foreground = [p for p in roots if p.tty == pane.tty
                  and p.tty not in ("?", "??", "-")
                  and p.pgid > 0 and p.pgid == p.tpgid == pane.tpgid]
    if len(foreground) != 1:
        raise UnsafeSession("no unique foreground Codex process")
    return foreground[0]


def select_record(pane_pid, processes, records):
    process = pane_codex(pane_pid, processes)
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
        raise UnsafeSession("no Codex hook record for this Codex process generation")
    # /new and /resume can switch conversations without changing the process.
    latest = max(r["updatedAt"] for r in matches)
    matches = [r for r in matches if r["updatedAt"] == latest]
    if len({r.get("sessionId") for r in matches if isinstance(r.get("sessionId"), str)}) != 1:
        raise UnsafeSession("ambiguous Codex hook records")
    record = matches[0]
    if record.get("isRestorable") is False:
        raise UnsafeSession("hook marked the current conversation non-restorable")
    return record


VALUE_OPTIONS = {
    "--config", "-c", "--model", "-m", "--local-provider", "--profile", "-p",
    "--sandbox", "-s", "--ask-for-approval", "-a", "--cd", "-C", "--add-dir",
    "--enable", "--disable",
}
BOOL_OPTIONS = {
    "--oss", "--search", "--no-alt-screen", "--full-auto", "--yolo",
    "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust",
    "--strict-config", "--approve-for-me",
}
SELECTOR_OPTIONS = {"--last", "--all", "--include-non-interactive"}
NON_INTERACTIVE = {
    "exec", "e", "review", "login", "logout", "mcp", "mcp-server", "app-server",
    "app", "completion", "sandbox", "debug", "apply", "a", "fork", "cloud",
    "exec-server", "features", "help", "remote-control", "plugin", "archive",
    "unarchive", "delete",
}


def single_line(value):
    if (not isinstance(value, str) or not value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise UnsafeSession("invalid or multiline launch data")
    return value


def launch_options(args):
    preserved = []
    first_positional = True
    index = 0
    while index < len(args):
        arg = single_line(args[index])
        if arg == "--":
            break  # Everything after -- is a prompt, not a launch option.
        if not arg.startswith("-"):
            if first_positional and arg in NON_INTERACTIVE:
                raise UnsafeSession("unsupported Codex subcommand")
            first_positional = False
            index += 1  # Drop resume, previous IDs, and initial prompts.
            continue
        option, equals, value = arg.partition("=")
        if option in SELECTOR_OPTIONS and not equals:
            index += 1
            continue
        if option in BOOL_OPTIONS and not equals:
            preserved.append(arg)
            index += 1
            continue
        if option not in VALUE_OPTIONS:
            raise UnsafeSession("unsupported Codex launch option")
        if not equals:
            index += 1
            if index == len(args) or args[index].startswith("-"):
                raise UnsafeSession("missing Codex option value")
            value = args[index]
        value = single_line(value)
        if option in ("-c", "--config"):
            key = value.split("=", 1)[0].strip()
            if key == "hooks" or key.startswith("hooks."):
                raise UnsafeSession("captured inline hooks require a fresh launch configuration")
        if option not in ("--cd", "-C"):
            preserved.extend([option, value])
        index += 1
    return preserved


def resume_command(record):
    try:
        session = str(uuid.UUID(record["sessionId"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise UnsafeSession("invalid Codex session ID") from None
    launch = record.get("launchCommand")
    if not isinstance(launch, dict):
        raise UnsafeSession("Codex launch capture is missing")
    args = launch.get("arguments")
    if (not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args)
            or Path(args[0]).name != "codex"):
        raise UnsafeSession("unsupported Codex launcher")
    cwd = single_line(record.get("cwd") or launch.get("workingDirectory"))
    if not os.path.isabs(cwd):
        raise UnsafeSession("launch directory must be absolute")
    environment = launch.get("environment") or {}
    if not isinstance(environment, dict):
        raise UnsafeSession("invalid captured environment")
    command = []
    if "CODEX_HOME" in environment:
        home = single_line(environment["CODEX_HOME"])
        if not os.path.isabs(home):
            raise UnsafeSession("CODEX_HOME must be absolute")
        command.extend(["env", "CODEX_HOME=" + home])
    # Resolve Codex on PATH on the machine performing the restore; temporary
    # launch shims and credentials do not belong in a durable tmux snapshot.
    command.extend(["codex", "resume", session])
    command.extend(launch_options(args[1:]))
    command.extend(["--cd", cwd])
    return " ".join(shlex.quote(arg) for arg in command)

