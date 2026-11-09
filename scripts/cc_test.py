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
import hostos  # noqa: E402
import rule_hooks  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"
# Every command of the bash ./cc this CLI replaces (docs/kit/decisions/0033-native-core-nix-optional.md).
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

    def doctor(self, claude: tuple[int, ...] | None, tools: tuple[str, ...] = ("git", "claude"), onedrive: bool = False
               ) -> tuple[int, str]:
        """cc doctor with only `tools` on PATH, Git Bash present, the CC in OneDrive or not, and no sub-scripts."""
        with mock.patch.object(cc.shutil, "which", side_effect=lambda name: name if name in tools else None), \
                mock.patch.object(hostos, "resolve_exe", side_effect=lambda name: name if name in tools else None), \
                mock.patch.object(rule_hooks, "claude_version", return_value=claude), \
                mock.patch.object(cc, "uv_binary", return_value=None), mock.patch.object(cc, "git_bash", return_value=True), \
                mock.patch.object(cc, "in_onedrive", return_value=onedrive), mock.patch.object(cc, "script", return_value=0), \
                mock.patch.object(cc.subprocess, "run",
                                  return_value=subprocess.CompletedProcess([], 1, stdout="git version 2.50.0\n")):
            code, out, _ = run("doctor")
        return code, out

    def test_doctor_exits_0_without_an_error_line(self) -> None:
        code, out = self.doctor((2, 1, 318))
        self.assertNotIn("error:", out)
        self.assertEqual(code, 0, out)
        self.assertIn("claude 2.1.318", out)

    def test_doctor_exits_1_when_it_printed_an_error_line(self) -> None:
        code, out = self.doctor((2, 1, 318), tools=("claude",))
        self.assertIn("error: git is missing", out)
        self.assertEqual(code, 1, out)

    def test_a_claude_code_too_old_for_fail_closed_hooks_is_an_error(self) -> None:
        code, out = self.doctor((2, 1, 294))
        self.assertIn("error: Claude Code 2.1.294 is older than 2.1.295", out)
        self.assertEqual(code, 1, out)
        self.assertEqual(self.doctor((2, 1, 295))[0], 0, "2.1.295 is the first version that blocks on a failed hook")

    def test_a_missing_claude_is_only_a_note(self) -> None:
        code, out = self.doctor(None, tools=("git",))
        self.assertIn("note: Claude Code is not found", out)
        self.assertEqual(code, 0, out)

    def test_an_unreadable_claude_version_is_a_warning(self) -> None:
        code, out = self.doctor(None)
        self.assertIn("warning: cannot read the Claude Code version", out)
        self.assertEqual(code, 0, out)

    def test_the_claude_version_is_parsed_from_its_output(self) -> None:
        answer = subprocess.CompletedProcess([], 0, stdout="2.1.318 (Claude Code)\n")
        with mock.patch.object(rule_hooks.hostos, "resolve_exe", return_value="claude"), \
                mock.patch.object(rule_hooks.subprocess, "run", return_value=answer):
            self.assertEqual(rule_hooks.claude_version(), (2, 1, 318))
        self.assertLess((2, 1, 294), rule_hooks.FAIL_CLOSED_CLAUDE)
        self.assertGreaterEqual((2, 1, 295), rule_hooks.FAIL_CLOSED_CLAUDE)
        self.assertGreaterEqual((2, 2, 0), rule_hooks.FAIL_CLOSED_CLAUDE)

    def test_a_cc_inside_onedrive_is_found(self) -> None:
        base = Path("/home/me")
        self.assertTrue(cc.in_onedrive(base / "Sync" / "cc", {"OneDriveCommercial": str(base / "Sync")}))
        self.assertTrue(cc.in_onedrive(base / "OneDrive - Foxcale" / "code" / "cc", {}))
        self.assertTrue(cc.in_onedrive(base / "onedrive" / "cc", {}))
        self.assertFalse(cc.in_onedrive(base / "code" / "cc", {"OneDrive": str(base / "OneDrive")}))
        self.assertFalse(cc.in_onedrive(base / "SyncOther" / "cc", {"OneDriveConsumer": str(base / "Sync"), "OneDrive": ""}))

    def test_doctor_warns_for_a_cc_inside_onedrive(self) -> None:
        code, out = self.doctor((2, 1, 318), onedrive=True)
        self.assertIn("warning: this CC is inside OneDrive", out)
        self.assertEqual(code, 0, out)
        self.assertNotIn("OneDrive", self.doctor((2, 1, 318))[1])


