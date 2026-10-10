"""Codex-specific transcript validation, launch policy, and resume command."""
import json
import os
from pathlib import Path
import shlex
import uuid

from conversation_common import IgnoredSession, UnsafeSession, single_line

NAME = "codex"
ENV_NAME = "CODEX_HOME"
DEFAULT_DIR = "~/.codex"
SETTINGS_FILE = "hooks.json"
MISSING_RECORD_HINT = (
    "Codex may wait until the first prompt to run its hook; "
    "submit a prompt in this pane, then retry (check /hooks if it persists)"
)


def root_session(payload, home):
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
            raise IgnoredSession()
        if source not in ("cli", "tui"):
            raise UnsafeSession("session is not a verified root CLI conversation")
        return session
    except (KeyError, ValueError, TypeError, AttributeError):
        raise UnsafeSession("unsupported Codex session metadata") from None


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
