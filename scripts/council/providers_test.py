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
import time
from pathlib import Path

import providers
from providers import hostos

# The fake CLI answers status and models, and in --print mode does what FAKE_CURSOR_ACT says:
# "reply" streams a preamble, a tool call and a closing message; "write" also writes into its workspace;
# "echo" closes with the prompt it read; "hang" starts a long child that shares its stdout, then waits for it.
# Like the real CLIs it reads and writes UTF-8, whatever the console's code page.
FAKE_CURSOR = """
import json, os, subprocess, sys
from pathlib import Path
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
if sys.argv[1:] == ["status"]:
    print("\\u2713 Logged in as dev@example.com" if os.environ.get("FAKE_CURSOR_LOGIN") else "Not logged in")
    sys.exit(0)
if sys.argv[1:] == ["models"]:
    print("Available models\\n\\nauto - Auto (default)\\ngrok-4.7-high - Grok 4.7  High\\nclaude-opus-5-thinking-high - Claude")
    sys.exit(0)
workspace = Path(sys.argv[sys.argv.index("--workspace") + 1])
prompt = sys.stdin.read()
act = os.environ.get("FAKE_CURSOR_ACT")
Path(os.environ["FAKE_CURSOR_SEEN"]).write_text(json.dumps({
    "argv": sys.argv[1:], "cwd": os.getcwd(), "prompt": prompt,
    "config": (workspace / ".cursor" / "cli.json").read_text(),
    "files": sorted(Path(p.relative_to(workspace)).as_posix() for p in workspace.rglob("*") if p.is_file())}))
if act == "write":
    (workspace / "app.py").write_text("tampered")
def emit(event): print(json.dumps(event, ensure_ascii=False), flush=True)
text = lambda words: {"type": "assistant", "message": {"content": [{"type": "text", "text": words}]}}
emit({"type": "system", "subtype": "init"})
if act == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    Path(os.environ["FAKE_CURSOR_CHILD"]).write_text(str(child.pid))
    sys.exit(child.wait())
emit(text("I'll check the code first."))
for _ in range(int(os.environ.get("FAKE_CURSOR_TOOLS", "1"))):
    emit({"type": "tool_call", "subtype": "started", "tool_call": {"readToolCall": {"args": {"path": str(workspace / "app.py")}}}})
if act == "echo":
    emit(text("heard: " + prompt))
else:
    emit(text("DECISION: A"))
    emit(text("## Reasoning\\nA reuses the parser."))
emit({"type": "result", "subtype": "success", "result": "I'll check the code first.DECISION: A## Reasoning",
      "usage": {"inputTokens": 11273, "outputTokens": 18, "cacheReadTokens": 500, "cacheWriteTokens": 7}})
"""


# The fake Codex answers `login status`, and for `exec` replies with the next entry of FAKE_CODEX_REPLIES (the last one
# repeats), as the real CLI does: JSONL events on stdout and the final message in --output-last-message. The reply
# READ is the content of secret.txt in its --cd folder. FAKE_CODEX_WRITE makes it write into that folder itself.
FAKE_CODEX = """
import json, os, sys
from pathlib import Path
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
if sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT")
    sys.exit(0)
argv = sys.argv[1:]
cwd = Path(argv[argv.index("--cd") + 1])
prompt = sys.stdin.read()
seen = Path(os.environ["FAKE_CODEX_SEEN"])
calls = json.loads(seen.read_text(encoding="utf-8")) if seen.exists() else []
replies = json.loads(os.environ["FAKE_CODEX_REPLIES"])
reply = replies[min(len(calls), len(replies) - 1)]
if reply == "READ":
    reply = (cwd / "secret.txt").read_text(encoding="utf-8").strip() + " BLOCKED"
calls.append({"argv": argv, "prompt": prompt})
seen.write_text(json.dumps(calls), encoding="utf-8")
if os.environ.get("FAKE_CODEX_WRITE"):
    (cwd / "calc.py").write_text("tampered\\n", encoding="utf-8")
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": reply}}), flush=True)
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5}}), flush=True)
with open(argv[argv.index("--output-last-message") + 1], "w", encoding="utf-8", newline="") as last:
    last.write(reply)
"""

CALC = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
CALC_DIFF = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""
# Wrong context: `git apply --check` rejects it.
STALE_DIFF = CALC_DIFF.replace("-    return a - b", "-    return a * b")


