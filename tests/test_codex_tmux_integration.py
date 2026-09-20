"""No cmux: native hook ancestry plus actual tmux save/restore entrypoints."""
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
OPTION = "@resurrect-codex-session"


@unittest.skipUnless(shutil.which("tmux") and shutil.which("cc"), "requires tmux and cc")
class StandaloneCodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="resurrect-codex-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.socket = self.base / "tmux.sock"
        self.logs = self.base / "logs"; self.logs.mkdir()
        self.home = self.base / "codex home"; (self.home / "sessions").mkdir(parents=True)
        self.bin = self.base / "bin"; self.bin.mkdir()
        self.snapshots = self.base / "snapshots"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("CMUX", "TMUX", "CODEX", "RESURRECT"))}
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        CODEX_HOME=str(self.home), CODEX_TEST_LOG_DIR=str(self.logs),
                        CODEX_TEST_HOOK=str(ROOT / "scripts/codex_session_recorder.py"),
                        CODEX_TEST_EMITTER=str(ROOT / "tests/fixtures/emit_codex_hook.py"), LC_ALL="C")
        self.config = self.base / "tmux.conf"
        self.config.write_text("\n".join([
            # An explicit /bin/sh default-command adds an extra parent shell
            # under Linux dash; upstream ps intentionally captures direct children.
            "set -g default-shell /bin/sh", "set -g default-command 'exec /bin/sh'",
            "set -g status off", "set -g base-index 0", "set -g pane-base-index 0",
            "set -g @resurrect-save-command-strategy codex",
            "set -g @resurrect-processes '\"~codex resume\"'",
            "set -g @resurrect-dir " + shlex.quote(str(self.snapshots)),
        ]) + "\n")
        subprocess.run(["cc", str(ROOT / "tests/fixtures/codex.c"), "-o", str(self.bin / "codex")],
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
            value = predicate()
            if value:
                return value
            time.sleep(.04)
        self.fail("timed out waiting for private test pane")

    def launch(self, pane="work:0.0", args="--model test-model", prefix=""):
        before = set(self.logs.glob("*.argv"))
        self.tmux("send-keys", "-t", pane, prefix + "codex " + args, "C-m")
        log = self.wait(lambda: next(iter(set(self.logs.glob("*.argv")) - before), None))
        self.wait(lambda: log.stat().st_size)
        return int(log.stem)

    def transcript(self, session, source="cli"):
        path = self.home / "sessions" / (session + ".jsonl")
        path.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": session, "source": source, "cwd": str(self.base)}}) + "\n")
        return path

    def fire(self, pid, session=A, event="SessionStart", source="cli", **fields):
        path = self.transcript(session, source)
        payload = {"session_id": session, "hook_event_name": event,
                   "source": "startup", "cwd": str(self.base), "transcript_path": str(path),
                   **fields}
        done = self.logs / f"{pid}.done"
        old = done.read_text() if done.exists() else ""
        (self.logs / f"{pid}.payload").write_text(json.dumps(payload))
        os.kill(pid, signal.SIGUSR1)
        self.wait(lambda: done.exists() and done.read_text() != old and len(done.read_text().split()) == 2)
        self.assertEqual(done.read_text().split()[1], "0", "recording hooks must fail open")

    def binding(self, pane="work:0.0"):
        value = self.tmux("show-options", "-pqv", "-t", pane, OPTION).stdout.strip()
        return json.loads(value) if value else None

    def script(self, name, success=True):
        result = subprocess.run(["bash", str(ROOT / "scripts" / name), "quiet"],
                                env=self.env, text=True, capture_output=True, timeout=25)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, "unsafe save must report failure")
        return result

    def commands(self):
        return [l.split("\t")[-1].removeprefix(":")
                for l in (self.snapshots / "last").read_text().splitlines() if l.startswith("pane\t")]

    def test_hooks_record_two_same_directory_panes_and_restore_exact_ids(self):
        first = self.launch(); self.fire(first, A)
        self.tmux("split-window", "-d", "-t", "work:0", "-c", str(self.base))
        second = self.launch("work:0.1"); self.fire(second, B)
        self.assertIsNotNone(self.binding(), "standalone hook must bind the tmux pane")
        self.script("save.sh")
        self.assertIn("codex resume " + A, self.commands()[0])
        self.assertIn("codex resume " + B, self.commands()[1])
        old = set(self.logs.glob("*.argv"))
        self.tmux("kill-server"); self.start_server("0")
        self.script("restore.sh")
        self.wait(lambda: len(set(self.logs.glob("*.argv")) - old) == 2)
        self.wait(lambda: self.binding("work:0.0") and self.binding("work:0.1"))
        restored = [p.read_text().splitlines() for p in set(self.logs.glob("*.argv")) - old]
        self.assertEqual({tuple(a[1:3]) for a in restored}, {("resume", A), ("resume", B)})
        # No new user prompt: a native SessionStart event is enough for resave.
        self.script("save.sh")
        self.assertEqual({shlex.split(c)[4] for c in self.commands()}, {A, B})

    def test_missing_hook_defers_save_and_preserves_previous_snapshot(self):
        pid = self.launch(); self.fire(pid)
        self.script("save.sh")
        last = self.snapshots / "last"; before = (last.readlink(), last.read_bytes())
        self.tmux("set-option", "-p", "-t", "work:0.0", OPTION, "")
        r = self.script("save.sh", success=False)
        self.assertEqual((last.readlink(), last.read_bytes()), before)
        self.assertIn("Codex", r.stderr)

    def test_restored_idle_process_without_hook_cannot_erase_resume_snapshot(self):
        pid = self.launch(); self.fire(pid); self.script("save.sh")
        last = self.snapshots / "last"; before = (last.readlink(), last.read_bytes())
        old = set(self.logs.glob("*.argv"))
        self.tmux("kill-server")
        self.env["CODEX_TEST_SKIP_RESUME_HOOK"] = "1"
        self.start_server("0"); self.script("restore.sh")
        self.wait(lambda: len(set(self.logs.glob("*.argv")) - old) == 1)
        self.script("save.sh", success=False)
        self.assertEqual((last.readlink(), last.read_bytes()), before)
        self.assertIn("codex resume " + A, self.commands()[0])

    def test_switching_conversation_replaces_id_without_persisting_prompt(self):
        pid = self.launch(args='--model test-model --yolo "initial prompt"')
        self.fire(pid, A); self.fire(pid, B, event="UserPromptSubmit", prompt="secret prompt")
        self.script("save.sh"); command = self.commands()[0]
        self.assertIn("codex resume " + B, command)
        self.assertNotIn(A, command); self.assertNotIn("prompt", command)
        self.assertNotIn("prompt", json.dumps(self.binding()))
        self.assertIn("--yolo", command)

    def test_subagent_event_in_same_native_process_cannot_replace_parent(self):
        pid = self.launch(); self.fire(pid, A)
        original = self.binding()
        self.fire(pid, B, source={"subagent": {"thread_spawn": {"parent_thread_id": A}}})
        self.assertIsNotNone(original, "parent hook was never recorded")
        self.assertEqual(self.binding(), original)
        self.script("save.sh"); self.assertIn("codex resume " + A, self.commands()[0])

    def test_spoofed_hook_from_other_process_does_not_bind_pane(self):
        pid = self.launch(); self.fire(pid, A); before = self.binding()
        payload = {"session_id": B, "hook_event_name": "SessionStart", "cwd": str(self.base),
                   "transcript_path": str(self.transcript(B))}
        r = subprocess.run(["python3", str(ROOT / "scripts/codex_session_recorder.py")],
                           input=json.dumps(payload), text=True, capture_output=True,
                           env={**self.env, "TMUX_PANE": "%0"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(before)
        self.assertEqual(self.binding(), before)

    def test_stale_process_generation_blocks_save(self):
        pid = self.launch(); self.fire(pid)
        record = self.binding(); self.assertIsNotNone(record)
        record["pidStartSeconds"] -= 1
        self.tmux("set-option", "-p", "-t", "work:0.0", OPTION, json.dumps(record))
        self.script("save.sh", success=False)
        self.assertFalse((self.snapshots / "last").exists())

    def test_unverified_new_session_cannot_fall_back_to_prior_session(self):
        pid = self.launch(); self.fire(pid, A); self.script("save.sh")
        last = self.snapshots / "last"; before = (last.readlink(), last.read_bytes())
        self.fire(pid, B, event="UserPromptSubmit",
                  transcript_path=str(self.home / "sessions" / "not-ready.jsonl"))
        self.script("save.sh", success=False)
        self.assertEqual((last.readlink(), last.read_bytes()), before)
        # A later valid event recovers without restarting the plugin/server.
        self.fire(pid, B, event="UserPromptSubmit")
        self.script("save.sh"); self.assertIn("codex resume " + B, self.commands()[0])

    def test_rapid_identical_saves_keep_last_valid(self):
        pid = self.launch(); self.fire(pid)
        self.script("save.sh"); expected = self.commands()
        for _ in range(3):
            self.script("save.sh"); self.assertEqual(self.commands(), expected)

    def test_non_codex_uses_upstream_process_capture(self):
        self.tmux("send-keys", "-t", "work:0.0", "sleep 4321", "C-m")
        self.wait(lambda: self.tmux("display-message", "-p", "-t", "work:0.0", "#{pane_current_command}").stdout.strip() == "sleep")
        self.script("save.sh"); self.assertIn("sleep 4321", self.commands()[0])


if __name__ == "__main__":
    unittest.main()
