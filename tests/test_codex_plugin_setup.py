"""Loading the plugin must register hooks without a manual installer command."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TPM = os.environ.get("RESURRECT_TEST_TPM")


@unittest.skipUnless(shutil.which("tmux"), "requires tmux")
class PluginSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="resurrect-setup-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.socket = self.base / "tmux.sock"
        self.home = self.base / "codex home"
        self.home.mkdir()
        self.path = self.home / "hooks.json"
        self.xdg = self.base / "config"
        (self.xdg / "tmux").mkdir(parents=True)
        self.config = self.xdg / "tmux/tmux.conf"
        self.settings = ("set -g default-shell /bin/sh\n"
                         "set -g default-command 'exec /bin/sh'\n"
                         "set -g status off\n")
        self.config.write_text(self.settings)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("TMUX", "CMUX", "CODEX", "RESURRECT"))}
        self.env.update(CODEX_HOME=str(self.home), XDG_CONFIG_HOME=str(self.xdg))
        self.addCleanup(lambda: self.tmux("kill-server", check=False))
        self.tmux("new-session", "-d", "-s", "setup")

    def tmux(self, *args, check=True):
        return subprocess.run(["tmux", "-S", str(self.socket), "-f", str(self.config), *args],
                              env=self.env, text=True, capture_output=True, check=check, timeout=30)

    def load(self, strategy="codex"):
        self.tmux("set-option", "-g", "@resurrect-save-command-strategy", strategy)
        self.tmux("run-shell", "bash " + shlex.quote(str(ROOT / "resurrect.tmux")))

    def status(self):
        return self.tmux("show-options", "-gqv", "@resurrect-codex-hooks-status").stdout.strip()

    def assert_registered(self):
        self.assertTrue(self.path.exists(), "plugin load did not register Codex hooks")
        config = json.loads(self.path.read_text())
        self.assertIn("hooks", config, "plugin load did not add hook configuration")
        for event in ("SessionStart", "UserPromptSubmit"):
            self.assertIn(event, config["hooks"], "plugin load did not register the event")
            handlers = [hook for group in config["hooks"][event] for hook in group["hooks"]
                        if "--tmux-resurrect-codex-hook-v1" in hook.get("command", "")]
            self.assertEqual(len(handlers), 1)
            argv = shlex.split(handlers[0]["command"])
            self.assertEqual(Path(argv[1]).name, "codex_session_recorder.py")
            self.assertTrue(Path(argv[1]).is_file(), "registered recorder must exist")
        self.assertEqual(self.status(), "registered")
        return config

    def test_reload_registers_once_and_preserves_existing_hooks_and_trust_config(self):
        other = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo user-hook"}]}]}}
        self.path.write_text(json.dumps(other))
        trust = self.home / "config.toml"
        trust.write_text('# Existing trust configuration stays user-owned.\n')
        before_trust = trust.read_bytes()
        self.load()
        config = self.assert_registered()
        self.assertEqual(config["hooks"]["Stop"], other["hooks"]["Stop"])
        before = (self.path.read_bytes(), self.path.stat().st_mtime_ns)
        self.load()
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)
        self.assertEqual(len(list(self.home.glob("hooks.json.bak.*"))), 1)
        self.assertEqual(trust.read_bytes(), before_trust)

    def test_default_strategy_leaves_codex_configuration_untouched(self):
        self.load("ps")
        self.assertFalse(self.path.exists())
        self.assertEqual(self.status(), "")
        self.assertTrue(self.tmux("show-options", "-gqv", "@resurrect-save-script-path").stdout.strip())

    def test_bad_config_reports_failure_without_breaking_plugin_and_can_retry(self):
        self.path.write_text("{broken")
        self.load()
        self.assertEqual(self.path.read_text(), "{broken")
        self.assertEqual(self.status(), "error")
        self.assertTrue(self.tmux("list-keys", "-T", "prefix", "C-s").stdout.strip())
        self.path.write_text("{}")
        self.load()
        self.assert_registered()

    def test_unavailable_python_reports_failure_and_preserves_configuration(self):
        self.path.write_text("{}")
        bin_dir = self.base / "bin"
        bin_dir.mkdir()
        python = bin_dir / "python3"
        python.write_text("#!/bin/sh\nexit 127\n")
        python.chmod(0o755)
        self.tmux("set-environment", "-g", "PATH", str(bin_dir) + os.pathsep + self.env["PATH"])
        self.load()
        self.assertEqual(self.status(), "error")
        self.assertEqual(self.path.read_text(), "{}")
        self.assertTrue(self.tmux("list-keys", "-T", "prefix", "C-r").stdout.strip())

    def test_concurrent_plugin_loads_create_only_one_registration_and_backup(self):
        self.path.write_text("{}")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.load(), range(4)))
        self.assert_registered()
        self.assertEqual(len(list(self.home.glob("hooks.json.bak.*"))), 1)

    @unittest.skipUnless(TPM, "set RESURRECT_TEST_TPM to a TPM checkout for its real install binding")
    def test_tpm_install_binding_clones_plugin_and_registers_hooks(self):
        # A local git source avoids network access and tests the working tree,
        # including changes that have not been committed to the developer's repo.
        source = self.base / "source/tmux-resurrect"
        source.mkdir(parents=True)
        shutil.copy2(ROOT / "resurrect.tmux", source / "resurrect.tmux")
        shutil.copytree(ROOT / "scripts", source / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        for args in (("init", "-b", "test"), ("add", "."),
                     ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "commit", "-m", "Local plugin fixture")):
            subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
                           cwd=source, env=self.env, check=True, capture_output=True)
        plugins = self.base / "plugins"
        plugins.mkdir()
        self.tmux("set-environment", "-g", "TMUX_PLUGIN_MANAGER_PATH", str(plugins))
        self.config.write_text(self.settings +
            "set -g @resurrect-save-command-strategy codex\n" +
            "set -g @resurrect-processes '\"~codex resume\"'\n" +
            "set -g @plugin " + shlex.quote(str(source) + "#test") + "\n" +
            "run-shell " + shlex.quote(str(Path(TPM) / "tpm")) + "\n")
        self.tmux("source-file", str(self.config))
        self.assertFalse(self.path.exists())
        # Execute precisely the command registered for prefix + I, in the
        # private server. No direct hook-installer invocation is involved.
        binding = shlex.split(self.tmux("list-keys", "-T", "prefix", "I").stdout)
        self.tmux(*binding[binding.index("run-shell"):])
        config = self.assert_registered()
        command = config["hooks"]["SessionStart"][-1]["hooks"][0]["command"]
        self.assertEqual(Path(shlex.split(command)[1]).parent,
                         (plugins / "tmux-resurrect/scripts").resolve())


if __name__ == "__main__":
    unittest.main()