def install_cli(bin_dir: Path, name: str, source: str) -> None:
    """A fake CLI found on PATH by its bare name, as the real one is: an npm-style .cmd shim on Windows, a shell
    script elsewhere; both start the Python source with this interpreter."""
    (bin_dir / f"{name}.py").write_text(source, encoding="utf-8")
    if hostos.WINDOWS:
        (bin_dir / f"{name}.cmd").write_text(f'@"{sys.executable}" "%~dp0{name}.py" %*\r\n', encoding="utf-8")
    else:
        shim = bin_dir / name
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/{name}.py" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    found = hostos.resolve_exe(name)
    # Never a real, paid CLI: the fake must be the one PATH finds.
    assert found and Path(found).parent == bin_dir, f"{name} resolves to {found}, not the fake in {bin_dir}"


def wait_dead(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while hostos.alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


def check_utf8_round_trip(role: dict, worktree: Path, log: Path) -> None:
    """Text that no Windows code page holds whole goes to the CLI on stdin and comes back on stdout unchanged."""
    words = "🤖✓Ж"
    os.environ["FAKE_CURSOR_ACT"] = "echo"
    said: list[str] = []
    reply = providers.run(role, f"Judge {words}.", worktree, log, on_say=said.append)
    assert json.loads(Path(os.environ["FAKE_CURSOR_SEEN"]).read_text())["prompt"] == f"Judge {words}.", "stdin is UTF-8"
    assert reply == f"heard: Judge {words}.", f"stdout is UTF-8: {reply!r}"
    assert f"say: heard: Judge {words}." in log.read_text(encoding="utf-8") and said[-1] == f"heard: Judge {words}.", said
    assert providers.login_status("cursor") == (True, "logged in as dev@example.com"), "the status check reads the UTF-8 mark"


def check_timeout_stops_the_tree(role: dict, worktree: Path, log: Path, base: Path) -> None:
    """A CLI shim whose child keeps stdout open still ends at its timeout, and leaves no child behind."""
    child_file = base / "child.pid"
    os.environ.update({"FAKE_CURSOR_ACT": "hang", "FAKE_CURSOR_CHILD": str(child_file)})
    timeout = 2
    began = time.monotonic()
    try:
        providers.run(role, "Judge.", worktree, log, timeout=timeout)
        raise AssertionError("a call stopped at its timeout must fail")
    except providers.ProviderError as error:
        assert "failed" in str(error), error
    took = time.monotonic() - began
    child = int(child_file.read_text())
    assert took < timeout + 5, f"the call returned {took:.1f}s after it started; the timeout was {timeout}s"
    assert wait_dead(child, 5), f"the shim's child {child} outlived the timeout"


def check_codex_windows_flags() -> None:
    """On Windows every Codex role gets the unelevated Windows sandbox, and a writer runs read-only (diff mode);
    elsewhere nothing changes."""
    codex = {"id": "w", "provider": "codex", "model": "m", "access": "write-worktree"}
    saved = providers.WINDOWS
    try:
        providers.WINDOWS = True
        for access in ("read-only", "read-only-web", "write-worktree"):
            argv = providers.command({**codex, "access": access}, Path("."), Path("l"))
            assert argv[argv.index("--sandbox") + 1] == "read-only", (access, argv)
            assert 'windows.sandbox="unelevated"' in argv, (access, argv)
            assert argv[argv.index('windows.sandbox="unelevated"') - 1] == "--config", argv
        assert providers.diff_mode(codex) and not providers.diff_mode({**codex, "access": "read-only"})
        assert not providers.diff_mode({**codex, "provider": "claude"}), "Claude writes through its own tools"
        claude = providers.command({**codex, "provider": "claude"}, Path("."), Path("l"))
        assert not any("windows.sandbox" in part for part in claude), claude
        providers.WINDOWS = False
        argv = providers.command(codex, Path("."), Path("l"))
        assert argv[argv.index("--sandbox") + 1] == "workspace-write", argv
        assert not any("windows.sandbox" in part for part in argv), argv
        assert not providers.diff_mode(codex)
    finally:
        providers.WINDOWS = saved


def check_diff_parsing() -> None:
    block = lambda body: f"```diff\n{body}```"
    other = CALC_DIFF.replace("calc.py", "other.py")
    assert providers.last_diff(f"First try:\n{block(other)}\nBetter:\n{block(CALC_DIFF)}\nDone.") == CALC_DIFF, "the last block"
    assert providers.last_diff("No change is needed.\n```python\nx = 1\n```") is None, "only a diff block counts"
    assert providers.unsafe_paths(CALC_DIFF) == []
    new_file = "diff --git a/n.py b/n.py\nnew file mode 100644\n--- /dev/null\n+++ b/n.py\n@@ -0,0 +1 @@\n+x = 1\n"
    assert providers.unsafe_paths(new_file) == [], "/dev/null is not a path"
    # A removed line that reads like a header is hunk content, not a path.
    sql = "diff --git a/q.sql b/q.sql\n--- a/q.sql\n+++ b/q.sql\n@@ -1,2 +1 @@\n--- ../comment\n select 1\n"
    assert providers.unsafe_paths(sql) == [], providers.unsafe_paths(sql)
    for bad in ("../outside.py", "/etc/passwd", "C:/Windows/x.py", ".git/hooks/pre-commit", "src/../../x.py", ".GIT/config",
                "vendor/lib/.git/config"):
        diff = CALC_DIFF.replace("calc.py", bad)
        assert bad in providers.unsafe_paths(diff), (bad, providers.unsafe_paths(diff))
    rename = "diff --git a/calc.py b/.git/config\nsimilarity index 100%\nrename from calc.py\nrename to .git/config\n"
    assert ".git/config" in providers.unsafe_paths(rename), "a rename is a write too"
    link = "diff --git a/l b/l\nnew file mode 120000\n--- /dev/null\n+++ b/l\n@@ -0,0 +1 @@\n+/etc\n"
    assert providers.unsafe_paths(link), "a symlink could point a later write outside the worktree"


def check_codex_diff_mode(base: Path) -> None:
    """A Codex writer on Windows replies with a diff; the runner checks and applies it, retries once, and fails closed."""
    install_cli(base / "bin", "codex", FAKE_CODEX)
    seen = base / "codex-seen.json"
    repo = base / "diffrepo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "core.autocrlf", "false"], check=True)
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    (repo / "calc.py").write_bytes(CALC.encode())
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], check=True)
    writer = {"id": "writer", "provider": "codex", "model": "m", "access": "write-worktree"}
    log = base / "logs" / "write-0.log"  # outside the worktree, as in a council run

    def call(*replies: str, write: bool = False) -> tuple[str, list[dict]]:
        seen.unlink(missing_ok=True)
        os.environ["FAKE_CODEX_REPLIES"] = json.dumps(list(replies))
        os.environ.pop("FAKE_CODEX_WRITE", None)
        if write:
            os.environ["FAKE_CODEX_WRITE"] = "1"
        try:
            return providers.run(writer, "# Task\n\nFix add.", repo, log), json.loads(seen.read_text(encoding="utf-8"))
        finally:
            os.environ.pop("FAKE_CODEX_WRITE", None)

    def reset(content: bytes = CALC.encode()) -> None:
        (repo / "calc.py").write_bytes(content)

    os.environ["FAKE_CODEX_SEEN"] = str(seen)
    saved = providers.WINDOWS
    try:
        providers.WINDOWS = True
        reply, calls = call(f"Fixed the sign.\n```diff\n{CALC_DIFF}```")
        assert (repo / "calc.py").read_bytes() == FIXED.encode(), (repo / "calc.py").read_bytes()
        assert reply.startswith("Fixed the sign."), reply
        assert len(calls) == 1 and calls[0]["prompt"].startswith("# Task\n\nFix add."), calls
        assert "```diff" in calls[0]["prompt"] and "a/ and b/" in calls[0]["prompt"], "the prompt asks for a diff"
        assert calls[0]["argv"][calls[0]["argv"].index("--sandbox") + 1] == "read-only", calls[0]["argv"]
        assert "diff mode: applied" in log.read_text(encoding="utf-8"), log.read_text(encoding="utf-8")

        # A file checked out with CRLF keeps its line endings.
        reset(CALC.replace("\n", "\r\n").encode())
        call(f"```diff\n{CALC_DIFF}```")
        assert (repo / "calc.py").read_bytes() == FIXED.replace("\n", "\r\n").encode(), (repo / "calc.py").read_bytes()

        reset()
        reply, calls = call("The code is already correct.")
        assert (repo / "calc.py").read_bytes() == CALC.encode() and len(calls) == 1
        assert "no change" in log.read_text(encoding="utf-8"), "no diff block is no change, and the log says so"

        for bad in ("../calc.py", ".git/config"):
            try:
                call(f"```diff\n{CALC_DIFF.replace('calc.py', bad)}```")
                raise AssertionError(f"a diff that touches {bad} must be refused")
            except providers.ProviderError as error:
                assert bad in str(error), error
            assert (repo / "calc.py").read_bytes() == CALC.encode(), "nothing is applied"

        # A diff that does not apply gets one retry with the git error and the rejected diff.
        reply, calls = call(f"```diff\n{STALE_DIFF}```", f"Corrected.\n```diff\n{CALC_DIFF}```")
        assert (repo / "calc.py").read_bytes() == FIXED.encode(), "the corrected diff is applied"
        assert len(calls) == 2 and reply.startswith("Corrected."), (len(calls), reply)
        retry = calls[1]["prompt"]
        assert retry.startswith("# Task\n\nFix add.") and "return a * b" in retry and "error" in retry.lower(), retry

        reset()
        try:
            call(f"```diff\n{STALE_DIFF}```")
            raise AssertionError("a diff that fails twice must fail the call")
        except providers.ProviderError as error:
            assert str(log) in str(error), error
        assert len(json.loads(seen.read_text(encoding="utf-8"))) == 2, "one retry, no more"
        assert (repo / "calc.py").read_bytes() == CALC.encode()

        # The sandbox should stop it, but if the model writes the worktree itself, the call fails and no diff is applied.
        try:
            call(f"```diff\n{CALC_DIFF}```", write=True)
            raise AssertionError("a writer that changed the worktree itself must fail")
        except providers.ProviderError as error:
            assert "changed the worktree" in str(error), error
        assert (repo / "calc.py").read_text(encoding="utf-8") == "tampered\n", "the diff is not applied on top"

        # Linux and macOS: the writer edits in its workspace-write sandbox, and its reply is not applied.
        providers.WINDOWS = False
        reset()
        reply, calls = call(f"```diff\n{CALC_DIFF}```")
        assert "```diff" not in calls[0]["prompt"] and (repo / "calc.py").read_bytes() == CALC.encode(), calls
    finally:
        providers.WINDOWS = saved


