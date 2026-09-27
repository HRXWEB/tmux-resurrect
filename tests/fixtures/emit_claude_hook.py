"""Emit a Claude resume hook from the native test process descendant."""
import json
import os
from pathlib import Path
import subprocess
import sys

session = sys.argv[1]
project = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-fixture"
payload = {"session_id": session, "hook_event_name": "SessionStart", "source": "resume",
           "cwd": os.getcwd(), "transcript_path": str(project / (session + ".jsonl"))}
sys.exit(subprocess.run([sys.executable, os.environ["CODEX_TEST_HOOK"], os.environ["CODEX_TEST_PROVIDER"]],
                        input=json.dumps(payload), text=True).returncode)
