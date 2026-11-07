#!/usr/bin/env python3
"""Mirror the canonical agent skills into every supported AI tool's directory."""

from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import sys
from pathlib import Path


# Codex, Cursor and OpenCode read .agents/skills; Claude Code reads only
# .claude/skills. Copies (not symlinks) survive clones on native Windows.
SOURCE = Path(".agents/skills")
TARGETS = (Path(".claude/skills"),)
INSTRUCTION_STUBS = {
    Path("CLAUDE.md"): "@AGENTS.md",
}
# Claude Code hooks that enforce the hard rules (docs/kit/decisions/0013-rule-hooks.md). This script owns
# the "hooks" key of the shared settings; every other key, such as permissions, is left alone.
SETTINGS = Path(".claude/settings.json")


# Shell form through the POSIX shim: sh -c on Linux and macOS, Git Bash on Windows, where Claude Code sets
# CLAUDE_PROJECT_DIR as C:/... Exec form cannot spawn the extensionless shim, and PowerShell must never call it
# (it reached cc.cmd with no stdin and allowed the call). "onFailure": "block" (Claude Code >= 2.1.295) makes a
# crash, spawn failure, timeout or exit other than 0/2 block the call instead of failing open. Without Git Bash on
# Windows this form fails closed. Why: plan native-core, spike S2.
def hook(event: str) -> list[dict[str, object]]:
    return [{"type": "command", "command": f'"$CLAUDE_PROJECT_DIR/cc" hook {event}', "timeout": 15,
             "onFailure": "block"}]


HOOKS = {
    "PreToolUse": [
        {"matcher": "Bash|PowerShell", "hooks": hook("pre-bash")},
        {"matcher": "Read|Grep|Glob", "hooks": hook("pre-read")},
        {"matcher": "Edit|MultiEdit|Write", "hooks": hook("pre-edit")},
    ],
    "PostToolUse": [
        {"matcher": "Edit|MultiEdit|Write", "hooks": hook("post-edit")},
        {"matcher": "Read|Grep|Glob", "hooks": hook("post-read")},
    ],
    "Stop": [{"hooks": hook("stop")}],
    "SessionStart": [{"matcher": "startup", "hooks": hook("session-start")}],
}


def files_under(root: Path) -> set[Path]:
    if not root.is_dir():
        return set()
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def drift(root: Path) -> list[str]:
    source = root / SOURCE
    expected = files_under(source)
    if not expected:
        return [f"{SOURCE}: no canonical skills found"]
    problems: list[str] = []
    for target in TARGETS:
        actual = files_under(root / target)
        for relative in sorted(expected - actual):
            problems.append(f"{target / relative}: missing")
        for relative in sorted(actual - expected):
            problems.append(f"{target / relative}: not in {SOURCE}")
        for relative in sorted(expected & actual):
            if not filecmp.cmp(source / relative, root / target / relative, shallow=False):
                problems.append(f"{target / relative}: differs from {SOURCE / relative}")
    for stub, marker in INSTRUCTION_STUBS.items():
        path = root / stub
        if not path.is_file() or marker not in path.read_text(encoding="utf-8"):
            problems.append(f"{stub}: must exist and contain '{marker}'")
    try:
        if json.loads((root / SETTINGS).read_text(encoding="utf-8")).get("hooks") != HOOKS:
            problems.append(f"{SETTINGS}: hooks differ from scripts/agent-configs.py")
    except (OSError, ValueError) as error:
        problems.append(f"{SETTINGS}: {error}")
    return problems


def sync(root: Path) -> None:
    source = root / SOURCE
    if not files_under(source):
        raise ValueError(f"{SOURCE}: no canonical skills found")
    for target in TARGETS:
        destination = root / target
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        print(f"synced {SOURCE} -> {target}")
    path = root / SETTINGS
    settings = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    settings["hooks"] = HOOKS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"synced rule hooks -> {SETTINGS}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("command", choices=("sync", "check"))
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "sync":
            sync(root)
            return 0
        problems = drift(root)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if problems:
        print("agent config drift (run ./cc agents sync):", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print(f"agent configs in sync: {SOURCE} -> {', '.join(map(str, TARGETS))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
