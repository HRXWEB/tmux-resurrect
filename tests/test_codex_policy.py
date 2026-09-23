"""Identity and command-policy regression tests using synthetic hook records."""
import importlib
from pathlib import Path
import shlex
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
A = "01950000-0000-7000-8000-000000000001"
B = "01950000-0000-7000-8000-000000000002"


class CodexPolicyTests(unittest.TestCase):
    def setUp(self):
        try:
            self.m = importlib.import_module("codex_common")
        except ModuleNotFoundError:
            self.fail("standalone Codex policy has not been implemented")
        p = self.m.Process
        self.processes = {10: p(10, 1, 10, 20, "pts/1", 100, "/bin/sh"),
                          20: p(20, 10, 20, 20, "pts/1", 101, "/opt/bin/codex"),
                          30: p(30, 1, 30, 40, "pts/2", 100, "/bin/sh"),
                          40: p(40, 30, 40, 40, "pts/2", 102, "/opt/bin/codex")}

    def record(self, session=A, pid=20, started=101, args=None):
        return {"sessionId": session, "pid": pid, "pidStartSeconds": started,
                "updatedAt": 200, "cwd": "/same project", "surfaceId": "shared-surface",
                "launchCommand": {"executablePath": "/opt/bin/codex",
                                  "arguments": ["codex"] + (args or []),
                                  "workingDirectory": "/same project"}}

    def test_same_cwd_and_surface_do_not_override_process_ownership(self):
        records = [self.record(), self.record(B, 40, 102)]
        self.assertEqual(self.m.select_record(10, self.processes, records)["sessionId"], A)
        self.assertEqual(self.m.select_record(30, self.processes, records)["sessionId"], B)

    def test_reused_pid_and_missing_generation_are_rejected(self):
        for start in (99, None):
            with self.subTest(start=start), self.assertRaises(self.m.UnsafeSession):
                self.m.select_record(10, self.processes, [self.record(started=start)])

    def test_background_codex_does_not_resume_as_foreground(self):
        self.processes[20] = self.m.Process(20, 10, 20, 10, "pts/1", 101, "codex")
        self.processes[10] = self.m.Process(10, 1, 10, 10, "pts/1", 100, "sh")
        with self.assertRaises(self.m.UnsafeSession):
            self.m.select_record(10, self.processes, [self.record()])

    def test_child_agent_cannot_replace_main_codex(self):
        self.processes[21] = self.m.Process(21, 20, 20, 20, "pts/1", 103, "codex")
        child = self.record(B, 21, 103)
        child["updatedAt"] = 300
        self.assertEqual(self.m.select_record(10, self.processes, [self.record(), child])["sessionId"], A)
        with self.assertRaises(self.m.UnsafeSession):
            self.m.select_record(10, self.processes, [child])

    def test_new_session_in_same_process_uses_latest_hook_event(self):
        latest = self.record(B)
        latest["updatedAt"] = 300
        self.assertEqual(self.m.select_record(10, self.processes, [self.record(), latest])["sessionId"], B)
        latest["updatedAt"] = 200
        with self.assertRaises(self.m.UnsafeSession):
            self.m.select_record(10, self.processes, [self.record(), latest])

    def test_nonrestorable_latest_session_does_not_fall_back_to_older_session(self):
        latest = self.record(B)
        latest.update(updatedAt=300, isRestorable=False)
        with self.assertRaises(self.m.UnsafeSession):
            self.m.select_record(10, self.processes, [self.record(), latest])

    def test_nonrestorable_marker_without_session_id_reports_its_state(self):
        marker = self.record()
        marker.pop("sessionId")
        marker["isRestorable"] = False
        with self.assertRaisesRegex(self.m.UnsafeSession, "non-restorable"):
            self.m.select_record(10, self.processes, [marker])

    def test_exec_codex_at_pane_root_is_supported(self):
        self.assertEqual(self.m.select_record(20, self.processes, [self.record()])["sessionId"], A)

    def test_missing_or_nonrestorable_record_is_skipped(self):
        bad = self.record()
        bad["isRestorable"] = False
        for records in ([], [bad]):
            with self.subTest(records=records), self.assertRaises(self.m.UnsafeSession):
                self.m.select_record(10, self.processes, records)

    def test_non_codex_pane_requests_original_strategy(self):
        with self.assertRaises(self.m.NotCodex):
            self.m.select_record(30, {30: self.processes[30]}, [])

    def test_resume_drops_old_selector_and_prompt_and_preserves_options(self):
        r = self.record(args=["--model", "test-model", "resume", B, "old prompt", "--yolo", "--last"])
        self.assertEqual(shlex.split(self.m.resume_command(r)),
                         ["codex", "resume", A, "--model", "test-model", "--yolo", "--cd", "/same project"])

    def test_custom_home_and_shell_metacharacters_are_literal(self):
        value = 'model_reasoning_effort="high"; $(touch /tmp/should-not-exist)'
        r = self.record(args=["-c", value, "--profile", "a'b"])
        r["launchCommand"]["environment"] = {"CODEX_HOME": "/custom home", "SECRET_TOKEN": "not-replayed"}
        argv = shlex.split(self.m.resume_command(r))
        self.assertEqual(argv[:5], ["env", "CODEX_HOME=/custom home", "codex", "resume", A])
        self.assertIn(value, argv)
        self.assertIn("a'b", argv)
        self.assertNotIn("not-replayed", argv)

    def test_unsupported_invocations_are_rejected_instead_of_losing_options(self):
        for args in (["exec", "do something"], ["--remote", "ws://host"],
                     ["--unknown-flag"], ["-m"], ["--worktree"], ["--image", "photo.png"],
                     ["-c", "hooks.SessionStart=[]"]):
            with self.subTest(args=args), self.assertRaises(self.m.UnsafeSession):
                self.m.resume_command(self.record(args=args))

    def test_missing_capture_invalid_ids_and_control_characters_are_rejected(self):
        cases = []
        bad = self.record(); bad.pop("launchCommand"); cases.append(bad)
        bad = self.record(); bad["sessionId"] = "bad; command"; cases.append(bad)
        bad = self.record(); bad["cwd"] = "/a\nnew line"; cases.append(bad)
        bad = self.record(args=["-c", "key=\tvalue"]); cases.append(bad)
        for r in cases:
            with self.subTest(record=r), self.assertRaises(self.m.UnsafeSession):
                self.m.resume_command(r)


if __name__ == "__main__":
    unittest.main()
