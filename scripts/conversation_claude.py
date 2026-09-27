"""Claude-specific transcript validation, launch policy, and resume command."""
import os
from pathlib import Path
import shlex
import uuid

from conversation_common import UnsafeSession, single_line

NAME = "claude"
ENV_NAME = "CLAUDE_CONFIG_DIR"
DEFAULT_DIR = "~/.claude"
SETTINGS_FILE = "settings.json"
HOOK_CHILD_SUBCOMMANDS = frozenset()


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


VALUE_OPTIONS = {"--model", "--permission-mode", "--effort", "--agent"}
BOOL_OPTIONS = {"--dangerously-skip-permissions", "--allow-dangerously-skip-permissions"}
SELECTOR_OPTIONS = {"-c", "--continue", "--fork-session"}


def launch_options(args):
    """Keep only simple interactive settings; never replay an initial prompt."""
    preserved = []
    index = 0
    if args and args[0] not in ("-r", "--resume", "-c", "--continue") and not args[0].startswith("-"):
        raise UnsafeSession("unsupported Claude subcommand or initial prompt")
    while index < len(args):
        arg = single_line(args[index])
        if arg == "--":
            if index + 1 < len(args):
                raise UnsafeSession("Claude initial prompt cannot be restored")
            break
        option, equals, value = arg.partition("=")
        if option in SELECTOR_OPTIONS and not equals:
            index += 1
            continue
        if option in ("-r", "--resume"):
            if not equals and index + 1 < len(args) and not args[index + 1].startswith("-"):
                index += 1
            index += 1
            continue
        if option in BOOL_OPTIONS and not equals:
            preserved.append(option)
            index += 1
            continue
        if option in VALUE_OPTIONS:
            if not equals:
                index += 1
                if index == len(args) or args[index].startswith("-"):
                    raise UnsafeSession("missing Claude option value")
                value = args[index]
            preserved.extend([option, single_line(value)])
            index += 1
            continue
        raise UnsafeSession("unsupported Claude launch option or prompt")
    return preserved


def resume_command(record):
    try:
        session = str(uuid.UUID(record["sessionId"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise UnsafeSession("invalid Claude session ID") from None
    launch = record.get("launchCommand")
    if not isinstance(launch, dict):
        raise UnsafeSession("Claude launch capture is missing")
    args = launch.get("arguments")
    if not isinstance(args, list) or not args or args[0] != "claude":
        raise UnsafeSession("unsupported Claude launcher")
    cwd = single_line(record.get("cwd"))
    if not os.path.isabs(cwd):
        raise UnsafeSession("Claude working directory must be absolute")
    command = []
    environment = launch.get("environment") or {}
    if not isinstance(environment, dict):
        raise UnsafeSession("invalid Claude environment")
    if "CLAUDE_CONFIG_DIR" in environment:
        config_dir = single_line(environment["CLAUDE_CONFIG_DIR"])
        if not os.path.isabs(config_dir):
            raise UnsafeSession("CLAUDE_CONFIG_DIR must be absolute")
        command.extend(["env", "CLAUDE_CONFIG_DIR=" + config_dir])
    command.extend(["claude", "--resume", session])
    command.extend(launch_options(args[1:]))
    return " ".join(shlex.quote(arg) for arg in command)
