#!/usr/bin/env python3
"""Test ./cc agents: sync owns only the hooks key of .claude/settings.json, and check catches hand edits to it."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "agent-configs.py"


def run(root: Path, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), str(root), command], capture_output=True, text=True)


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".agents/skills/tdd").mkdir(parents=True)
        (root / ".agents/skills/tdd/SKILL.md").write_text("skill\n")
        (root / "CLAUDE.md").write_text("@AGENTS.md\n")
        settings = root / ".claude/settings.json"
        settings.parent.mkdir()
        permissions = {"allow": ["Bash(./cc help)"]}
        settings.write_text(json.dumps({"permissions": permissions, "hooks": {"Stop": []}}))

        assert run(root, "check").returncode == 1, "a hooks block that differs is drift"
        assert run(root, "sync").returncode == 0
        synced = json.loads(settings.read_text())
        assert synced["permissions"] == permissions, "sync leaves the permissions alone"
        # Every entry runs the shim in shell form and blocks when the hook itself fails (spike S2).
        expected = {
            ("PreToolUse", "Bash|PowerShell"): "pre-bash",
            ("PreToolUse", "Read|Grep|Glob"): "pre-read",
            ("PreToolUse", "Edit|MultiEdit|Write"): "pre-edit",
            ("PostToolUse", "Edit|MultiEdit|Write"): "post-edit",
            ("PostToolUse", "Read|Grep|Glob"): "post-read",
            ("Stop", None): "stop",
            ("SessionStart", "startup"): "session-start",
        }
        actual = {(event, group.get("matcher")): group["hooks"]
                  for event, groups in synced["hooks"].items() for group in groups}
        assert set(actual) == set(expected), sorted(actual, key=str)
        for key, name in expected.items():
            assert actual[key] == [{"type": "command", "command": f'"$CLAUDE_PROJECT_DIR/cc" hook {name}',
                                    "timeout": 15, "onFailure": "block"}], (key, actual[key])
        assert "scripts/hook" not in settings.read_text(), "the old bash launcher is gone"
        assert run(root, "check").returncode == 0, run(root, "check").stderr

        for field, value in (("onFailure", None), ("timeout", 60), ("command", '"$CLAUDE_PROJECT_DIR"/scripts/hook stop')):
            changed = json.loads(json.dumps(synced))
            if value is None:
                del changed["hooks"]["Stop"][0]["hooks"][0][field]
            else:
                changed["hooks"]["Stop"][0]["hooks"][0][field] = value
            settings.write_text(json.dumps(changed))
            result = run(root, "check")
            assert result.returncode == 1 and "hooks differ" in result.stderr, f"a hand-changed {field} is drift"
        settings.write_text(json.dumps(synced))

        synced["hooks"]["PreToolUse"].pop()
        settings.write_text(json.dumps(synced))
        result = run(root, "check")
        assert result.returncode == 1 and "hooks differ" in result.stderr, "removing a hook by hand is caught"

        settings.unlink()
        assert run(root, "sync").returncode == 0 and "hooks" in json.loads(settings.read_text()), "sync creates the file"
    print("agent configs test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
