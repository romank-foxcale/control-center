#!/usr/bin/env python3
"""Test the Cursor provider with a fake cursor-agent: read-only is enforced by a snapshot that carries
Cursor's deny config, a write that slips through fails the call, and the reply is the closing message."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import providers

# The fake CLI answers status and models, and in --print mode does what FAKE_CURSOR_ACT says:
# "reply" streams a preamble, a tool call and a closing message; "write" also writes into its workspace.
FAKE_CURSOR = f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
if sys.argv[1:] == ["status"]:
    print("\\u2713 Logged in as dev@example.com" if os.environ.get("FAKE_CURSOR_LOGIN") else "Not logged in")
    sys.exit(0)
if sys.argv[1:] == ["models"]:
    print("Available models\\n\\nauto - Auto (default)\\ngrok-4.7-high - Grok 4.7  High\\nclaude-opus-5-thinking-high - Claude")
    sys.exit(0)
workspace = Path(sys.argv[sys.argv.index("--workspace") + 1])
Path(os.environ["FAKE_CURSOR_SEEN"]).write_text(json.dumps({{
    "argv": sys.argv[1:], "cwd": os.getcwd(), "prompt": sys.stdin.read(),
    "config": (workspace / ".cursor" / "cli.json").read_text(),
    "files": sorted(str(p.relative_to(workspace)) for p in workspace.rglob("*") if p.is_file())}}))
if os.environ.get("FAKE_CURSOR_ACT") == "write":
    (workspace / "app.py").write_text("tampered")
def emit(event): print(json.dumps(event), flush=True)
text = lambda words: {{"type": "assistant", "message": {{"content": [{{"type": "text", "text": words}}]}}}}
emit({{"type": "system", "subtype": "init"}})
emit(text("I'll check the code first."))
for _ in range(int(os.environ.get("FAKE_CURSOR_TOOLS", "1"))):
    emit({{"type": "tool_call", "subtype": "started", "tool_call": {{"readToolCall": {{"args": {{"path": str(workspace / "app.py")}}}}}}}})
emit(text("DECISION: A"))
emit(text("## Reasoning\\nA reuses the parser."))
emit({{"type": "result", "subtype": "success", "result": "I'll check the code first.DECISION: A## Reasoning",
       "usage": {{"inputTokens": 11273, "outputTokens": 18, "cacheReadTokens": 500, "cacheWriteTokens": 7}}}})
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        bin_dir = base / "bin"
        bin_dir.mkdir()
        (bin_dir / "cursor-agent").write_text(FAKE_CURSOR)
        (bin_dir / "cursor-agent").chmod(0o755)
        seen = base / "seen.json"
        os.environ.update({"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_CURSOR_SEEN": str(seen)})

        assert providers.login_status("cursor") == (False, "not logged in; run: cursor-agent login")
        assert providers.models("cursor") == [], "no models while logged out"
        os.environ["FAKE_CURSOR_LOGIN"] = "1"
        assert providers.login_status("cursor")[0]
        assert providers.models("cursor") == ["auto", "grok-4.7-high", "claude-opus-5-thinking-high"], providers.models("cursor")

        role = {"id": "judge", "provider": "cursor", "model": "grok-4.7-high", "access": "read-only"}
        argv = providers.command(role, base, base / "last.txt")
        assert "--force" not in argv and "--yolo" not in argv, "shell stays off"
        assert argv[argv.index("--model") + 1] == "grok-4.7-high"
        try:
            providers.command({**role, "access": "write-worktree"}, base, base / "last.txt")
            raise AssertionError("a writing cursor role must be refused")
        except providers.ProviderError:
            pass

        worktree = base / "worktree"
        worktree.mkdir()
        git = ["git", "-C", str(worktree), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", str(worktree)], check=True)
        (worktree / ".gitignore").write_text("build/\n")
        (worktree / "app.py").write_text("print('app')\n")
        (worktree / ".cursor").mkdir()
        (worktree / ".cursor" / "cli.json").write_text('{"permissions": {"allow": ["Write(**)"]}}')
        # Cursor also runs a folder's Claude Code hooks, so the snapshot leaves the Claude settings out.
        (worktree / ".claude").mkdir()
        (worktree / ".claude" / "settings.json").write_text('{"hooks": {"PreToolUse": []}}')
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "base"], check=True)
        (worktree / "app.py").write_text("print('changed by the writer')\n")  # the diff under review
        (worktree / "new.py").write_text("x = 1\n")
        (worktree / "build").mkdir()
        (worktree / "build" / "huge.bin").write_text("ignored")

        log = base / "logs" / "verdict.log"
        reply = providers.run(role, "# Task\n\nJudge.", worktree, log)
        call = json.loads(seen.read_text())
        assert reply == "DECISION: A\n\n## Reasoning\nA reuses the parser.", f"the closing messages, no preamble: {reply!r}"
        assert call["prompt"] == "# Task\n\nJudge.", "the prompt goes on stdin, not argv"
        assert Path(call["cwd"]) != worktree and Path(call["argv"][call["argv"].index("--workspace") + 1]) == Path(call["cwd"])
        assert json.loads(call["config"]) == providers.CURSOR_DENY, "the project's own config is replaced by the deny config"
        assert call["files"] == [".cursor/cli.json", ".gitignore", "app.py", "new.py"], call["files"]
        assert not Path(call["cwd"]).exists(), "the snapshot is deleted"
        assert "tool: read" in log.read_text()
        assert providers.usage(log) == {"input": 11280, "cached": 500, "output": 18}, "cache writes are new input"
        assert log.read_text().splitlines()[-1] == "usage: 11280 new input, 500 cached input, 18 output tokens"
        assert (worktree / ".cursor" / "cli.json").read_text().startswith('{"permissions": {"allow"'), "the worktree is untouched"

        os.environ["FAKE_CURSOR_ACT"] = "write"
        try:
            providers.run(role, "Judge.", worktree, log)
            raise AssertionError("a write in the snapshot must fail the call")
        except providers.ProviderError as error:
            assert "changed its read-only snapshot" in str(error), error
        assert (worktree / "app.py").read_text() == "print('changed by the writer')\n", "the worktree is untouched"
        assert not Path(json.loads(seen.read_text())["cwd"]).exists(), "the snapshot is deleted after a failure too"

        # A caller-owned snapshot: every call runs in it, none copies again, and the caller sees a write afterwards.
        os.environ["FAKE_CURSOR_ACT"] = "reply"
        shared, before = providers.snapshot(worktree)
        for _ in range(2):
            providers.run(role, "Judge.", worktree, log, shared=shared)
            assert Path(json.loads(seen.read_text())["cwd"]) == shared, "the call runs in the shared snapshot"
        assert shared.exists() and providers.fingerprint(shared) == before, "the caller owns the snapshot; no write"
        # A risk run reads the CC with its git-ignored clones: they are copied in at the same place.
        clone = worktree / "build" / "api"
        clone.mkdir()
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        (clone / "server.py").write_text("serve()\n")
        with_clone, _ = providers.snapshot(worktree, [clone])
        assert (with_clone / "build" / "api" / "server.py").read_text() == "serve()\n", "the clone is in the snapshot"
        assert not (with_clone / "build" / "huge.bin").exists(), "the rest of the ignored folder is not"
        shutil.rmtree(with_clone)
        os.environ["FAKE_CURSOR_ACT"] = "write"
        providers.run(role, "Judge.", worktree, log, shared=shared)
        assert providers.fingerprint(shared) != before, "a write in the shared snapshot shows in its fingerprint"
        assert (worktree / "app.py").read_text() == "print('changed by the writer')\n", "the worktree is untouched"
        shutil.rmtree(shared)

        # The tool-call cap: the count is what the log shows, the cap itself is allowed, one past it stops the call.
        os.environ["FAKE_CURSOR_ACT"] = "reply"
        os.environ["FAKE_CURSOR_TOOLS"] = "3"
        assert providers.run({**role, "maxToolCalls": 3}, "Judge.", worktree, log).startswith("DECISION: A")
        assert providers.tool_calls(log) == 3, log.read_text()
        try:
            providers.run({**role, "maxToolCalls": 2}, "Judge.", worktree, log)
            raise AssertionError("a call past its cap must be stopped")
        except providers.ToolCapError as error:
            assert "reached its cap of 2 tool calls" in str(error), error
        assert log.read_text().splitlines()[-2:] == [
            "stopped: reached the cap of 2 tool calls", "usage: not reported (the call ended before its CLI reported tokens)",
        ], log.read_text()
        assert providers.usage(log) is None, "a call stopped before its result has no usage, not zero"
        assert providers.max_tool_calls(role) == providers.DEFAULT_MAX_TOOL_CALLS

    # read-only-web: web search and fetch on top of reading, and nothing else; plain read-only gets no web.
    # Claude's dontAsk mode denies WebSearch unless it is also allowed (checked with the real CLI, 2026-10).
    claude = {"id": "risk", "provider": "claude", "model": "m", "access": "read-only-web"}
    argv = providers.command(claude, Path("."), Path("last.txt"))
    tools = argv[argv.index("--tools") + 1:argv.index("--allowedTools")]
    assert tools == ["Read", "Grep", "Glob", "WebSearch", "WebFetch"], argv
    assert argv[argv.index("--allowedTools") + 1:] == ["WebSearch", "WebFetch"], argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk", argv
    assert argv[argv.index("--setting-sources") + 1] == "user", "the folder's project settings stay out"
    assert json.loads(argv[argv.index("--settings") + 1]) == {"disableAllHooks": True}, "hooks never answer for a role"
    plain = providers.command({**claude, "access": "read-only"}, Path("."), Path("last.txt"))
    assert "--allowedTools" not in plain and "WebSearch" not in plain, plain
    codex = providers.command({**claude, "provider": "codex"}, Path("."), Path("last.txt"))
    assert codex[codex.index("--sandbox") + 1] == "read-only" and 'web_search="live"' in codex, codex
    assert 'web_search="live"' not in providers.command({**claude, "provider": "codex", "access": "read-only"}, Path("."), Path("l"))
    try:
        providers.command({**claude, "provider": "cursor"}, Path("."), Path("last.txt"))
        raise AssertionError("cursor has no web access level")
    except providers.ProviderError:
        pass

    # Codex edits and searches count as tool calls, like its commands.
    change = json.dumps({"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": "a.py"}, {"path": "b.py"}]}})
    assert providers.parse_codex_event(change)[1] == "tool: edit a.py, b.py"
    search = json.dumps({"type": "item.completed", "item": {"type": "web_search", "query": "pytest fixtures"}})
    assert providers.parse_codex_event(search)[1] == "tool: web search pytest fixtures"

    # Usage as each CLI reported it in a real call (2026-10): Claude splits cache tokens out, Codex counts
    # its cached tokens inside input_tokens, and its output_tokens already include reasoning.
    claude = {"type": "result", "usage": {"input_tokens": 2, "cache_creation_input_tokens": 2793,
                                          "cache_read_input_tokens": 1556, "output_tokens": 4}}
    assert providers.call_usage("claude", claude) == {"input": 2795, "cached": 1556, "output": 4}
    assert providers.call_usage("claude", {"type": "assistant", "message": {"usage": {"input_tokens": 9}}}) is None, \
        "per-message usage would count the call twice"
    codex = {"type": "turn.completed", "usage": {"input_tokens": 13869, "cached_input_tokens": 1408,
                                                 "output_tokens": 23, "reasoning_output_tokens": 16}}
    assert providers.call_usage("codex", codex) == {"input": 12461, "cached": 1408, "output": 23}
    assert providers.describe({"input": 12000, "cached": 27100, "output": 2100}) == \
        "41.2k tokens (12.0k new input, 27.1k cached, 2.1k output)"
    assert providers.describe({"input": 1_250_000, "cached": 0, "output": 900}) == \
        "1.3M tokens (1.2M new input, 0 cached, 900 output)"
    assert providers.describe(None) == "tokens not reported"
    print("providers test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
