"""Emit a root resume event from a native test Codex process's descendant."""
import json
import os
from pathlib import Path
import subprocess
import sys

session = sys.argv[1]
payload = {"session_id": session, "hook_event_name": "SessionStart", "source": "resume",
           "cwd": os.getcwd(),
           "transcript_path": str(Path(os.environ["CODEX_HOME"]) / "sessions" / (session + ".jsonl"))}
sys.exit(subprocess.run([sys.executable, os.environ["CODEX_TEST_HOOK"]],
                        input=json.dumps(payload), text=True).returncode)
