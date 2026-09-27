#!/usr/bin/env python3
"""Install/remove only tmux-resurrect's Claude Code hooks."""
import argparse
import copy
import os
from pathlib import Path
import shlex
import sys

from codex_hook_setup import update_file

MARKER = "--tmux-resurrect-claude-hook-v1"
EVENTS = ("SessionStart", "UserPromptSubmit")


def ours(hook):
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return False
    try:
        args = shlex.split(hook.get("command", ""))
        return (len(args) == 3 and args[2] == MARKER
                and Path(args[1]).name == "claude_session_recorder.py")
    except ValueError:
        return False


def update(config, install):
    result = copy.deepcopy(config)
    if not isinstance(result, dict) or not isinstance(result.get("hooks", {}), dict):
        raise ValueError("settings.json must contain an object with an optional hooks object")
    hooks = result.setdefault("hooks", {})
    command = "python3 " + shlex.quote(str(Path(__file__).resolve().with_name("claude_session_recorder.py"))) + " " + MARKER
    for event in EVENTS:
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise ValueError("hook event groups must be arrays")
        kept = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
                raise ValueError("hook groups must contain a hooks array")
            handlers = group.get("hooks", [])
            remaining = [hook for hook in handlers if not ours(hook)]
            if remaining or remaining == handlers:
                kept.append({**group, "hooks": remaining} if remaining != handlers else group)
        if install:
            kept.append({"hooks": [{"type": "command", "command": command, "timeout": 5}]})
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--claude-config-dir", default=os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude"))
    args = parser.parse_args()
    if sys.version_info < (3, 9):
        print("Resurrect Claude hooks require Python 3.9 or newer.", file=sys.stderr)
        return 1
    try:
        configured = Path(args.claude_config_dir).expanduser().absolute() / "settings.json"
        path = configured.resolve()
        if not path.exists() and args.action == "uninstall":
            print("No resurrect Claude hooks installed.")
            return 0
        if not update_file(path, args.action == "install", update):
            print("Claude hooks already up to date.")
            return 0
        print("Resurrect Claude hooks " + ("installed." if args.action == "install" else "removed."))
        return 0
    except (OSError, ValueError, TypeError):
        print("Cannot update Claude settings.json: check JSON, file access and concurrent edits.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
