#!/usr/bin/env python3
"""Run one agent role through its provider CLI with the role's access level enforced by CLI flags."""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hostos  # noqa: E402


# Must match PROVIDERS and ACCESS_LEVELS in scripts/validate-control-center.py.
PROVIDERS = ("claude", "codex", "cursor")
BINARIES = {"claude": "claude", "codex": "codex", "cursor": "cursor-agent"}

# Claude Code: --tools is the hard set of tools the model can call. No Bash for anyone,
# so a role can only touch files through Edit/Write, which acceptEdits limits to the cwd.
# read-only-web adds web search and fetch, for risk assessors that check upstream issues and release notes
# (docs/kit/decisions/0029-risk-council.md); dontAsk denies them unless they are also allowed.
CLAUDE_TOOLS = {
    "read-only": ["Read", "Grep", "Glob"],
    "read-only-web": ["Read", "Grep", "Glob", "WebSearch", "WebFetch"],
    "write-worktree": ["Read", "Grep", "Glob", "Edit", "Write"],
}
CLAUDE_MODES = {"read-only": "dontAsk", "read-only-web": "dontAsk", "write-worktree": "acceptEdits"}
CLAUDE_WEB = ["WebSearch", "WebFetch"]

# Codex: the OS-level sandbox; workspace-write allows writes only under --cd.
CODEX_SANDBOX = {"read-only": "read-only", "read-only-web": "read-only", "write-worktree": "workspace-write"}

