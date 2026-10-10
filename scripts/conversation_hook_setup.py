#!/usr/bin/env python3
"""Install/remove one provider's conversation hook handlers safely."""
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys
import tempfile
import time

from conversation_providers import PROVIDERS

MARKER = "--tmux-resurrect-conversation-hook-v1"
EVENTS = ("SessionStart", "UserPromptSubmit")


def ours(hook, provider):
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return False
    try:
        args = shlex.split(hook.get("command", ""))
        return (len(args) == 4 and args[2:] == [provider.NAME, MARKER]
                and Path(args[1]).name == "conversation_hook.py")
    except ValueError:
        return False


def update(config, install, provider):
    result = copy.deepcopy(config)
    if not isinstance(result, dict) or not isinstance(result.get("hooks", {}), dict):
        raise ValueError("settings must contain an object with an optional hooks object")
    hooks = result.setdefault("hooks", {})
    command = ("python3 " + shlex.quote(str(Path(__file__).resolve().with_name("conversation_hook.py")))
               + " " + provider.NAME + " " + MARKER)
    for event in EVENTS:
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise ValueError("hook event groups must be arrays")
        kept = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
                raise ValueError("hook groups must contain a hooks array")
            handlers = group.get("hooks", [])
            remaining = [hook for hook in handlers if not ours(hook, provider)]
            if remaining or remaining == handlers:
                kept.append({**group, "hooks": remaining} if remaining != handlers else group)
        if install:
            kept.append({"hooks": [{"type": "command", "command": command, "timeout": 5}]})
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    return result


def update_file(path, install, updater):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Multiple tmux servers can load this plugin against the same config directory.
    lock_path = path.with_name("." + path.name + ".resurrect.lock")
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = path.read_bytes() if path.exists() else None
        config = json.loads(before) if before is not None else {}
        changed = updater(config, install)
        if changed == config:
            return False
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
                raise ValueError("settings changed during installation; retry")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--provider", required=True, choices=tuple(PROVIDERS))
    parser.add_argument("--config-dir")
    args = parser.parse_args()
    if sys.version_info < (3, 9):
        print("Resurrect conversation hooks require Python 3.9 or newer.", file=sys.stderr)
        return 1
    provider = PROVIDERS[args.provider]
    try:
        config_dir = args.config_dir or os.environ.get(provider.ENV_NAME, provider.DEFAULT_DIR)
        configured = Path(config_dir).expanduser().absolute() / provider.SETTINGS_FILE
        path = configured.resolve()
        if not path.exists() and args.action == "uninstall":
            print("No " + provider.NAME + " conversation hooks installed.")
            return 0
        updater = lambda config, install: update(config, install, provider)
        if not update_file(path, args.action == "install", updater):
            print(provider.NAME + " conversation hooks already up to date.")
            return 0
        print(provider.NAME + " conversation hooks "
              + ("installed." if args.action == "install" else "removed."))
        return 0
    except (OSError, ValueError, TypeError):
        print("Cannot update " + provider.NAME + " hooks: check JSON, file access and concurrent edits.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
