#!/usr/bin/env python3
"""The CC's command line, on Windows, macOS and Linux. The cc / cc.cmd shims run it with the pinned Python from
uv.lock (plan native-core). Every command keeps the name, arguments and exit codes of the former bash ./cc.

check, verify and show run natively and add their Nix part when Nix is installed. build, run, relock, gc and eval
belong to the optional Nix extra and fail with a clear message without it. The command x OS table is in
docs/kit/rules/executable-processes.md.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

USAGE = """Usage: ./cc <command> [arguments]

Commands:
  doctor [--disk]  Check local prerequisites; --disk shows where disk space goes (read-only)
  backup [--to DIR] [--push]  Copy the CC's history, uncommitted work and .cc-local state outside the CC (on WSL: to a Windows drive); --push also sends it to the private cc-backup branch; irreplaceable localData is mirrored, never pushed
  backup where | backup restore <name> [--all]  Where every backup and data mirror is, with links and restore commands; copy a data entry back
  data remove <name> [--mirror]  Delete a localData entry (and its mirrors) after you type <cc name>/<name> in your own terminal
  gc [--apply] [--days N]  (Nix extra) Free Nix store space: remove this CC's result links, collect old generations (dry run without --apply)
  relock         (Nix extra) Move only the locked nixpkgs to the kit's recommended rev, so CCs share Nix store paths
  show           Show the check groups and, with Nix, the flake packages, checks and apps
  check          Run every CC check, plus nix flake check when Nix is installed
  build <name>   (Nix extra) Build .#<name>
  run <name>     (Nix extra) Run .#<name>
  verify [repo...] [--quality] [--full]  Run every check plus each catalog target gate (and quality gates)
  council <run|approve|reject|status|models|set-role|set> ...  Multi-model councils and their settings
  watch [run]    Follow a council or verify run live: steps, agent talk, stalls, result
  eval <run|compare|trace|regrade> ...  (Nix extra) Measure an agent-context change: headless claude runs, graded, with tokens, cost and time
  settings [show]  Settings panel: agents and models, councils, repos; show prints them as text
  validate       Validate the Markdown knowledge graph
  agents <sync|check>  Mirror .agents/skills into .claude/skills
  hook <event>   Run a rule hook on the event JSON in stdin (called by Claude Code, see .claude/settings.json)
  bootstrap install [target]  Materialize a new CC from this clone
  bootstrap validate          Validate the configured CC contract
  repo <add|new|list|show|remove|set-branch|set-remote|set-kind|set-status|set-targets|set-stack|strays> ...
  prs [repo...] [--json]  Open pull requests of the catalog repos, newest first, new ones and waiting threads marked
  prs show <repo> <n> [--diff] | prs checkout <repo> <n>  One PR for review; its head as feature pr-<repo>-<n>
  prs threads|judge <repo> <n> [--all] [--json]  Review threads waiting on you; judge them with anonymous sceptics
  prs learn [repo...] [--since YYYY-MM-DD] [--harvest-only] [--json]  Accepted review comments of merged PRs since the last run; the review-learner agent turns them into candidate lessons
  worktree <create|add|land|status|remove> ...
  migrate-layout [--dry-run]  Move this CC's clones and worktrees from ../repos and ../worktrees into repos/ and worktrees/
  trello <status|boards|lists|connect|link|show|comment|move> ...  Trello cards, on demand only
  open <feature> [--tool claude|codex]  Start a coding session in the CC with the feature's worktrees
  plan <create|list|show|accept|archive|complete> ...
  feature <name> <show|check|build|run|verify> ...  Use feature input overrides
"""

# Commands that only forward their arguments to one script, with the CC root first.
FORWARD = {
    "data": "data.py",
    "repo": "repositories.py",
    "prs": "prs.py",
    "worktree": "worktrees.py",
    "migrate-layout": "migrate_layout.py",
    "trello": "trello.py",
    "watch": "events.py",
    "council": "council/run.py",
    "plan": "plans.py",
}


class Usage(Exception):
    """A usage error: the message goes to stderr and the exit code is the given one."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def script(name: str, *arguments: str, root: bool = True) -> int:
    """Run scripts/<name> with this interpreter; the CC root is the first argument unless root=False."""
    prefix = [str(ROOT)] if root else []
    return subprocess.run([sys.executable, str(SCRIPTS / name), *prefix, *arguments]).returncode


def require_nix() -> str:
    nix = shutil.which("nix")
    if nix is None:
        raise Usage("error: Nix is not installed; this command needs the optional Nix extra "
                    "(see https://nixos.org/download/)", 1)
    return nix