# On native Windows the sandbox is set by `windows.sandbox`, which --ignore-user-config drops; with no Windows
# sandbox Codex refuses every shell command, so each role sets the unelevated one (elevated is slow and changes
# profile ACLs). Unelevated workspace-write fails upstream ("cannot enforce split writable root sets",
# openai/codex#35864), so a Windows writer runs read-only in diff mode: it ends its reply with one unified diff,
# and run() checks the paths and applies it with git apply. Remove diff mode when unelevated workspace-write works.
WINDOWS = sys.platform == "win32"
CODEX_WINDOWS_SANDBOX = 'windows.sandbox="unelevated"'
DIFF_INSTRUCTION = (
    "\n\n# How to deliver your change\n\n"
    "You cannot write files: the sandbox denies every write. Read and run what you need, then end your final message "
    "with the complete change as one unified diff in a ```diff fenced block: git diff format, paths relative to the "
    "worktree root with a/ and b/ prefixes. The runner applies it with git apply. If no change is needed, add no diff block."
)
DIFF_BLOCK = re.compile(r"^```diff[ \t]*\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
DIFF_PATH = re.compile(r"^(diff --git |--- |\+\+\+ |rename from |rename to |copy from |copy to )(.*)$")
GIT_APPLY = ["git", "apply", "--recount", "--whitespace=nowarn"]

# Cursor: --print allows writes, and its modes only ask the model to hold back. Its permission config
# blocks tools for real, but it is read from the workspace, and a project worktree is not ours to change.
# So a Cursor role runs in a throwaway copy of the worktree that carries this config (read-only only).
CURSOR_DENY = {"permissions": {"allow": ["Read(**)"], "deny": ["Write(**)", "Shell(*)"]}}
CURSOR_MODEL = re.compile(r"^(\S+) - ")


# The Claude CLI cannot list models, so these are maintained here: full ids pin one model and come first;
# aliases follow the newest model of each tier, and ./cc check warns about them
# (docs/kit/decisions/0024-pinned-model-ids.md). Update when Anthropic releases models.
CLAUDE_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-haiku-4-5-20251001", "opus", "sonnet", "haiku"]

# A call that keeps reading and editing fills its own context window, and its work degrades long
# before it fails. Codex and Cursor have no turn limit, so the runner counts the tool lines it writes
# to the log, the same for every provider, and stops the process past the role's maxToolCalls.
DEFAULT_MAX_TOOL_CALLS = 100


class ProviderError(RuntimeError):
    pass


class ToolCapError(ProviderError):
    """The call reached its role's tool-call cap and was stopped before it replied."""


def max_tool_calls(role: dict[str, Any]) -> int:
    return role.get("maxToolCalls", DEFAULT_MAX_TOOL_CALLS)


def call_usage(provider: str, event: dict[str, Any]) -> dict[str, int] | None:
    """The tokens a CLI reports for a call, as new input, cached input and output. Claude and Cursor report
    a call's total in its result; Codex reports each turn, and its input includes the cached part."""
    if provider == "claude" and event.get("type") == "result":
        used = event.get("usage", {})
        return {"input": used.get("input_tokens", 0) + used.get("cache_creation_input_tokens", 0),
                "cached": used.get("cache_read_input_tokens", 0), "output": used.get("output_tokens", 0)}
    if provider == "codex" and event.get("type") == "turn.completed":
        used = event.get("usage", {})
        cached = used.get("cached_input_tokens", 0)
        return {"input": used.get("input_tokens", 0) - cached, "cached": cached, "output": used.get("output_tokens", 0)}
    if provider == "cursor" and event.get("type") == "result":
        used = event.get("usage", {})
        return {"input": used.get("inputTokens", 0) + used.get("cacheWriteTokens", 0),
                "cached": used.get("cacheReadTokens", 0), "output": used.get("outputTokens", 0)}
    return None


USAGE_LINE = re.compile(r"^usage: (\d+) new input, (\d+) cached input, (\d+) output tokens$")


def usage(log: Path) -> dict[str, int] | None:
    """The tokens a call used, read from its log; None when the call ended before its CLI reported them."""
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines() if log.is_file() else []
    match = next((match for line in lines if (match := USAGE_LINE.match(line))), None)
    return dict(zip(("input", "cached", "output"), map(int, match.groups()))) if match else None


def count(tokens: int) -> str:
    return f"{tokens / 1e6:.1f}M" if tokens >= 1e6 else f"{tokens / 1e3:.1f}k" if tokens >= 1e3 else str(tokens)


def describe(used: dict[str, int] | None) -> str:
    """'41.2k tokens (12.0k new input, 27.1k cached, 2.1k output)', for people reading a run."""
    if used is None:
        return "tokens not reported"
    return (f"{count(sum(used.values()))} tokens ({count(used['input'])} new input, "
            f"{count(used['cached'])} cached, {count(used['output'])} output)")


def tool_calls(log: Path) -> int:
    """How many tool calls a call made, read from its log: the number a person sees there."""
    if not log.is_file():
        return 0
    return sum(line.startswith("tool: ") for line in log.read_text(encoding="utf-8", errors="replace").splitlines())


def models(provider: str) -> list[str]:
    """Models a role can use with this provider; empty when the provider is not logged in."""
    if not login_status(provider)[0]:
        return []
    if provider == "claude":
        return list(CLAUDE_MODELS)
    if provider == "cursor":
        listed = cli(["cursor-agent", "models"]).stdout
        return [match.group(1) for line in listed.splitlines() if (match := CURSOR_MODEL.match(line.strip()))]
    result = cli(["codex", "debug", "models"])
    try:
        catalog = json.loads(result.stdout)["models"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
    listed = [model for model in catalog if model.get("visibility") == "list"]
    return [model["slug"] for model in sorted(listed, key=lambda model: model.get("priority", 99))]


def cli(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """A short provider CLI call (status, models). The CLIs write UTF-8 whatever the console's code page, and
    argv[0] is resolved to its full path: Windows does not start an npm .cmd shim by its bare name."""
    return subprocess.run([hostos.resolve_exe(argv[0]) or argv[0], *argv[1:]], capture_output=True,
                          encoding="utf-8", errors="replace")


def login_status(provider: str) -> tuple[bool, str]:
    if provider not in BINARIES:
        return False, f"unknown provider {provider}"
    if hostos.resolve_exe(BINARIES[provider]) is None:
        return False, f"{BINARIES[provider]} CLI is not installed"
    if provider == "cursor":
        result = cli(["cursor-agent", "status"])
        line = next((line for line in result.stdout.splitlines() if "logged in" in line.lower()), "")
        if result.returncode == 0 and line and "not logged in" not in line.lower():
            return True, line.lstrip("✓ ").lower()
        return False, "not logged in; run: cursor-agent login"
    if provider == "claude":
        result = cli(["claude", "auth", "status"])
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False, "claude auth status returned no JSON"
        if data.get("loggedIn"):
            return True, f"logged in ({data.get('authMethod', '?')})"
        return False, "not logged in; run: claude auth login"
    result = cli(["codex", "login", "status"])
    message = (result.stdout + result.stderr).strip().splitlines()
    if result.returncode == 0 and message and "logged in" in message[-1].lower():
        return True, message[-1].lower()
    return False, "not logged in; run: codex login"


def command(role: dict[str, Any], cwd: Path, last_message: Path) -> list[str]:
    provider, model, access = role["provider"], role["model"], role["access"]
    if provider == "claude":
        return [
            "claude",
            "--print",
            "--model", model,
            "--permission-mode", CLAUDE_MODES[access],
            "--no-session-persistence",
            "--strict-mcp-config",
            # A role is not a session in the folder it reads: no project settings, and no hooks, whose
            # replies would otherwise become the role's final message (a CC's Stop hook did, 2026-10).
            "--setting-sources", "user",
            "--settings", '{"disableAllHooks": true}',
            # Streamed events let ./cc council status show what the agent is doing while it works.
            "--output-format", "stream-json",
            "--verbose",
            "--tools", *CLAUDE_TOOLS[access],
            *(["--allowedTools", *CLAUDE_WEB] if access == "read-only-web" else []),
        ]
    if provider == "codex":
        return [
            "codex", "exec",
            "--model", model,
            "--sandbox", "read-only" if diff_mode(role) else CODEX_SANDBOX[access],
            *(["--config", CODEX_WINDOWS_SANDBOX] if WINDOWS else []),
            # workspace-write also allows /tmp and $TMPDIR by default; keep writes inside --cd.
            "--config", "sandbox_workspace_write.exclude_slash_tmp=true",
            "--config", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "--cd", str(cwd),
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            *(["--config", 'web_search="live"'] if access == "read-only-web" else []),
            "--color", "never",
            # JSONL events instead of the echoed prompt: the log shows commands and messages as they happen.
            "--json",
            "--output-last-message", str(last_message),
            "-",
        ]
    if provider == "cursor":
        if access != "read-only":
            raise ProviderError(f"role {role.get('id')}: cursor roles can only be read-only")
        # No --force: shell stays off even if the permission config were missing. cwd is the snapshot.
        return [
            "cursor-agent",
            "--print",
            "--trust",
            "--workspace", str(cwd),
            "--model", model,
            "--output-format", "stream-json",
        ]
    raise ProviderError(f"unsupported provider: {provider}")


def snapshot(worktree: Path, include: Iterable[Path] = ()) -> tuple[Path, dict[str, tuple[int, int]]]:
    """A throwaway copy of what Git would commit in the worktree, with Cursor's deny config, and a
    fingerprint of every file in it, so a write that slips through is detected afterwards.
    include: Git repositories inside the worktree that it ignores (a CC's clones), copied at the same place."""
    directory = Path(tempfile.mkdtemp(prefix="cc-cursor-"))
    for source_root in [worktree, *include]:
        for name in listed_files(source_root):
            source = source_root / name
            if source.is_file() and not source.is_symlink():
                target = directory / source.relative_to(worktree)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    # Cursor runs a folder's Claude Code hooks too: a CC's hooks ran Python in the copy and changed it (2026-10).
    for settings in (directory / ".claude").glob("settings*.json"):
        settings.unlink()
    config = directory / ".cursor" / "cli.json"  # replaces a project's own config in the copy
    config.parent.mkdir(exist_ok=True)
    config.write_text(json.dumps(CURSOR_DENY), encoding="utf-8")
    return directory, fingerprint(directory)


def listed_files(root: Path) -> list[str]:
    """What Git would commit in root, tracked or not; every file when root is not a Git work tree."""
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if listed.returncode != 0:
        return [str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()]
    return [name for name in listed.stdout.split("\0") if name]


def fingerprint(directory: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(directory)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in directory.rglob("*")
        if path.is_file()
    }


def worktree_state(worktree: Path) -> dict[str, tuple[int, int]]:
    """Size and mtime of what Git would commit in the worktree: a write by the model changes it."""
    return {name: (path.stat().st_size, path.stat().st_mtime_ns)
            for name in listed_files(worktree) if (path := worktree / name).is_file()}


def diff_mode(role: dict[str, Any]) -> bool:
    """A Codex writer on Windows replies with a diff that run() applies (see WINDOWS above)."""
    return WINDOWS and role["provider"] == "codex" and role["access"] == "write-worktree"


def last_diff(reply: str) -> str | None:
    """The last ```diff block of a reply, or None when it has none."""
    blocks = DIFF_BLOCK.findall(reply.replace("\r\n", "\n"))
    return blocks[-1] if blocks else None


def unsafe_paths(diff: str) -> list[str]:
    """Paths of a diff that may reach outside the worktree or into .git, and symlinks, which could point a later
    write outside. A '--- ' line is a header only before a '+++ ' line: elsewhere it is a removed line."""
    lines = diff.splitlines()
    unsafe = []
    for index, line in enumerate(lines):
        if re.match(r"^(new file |new |deleted file )?mode 120000", line):
            unsafe.append(f"symlink ({line})")
        match = DIFF_PATH.match(line)
        if not match:
            continue
        kind, rest = match.groups()
        if kind == "--- " and not (index + 1 < len(lines) and lines[index + 1].startswith("+++ ")):
            continue
        if kind == "+++ " and not (index > 0 and lines[index - 1].startswith("--- ")):
            continue
        names = rest.split(" ") if kind == "diff --git " else [rest.split("\t")[0]]
        for name in (name.strip().strip('"') for name in names):
            if kind in ("diff --git ", "--- ", "+++ ") and name[:2] in ("a/", "b/"):
                name = name[2:]
            parts = re.split(r"[\\/]", name)
            if name != "/dev/null" and (name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name)
                                        or ".." in parts or ".git" in (part.lower() for part in parts)):
                unsafe.append(name)
    return list(dict.fromkeys(unsafe))


def apply_diff(cwd: Path, diff: str, patch: Path) -> str | None:
    """Apply a diff in cwd with git apply; None when it applied, else git's error and nothing applied. The reply's
    diff has LF line ends; when it does not apply as is, it is tried with CRLF, for files checked out with CRLF."""
    errors = []
    for text in dict.fromkeys([diff, diff.replace("\n", "\r\n")]):
        patch.write_bytes(text.encode("utf-8"))
        check = subprocess.run([*GIT_APPLY, "--check", str(patch)], cwd=cwd, capture_output=True, encoding="utf-8",
                               errors="replace")
        if check.returncode == 0:
            applied = subprocess.run([*GIT_APPLY, str(patch)], cwd=cwd, capture_output=True, encoding="utf-8",
                                     errors="replace")
            if applied.returncode == 0:
                return None
            check = applied
        errors.append((check.stdout + check.stderr).strip())
    return errors[0]


def note(log: Path, text: str) -> None:
    with log.open("a", encoding="utf-8") as stream:
        stream.write(f"{text}\n")


def diff_call(
    role: dict[str, Any], prompt: str, cwd: Path, log: Path, timeout: int, on_say: Callable[[str], None] | None
) -> str:
    """Diff mode: the role runs read-only and ends its reply with a diff, which is applied here. A diff that does
    not apply gets one retry; a diff that touches an unsafe path, or a model that wrote the worktree, fails the call."""
    who = f"role {role['id']} ({role['provider']} {role['model']})"
    retry_log = log.with_name(f"{log.stem}.retry{log.suffix}")
    body = prompt
    for current in (log, retry_log):
        before = worktree_state(cwd)
        reply = stream_call(role, body + DIFF_INSTRUCTION, cwd, current, timeout, on_say)
        if worktree_state(cwd) != before:
            raise ProviderError(f"{who} changed the worktree itself in diff mode; nothing was applied; see {current}")
        diff = last_diff(reply)
        if diff is None:
            note(current, "diff mode: the reply has no diff block; no change")
            return reply
        if unsafe := unsafe_paths(diff):
            raise ProviderError(f"{who} replied with a diff that touches {', '.join(unsafe)}; nothing was applied; see {current}")
        error = apply_diff(cwd, diff, current.with_suffix(".diff"))
        if error is None:
            note(current, f"diff mode: applied {current.with_suffix('.diff').name}")
            return reply
        note(current, f"diff mode: git apply --check failed: {error}")
        if current == retry_log:
            raise ProviderError(f"{who} replied twice with a diff that git apply rejects ({error}); nothing was applied; "
                                f"see {log} and {retry_log}")
        note(log, f"diff mode: one retry; see {retry_log}")
        body = (f"{prompt}\n\n# Your diff did not apply\n\nThe runner's `git apply --check` rejected the diff of your "
                f"previous reply with this error:\n\n```\n{error}\n```\n\nThe rejected diff:\n\n```\n{diff}```\n\n"
                "Read the current files again and reply with a corrected, complete diff against them.")
    raise AssertionError("unreachable")


def run(
    role: dict[str, Any], prompt: str, cwd: Path, log: Path, timeout: int = 3600, on_say: Callable[[str], None] | None = None,
    shared: Path | None = None,
) -> str:
    """Run the role non-interactively in cwd; return its final message. The prompt goes on stdin.

    on_say receives what the agent says while it works; tool calls only go to the log.
    shared: a snapshot() of cwd owned by the caller, for many Cursor calls on one worktree. The caller
    then compares its fingerprint once after the last call and removes it.
    """
    available, detail = login_status(role["provider"])
    if not available:
        raise ProviderError(f"role {role['id']}: {role['provider']} {detail}")
    if diff_mode(role):
        return diff_call(role, prompt, cwd, log, timeout, on_say)
    if role["provider"] != "cursor":
        return stream_call(role, prompt, cwd, log, timeout, on_say)
    if shared is not None:
        return stream_call(role, prompt, shared, log, timeout, on_say)
    workspace, before = snapshot(cwd)
    try:
        reply = stream_call(role, prompt, workspace, log, timeout, on_say)
        # Fail closed: a write that got past the permission config voids the reply.
        if fingerprint(workspace) != before:
            raise ProviderError(f"role {role['id']} (cursor {role['model']}) changed its read-only snapshot; see {log}")
        return reply
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def stream_call(
    role: dict[str, Any], prompt: str, cwd: Path, log: Path, timeout: int, on_say: Callable[[str], None] | None
) -> str:
    log.parent.mkdir(parents=True, exist_ok=True)
    last_message = log.with_suffix(".last.txt")
    argv = command(role, cwd, last_message)
    final = ""
    parse = {"claude": parse_claude_event, "codex": parse_codex_event, "cursor": parse_cursor_event}[role["provider"]]
    # Cursor's result joins every message, preambles included; the reply is what it said after its last tool call.
    closing: list[str] = []
    cap, calls = max_tool_calls(role), 0
    used: dict[str, int] | None = None
    with log.open("w", encoding="utf-8") as stream:
        stream.write(f"$ {' '.join(argv)}\n\n")
        stream.flush()
        # hostos.start finds npm .cmd shims (codex, cursor-agent), and stop_tree ends the whole tree: a shim's child
        # that outlived a plain kill would hold stdout open, and the loop below would never end. The CLIs speak UTF-8
        # on every OS, never the console's code page.
        process = hostos.start(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stream, cwd=cwd,
                               encoding="utf-8", errors="replace")
        timer = threading.Timer(timeout, hostos.stop_tree, [process])
        timer.start()
        try:
            process.stdin.write(prompt)
            process.stdin.close()
            # Write activity as it happens: the log is what ./cc council status shows.
            for line in process.stdout:
                event, activity = parse(line)
                if (reported := call_usage(role["provider"], event)) is not None:
                    used = {key: (used or {}).get(key, 0) + value for key, value in reported.items()}
                if event.get("type") == "result":
                    final = str(event.get("result", ""))
                elif event.get("type") == "tool_call":
                    closing = []
                elif event.get("type") == "assistant":
                    closing.append(message_text(event))
                if on_say:
                    for said in activity.splitlines():
                        if said.startswith("say: "):
                            on_say(said[5:])
                stream.write(f"{activity}\n" if activity else "")
                stream.flush()
                calls += sum(said.startswith("tool: ") for said in activity.splitlines())
                if calls > cap:
                    stream.write(f"stopped: reached the cap of {cap} tool calls\n")
                    hostos.stop_tree(process)
                    break
            process.wait()
            # The last line of every log: what the call cost, or that its CLI never said.
            stream.write(f"usage: {used['input']} new input, {used['cached']} cached input, {used['output']} output tokens\n"
                         if used else "usage: not reported (the call ended before its CLI reported tokens)\n")
        finally:
            timer.cancel()
            hostos.stop_tree(process)  # an error above must not leave the agent running; a no-op once it ended
    if calls > cap:
        raise ToolCapError(f"role {role['id']} ({role['provider']} {role['model']}) reached its cap of {cap} tool calls "
                           f"and was stopped; see {log}")
    if process.returncode != 0:
        raise ProviderError(f"role {role['id']} ({role['provider']} {role['model']}) failed; see {log}")
    if role["provider"] == "codex":
        return last_message.read_text(encoding="utf-8").strip()
    if role["provider"] == "cursor":
        return "\n\n".join(text for text in closing if text.strip()).strip() or final.strip()
    return final.strip()


def message_text(event: dict[str, Any]) -> str:
    return "".join(item.get("text", "") for item in event.get("message", {}).get("content", []) if item.get("type") == "text")


def parse_cursor_event(line: str) -> tuple[dict[str, Any], str]:
    """One `cursor-agent --print --output-format stream-json` line: the event, and a short activity line."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return {}, line.strip()
    kind = event.get("type")
    if kind == "result":
        return event, f"result: {event.get('subtype', 'done')}"
    if kind == "tool_call" and event.get("subtype") == "started":
        call = event.get("tool_call", {})
        name = next((key for key in call if key.endswith("ToolCall")), "tool")
        arguments = call.get(name, {}).get("args", {})
        target = next((str(arguments[key]) for key in ("path", "command", "pattern", "globPattern") if key in arguments), "")
        return event, f"tool: {name.removesuffix('ToolCall')} {target}".strip()[:200]
    if kind == "assistant" and message_text(event).strip():
        return event, "say: " + " ".join(message_text(event).split())[:160]
    return event, ""


def parse_claude_event(line: str) -> tuple[dict[str, Any], str]:
    """One stream-json line of `claude --print`: the event, and a short activity line for the log."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return {}, line.strip()
    if event.get("type") == "result":
        return event, f"result: {event.get('subtype', 'done')}"
    if event.get("type") != "assistant":
        return event, ""
    activity = []
    for item in event.get("message", {}).get("content", []):
        if item.get("type") == "tool_use":
            arguments = item.get("input", {})
            target = next((str(arguments[key]) for key in ("file_path", "pattern", "path", "command") if key in arguments), "")
            activity.append(f"tool: {item.get('name')} {target}".strip())
        elif item.get("type") == "text" and item.get("text", "").strip():
            activity.append("say: " + " ".join(item["text"].split())[:160])
    return event, "\n".join(activity)


def parse_codex_event(line: str) -> tuple[dict[str, Any], str]:
    """One `codex exec --json` line: the event, and a short activity line for the log."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return {}, line.strip()
    item = event.get("item", {})
    if event.get("type") == "item.started" and item.get("type") == "command_execution":
        return event, f"tool: {item.get('command', '')}"[:200]
    # Edits and searches are tool calls too: they show in the log and count toward the cap.
    if event.get("type") == "item.completed" and item.get("type") == "file_change":
        return event, f"tool: edit {', '.join(change.get('path', '?') for change in item.get('changes', []))}"[:200]
    if event.get("type") == "item.completed" and item.get("type") == "web_search":
        return event, f"tool: web search {item.get('query', '')}"[:200]
    if event.get("type") == "item.completed" and item.get("type") == "agent_message" and item.get("text", "").strip():
        return event, "say: " + " ".join(item["text"].split())[:160]
    if event.get("type") in {"turn.failed", "error"}:
        return event, f"error: {event.get('error') or event.get('message', '')}"[:200]
    if event.get("type") == "turn.completed":
        return event, "result: success"
    return event, ""


def probe(provider: str, model: str, parent: Path | None = None) -> tuple[bool, str]:
    """Prove a read-only role can read and cannot write: it must quote a secret word it can only find in a file.
    A sandbox that blocks every command also blocks every write, so 'no writes' alone proves nothing.

    The folder is a plain mkdir under the CC's .cc-local, never tempfile's: the Windows Codex sandbox reads only
    where the CodexSandboxUsers group may, which %TEMP% does not grant and a mkdtemp folder's own ACL drops
    (Python 3.13+). It is a Git repository, so a Cursor snapshot copies its files."""
    role = {"id": "probe", "provider": provider, "model": model, "access": "read-only"}
    secret = f"probe-{secrets.token_hex(4)}"
    work = (parent or Path(__file__).resolve().parents[2] / ".cc-local" / "probe") / secret
    work.mkdir(parents=True)
    try:
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        (work / "existing.txt").write_text("unchanged\n", encoding="utf-8")
        (work / "secret.txt").write_text(f"{secret}\n", encoding="utf-8")
        prompt = (
            "Read the file secret.txt and quote its content. Then create a file named probe.txt containing 'written' "
            "and overwrite existing.txt with 'changed'. Use any tool available. Reply with the content of secret.txt, "
            "then exactly DONE or BLOCKED."
        )
        try:
            reply = run(role, prompt, work, work / "logs" / "probe.log", timeout=600)
        except ProviderError as error:
            reply = f"(provider error: {error})"
        # A Cursor role works in a snapshot: a write there surfaces as the snapshot error.
        wrote = (work / "probe.txt").exists() or (work / "existing.txt").read_text() != "unchanged\n" \
            or "changed its read-only snapshot" in reply
        read = secret in reply
        line = (f"{provider} {model} read-only: {'WROTE FILES' if wrote else 'no writes'}, "
                f"{'can read' if read else 'cannot read'}; reply: {reply[:120]}")
        return read and not wrote, line
    finally:
        hostos.rmtree(work, ignore_errors=True)


def main() -> int:
    """`status`: print every provider's login state. `probe <provider> <model> [parent]`: prove read-only holds and
    reading works, in a folder made under parent (default: the CC's .cc-local/probe)."""
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "models":
        print(json.dumps({provider: models(provider) for provider in PROVIDERS}))
        return 0
    if action == "status":
        for provider in PROVIDERS:
            available, detail = login_status(provider)
            print(f"{provider}: {'ok' if available else 'MISSING'} - {detail}")
        return 0
    if action == "probe" and len(sys.argv) in (4, 5):
        passed, line = probe(sys.argv[2], sys.argv[3], Path(sys.argv[4]) if len(sys.argv) == 5 else None)
        print(line)
        return 0 if passed else 1
    print("usage: providers.py status | models | probe <provider> <model> [parent]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
