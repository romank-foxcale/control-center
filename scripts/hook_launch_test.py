#!/usr/bin/env python3
"""Run the literal rule-hook commands of .claude/settings.json the way Claude Code runs them, and check they deny.

Claude Code runs a shell-form hook with `sh -c` on Linux and macOS and with Git Bash on Windows, where it sets
CLAUDE_PROJECT_DIR as C:/... (plan native-core, spike S2). The PATH here has no Python, so the hook must reach
its pinned Python through the cc shim and uv. Not a Nix check: it needs the shim's uv and Python in .cc-local
(the first run downloads them if they are missing). Run: python -B scripts/hook_launch_test.py
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = os.name == "nt"
LIMIT = 5.0  # seconds, cold and warm
# What the shim and the hooks call on POSIX; the test PATH links only these, so no python is on it.
POSIX_TOOLS = ("sh", "awk", "uname", "dirname", "git", "cat", "cut", "mkdir", "mktemp", "rm", "mv", "chmod", "tar",
               "gzip", "curl", "wget", "sha256sum", "shasum")


def commands() -> dict[str, str]:
    """The literal command of each rule hook, keyed by its cc hook event name."""
    settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    found = {}
    for groups in settings["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                assert hook.get("onFailure") == "block" and hook.get("timeout") == 15, hook
                found[hook["command"].rsplit(" ", 1)[1]] = hook["command"]
    return found


def git_bash() -> Path:
    git = shutil.which("git")
    candidates = [Path(git).resolve().parent.parent / "bin" / "bash.exe"] if git else []
    if git:  # git.exe lives in Git\cmd, Git\bin or Git\mingw64\bin
        candidates.append(Path(git).resolve().parent.parent.parent / "bin" / "bash.exe")
    candidates.append(Path(r"C:\Program Files\Git\bin\bash.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit("hook launch test: Git Bash not found; Git for Windows is a prerequisite of the CC on Windows")


def environment(bin_dir: Path, cache: Path) -> dict[str, str]:
    env = {"CLAUDE_PROJECT_DIR": ROOT.as_posix(), "UV_CACHE_DIR": str(cache)}
    if WINDOWS:
        system = os.environ.get("SYSTEMROOT", r"C:\Windows")
        git_cmd = git_bash().parent.parent / "cmd"
        env["PATH"] = os.pathsep.join([str(Path(system) / "System32"), system, str(git_cmd)])
        for name in ("SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
                     "PROGRAMDATA", "COMSPEC", "PATHEXT", "WINDIR", "HOMEDRIVE", "HOMEPATH"):
            if name in os.environ:
                env[name] = os.environ[name]
    else:
        for tool in POSIX_TOOLS:
            found = shutil.which(tool)
            if found and not (bin_dir / tool).exists():
                (bin_dir / tool).symlink_to(found)
        env["PATH"] = str(bin_dir)
        env["HOME"] = os.environ.get("HOME", str(bin_dir))
    return env


def launcher(command: str) -> list[str]:
    return [str(git_bash()), "-c", command] if WINDOWS else ["sh", "-c", command]


def run(command: str, event: dict, env: dict[str, str]) -> tuple[str | None, str, float]:
    """The hook's decision (deny, ask, or None for allow), its reason or stderr, and the wall time."""
    start = time.perf_counter()
    result = subprocess.run(launcher(command), input=json.dumps(event).encode(), capture_output=True, env=env,
                            timeout=15)
    elapsed = time.perf_counter() - start
    stderr = result.stderr.decode(errors="replace")
    if result.returncode == 2:
        return "deny", stderr, elapsed
    assert result.returncode == 0, f"exit {result.returncode} (blocks as a failure, enforces nothing): {stderr}"
    output = result.stdout.decode(errors="replace").strip()
    if not output:
        return None, stderr, elapsed
    decision = json.loads(output)["hookSpecificOutput"]
    return decision["permissionDecision"], decision["permissionDecisionReason"], elapsed


def main() -> int:
    if not list((ROOT / ".cc-local/tools/uv").glob("*/uv.exe" if WINDOWS else "*/uv")):
        print("hook launch test: no uv in .cc-local yet; the first run downloads uv and Python (not timed)")
        subprocess.run([str(ROOT / ("cc.cmd" if WINDOWS else "cc")), "help"], check=True, capture_output=True)
    hooks = commands()
    with tempfile.TemporaryDirectory() as directory, \
            tempfile.TemporaryDirectory(dir=ROOT / ".cc-local", prefix="hook-launch-",
                                        ignore_cleanup_errors=True) as nested:
        bin_dir, cache = Path(directory) / "bin", Path(directory) / "uv-cache"
        bin_dir.mkdir()
        cache.mkdir()
        env = environment(bin_dir, cache)
        probe = subprocess.run(launcher("command -v python3 python py || true"), capture_output=True, text=True, env=env)
        assert not probe.stdout.strip(), f"the test PATH must have no Python: {probe.stdout}"

        # (c) needs a repository inside the CC with no passing verify: a fresh one under .cc-local (git-ignored).
        project = Path(nested)
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.com"]
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        (project / "file.txt").write_text("x\n")
        subprocess.run(["git", "-C", str(project), "add", "file.txt"], check=True)
        subprocess.run(["git", "-C", str(project), *identity, "commit", "-q", "-m", "one"], check=True)

        trailer = "Co-" + "Authored-By: Claude <noreply@" + "anthropic.com>"
        cases = [
            ("AI attribution in a commit", "pre-bash", "deny", "forbids crediting an AI",
             {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(ROOT),
              "tool_input": {"command": f'git commit -m "Fix the parser\n\n{trailer}"'}}),
            ("Edit in a reference repo", "pre-edit", "deny", "reference repo",
             {"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": str(ROOT),
              "tool_input": {"file_path": str(ROOT / "repos/reference/poc/main.py"), "old_string": "a",
                             "new_string": "b"}}),
            # An unverified push asks the user (docs/kit/decisions/0028-verify-before-push.md): it is not allowed.
            ("unverified git push", "pre-bash", "ask", "No passing",
             {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(project),
              "tool_input": {"command": "git push origin HEAD"}}),
        ]
        times: list[float] = []
        for name, hook, expected, reason_part, event in cases:
            for _ in range(2):
                decision, reason, elapsed = run(hooks[hook], event, env)
                assert decision == expected and reason_part in reason, (name, decision, reason)
                times.append(elapsed)
            print(f"ok: {name}: {decision} ({times[-2]:.2f} s, then {times[-1]:.2f} s)")
        cold, warm = times[0], times[1:]
        print(f"cold (first launch, empty uv cache): {cold:.2f} s; warm median {statistics.median(warm):.2f} s, "
              f"max {max(warm):.2f} s")
        assert cold < LIMIT and max(warm) < LIMIT, f"a hook took longer than {LIMIT} s"
    print("hook launch test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