class Check(unittest.TestCase):
    def stamp(self, nix: str | None) -> mock.MagicMock:
        import checks
        import verified
        import verify
        with mock.patch.object(checks, "main", return_value=0), \
                mock.patch.object(verify, "tree_snapshot", return_value={cc.ROOT: "tree"}), \
                mock.patch.object(hostos, "resolve_exe", side_effect=lambda name: nix if name == "nix" else name), \
                mock.patch.object(verified, "record") as record, mock.patch.object(cc.subprocess, "run"):
            self.assertEqual(cc.check([]), 0)
        return record

    def test_a_pass_with_nix_stamps_the_nix_part_as_run(self) -> None:
        self.stamp("nix").assert_called_once_with(cc.ROOT, cc.ROOT, "check", tree="tree", nix=True)

    def test_a_pass_without_nix_stamps_the_nix_part_as_not_run(self) -> None:
        self.stamp(None).assert_called_once_with(cc.ROOT, cc.ROOT, "check", tree="tree", nix=False)


class Council(unittest.TestCase):
    def test_status_outside_a_control_center_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "council" / "run.py"), directory, "status"],
                                    capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("not a Control Center yet: run ./cc bootstrap install", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("No such file", result.stderr)


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

    def shim(self, *argv: str, stdin: str | None = None, env: dict[str, str] | None = None
             ) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(ROOT / ".cc-local" / "tools" / "python"), **(env or {})}
        command = ["cmd", "/c", str(self.copy / "cc.cmd")] if WINDOWS else [str(self.copy / "cc")]
        return subprocess.run([*command, *argv], capture_output=True, text=True, encoding="utf-8", env=env,
                              timeout=600, input=stdin)

    def test_a_hook_on_a_cc_not_set_up_is_refused_at_once_without_a_download(self) -> None:
        import json
        import time
        for missing in ("venv", "uv and venv"):
            if missing == "uv and venv":
                shutil.rmtree(self.uv_dir)
            for event in ("pre-bash", "stop"):
                with self.subTest(missing=missing, event=event):
                    started = time.monotonic()
                    result = self.shim("hook", event, stdin='{"tool_input": {"command": "ls"}}')
                    elapsed = time.monotonic() - started
                    self.assertEqual(result.returncode, 0, result.stderr)
                    answer = json.loads(result.stdout)
                    if event == "pre-bash":
                        self.assertEqual(answer["hookSpecificOutput"]["permissionDecision"], "deny")
                        text = answer["hookSpecificOutput"]["permissionDecisionReason"]
                    else:
                        text = answer["systemMessage"]
                    self.assertIn("not set up on this machine yet: run ./cc doctor (or .\\cc.cmd doctor)", text)
                    self.assertNotIn("downloading", result.stderr)
                    self.assertLess(elapsed, 5, "a hook that is not set up must answer at once (under 2 s locally)")
        self.assertEqual(sorted(path.name for path in (self.copy / ".cc-local").rglob("*")), ["tools", "uv"])

    def test_a_hook_after_a_pin_update_uses_the_uv_already_here(self) -> None:
        self.assertEqual(self.shim("help").returncode, 0)  # set the CC up with the pinned uv
        shutil.copy2(ROOT / "scripts" / "hook_guard.py", self.copy / "scripts" / "hook_guard.py")
        self.uv_dir.rename(self.uv_dir.parent / "0.0.1")  # a pull moved the pin; only the older uv is here
        result = self.shim("hook", "pre-bash", stdin='{"tool_input": {"command": "ls"}}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"hookEventName": "PreToolUse"', result.stdout)  # the guard answered, not the shim
        self.assertNotIn("not set up", result.stdout)
        self.assertNotIn("downloading", result.stderr)
        self.assertFalse(self.uv_dir.exists(), "a hook must never download uv")

    def test_a_user_uv_config_does_not_reach_the_cc(self) -> None:
        config = Path(self.temporary.name) / "config"
        (config / "uv").mkdir(parents=True)
        (config / "uv" / "uv.toml").write_text("this is not toml\n", encoding="utf-8")
        result = self.shim("help", env={"APPDATA": str(config), "XDG_CONFIG_HOME": str(config)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: ./cc", result.stdout)

    def test_a_multi_line_argument_reaches_the_cli_whole_or_is_refused(self) -> None:
        (self.copy / "scripts" / "cc.py").write_text("import sys\nprint(repr(sys.argv[1:]))\n", encoding="utf-8")
        text = 'line one\nline "two" & three'
        result = self.shim("plan", text, "after")
        if WINDOWS:  # cmd.exe would cut the command at the line break and drop the rest without a word
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("an argument has a line break", result.stderr)
            self.assertEqual(result.stdout, "")
        else:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), repr(["plan", text, "after"]))
        self.assertEqual(self.shim("plan", "one line", "after").stdout.strip(), repr(["plan", "one line", "after"]))

    def test_a_hook_reaches_the_guard_even_with_a_broken_cli(self) -> None:
        self.assertEqual(self.shim("help").returncode, 0)  # set the CC up, so the hook does not refuse at once
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
