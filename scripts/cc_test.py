#!/usr/bin/env python3
"""Test scripts/cc.py and the cc / cc.cmd shims: the command surface of the former bash ./cc, usage errors and their
exit codes, the clear failure without Nix, and the shim's refusals (a bad uv download, a stale uv.lock)."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"
# Every command of the bash ./cc this CLI replaces (plan native-core, W1).
COMMANDS = ["doctor", "backup", "data", "relock", "gc", "show", "check", "build", "run", "verify", "validate", "agents", "hook",
            "bootstrap", "repo", "prs", "worktree", "migrate-layout", "trello", "open", "watch", "council", "eval",
            "settings", "plan", "feature"]


def run(*argv: str) -> tuple[int, str, str]:
    """cc.main in this process, without a feature context, which `feature` would otherwise leave behind."""
    out, err = io.StringIO(), io.StringIO()
    environment = {key: value for key, value in os.environ.items() if key != "CC_FEATURE"}
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.dict(os.environ, environment, clear=True):
        code = cc.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Surface(unittest.TestCase):
    def test_usage_lists_every_command(self) -> None:
        code, out, _ = run("help")
        self.assertEqual(code, 0)
        listed = {line.split()[0] for line in out.splitlines() if line.startswith("  ")}
        self.assertEqual(listed, set(COMMANDS))

    def test_no_command_prints_usage(self) -> None:
        self.assertEqual(run()[0], 0)
        for flag in ("-h", "--help"):
            self.assertEqual(run(flag)[0], 0)

    def test_every_command_is_dispatched(self) -> None:
        # A command that is not dispatched falls through to "unknown command"; none may.
        with mock.patch.object(cc, "script", return_value=0), mock.patch.object(cc, "nix", return_value=0), \
                mock.patch.object(cc, "require_nix", return_value="nix"), mock.patch.object(cc, "lock_flake"), \
                mock.patch.object(cc, "doctor", return_value=0), mock.patch.object(cc, "check", return_value=0), \
                mock.patch.object(cc, "feature_overrides", return_value=[]):
            for command in COMMANDS:
                code, _, err = run(command, "validate" if command == "bootstrap" else "x", "show")
                self.assertNotIn("unknown command", err, command)
                self.assertEqual(code, 0, f"{command}: {err}")

    def test_an_unknown_command_exits_2_with_usage(self) -> None:
        code, out, err = run("nope")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("error: unknown command: nope", err)
        self.assertIn("Usage: ./cc", err)


class UsageErrors(unittest.TestCase):
    def test_exit_codes_match_the_bash_cc(self) -> None:
        cases = {
            ("build",): (1, "usage: ./cc build <name>"),
            ("run",): (1, "usage: ./cc run <name>"),
            ("feature",): (1, "usage: ./cc feature <name>"),
            ("feature", "x"): (2, "error: feature requires a command"),
            ("bootstrap",): (2, "usage: ./cc bootstrap <install [target]|validate>"),
            ("bootstrap", "nope"): (2, "usage: ./cc bootstrap"),
        }
        for argv, (expected, message) in cases.items():
            code, _, err = run(*argv)
            self.assertEqual(code, expected, argv)
            self.assertIn(message, err, argv)

    def test_a_feature_runs_its_command_with_the_feature_set(self) -> None:
        seen: list[str | None] = []
        with mock.patch.object(cc, "check", side_effect=lambda _: seen.append(os.environ.get("CC_FEATURE")) or 0):
            self.assertEqual(run("feature", "demo", "check")[0], 0)
        self.assertEqual(seen, ["demo"])
        self.assertNotIn("CC_FEATURE", os.environ)


class WithoutNix(unittest.TestCase):
    def test_nix_commands_fail_clearly(self) -> None:
        real_which = shutil.which
        with mock.patch.object(cc.shutil, "which", side_effect=lambda name: None if name == "nix" else real_which(name)):
            # check and verify run natively without Nix (scripts/checks.py); running them here would recurse into this
            # test. show without arguments lists the check groups; with flake arguments it needs Nix.
            for argv in (("show", "--json"), ("build", "x"), ("run", "x"), ("relock",), ("gc",), ("eval", "run")):
                code, _, err = run(*argv)
                self.assertEqual(code, 1, argv)
                self.assertIn("needs the optional Nix extra", err, argv)


    def test_show_lists_the_check_groups_without_nix(self) -> None:
        real_which = shutil.which
        with mock.patch.object(cc.shutil, "which", side_effect=lambda name: None if name == "nix" else real_which(name)):
            code, out, _ = run("show")
        self.assertEqual(code, 0)
        self.assertIn("  core: hostos_test.py, cc_test.py, checks_test.py", out)
        self.assertIn("optional Nix extra", out)

    def test_verify_runs_without_nix_and_never_locks(self) -> None:
        real_which = shutil.which
        with mock.patch.object(cc.shutil, "which", side_effect=lambda name: None if name == "nix" else real_which(name)), \
                mock.patch.object(cc, "script", return_value=0) as called, mock.patch.object(cc, "lock_flake") as lock:
            self.assertEqual(run("verify", "api")[0], 0)
        called.assert_called_once_with("verify.py", "api")
        lock.assert_not_called()


class Doctor(unittest.TestCase):
    def test_a_cc_on_a_windows_drive_in_wsl_is_flagged(self) -> None:
        with mock.patch.object(cc.sys, "platform", "linux"), \
                mock.patch.object(cc.platform, "release", return_value="6.6.87.2-microsoft-standard-WSL2"):
            self.assertTrue(cc.wsl_on_windows_drive(Path("/mnt/c/Users/me/cc")))
            self.assertFalse(cc.wsl_on_windows_drive(Path("/home/me/cc")))
        with mock.patch.object(cc.sys, "platform", "linux"), mock.patch.object(cc.platform, "release", return_value="6.8.0-45-generic"):
            self.assertFalse(cc.wsl_on_windows_drive(Path("/mnt/c/Users/me/cc")))

    def test_uv_is_found(self) -> None:
        self.assertIsNotNone(cc.uv_binary(), "the shim ran this test through uv")


class Forwarding(unittest.TestCase):
    def test_a_forwarded_command_runs_its_script_with_this_interpreter(self) -> None:
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "cc.py"), "plan", "list"],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        # plans.py's own output: a table, or "no plans" in a clean clone without an active plan.
        self.assertTrue("LIFECYCLE" in result.stdout or result.stdout == "no plans\n", result.stdout)


class Shim(unittest.TestCase):
    """The shim in a copy of the CC's uv files, with the uv already downloaded copied in (no network)."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.copy = Path(self.temporary.name) / "cc"
        (self.copy / "scripts").mkdir(parents=True)
        for name in ("cc", "cc.cmd", ".uv-pin", ".python-version", "pyproject.toml", "uv.lock"):
            shutil.copy2(ROOT / name, self.copy / name)
        for name in ("cc.py", "uv-bootstrap.ps1"):
            shutil.copy2(ROOT / "scripts" / name, self.copy / "scripts" / name)
        version = next(line.split()[1] for line in (ROOT / ".uv-pin").read_text().splitlines()
                       if line.startswith("version "))
        uv = ROOT / ".cc-local" / "tools" / "uv" / version / ("uv.exe" if WINDOWS else "uv")
        if not uv.is_file():
            self.skipTest(f"no downloaded uv at {uv}; run the shim once first")
        self.uv_dir = self.copy / ".cc-local" / "tools" / "uv" / version
        self.uv_dir.mkdir(parents=True)
        shutil.copy2(uv, self.uv_dir / uv.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def shim(self, *argv: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(ROOT / ".cc-local" / "tools" / "python")}
        command = ["cmd", "/c", str(self.copy / "cc.cmd")] if WINDOWS else [str(self.copy / "cc")]
        return subprocess.run([*command, *argv], capture_output=True, text=True, encoding="utf-8", env=env,
                              timeout=600, input=stdin)

    def test_a_hook_reaches_the_guard_even_with_a_broken_cli(self) -> None:
        shutil.copy2(ROOT / "scripts" / "hook_guard.py", self.copy / "scripts" / "hook_guard.py")
        (self.copy / "scripts" / "cc.py").write_text("this is not python\n", encoding="utf-8")
        (self.copy / "scripts" / "rule_hooks.py").write_text("import missing_module\n", encoding="utf-8")
        result = self.shim("hook", "pre-bash", stdin='{"tool_input": {"command": "git push"}}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"permissionDecision": "deny"', result.stdout)
        self.assertIn("missing_module", result.stdout)

    def test_the_shim_runs_the_cli(self) -> None:
        result = self.shim("help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: ./cc", result.stdout)

    def test_a_stale_lock_is_refused_and_left_unchanged(self) -> None:
        pyproject = self.copy / "pyproject.toml"
        pyproject.write_text(pyproject.read_text().replace('"textual>=6",', '"textual>=6",\n  "six",'))
        before = (self.copy / "uv.lock").read_bytes()
        result = self.shim("help")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Usage: ./cc", result.stdout)
        self.assertEqual((self.copy / "uv.lock").read_bytes(), before)

    def test_a_download_with_a_wrong_hash_is_refused(self) -> None:
        shutil.rmtree(self.uv_dir)
        pin = self.copy / ".uv-pin"
        pin.write_text("\n".join(line.rsplit(" ", 1)[0] + " " + "0" * 64 if line.startswith("uv-") else line
                                 for line in pin.read_text().splitlines()) + "\n")
        result = self.shim("help")
        if "SHA-256 mismatch" not in result.stderr and ("resolve" in result.stderr or "connect" in result.stderr):
            self.skipTest(f"no network: {result.stderr.strip()[-200:]}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertFalse((self.uv_dir / ("uv.exe" if WINDOWS else "uv")).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
