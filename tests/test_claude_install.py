"""Claude hook installation preserves settings and unrelated handlers."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ClaudeInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="claude install ")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home / "settings.json"
        self.original = {"theme": "dark", "hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "echo existing"}]}]}}
        self.path.write_text(json.dumps(self.original))

    def run_installer(self, action):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/conversation_hook_setup.py"), action,
                                 "--provider", "claude", "--config-dir", str(self.home)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_install_reload_uninstall(self):
        self.run_installer("install")
        first = self.path.read_bytes()
        config = json.loads(first)
        self.assertEqual(config["theme"], "dark")
        self.assertEqual(config["hooks"]["SessionStart"][0], self.original["hooks"]["SessionStart"][0])
        self.assertEqual(len(config["hooks"]["SessionStart"]), 2)
        self.assertIn("UserPromptSubmit", config["hooks"])
        self.run_installer("install")
        self.assertEqual(self.path.read_bytes(), first)
        self.run_installer("uninstall")
        self.assertEqual(json.loads(self.path.read_text()), self.original)


if __name__ == "__main__":
    unittest.main()
