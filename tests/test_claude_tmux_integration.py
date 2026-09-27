"""Exercise actual tmux save and restore with two foreground Claude fixtures."""
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
A = "01950000-0000-7000-8000-000000000001"
B = "01950000-0000-7000-8000-000000000002"


@unittest.skipUnless(shutil.which("tmux") and shutil.which("cc"), "requires tmux and cc")
class ClaudeTmuxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="resurrect-claude-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.socket = self.base / "tmux.sock"
        self.logs = self.base / "logs"; self.logs.mkdir()
        self.config_dir = self.base / "claude home"
        self.project = self.config_dir / "projects" / "-fixture"; self.project.mkdir(parents=True)
        self.bin = self.base / "bin"; self.bin.mkdir()
        self.snapshots = self.base / "snapshots"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("CMUX", "TMUX", "CODEX", "CLAUDE", "RESURRECT"))}
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        CLAUDE_CONFIG_DIR=str(self.config_dir), CODEX_TEST_LOG_DIR=str(self.logs),
                        CODEX_TEST_HOOK=str(ROOT / "scripts/conversation_hook.py"),
                        CODEX_TEST_PROVIDER="claude",
                        CODEX_TEST_EMITTER=str(ROOT / "tests/fixtures/emit_claude_hook.py"), LC_ALL="C")
        self.config = self.base / "tmux.conf"
        self.config.write_text("\n".join([
            "set -g default-shell /bin/sh", "set -g default-command 'exec /bin/sh'",
            "set -g status off", "set -g base-index 0", "set -g pane-base-index 0",
            "set -g @resurrect-save-command-strategy conversation",
            "set -g @resurrect-processes '\"~claude --resume\"'",
            "set -g @resurrect-dir " + shlex.quote(str(self.snapshots)),
        ]) + "\n")
        subprocess.run(["cc", str(ROOT / "tests/fixtures/codex.c"), "-o", str(self.bin / "claude")],
                       check=True, capture_output=True)
        self.addCleanup(lambda: self.tmux("kill-server", check=False))
        self.start_server("work")

    def tmux(self, *args, check=True):
        return subprocess.run(["tmux", "-S", str(self.socket), "-f", str(self.config), *args],
                              env=self.env, text=True, capture_output=True, check=check, timeout=15)

    def start_server(self, name):
        self.tmux("new-session", "-d", "-s", name, "-c", str(self.base))
        server = self.tmux("display-message", "-p", "#{pid}").stdout.strip()
        self.env["TMUX"] = f"{self.socket},{server},0"

    def wait(self, predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.04)
        self.fail("timed out waiting for Claude fixture")

    def launch(self, pane):
        before = set(self.logs.glob("*.argv"))
        self.tmux("send-keys", "-t", pane, "claude --model sonnet", "C-m")
        log = self.wait(lambda: next(iter(set(self.logs.glob("*.argv")) - before), None))
        return int(log.stem)

    def fire(self, pid, session):
        transcript = self.project / (session + ".jsonl")
        transcript.write_text("{}\n")
        payload = {"session_id": session, "hook_event_name": "SessionStart",
                   "source": "startup", "cwd": str(self.base), "transcript_path": str(transcript)}
        done = self.logs / f"{pid}.done"
        old = done.read_text() if done.exists() else ""
        (self.logs / f"{pid}.payload").write_text(json.dumps(payload))
        os.kill(pid, signal.SIGUSR1)
        self.wait(lambda: done.exists() and done.read_text() != old)

    def script(self, name, success=True):
        result = subprocess.run(["bash", str(ROOT / "scripts" / name), "quiet"],
                                env=self.env, text=True, capture_output=True, timeout=25)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return result

    def commands(self):
        return [line.split("\t")[-1].removeprefix(":") for line in
                (self.snapshots / "last").read_text().splitlines() if line.startswith("pane\t")]

    def test_two_panes_restore_their_own_sessions_and_resave_idle(self):
        first = self.launch("work:0.0"); self.fire(first, A)
        self.tmux("split-window", "-d", "-t", "work:0", "-c", str(self.base))
        second = self.launch("work:0.1"); self.fire(second, B)
        self.script("save.sh")
        self.assertEqual({parts[parts.index("--resume") + 1] for parts in
                          (shlex.split(c) for c in self.commands())}, {A, B})
        old = set(self.logs.glob("*.argv"))
        self.tmux("kill-server"); self.start_server("0")
        self.script("restore.sh")
        self.wait(lambda: len(set(self.logs.glob("*.argv")) - old) == 2)
        self.wait(lambda: self.tmux("show-options", "-pqv", "-t", "work:0.0",
                                   "@resurrect-conversation").stdout.strip())
        restored = [p.read_text().splitlines() for p in set(self.logs.glob("*.argv")) - old]
        self.assertEqual({tuple(a[1:3]) for a in restored}, {("--resume", A), ("--resume", B)})
        self.script("save.sh")

    def test_missing_hook_preserves_previous_snapshot(self):
        pid = self.launch("work:0.0"); self.fire(pid, A)
        self.script("save.sh")
        last = self.snapshots / "last"; before = (last.readlink(), last.read_bytes())
        self.tmux("set-option", "-p", "-t", "work:0.0", "@resurrect-conversation", "")
        self.script("save.sh", success=False)
        self.assertEqual((last.readlink(), last.read_bytes()), before)


if __name__ == "__main__":
    unittest.main()
