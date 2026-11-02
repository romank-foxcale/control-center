#!/usr/bin/env python3
"""Test ./cc plan complete and archive: the evidence or reason lands verbatim in the closure block, even when it
holds backslashes or group references such as a Windows path or \\1."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "plans.py"
TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "task-plan.md"
DETAIL = r"copied to C:\Users\x\update-center and kept \1 as is"


def plans(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-B", str(SCRIPT), str(root), *args], capture_output=True, text=True)


class ClosureText(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "templates").mkdir()
        (self.root / "templates" / "task-plan.md").write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        for lifecycle in ("active", "archived", "completed"):
            (self.root / "plans" / lifecycle).mkdir(parents=True)
        self.assertEqual(plans(self.root, "create", "demo", "--title", "Demo").returncode, 0)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def closure_line(self, lifecycle: str, label: str) -> str:
        content = (self.root / "plans" / lifecycle / "demo.md").read_text(encoding="utf-8")
        block = content.split("## Lifecycle closure\n\n", 1)[1]
        return next(line for line in block.splitlines() if line.startswith(f"- {label}: "))

    def test_complete_keeps_evidence_verbatim(self) -> None:
        self.assertEqual(plans(self.root, "accept", "demo").returncode, 0)
        result = plans(self.root, "complete", "demo", "--evidence", DETAIL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.closure_line("completed", "evidence"), f"- evidence: {DETAIL}")

    def test_archive_keeps_reason_verbatim(self) -> None:
        result = plans(self.root, "archive", "demo", "--reason", DETAIL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.closure_line("archived", "reason"), f"- reason: {DETAIL}")


if __name__ == "__main__":
    unittest.main()
