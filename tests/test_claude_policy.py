"""Claude Code pane identity, transcript origin and resume command policy."""
import importlib
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
A = "01950000-0000-7000-8000-000000000001"
B = "01950000-0000-7000-8000-000000000002"


class ClaudePolicyTests(unittest.TestCase):
    def setUp(self):
        self.common = importlib.import_module("conversation_common")
        self.claude = importlib.import_module("conversation_claude")
        p = self.common.Process
        self.processes = {10: p(10, 1, 10, 20, "pts/1", 100, "/bin/sh"),
                          20: p(20, 10, 20, 20, "pts/1", 101, "/opt/bin/claude"),
                          30: p(30, 1, 30, 40, "pts/2", 100, "/bin/sh"),
                          40: p(40, 30, 40, 40, "pts/2", 102, "/opt/bin/claude")}

    def record(self, session=A, pid=20, started=101, args=None):
        return {"sessionId": session, "pid": pid, "pidStartSeconds": started,
                "updatedAt": 200, "cwd": "/same project",
                "launchCommand": {"arguments": ["claude"] + (args or [])}}

    def test_two_panes_same_directory_restore_distinct_ids(self):
        records = [self.record(), self.record(B, 40, 102)]
        for pane, session in ((10, A), (30, B)):
            selected = self.common.select_record(pane, self.processes, records, "claude")
            self.assertEqual(shlex.split(self.claude.resume_command(selected)),
                             ["claude", "--resume", session])

    def test_stale_or_background_record_is_rejected(self):
        with self.assertRaises(self.common.UnsafeSession):
            self.common.select_record(10, self.processes, [self.record(started=99)], "claude")
        self.processes[20] = self.common.Process(20, 10, 20, 10, "pts/1", 101, "claude")
        with self.assertRaises(self.common.UnsafeSession):
            self.common.select_record(10, self.processes, [self.record()], "claude")

    def test_background_codex_child_does_not_hide_foreground_claude(self):
        dispatch = importlib.import_module("conversation_common")
        self.processes[21] = self.common.Process(21, 20, 21, 20, "pts/1", 103, "codex")
        self.assertEqual(dispatch.foreground_agent(10, self.processes, ("codex", "claude"))[0], "claude")

    def test_nonrestorable_new_session_blocks_old_id(self):
        marker = self.record(B)
        marker.update(updatedAt=300, isRestorable=False)
        with self.assertRaises(self.common.UnsafeSession):
            self.common.select_record(10, self.processes, [self.record(), marker], "claude")

    def test_resume_preserves_supported_options_and_config_dir(self):
        record = self.record(args=["--model", "sonnet", "--resume", B,
                                   "--permission-mode", "plan"])
        record["launchCommand"]["environment"] = {"CLAUDE_CONFIG_DIR": "/custom claude"}
        self.assertEqual(shlex.split(self.claude.resume_command(record)),
                         ["env", "CLAUDE_CONFIG_DIR=/custom claude", "claude", "--resume", A,
                          "--model", "sonnet", "--permission-mode", "plan"])

    def test_unknown_or_noninteractive_launch_defers(self):
        for args in (["-p", "prompt"], ["--worktree"], ["--add-dir", "/extra"],
                     ["mcp", "list"], ["initial prompt"], ["--model"]):
            with self.subTest(args=args), self.assertRaises(self.common.UnsafeSession):
                self.claude.resume_command(self.record(args=args))

    def test_transcript_must_be_root_session_and_matching_uuid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "projects" / "-my-project"
            project.mkdir(parents=True)
            path = project / (A + ".jsonl")
            path.write_text(json.dumps({"type": "user"}) + "\n")
            self.assertEqual(self.claude.root_session({"session_id": A,
                                                       "transcript_path": str(path)}, root), A)
            with self.assertRaises(self.common.UnsafeSession):
                self.claude.root_session({"session_id": B, "transcript_path": str(path)}, root)
            child = project / A / "subagents" / (A + ".jsonl")
            child.parent.mkdir(parents=True)
            child.write_text("{}\n")
            with self.assertRaises(self.common.UnsafeSession):
                self.claude.root_session({"session_id": A, "transcript_path": str(child)}, root)


if __name__ == "__main__":
    unittest.main()
