#!/usr/bin/env python3
"""Test ./cc agents: sync owns only the hooks key of .claude/settings.json, and check catches hand edits to it."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "agent-configs.py"
spec = importlib.util.spec_from_file_location("agent_configs", SCRIPT)
configs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configs)


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
        assert synced["hooks"]["Stop"][0]["hooks"][0]["command"] == '"$CLAUDE_PROJECT_DIR"/scripts/hook stop'
        assert run(root, "check").returncode == 0, run(root, "check").stderr

        synced["hooks"]["PreToolUse"].pop()
        settings.write_text(json.dumps(synced))
        result = run(root, "check")
        assert result.returncode == 1 and "hooks differ" in result.stderr, "removing a hook by hand is caught"

        settings.unlink()
        assert run(root, "sync").returncode == 0 and "hooks" in json.loads(settings.read_text()), "sync creates the file"

        # Codex reads .codex/hooks.json: the same scripts/hook, with the Codex handlers, owned whole by sync.
        codex = root / ".codex/hooks.json"
        hooks = json.loads(codex.read_text())["hooks"]
        assert hooks["PreToolUse"][0]["matcher"] == "^(Bash|apply_patch)$", hooks
        assert hooks["PreToolUse"][0]["hooks"][0]["command"].endswith("/scripts/hook\" codex-pre-tool"), hooks
        assert hooks["PostToolUse"][0]["hooks"][0]["command"].endswith("codex-post-tool"), hooks
        assert {event: group[0]["hooks"][0]["command"].split()[-1] for event, group in hooks.items()} == {
            "PreToolUse": "codex-pre-tool", "PostToolUse": "codex-post-tool", "Stop": "stop", "SessionStart": "session-start"}
        assert "/mnt/" not in codex.read_text() and directory not in codex.read_text(), "no absolute path is committed"
        codex.write_text(codex.read_text().replace('"timeout": 15', '"timeout": 16', 1))
        result = run(root, "check")
        assert result.returncode == 1 and ".codex/hooks.json" in result.stderr, "a hand edit is drift (and loses trust)"
        assert run(root, "sync").returncode == 0 and run(root, "check").returncode == 0

        # The trust report reads Codex's own hooks/list answer: a hook Codex skips must be named, with the fix.
        source = str(codex.resolve())
        keys = configs.codex_hook_keys(root)
        assert len(keys) == 4 and keys[0] == f"{source}:pre_tool_use:0:0", keys
        listed = [{"key": key, "sourcePath": source, "enabled": True, "trustStatus": "trusted"} for key in keys]
        user = {"key": "/home/u/.codex/hooks.json:stop:0:0", "sourcePath": "/home/u/.codex/hooks.json", "enabled": True,
                "trustStatus": "untrusted"}
        assert configs.codex_trust_problems(root, [*listed, user]) == [], "a user hook is not the CC's business"
        assert "not trusted" in configs.codex_trust_problems(root, [user])[0], "no CC hook listed: the folder is not trusted"
        listed[0]["trustStatus"], listed[2]["enabled"] = "modified", False
        problems = configs.codex_trust_problems(root, listed[:3])
        assert any("pre_tool_use:0:0" in p and "modified" in p for p in problems), problems
        assert any("stop:0:0" in p and "disabled" in p for p in problems), problems
        assert any("session_start:0:0" in p and "not loaded" in p for p in problems), problems
        assert len(problems) == 3, problems
    print("agent configs test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
