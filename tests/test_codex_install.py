"""Installation must preserve unrelated hooks and user configuration."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HookInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex install ")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home / "hooks.json"
        self.other = {"description": "user config", "hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "echo existing-cmux-hook"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "echo other"}]}]}}
        self.path.write_text(json.dumps(self.other))

    def run_installer(self, action, ok=True):
        r = subprocess.run([sys.executable, str(ROOT / "scripts/codex_hook_setup.py"), action,
                            "--codex-home", str(self.home)], capture_output=True, text=True)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        else:
            self.assertNotEqual(r.returncode, 0)
        return r

    def test_install_twice_then_uninstall_preserves_other_hooks(self):
        self.run_installer("install")
        first = self.path.read_bytes(); config = json.loads(first)
        self.assertEqual(config["hooks"]["SessionStart"][0], self.other["hooks"]["SessionStart"][0])
        self.assertEqual(len(config["hooks"]["SessionStart"]), 2)
        self.assertEqual(len(config["hooks"]["UserPromptSubmit"]), 1)
        self.assertEqual(config["hooks"]["Stop"], self.other["hooks"]["Stop"])
        self.run_installer("install"); self.assertEqual(self.path.read_bytes(), first)
        backups = list(self.home.glob("hooks.json.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), self.other)
        self.run_installer("uninstall")
        self.assertEqual(json.loads(self.path.read_text()), self.other)

    def test_invalid_json_is_not_overwritten(self):
        self.path.write_text("{broken")
        self.run_installer("install", ok=False)
        self.assertEqual(self.path.read_text(), "{broken")
        self.assertFalse(list(self.home.glob("hooks.json.bak.*")))

    def test_symlinked_config_keeps_symlink_and_backs_up_real_file(self):
        real = self.home / "tracked.json"
        self.path.rename(real); self.path.symlink_to(real)
        self.run_installer("install")
        self.assertTrue(self.path.is_symlink())
        self.assertIn("UserPromptSubmit", json.loads(real.read_text())["hooks"])
        self.assertTrue(list(self.home.glob("tracked.json.bak.*")))


if __name__ == "__main__":
    unittest.main()
