#!/usr/bin/env python3
"""Test scripts/hook_guard.py: with working rule hooks it hands the call to them; when they cannot be loaded (a syntax
error in rule_hooks.py or a module it imports) it still lets the agent read and repair scripts/, and blocks the rest."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


class Guard(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.temporary.name)) / "cc"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "docs").mkdir()
        shutil.copy2(SCRIPTS / "hook_guard.py", self.root / "scripts" / "hook_guard.py")
        # A module the rule hooks import, broken the way an edit broke hostos.py (a null byte, 2026-11-08).
        (self.root / "scripts" / "rule_hooks.py").write_text("import hostos\n", encoding="utf-8")
        (self.root / "scripts" / "hostos.py").write_bytes(b"X = b'\x00'\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def hook(self, event: str, payload: dict) -> tuple[int, dict | None, str]:
        result = subprocess.run([sys.executable, "-B", str(self.root / "scripts" / "hook_guard.py"), event],
                                input=json.dumps({"cwd": str(self.root), **payload}), capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        return result.returncode, json.loads(result.stdout) if result.stdout.strip() else None, result.stderr

    def decision(self, output: dict | None) -> str | None:
        return (output or {}).get("hookSpecificOutput", {}).get("permissionDecision")

    def test_working_rule_hooks_get_the_call(self) -> None:
        (self.root / "scripts" / "hostos.py").write_text("", encoding="utf-8")
        (self.root / "scripts" / "rule_hooks.py").write_text(
            "import json, sys\ndef main():\n    print(json.dumps({'handled': sys.argv[1:], 'event': json.load(sys.stdin)['x']}))\n"
            "    return 0\n", encoding="utf-8")
        self.assertEqual(self.hook("pre-bash", {"x": 7})[:2], (0, {"handled": ["pre-bash"], "event": 7}))

    def test_the_broken_file_can_be_read_and_repaired(self) -> None:
        for name in ("hostos.py", "rule_hooks.py"):
            path = str(self.root / "scripts" / name)
            code, output, _ = self.hook("pre-edit", {"tool_name": "Edit", "tool_input": {"file_path": path}})
            self.assertEqual((code, self.decision(output)), (0, None), name)
            code, output, _ = self.hook("pre-read", {"tool_name": "Read", "tool_input": {"file_path": path}})
            self.assertEqual((code, self.decision(output)), (0, None), name)
        relative = self.hook("pre-edit", {"tool_name": "Write", "tool_input": {"file_path": "scripts/hostos.py"}})
        self.assertIsNone(self.decision(relative[1]), "a path relative to the cwd counts too")

    def test_everything_else_is_blocked_while_the_rules_are_down(self) -> None:
        code, output, _ = self.hook("pre-edit", {"tool_name": "Write", "tool_input": {"file_path": str(self.root / "docs" / "x.md")}})
        self.assertEqual((code, self.decision(output)), (0, "deny"))
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("import hostos", reason, "the reason names what failed to load")
        self.assertIn("scripts/", reason, "and says what can still be edited")
        escape = str(self.root / "scripts" / ".." / "docs" / "x.md")
        self.assertEqual(self.decision(self.hook("pre-edit", {"tool_input": {"file_path": escape}})[1]), "deny")
        self.assertEqual(self.decision(self.hook("pre-bash", {"tool_input": {"command": "git push"}})[1]), "deny")
        self.assertEqual(self.decision(self.hook("pre-bash", {"tool_input": {"command": "ls"}})[1]), "deny")
        outside = str(self.root.parent / "other" / "secret.txt")
        self.assertEqual(self.decision(self.hook("pre-read", {"tool_input": {"file_path": outside}})[1]), "ask")
        self.assertEqual(self.decision(self.hook("pre-read", {"tool_input": {"pattern": "x", "path": str(self.root.parent)}})[1]), "ask")

    def test_reports_never_block(self) -> None:
        for event in ("post-edit", "post-read", "stop", "session-start"):
            code, output, _ = self.hook(event, {})
            self.assertEqual(code, 0, event)
            self.assertIn("cannot be loaded", output["systemMessage"], event)

    def test_an_unknown_event_is_a_usage_error(self) -> None:
        self.assertEqual(self.hook("nope", {})[0], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
