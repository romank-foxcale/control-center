#!/usr/bin/env python3
"""Test how ./cc verify prints a gate: compact by default (a pass is one line, a failure is its tail), full with --full."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verified  # noqa: E402
import verify  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    if not condition:
        FAILURES.append(f"{name}: {detail}")


def gate(lines: int, code: int) -> list[str]:
    """A gate that prints `line 1` to `line <lines>` and exits with `code`."""
    return [sys.executable, "-c", f"[print(f'line {{n}}') for n in range(1, {lines + 1})]; raise SystemExit({code})"]


def run(root: Path, full: bool, argv: list[str]) -> tuple[int, str]:
    gates = verify.Gates(root, full=full)
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        code = gates.run("demo on linux", argv)
    return code, printed.getvalue()


def logged(root: Path, output: str) -> str:
    """The content of the log file the output names, relative to the CC root."""
    match = re.search(r"\.cc-local/\S+\.log", output)
    return (root / match.group(0)).read_text(encoding="utf-8") if match else ""


def test_a_failing_gate_should_print_only_its_tail_and_keep_the_full_log(root: Path) -> None:
    code, output = run(root, False, gate(100, 1))
    check("failing exit code", code == 1, code)
    # 100 lines with a tail of 80: lines 21 to 100 are printed, lines 1 to 20 only logged.
    check("tail printed", "line 21\n" in output and "line 100\n" in output, output)
    check("head not printed", "line 20\n" not in output, output)
    log = logged(root, output)
    check("log named and complete", "line 1\n" in log and "line 100\n" in log, output)


def test_a_passing_gate_should_print_one_line_without_its_output(root: Path) -> None:
    code, output = run(root, False, gate(5, 0))
    check("passing exit code", code == 0, code)
    check("one line", len(output.splitlines()) == 1 and "pass" in output, output)
    check("passing log complete", "line 5\n" in logged(root, output), output)


def test_full_should_stream_every_line(root: Path) -> None:
    code, output = run(root, True, gate(100, 1))
    check("full exit code", code == 1, code)
    check("full streams all", "line 1\n" in output and "line 100\n" in output, output)


def test_a_pass_should_stamp_the_cc_and_only_the_selected_worktrees(root: Path) -> None:
    (root / "control-center.json").write_text(json.dumps({"layout": {"projectsRoot": ".", "worktrees": "worktrees"}}))
    repositories = {name: {"worktree": f"worktrees/f/{name}_wt"} for name in ("api", "web")}
    (root / "worktrees" / "f").mkdir(parents=True)
    (root / "worktrees" / "f" / ".cc-worktree.json").write_text(json.dumps({"repositories": repositories}))
    for path in (root, root / "worktrees" / "f" / "api_wt", root / "worktrees" / "f" / "web_wt"):
        path.mkdir(exist_ok=True)
        (path / "a.txt").write_text(path.name)
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "add", "a.txt"], check=True)
    with contextlib.redirect_stderr(io.StringIO()):
        verify.stamp_trees(root, "f", ["api"])
    kinds = lambda path: (verified.lookup(root, verified.working_tree(path)) or {}).get("kinds")
    check("cc stamped", kinds(root) == ["verify"], kinds(root))
    check("selected worktree stamped", kinds(root / "worktrees" / "f" / "api_wt") == ["verify"])
    check("other worktree not stamped", kinds(root / "worktrees" / "f" / "web_wt") is None)


def main() -> int:
    os.environ.pop("CC_EVENTS", None)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        test_a_failing_gate_should_print_only_its_tail_and_keep_the_full_log(root)
        test_a_passing_gate_should_print_one_line_without_its_output(root)
        test_full_should_stream_every_line(root)
    with tempfile.TemporaryDirectory() as temporary:
        test_a_pass_should_stamp_the_cc_and_only_the_selected_worktrees(Path(temporary))
    for failure in FAILURES:
        print(f"FAIL {failure}")
    print("verify tests passed" if not FAILURES else f"{len(FAILURES)} verify test(s) failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