def check_probe(base: Path) -> None:
    """The probe passes only when the role wrote nothing and could read: a reply without the file's secret fails it."""
    os.environ.update({"FAKE_CODEX_SEEN": str(base / "probe-seen.json"), "FAKE_CODEX_REPLIES": json.dumps(["READ"])})
    passed, line = providers.probe("codex", "m", base / "probe")
    assert passed and "can read" in line and "no writes" in line, line
    os.environ["FAKE_CODEX_REPLIES"] = json.dumps(["BLOCKED"])
    passed, line = providers.probe("codex", "m", base / "probe")
    assert not passed and "cannot read" in line, line
    assert not any((base / "probe").iterdir()), "the probe folder is removed"


def main() -> int:
    check_codex_windows_flags()
    check_diff_parsing()
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        bin_dir = base / "bin"
        bin_dir.mkdir()
        seen = base / "seen.json"
        os.environ.update({"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_CURSOR_SEEN": str(seen)})
        install_cli(bin_dir, "cursor-agent", FAKE_CURSOR)

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
        # Compared resolved: the fake agent reports os.getcwd(), which on macOS is /private/var/... for /var/...
        workspace = os.path.realpath(call["argv"][call["argv"].index("--workspace") + 1])
        assert os.path.realpath(call["cwd"]) != os.path.realpath(worktree) and workspace == os.path.realpath(call["cwd"])
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
            assert os.path.realpath(json.loads(seen.read_text())["cwd"]) == os.path.realpath(shared), "the call runs in the shared snapshot"
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
        os.environ.pop("FAKE_CURSOR_TOOLS")

        check_utf8_round_trip(role, worktree, log)
        check_timeout_stops_the_tree(role, worktree, log, base)
        check_codex_diff_mode(base)
        check_probe(base)

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
