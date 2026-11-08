#!/usr/bin/env python3
"""Test scripts/checks.py: the registry matches the checks of flake.nix, a failing script fails its row and the exit
code, a passing one passes, the nix extra is a note without Nix, every group runs in a fresh temporary HOME, and the
summary CI compares across OSes counts each step's tests (scripts/ci_compare.py, tested here too)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checks  # noqa: E402
import ci_compare  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NIX_ONLY = {"contract-check", "gate-venv"}


def flake_checks() -> dict[str, tuple[str, list[str]]]:
    """Each check of flake.nix: (working directory, scripts it runs in order), paths relative to the CC root."""
    text = (ROOT / "flake.nix").read_text(encoding="utf-8")
    text = text[text.index("checks =\n"):text.index("apps =\n")]
    starts = list(re.finditer(r'pkgs\.runCommand "control-center-([\w-]+)"', text))
    found = {}
    for index, start in enumerate(starts):
        body = text[start.end():starts[index + 1].start() if index + 1 < len(starts) else len(text)]
        cwd = re.search(r"cd \$\{self\}/(\S+)", body)
        directory = cwd.group(1) if cwd else "."
        scripts = [match.group(1) or f"{directory}/{match.group(2)}"
                   for match in re.finditer(r"\$\{self\}/(scripts/\S+\.py)|python (?:-B )?(\w+\.py)", body)]
        found[start.group(1)] = (directory, scripts)
    return found


def quiet_run(*args, **kwargs) -> tuple[int, list[checks.Result], str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        status, results = checks.run(*args, **kwargs)
    return status, results, out.getvalue()


class Registry(unittest.TestCase):
    def test_the_registry_has_every_flake_check_plus_core(self) -> None:
        with tempfile.TemporaryDirectory() as materialized:
            (Path(materialized) / "control-center.json").write_text("{}", encoding="utf-8")
            groups = {group.name: group for group in checks.registry(Path(materialized))}
        flake = flake_checks()
        # Hand-checked against flake.nix: 13 checks, of which two are Nix-only.
        self.assertEqual(len(flake), 13)
        self.assertEqual(set(groups), (set(flake) - NIX_ONLY) | {"core"})
        for name, (cwd, scripts) in flake.items():
            if name in NIX_ONLY:
                continue
            self.assertEqual([step[0] for step in groups[name].steps], scripts, name)
            self.assertEqual(groups[name].cwd, cwd, name)
        self.assertEqual(flake["council"], ("scripts/council",
                                            ["scripts/council/providers_test.py", "scripts/council/council_test.py"]))
        self.assertEqual([step[0] for step in groups["core"].steps],
                         ["scripts/hostos_test.py", "scripts/cc_test.py", "scripts/checks_test.py"])

    def test_bootstrap_only_with_control_center_json(self) -> None:
        with tempfile.TemporaryDirectory() as template:
            self.assertNotIn("bootstrap", [group.name for group in checks.registry(Path(template))])
            (Path(template) / "control-center.json").write_text("{}", encoding="utf-8")
            bootstrap = [group for group in checks.registry(Path(template)) if group.name == "bootstrap"]
        self.assertEqual(bootstrap[0].steps, [("scripts/validate-control-center.py", checks.ROOT_ARGUMENT)])


def summary(platform: str, steps: list[tuple[str, int | None, int]], status: str = "pass") -> dict:
    return {"platform": platform, "groups": {"g": {"status": status, "steps": [
        {"script": script, "status": status, "ran": ran, "skipped": skipped} for script, ran, skipped in steps]}}}


class Compare(unittest.TestCase):
    STEPS = [("scripts/a_test.py", 4, 0), ("scripts/plain.py", None, 0), ("scripts/store_gc_test.py", 5, 0)]
    WINDOWS = [("scripts/a_test.py", 4, 0), ("scripts/plain.py", None, 0), ("scripts/store_gc_test.py", 5, 5)]

    def test_the_same_tests_everywhere_pass(self) -> None:
        self.assertEqual(ci_compare.differences(
            [summary("linux", self.STEPS), summary("darwin", self.STEPS), summary("win32", self.WINDOWS)]), [])

    def test_a_windows_skip_on_purpose_fails(self) -> None:
        windows = [("scripts/a_test.py", 4, 1), *self.WINDOWS[1:]]
        found = ci_compare.differences([summary("linux", self.STEPS), summary("win32", windows)])
        self.assertEqual(len(found), 1, found)
        self.assertIn("scripts/a_test.py", found[0])
        self.assertIn("win32 skipped 1", found[0])

    def test_a_missing_skip_that_is_expected_fails_too(self) -> None:
        found = ci_compare.differences([summary("linux", self.STEPS), summary("win32", self.STEPS)])
        self.assertTrue(any("store_gc_test.py" in line for line in found), found)

    def test_a_different_count_a_missing_step_and_a_failed_step_fail(self) -> None:
        fewer = [("scripts/a_test.py", 3, 0), ("scripts/plain.py", None, 0)]
        found = ci_compare.differences([summary("linux", self.STEPS), summary("darwin", fewer)])
        self.assertTrue(any("a_test.py" in line and "ran" in line for line in found), found)
        self.assertTrue(any("store_gc_test.py" in line and "not run on darwin" in line for line in found), found)
        found = ci_compare.differences([summary("linux", self.STEPS), summary("darwin", self.STEPS, "FAIL")])
        self.assertTrue(found, "a failed step fails the comparison")


class Runner(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        self.write("ok.py", "print('fine')\n")
        self.write("fail.py", "import sys\nprint('broken here')\nsys.exit(3)\n")
        self.write("marker.py", "from pathlib import Path\nPath('ran').write_text('x')\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, name: str, text: str) -> None:
        (self.root / "scripts" / name).write_text(text, encoding="utf-8")

    def test_a_failing_script_fails_its_row_and_the_exit(self) -> None:
        groups = [checks.Group("good", [("scripts/ok.py",)]),
                  checks.Group("bad", [("scripts/fail.py",), ("scripts/marker.py",)])]
        status, results, out = quiet_run(groups, self.root)
        self.assertEqual(status, 1)
        self.assertEqual([(result.name, result.status) for result in results], [("good", "pass"), ("bad", "FAIL")])
        self.assertRegex(out, r"(?m)^bad\s+FAIL\s+\d+\.\ds$")
        self.assertIn("broken here", out)
        self.assertIn("exited with 3", out)
        self.assertFalse((self.root / "ran").exists(), "a step after a failing one must not run")

    def test_passing_scripts_pass(self) -> None:
        groups = [checks.Group("good", [("scripts/ok.py",), ("scripts/marker.py",)])]
        status, results, out = quiet_run(groups, self.root)
        self.assertEqual((status, results[0].status), (0, "pass"))
        self.assertTrue((self.root / "ran").exists())
        self.assertNotIn("fine", out, "a passing group's output is not shown")

    def test_the_root_argument_and_cwd(self) -> None:
        (self.root / "scripts" / "sub").mkdir()
        self.write("where.py", "import os, sys\nprint(sys.argv[1:], os.getcwd())\n")
        result = checks.run_group(checks.Group("g", [("scripts/where.py", checks.ROOT_ARGUMENT, "x")],
                                               cwd="scripts/sub"), self.root)
        self.assertIn(f"[{str(self.root)!r}, 'x']", result.output)
        self.assertIn(str(self.root / "scripts" / "sub"), result.output)

    def test_a_group_runs_in_a_fresh_temporary_home(self) -> None:
        self.write("home.py", "import os\nfor key in ('HOME', 'USERPROFILE'):\n"
                              "    print(key, os.environ[key], os.path.isdir(os.environ[key]))\n")
        result = checks.run_group(checks.Group("g", [("scripts/home.py",)]), self.root)
        homes = dict(line.split(" ", 1) for line in result.output.splitlines() if line.startswith(("HOME", "USER")))
        self.assertEqual(homes["HOME"], homes["USERPROFILE"])
        home, exists = homes["HOME"].rsplit(" ", 1)
        self.assertEqual(exists, "True")
        self.assertNotEqual(home, os.environ.get("HOME"))
        self.assertTrue(Path(home).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()), home)
        self.assertFalse(Path(home).exists(), "the temporary HOME is removed after the group")

    def test_without_nix_the_extra_is_a_note_and_never_fails(self) -> None:
        with mock.patch.object(checks, "resolve_exe", side_effect=lambda name: None if name == "nix" else name):
            groups = [checks.Group("good", [("scripts/ok.py",)])]
            status, results, out = quiet_run(groups, self.root, lambda: checks.nix_extra(["--x"], self.root))
        self.assertEqual(status, 0)
        self.assertEqual((results[-1].name, results[-1].status), ("nix extra", "not run (optional, no Nix)"))
        self.assertEqual(out.splitlines()[-1].split("  ")[0].strip(), "nix extra")

    def test_the_summary_counts_each_steps_tests(self) -> None:
        self.write("unit.py", "import unittest\nclass T(unittest.TestCase):\n"
                              "    def test_a(self): pass\n    def test_b(self): pass\n"
                              "    @unittest.skip('no')\n    def test_c(self): pass\nunittest.main()\n")
        groups = [checks.Group("g", [("scripts/ok.py",), ("scripts/unit.py",)])]
        _, results, _ = quiet_run(groups, self.root)
        summary = self.root / "summary.json"
        checks.write_summary(summary, results)
        data = json.loads(summary.read_text(encoding="utf-8"))
        self.assertEqual(data["platform"], sys.platform)
        self.assertEqual(data["groups"]["g"]["steps"], [
            {"script": "scripts/ok.py", "status": "pass", "ran": None, "skipped": 0},
            {"script": "scripts/unit.py", "status": "pass", "ran": 3, "skipped": 1}])

    def test_a_failing_extra_fails_the_exit(self) -> None:
        status, _, out = quiet_run([checks.Group("good", [("scripts/ok.py",)])], self.root,
                                   lambda: checks.Result("nix extra", "FAIL", 1.0, "nix said no\n"))
        self.assertEqual(status, 1)
        self.assertIn("nix said no", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
