"""Exercise the real save/restore entrypoints on a private tmux socket."""
import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SESSION_A = "01950000-0000-7000-8000-000000000001"
SESSION_B = "01950000-0000-7000-8000-000000000002"


@unittest.skipUnless(shutil.which("tmux") and shutil.which("cc"), "requires tmux and a C compiler")
class TmuxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="resurrect-cmux-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.socket = self.base / "tmux.sock"
        self.logs = self.base / "logs"
        self.logs.mkdir()
        self.state = self.base / "hooks"
        self.state.mkdir()
        self.snapshots = self.base / "snapshots"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("CMUX_", "TMUX", "CODEX_"))}
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        CMUX_AGENT_HOOK_STATE_DIR=str(self.state),
                        CMUX_TEST_LOG_DIR=str(self.logs), LC_ALL="C")
        self.config = self.base / "tmux.conf"
        self.config.write_text("\n".join([
            "set -g default-shell /bin/sh", "set -g default-command /bin/sh",
            "set -g status off", "set -g base-index 0", "set -g pane-base-index 0",
            "set -g @resurrect-save-command-strategy cmux",
            "set -g @resurrect-processes '\"~codex resume\"'",
            "set -g @resurrect-dir " + shlex.quote(str(self.snapshots)),
        ]) + "\n")
        subprocess.run(["cc", str(ROOT / "tests/fixtures/cmux_codex.c"),
                        "-o", str(self.bin / "codex")], check=True, capture_output=True)
        self.addCleanup(lambda: self.tmux("kill-server", check=False))
        self.tmux("new-session", "-d", "-s", "work", "-c", str(self.base))
        server_pid = self.tmux("display-message", "-p", "#{pid}").stdout.strip()
        self.env["TMUX"] = f"{self.socket},{server_pid},0"

    def tmux(self, *args, check=True):
        return subprocess.run(["tmux", "-S", str(self.socket), "-f", str(self.config), *args],
                              env=self.env, text=True, capture_output=True, check=check, timeout=15)

    def wait_until(self, predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        self.fail("timed out waiting for isolated tmux test process")

    def launch(self, pane, args="--model test-model", home=None):
        before = set(self.logs.iterdir())
        prefix = "env CODEX_HOME=" + shlex.quote(str(home)) + " " if home else ""
        self.tmux("send-keys", "-t", pane, prefix + "codex " + args, "C-m")
        log = self.wait_until(lambda: next(iter(set(self.logs.iterdir()) - before), None))
        self.wait_until(lambda: log.stat().st_size)
        return int(log.name)

    def record(self, pid, session, args=None, home=None):
        started = subprocess.check_output(["ps", "-p", str(pid), "-o", "lstart="],
                                          env=self.env, text=True).strip()
        epoch = int(datetime.datetime.strptime(started, "%a %b %d %H:%M:%S %Y").timestamp())
        launch = {"executablePath": str(self.bin / "codex"),
                  "arguments": [str(self.bin / "codex")] + (args or ["--model", "test-model"]),
                  "workingDirectory": str(self.base)}
        if home:
            launch["environment"] = {"CODEX_HOME": str(home)}
        return {"sessionId": session, "pid": pid, "pidStartSeconds": epoch,
                "pidStartMicroseconds": 0, "updatedAt": time.time(),
                "workspaceId": "same-cmux-workspace", "surfaceId": "same-inherited-surface",
                "cwd": str(self.base), "launchCommand": launch}

    def write_records(self, records):
        (self.state / "codex-hook-sessions.json").write_text(json.dumps({
            "version": 1, "sessions": {r["sessionId"]: r for r in records}}))

    def script(self, name):
        result = subprocess.run(["bash", str(ROOT / "scripts" / name), "quiet"],
                                env=self.env, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def saved_commands(self):
        return [line.split("\t")[-1].removeprefix(":")
                for line in (self.snapshots / "last").read_text().splitlines()
                if line.startswith("pane\t")]

    def test_two_same_directory_panes_resume_distinct_sessions_after_server_restart(self):
        first = self.launch("work:0.0")
        self.tmux("split-window", "-d", "-t", "work:0", "-c", str(self.base))
        second = self.launch("work:0.1")
        self.write_records([self.record(first, SESSION_A), self.record(second, SESSION_B)])
        self.script("save.sh")
        commands = self.saved_commands()
        self.assertIn("codex resume " + SESSION_A, commands[0])
        self.assertIn("codex resume " + SESSION_B, commands[1])
        # Upstream filenames have one-second precision. Do not overwrite the
        # same file and trigger upstream's identical-snapshot deletion branch.
        saved_second = int((self.snapshots / "last").stat().st_mtime)
        self.wait_until(lambda: int(time.time()) > saved_second)
        self.script("save.sh")
        self.assertEqual(self.saved_commands(), commands)
        self.tmux("kill-server")
        self.wait_until(lambda: not self.socket.exists() or
                        self.tmux("list-sessions", check=False).returncode != 0)
        self.tmux("new-session", "-d", "-s", "0", "-c", str(self.base))
        # Deliberately consume an extra pane ID; IDs need not survive restart.
        self.tmux("split-window", "-d", "-t", "0")
        self.tmux("kill-pane", "-t", "0:0.1")
        self.env["TMUX"] = f"{self.socket},{self.tmux('display-message', '-p', '#{pid}').stdout.strip()},0"
        old_logs = set(self.logs.iterdir())
        # Restoration must work without the cmux store or any cmux process.
        (self.state / "codex-hook-sessions.json").unlink()
        self.script("restore.sh")
        self.wait_until(lambda: len(set(self.logs.iterdir()) - old_logs) == 2)
        new_logs = set(self.logs.iterdir()) - old_logs
        self.wait_until(lambda: all(p.stat().st_size for p in new_logs))
        argv = [p.read_text().splitlines() for p in new_logs]
        self.assertEqual({tuple(a[1:3]) for a in argv}, {("resume", SESSION_A), ("resume", SESSION_B)})
        for a in argv:
            self.assertIn("test-model", a)

    def test_unrecorded_codex_is_skipped_instead_of_starting_new_conversation(self):
        self.launch("work:0.0")
        result = self.script("save.sh")
        self.assertEqual(self.saved_commands(), [""])
        self.assertIn("cmux", result.stderr.lower())

    def test_corrupt_hook_store_does_not_save_a_fresh_codex_launch(self):
        self.launch("work:0.0")
        (self.state / "codex-hook-sessions.json").write_text("{incomplete")
        self.script("save.sh")
        self.assertEqual(self.saved_commands(), [""])

    def test_non_codex_process_uses_upstream_save_strategy(self):
        self.tmux("send-keys", "-t", "work:0.0", "sleep 4321", "C-m")
        self.wait_until(lambda: self.tmux("display-message", "-p", "-t", "work:0.0",
                                        "#{pane_current_command}").stdout.strip() == "sleep")
        self.script("save.sh")
        self.assertIn("sleep 4321", self.saved_commands()[0])

    def test_custom_home_and_launch_options_survive_restore(self):
        home = self.base / "custom codex home"
        home.mkdir()
        pid = self.launch("work:0.0", '--model test-model --yolo "initial prompt"', home)
        self.write_records([self.record(pid, SESSION_A,
                           ["--model", "test-model", "--yolo", "initial prompt"], home)])
        self.script("save.sh")
        command = self.saved_commands()[0]
        self.assertNotIn("initial prompt", command)
        self.assertIn("codex resume " + SESSION_A, command)
        self.assertIn("--yolo", command)
        self.tmux("split-window", "-d", "-t", "work:0", "-c", str(self.base))
        before = set(self.logs.iterdir())
        self.tmux("send-keys", "-t", "work:0.1", command, "C-m")
        log = self.wait_until(lambda: next(iter(set(self.logs.iterdir()) - before), None))
        self.wait_until(lambda: log.stat().st_size)
        self.assertIn("CODEX_HOME=" + str(home), log.read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