def lock_flake(nix: str) -> None:
    # Write or refresh flake.lock before evaluating: when an evaluation creates the lock itself, Nix sees the
    # source tree change mid-evaluation and aborts.
    subprocess.run([nix, "flake", "lock", str(ROOT), "--quiet"], check=True)


def source_overrides() -> list[str]:
    """--override-input for repos with no remote (their base clone) and for CC_FEATURE's worktrees: worktrees.py
    source_overrides is the one place that decides them, for every Nix call."""
    feature = os.environ.get("CC_FEATURE")
    output = subprocess.run([sys.executable, str(SCRIPTS / "worktrees.py"), str(ROOT), "nix-args",
                             *([feature] if feature else [])],
                            stdout=subprocess.PIPE, text=True, encoding="utf-8", check=True).stdout
    return [line for line in output.splitlines() if line]


def nix(*arguments: str) -> int:
    binary = require_nix()
    lock_flake(binary)
    return subprocess.run([binary, *arguments]).returncode


def target(arguments: list[str], command: str) -> tuple[str, list[str]]:
    if not arguments or not arguments[0]:
        raise Usage(f"usage: ./cc {command} <name>", 1)
    return arguments[0], arguments[1:]


def git_bash(git: str | None) -> bool:
    """Claude Code runs the rule hooks in Git Bash on Windows (plans/evidence/native-core/s2-hooks.md)."""
    candidates = [os.environ.get("CLAUDE_CODE_GIT_BASH_PATH", ""),
                  str(Path(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe"))]
    if git:  # <Git>\cmd\git.exe or <Git>\mingw64\bin\git.exe
        candidates += [str(parent / "bin" / "bash.exe") for parent in Path(git).parents]
    return any(candidate and Path(candidate).is_file() for candidate in candidates)


def uv_binary() -> str | None:
    """The uv that runs this CLI: uv run names itself in $UV; else the pinned copy the shim downloads."""
    if os.environ.get("UV") and Path(os.environ["UV"]).is_file():
        return os.environ["UV"]
    pin = (ROOT / ".uv-pin").read_text(encoding="utf-8").split()
    version = pin[pin.index("version") + 1] if "version" in pin else ""
    local = ROOT / ".cc-local" / "tools" / "uv" / version / ("uv.exe" if os.name == "nt" else "uv")
    return str(local) if version and local.is_file() else shutil.which("uv")


def wsl_on_windows_drive(root: Path = ROOT) -> bool:
    """A CC on a Windows drive (/mnt/c) seen from WSL: every file access crosses 9P, and a hook takes 13-24 s."""
    return sys.platform == "linux" and "microsoft" in platform.release().lower() and root.parts[1:2] == ("mnt",)


def doctor(arguments: list[str]) -> int:
    # Read-only, and never locks the flake. Why: docs/kit/decisions/0030-disk-management.md
    if arguments[:1] == ["--disk"]:
        return script("disk.py")
    sys.stdout.reconfigure(line_buffering=True)  # keep this order with the output of the scripts called below
    uv = uv_binary()
    print(subprocess.run([uv, "--version"], capture_output=True, text=True).stdout.strip() if uv
          else "warning: uv is not found; run the CC through ./cc or cc.cmd, which install the pinned uv")
    print(f"python {platform.python_version()} ({sys.executable})")
    if wsl_on_windows_drive():
        print("warning: this CC is on a Windows drive and runs in WSL; a rule hook takes longer than its 15 s timeout "
              "here, so Claude Code blocks every tool call. Use cc.cmd on Windows, or clone the CC inside WSL (~/...).")
    git = shutil.which("git")
    print(subprocess.run([git, "--version"], capture_output=True, text=True).stdout.strip() if git
          else "error: git is missing; the CC needs it")
    if os.name == "nt" and not git_bash(git):
        print("error: Git for Windows (Git Bash) is missing; the rule hooks need it")
    found = shutil.which("nix")
    print(subprocess.run([found, "--version"], capture_output=True, text=True).stdout.strip() if found
          else "note: Nix is absent; the optional Nix extra (build, run, relock, gc, Nix gates) is unavailable")
    if docker := shutil.which("docker") or shutil.which("podman"):
        print(subprocess.run([docker, "--version"], capture_output=True, text=True).stdout.strip())
    else:
        print("note: Docker/Podman is absent; container E2E apps will be unavailable")
    gh = shutil.which("gh")
    if gh and subprocess.run([gh, "auth", "status"], capture_output=True).returncode == 0:
        print("gh: logged in")
    else:
        print("note: gh is missing or not logged in; ./cc prs will be unavailable")
    script("council/providers.py", "status", root=False)
    script("repositories.py", "strays")
    return 0


def check(arguments: list[str]) -> int:
    # The native check groups, then nix flake check as an optional extra that gets the arguments. Why: plan native-core
    import checks
    import verify

    started = verify.tree_snapshot([ROOT])
    status = checks.main(arguments, ROOT)
    # A pass stamps the CC's tree, which the push hook looks for, only if no file changed during the run.
    # Why: docs/kit/decisions/0028-verify-before-push.md
    if status == 0:
        tree = verify.tree_snapshot([ROOT])[ROOT]
        if tree is None or tree != started[ROOT]:
            print("warning: no check stamp: files changed while check ran (or the tree cannot be read); run it again",
                  file=sys.stderr)
        else:
            script("verified.py", "record", str(ROOT), "check")
    # Notes whose evidence changed after verified_at: a warning, never a failure.
    # Why: docs/kit/decisions/0023-stale-evidence-warning.md
    subprocess.run([sys.executable, str(SCRIPTS / "validate-knowledge.py"), "--stale", str(ROOT)])
    return status


def show(arguments: list[str]) -> int:
    import checks

    print("Check groups (./cc check):")
    for group in checks.registry(ROOT):
        print(f"  {group.name}: {', '.join(Path(step[0]).name for step in group.steps)}")
    if shutil.which("nix") is None:
        print("note: Nix is absent; flake packages, apps and Nix gates belong to the optional Nix extra")
        return 0 if not arguments else nix("flake", "show")  # flake arguments need Nix: fail clearly
    print("\nFlake outputs (Nix extra):", flush=True)
    return nix("flake", "show", *source_overrides(), str(ROOT), *arguments)


def bootstrap(arguments: list[str]) -> int:
    command, rest = (arguments[0], arguments[1:]) if arguments else ("help", [])
    if command == "install":
        return script("materialize-template.py", rest[0] if rest else ".", root=False)
    if command == "validate":
        return script("validate-control-center.py")
    raise Usage("usage: ./cc bootstrap <install [target]|validate>")


def feature(arguments: list[str]) -> int:
    if not arguments or not arguments[0]:
        raise Usage("usage: ./cc feature <name> <show|check|build|run|verify> [arguments]", 1)
    if len(arguments) == 1:
        raise Usage("error: feature requires a command")
    os.environ["CC_FEATURE"] = arguments[0]
    return main(arguments[1:])


def main(argv: list[str]) -> int:
    command, arguments = (argv[0], argv[1:]) if argv else ("help", [])
    try:
        if command in FORWARD:
            return script(FORWARD[command], *arguments)
        if command == "doctor":
            return doctor(arguments)
        if command == "backup":
            return script("backup_where.py" if arguments[:1] in (["where"], ["restore"]) else "backup.py", *arguments)
        if command == "relock":
            require_nix()
            return script("relock.py")
        if command == "gc":
            require_nix()
            # Never touches .cc-local. Why: docs/kit/decisions/0030-disk-management.md
            return script("store_gc.py", *arguments)
        if command == "show":
            return show(arguments)
        if command == "check":
            return check(arguments)
        if command == "build":
            name, rest = target(arguments, "build")
            return nix("build", *source_overrides(), f"{ROOT}#{name}", *rest)
        if command == "run":
            name, rest = target(arguments, "run")
            return nix("run", *source_overrides(), f"{ROOT}#{name}", "--", *rest)
        if command == "verify":
            if found := shutil.which("nix"):
                lock_flake(found)
            return script("verify.py", *arguments)
        if command == "validate":
            return script("validate-knowledge.py")
        if command == "agents":
            return script("agent-configs.py", arguments[0] if arguments else "check")
        if command == "bootstrap":
            return bootstrap(arguments)
        if command == "hook":
            # Through the guard, which still lets scripts/ be read and repaired when the rules cannot load.
            return script("hook_guard.py", *arguments, root=False)
        if command == "open":
            return script("worktrees.py", "open", *arguments)
        if command == "eval":
            require_nix()
            return script("eval/run.py", *arguments)
        if command == "settings":
            return script("settings/app.py", *arguments)
        if command == "feature":
            return feature(arguments)
        if command in ("help", "-h", "--help"):
            print(USAGE, end="")
            return 0
        print(f"error: unknown command: {command}", file=sys.stderr)
        print(USAGE, end="", file=sys.stderr)
        return 2
    except Usage as error:
        print(error, file=sys.stderr)
        return error.code
    except subprocess.CalledProcessError as error:
        return error.returncode or 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
