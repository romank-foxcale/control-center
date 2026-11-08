#!/usr/bin/env python3
"""The entry point of every Claude Code rule hook: hand the call to rule_hooks.py, or decide alone when it cannot load.

The hooks block on failure ("onFailure": "block"), so a syntax error in rule_hooks.py or in a module it imports used to
block every tool call, the Read and Edit that would repair it included (2026-11-08: a null byte in hostos.py). While
the rules cannot load, this guard blocks what they exist to catch, and keeps the repair open:

- reading inside the CC is allowed; reading outside asks, as the rules do;
- editing is allowed only under the CC's scripts/, where the hook code lives; every other edit is denied;
- every shell command is denied, since nothing can check it;
- the reporting hooks (post-*, stop, session-start) never block.

It imports only the standard library, so the modules it guards cannot break it. Why: docs/kit/decisions/0013-rule-hooks.md
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
EVENTS = ("pre-bash", "pre-read", "post-read", "pre-edit", "post-edit", "stop", "session-start")


def load_error(error: BaseException) -> str:
    """What failed to load, with the import line that pulled it in: a SyntaxError for a null byte names no file."""
    frames = traceback.extract_tb(error.__traceback__)
    where = next((frame for frame in reversed(frames) if Path(frame.filename).parent == SCRIPTS), None)
    place = f" at scripts/{Path(where.filename).name}:{where.lineno} ({(where.line or '').strip()})" if where else ""
    named = f" in {error.filename}" if isinstance(error, SyntaxError) and error.filename else ""
    return f"{type(error).__name__}: {error}{named}{place}"


def inside(path: str, folder: Path, cwd: str) -> bool:
    full = os.path.normcase(os.path.realpath(os.path.join(cwd, os.path.expanduser(path))))
    base = os.path.normcase(os.path.realpath(folder))
    try:
        return os.path.commonpath([full, base]) == base
    except ValueError:  # another drive on Windows
        return False


def decide(event: str, payload: dict, problem: str) -> tuple[int, dict | None]:
    cwd = payload.get("cwd") or str(ROOT)
    tool_input = payload.get("tool_input") or {}
    paths = [tool_input[key] for key in ("file_path", "notebook_path", "path") if tool_input.get(key)]
    down = f"The CC's rule hooks cannot be loaded ({problem}), so nothing can check this call."

    def pre_tool(decision: str, reason: str) -> tuple[int, dict]:
        return 0, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                          "permissionDecisionReason": f"{down} {reason}"}}

    if event == "pre-read":
        if all(inside(path, ROOT, cwd) for path in paths or [cwd]):
            return 0, None
        return pre_tool("ask", "This reaches outside the CC; allow only if the user asked for it.")
    if event == "pre-edit":
        if paths and all(inside(path, SCRIPTS, cwd) for path in paths):
            return 0, None
        return pre_tool("deny", "Until the hooks load again only files under scripts/ can be edited: fix the file named "
                                "here, or ask the user to run git checkout on it.")
    if event == "pre-bash":
        return pre_tool("deny", "Shell commands are blocked until the hooks load again. Read and fix the file named here "
                                "(edits under scripts/ are allowed), then run ./cc check.")
    return 0, {"systemMessage": f"The CC's rule hooks cannot be loaded ({problem}); fix it, then run ./cc check."}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in EVENTS:
        print(f"usage: hook_guard.py <{'|'.join(EVENTS)}>", file=sys.stderr)
        return 2
    sys.path.insert(0, str(SCRIPTS))
    try:
        import rule_hooks
    except BaseException as error:  # SyntaxError, ImportError, NameError: anything raised while loading
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        problem = load_error(error)
    else:
        sys.argv = [rule_hooks.__file__, *argv]
        return rule_hooks.main()
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except ValueError:
        payload = {}
    code, output = decide(argv[0], payload if isinstance(payload, dict) else {}, problem)
    if output:
        print(json.dumps(output))
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
