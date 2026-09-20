#!/usr/bin/env python3
"""Add/remove only this plugin's hooks. Never change trust or other hooks."""
import argparse
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys
import tempfile
import time

MARKER = "--tmux-resurrect-codex-hook-v1"
EVENTS = ("SessionStart", "UserPromptSubmit")


def ours(hook):
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return False
    try:
        args = shlex.split(hook.get("command", ""))
        return len(args) == 3 and Path(args[1]).name == "codex_hook.py" and args[2] == MARKER
    except ValueError:
        return False


def update(config, install):
    result = copy.deepcopy(config)
    if not isinstance(result, dict) or not isinstance(result.get("hooks", {}), dict):
        raise ValueError("hooks.json must contain an object with an optional hooks object")
    hooks = result.setdefault("hooks", {})
    command = "python3 " + shlex.quote(str(Path(__file__).resolve().with_name("codex_hook.py"))) + " " + MARKER
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
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", "~/.codex"))
    args = parser.parse_args()
    try:
        configured = Path(args.codex_home).expanduser().absolute() / "hooks.json"
        path = configured.resolve()  # Preserve a dotfiles-managed symlink.
        before = path.read_bytes() if path.exists() else None
        if before is None and args.action == "uninstall":
            print("No resurrect Codex hooks installed.")
            return 0
        config = json.loads(before) if before is not None else {}
        changed = update(config, args.action == "install")
        if changed == config:
            print("Codex hooks already up to date.")
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        if before is not None:
            backup = path.with_name(path.name + ".bak." + str(time.time_ns()))
            shutil.copy2(path, backup)
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
        fd, temporary = tempfile.mkstemp(prefix=".resurrect-hooks-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(changed, file, indent=2, ensure_ascii=False)
                file.write("\n")
                file.flush(); os.fsync(file.fileno())
            os.chmod(temporary, mode)
            # Refuse a concurrent editor's change rather than losing it.
            current = path.read_bytes() if path.exists() else None
            if current != before:
                raise ValueError("hooks.json changed during installation; retry")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        print("Resurrect Codex hooks " + ("installed." if args.action == "install" else "removed."))
        if args.action == "install":
            print("In Codex, review/trust the two new hooks with /hooks. Existing hooks were preserved.")
        return 0
    except (OSError, ValueError, TypeError):
        print("Cannot update Codex hooks: check the JSON structure, file access, and concurrent edits. Configuration was not replaced.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
