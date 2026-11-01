#!/usr/bin/env python3
"""Claude Code hooks that enforce the AGENTS.md hard rules at the moment of a tool call.

Usage: rule_hooks.py <pre-bash|pre-read|post-read|pre-edit|post-edit|stop|session-start>, with the hook's JSON on stdin.
session-start is not a rule: it starts the setup interview in a CC that is not set up (docs/kit/decisions/0018-setup-starts-itself.md).
The gates (./cc check, ./cc verify) stay the source of truth: a hook only catches a break early,
and only in Claude Code. See docs/kit/decisions/0013-rule-hooks.md.
"""

from __future__ import annotations

import importlib.util
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import verified


ROOT = Path(__file__).resolve().parent.parent

AI_NAMES = r"(claude|anthropic|openai|chatgpt|gpt|codex|copilot|cursor|gemini|devin|aider|llm)"
# A trailer or footer starts a line (or a -m argument), so prose that mentions one is not a match.
ATTRIBUTION = re.compile(
    rf"""(?:^|(?:-m|--message|--body)[=\s]+["']?)[^\w\n]*(?P<line>(?:co-authored-by:[^\n]*\b{AI_NAMES}\b|generated (?:with|by)\b[^\n]{{0,40}}\b{AI_NAMES}\b)[^\n"']*)""",
    re.IGNORECASE | re.MULTILINE,
)
COMMIT_OR_PR = re.compile(r"\bgit\b[^;&|\n]*\bcommit\b|\bgh\s+pr\s+(?:create|edit)\b")
MESSAGE_FILE = re.compile(r"(?:\s-F|--file|--body-file)[=\s]+(['\"]?)([^\s'\"]+)\1")

# Markdown the CC may track outside the knowledge folders. Everything else belongs in a note, unless
# the CC declares more folders in control-center.json "markdownDirs" (for example research/).
MARKDOWN_ROOT_FILES = {"AGENTS.md", "BOOTSTRAP.md", "CLAUDE.md", "GUIDE.md", "README.md"}
MARKDOWN_DIRS = ("docs/", "plans/", ".agents/skills/", ".agents/roles/", ".claude/skills/")

# Build and test tools that must run through Nix (./cc build, check, verify) except for diagnosis.
BUILD_TOOLS = {"cmake", "ctest", "make", "gmake", "ninja", "meson", "msbuild", "pytest"}
BUILD_SUBCOMMANDS = {
    "docker": {"build", "buildx"},
    "podman": {"build"},
    "cargo": {"build", "test"},
    "go": {"build", "test"},
    "dotnet": {"build", "test"},
    "npm": {"test"},
    "pnpm": {"test"},
    "yarn": {"test"},
    "bun": {"test"},
}
WRAPPERS = {"sudo", "time", "env", "exec", "nohup", "command"}
SEGMENT = re.compile(r"&&|\|\||[;|\n]|\$\(|`")


def ai_attribution(text: str) -> str | None:
    """The first line that credits an AI as author or generator, if any."""
    match = ATTRIBUTION.search(text)
    return match.group("line").strip() if match else None


def extra_markdown_dirs(root: Path) -> tuple[str, ...]:
    """The folders this CC declares in control-center.json "markdownDirs"; none without the file."""
    try:
        declared = json.loads((root / "control-center.json").read_text(encoding="utf-8")).get("markdownDirs", [])
    except (OSError, ValueError):
        return ()
    return tuple(f"{folder.strip('/')}/" for folder in declared if isinstance(folder, str) and folder.strip("/"))


def markdown_allowed(relative: str, extra_dirs: tuple[str, ...] = ()) -> bool:
    relative = relative.replace("\\", "/")
    if relative in MARKDOWN_ROOT_FILES or relative.startswith(MARKDOWN_DIRS + extra_dirs):
        return True
    parts = relative.split("/")
    return len(parts) == 2 and parts[0] == "templates"


def nix_bypass(command: str) -> str | None:
    """The build or test command a shell line runs directly, if any."""
    for segment in SEGMENT.split(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        while words and (words[0] in WRAPPERS or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0])):
            words = words[1:]
        if not words:
            continue
        name = Path(words[0]).name.removesuffix(".exe")
        arguments = words[1:]
        if name in BUILD_TOOLS:
            return name
        if arguments and arguments[0] in BUILD_SUBCOMMANDS.get(name, ()):
            return f"{name} {arguments[0]}"
        if name == "docker" and arguments[:2] == ["compose", "build"]:
            return "docker compose build"
        if re.fullmatch(r"python[0-9.]*", name) and arguments[:2] == ["-m", "pytest"]:
            return "python -m pytest"
    return None


def attributed_commits(repository: Path, base: str) -> list[str]:
    """Short hashes and offending lines of commits in base..HEAD that credit an AI."""
    log = subprocess.run(
        ["git", "-C", str(repository), "log", "--format=%h%x00%B%x1e", f"{base}..HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout
    found = []
    for record in log.split("\x1e"):
        if "\x00" not in record:
            continue
        sha, body = record.strip().split("\x00", 1)
        line = ai_attribution(body)
        if line:
            found.append(f"{sha} ({line})")
    return found


# Irreplaceable data (docs/kit/decisions/0030-disk-management.md): localData entries tagged irreplaceable and their
# mirrors are deleted only by ./cc data remove, which the user runs and confirms in their own terminal. The hook reads
# shell text: a variable from an earlier command, a script file or an alias is not seen (0013 names these limits).
DELETE_VERBS = {"rm", "rmdir", "unlink", "shred", "truncate", "remove-item", "ri", "del", "erase", "rd"}
COPY_VERBS = {"mv", "cp", "move-item", "copy-item", "move", "copy"}
SHELLS = {"bash", "sh", "zsh", "dash"}
INTERPRETERS = {"python", "python3", "node", "perl", "ruby", "pwsh", "powershell", "powershell.exe"}
DESTRUCTIVE_CODE = re.compile(r"rmtree|\bremove\(|unlink|rmdir|\brename\(|\breplace\(|truncate|Remove-Item|"
                              r"[\"']w[b+]?[\"']|write_(?:bytes|text)", re.IGNORECASE)
GROUP_BREAK = {";", "&&", "||", "|", "&", "(", ")"}


def path_key(raw: str) -> str:
    """One spelling for a path seen from WSL or Windows: /mnt/c/A, C:\\A and \\\\wsl.localhost\\<distro>\\home\\u."""
    raw = raw.strip().strip("'\"")
    drive = re.match(r"^/mnt/([A-Za-z])(?:/(.*))?$", raw) or re.match(r"^([A-Za-z]):[\\/]?(.*)$", raw)
    if drive:
        return posixpath.normpath(f"{drive.group(1).lower()}:/{(drive.group(2) or '').replace(chr(92), '/')}").lower()
    share = re.match(r"^\\\\wsl(?:\.localhost|\$)\\[^\\]+\\(.*)$", raw)
    if share:
        raw = "/" + share.group(1).replace("\\", "/")
    return posixpath.normpath(raw)


def joined(cwd: str, raw: str) -> str:
    raw = os.path.expanduser(raw.strip("'\""))
    key = path_key(raw)
    return key if key.startswith("/") or re.match(r"^[a-z]:/", key) else posixpath.normpath(f"{path_key(cwd)}/{raw}")


def inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def protected(root: Path) -> tuple[list[str], dict[str, str], list[str]]:
    """Protected folders and files, the entry each belongs to, and the purge apps of irreplaceable entries. The mirrors
    come from the locations file ./cc backup writes, so they stay protected when the manifest is broken."""
    paths: dict[str, str] = {}
    purges: list[str] = []
    try:
        manifest = json.loads((root / "control-center.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    for entry in manifest.get("localData") or [] if isinstance(manifest, dict) else []:
        if isinstance(entry, dict) and "irreplaceable" in (entry.get("tags") or []) and isinstance(entry.get("path"), str):
            paths[joined(str(root), entry["path"])] = str(entry.get("name"))
            purges += [app for app in entry.get("purge") or [] if isinstance(app, str)]
    try:
        known = json.loads((root / ".cc-local" / "hooks" / "backup-locations.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    for name, mirrors in (known.get("mirrors") or {}).items() if isinstance(known, dict) else []:
        for mirror in mirrors if isinstance(mirrors, list) else []:
            paths[path_key(mirror)] = name
            paths[path_key(f"{mirror}.json")] = name
    return sorted(paths), paths, purges


def protected_hit(path: str, folders: list[str]) -> str | None:
    """The protected path that path is, is inside, or is a folder above."""
    return next((folder for folder in folders if inside(path, folder) or inside(folder, path)), None)


def destroys_data(command: str, cwd: str, root: Path, folders: list[str], purges: list[str]) -> str | None:
    """The protected path a shell line would delete, move away or overwrite, or the reason it is refused."""
    if re.search(r"(?:^|[\s/'\"])cc\s+data\s+remove\b", command):
        return "./cc data remove"
    for app in purges:
        if re.search(rf"(?:^|[\s/'\"])cc\s+run\s+{re.escape(app)}(?:\s|$|['\";&|])", command):
            return f"./cc run {app}"
    try:
        lexer = shlex.shlex(command.replace("\\", "\\\\"), posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        words = command.split()
    variables: dict[str, str] = {}
    groups: list[list[str]] = [[]]
    for word in words:
        if word in GROUP_BREAK:
            groups.append([])
        else:
            groups[-1].append(word)
    for group in groups:
        while group and group[0] in ("export", "set"):
            group = group[1:]
        while group and re.match(r"^[A-Za-z_]\w*=", group[0]):
            name, value = group[0].split("=", 1)
            variables[name] = value
            group = group[1:]
        group = [re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda m: variables.get(m.group(1), os.environ.get(m.group(1), m.group(0))),
                        word) for word in group]
        if not group:
            continue
        verb = posixpath.basename(group[0].replace("\\", "/")).lower()
        if verb in ("cmd", "cmd.exe") and len(group) > 2 and group[1].lower() == "/c":
            group, verb = group[2:], group[2].lower()
        if verb == "cd" and len(group) > 1:
            cwd = joined(cwd, group[1])
            continue
        operands = [word for word in group[1:] if not word.startswith("-") and not re.fullmatch(r"/[A-Za-z]", word)]
        redirects = [group[index + 1] for index, word in enumerate(group[:-1]) if word in (">", ">>")]
        operands = [word for word in operands if word not in (">", ">>", "<") and word not in redirects]
        for target in redirects:
            hit = protected_hit(joined(cwd, target), folders)
            if hit and inside(joined(cwd, target), hit):
                return hit
        if verb in DELETE_VERBS:
            hit = next((found for word in operands if (found := protected_hit(joined(cwd, word), folders))), None)
            if hit:
                return hit
            if any("*" in word or word == "." for word in operands) and protected_hit(cwd, folders):
                return protected_hit(cwd, folders)
        if verb in COPY_VERBS and len(operands) >= 2:
            sources, target = operands[:-1], joined(cwd, operands[-1])
            if verb in ("mv", "move-item", "move"):
                hit = next((found for word in sources if (found := protected_hit(joined(cwd, word), folders))), None)
                if hit:
                    return hit
            for source in sources:
                landing = posixpath.join(target, posixpath.basename(source.rstrip("/"))) \
                    if operands[-1].endswith("/") or Path(target).is_dir() else target
                hit = protected_hit(landing, folders)
                if hit and (not inside(landing, hit) or Path(landing).exists()):
                    return hit
        if verb == "find" and ("-delete" in group or any(group[index + 1].lower() in DELETE_VERBS
                                                           for index, word in enumerate(group[:-1])
                                                           if word in ("-exec", "-execdir"))):
            starts = [word for word in group[1:] if not word.startswith(("-", "(", "!"))][:1] or ["."]
            hit = protected_hit(joined(cwd, starts[0]), folders)
            if hit:
                return hit
        if verb == "rsync" and any(word.startswith("--delete") for word in group) and operands:
            hit = protected_hit(joined(cwd, operands[-1]), folders)
            if hit:
                return hit
        if verb == "git" and "clean" in group and any(re.fullmatch(r"-[A-Za-z]*[xX][A-Za-z]*", word) for word in group):
            here = joined(cwd, ".")
            nested = (joined(str(root), "worktrees"), joined(str(root), "repos"))
            if inside(here, path_key(str(root))) and not any(inside(here, folder) for folder in nested):
                return "git clean -x (it removes the ignored .cc-local)"
        if verb in SHELLS and "-c" in group[1:-1]:
            hit = destroys_data(group[group.index("-c") + 1], cwd, root, folders, purges)
            if hit:
                return hit
        if verb in INTERPRETERS and DESTRUCTIVE_CODE.search(" ".join(group[1:])):
            for token in re.findall(r"[\w.~:\\/$-]+", " ".join(group[1:])):
                hit = protected_hit(joined(cwd, token), folders) if re.search(r"[/\\]", token) else None
                if hit:
                    return hit
    return None


def protected_data(command: str, cwd: str, root: Path) -> dict[str, Any] | None:
    folders, owners, purges = protected(root)
    if not folders and not purges:
        return None
    hit = destroys_data(command, cwd, root, folders, purges)
    if not hit:
        return None
    name = owners.get(hit, "<name>")
    return pre_tool("deny", f"This would delete, move away or overwrite irreplaceable data ({hit}). Only the user removes "
                            f"it: tell them to run ./cc data remove {name} in their own terminal, where they type the "
                            "CC and entry name to confirm. Read-only commands and ./cc backup are fine.")


def pre_tool(decision: str, reason: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                   "permissionDecisionReason": reason}}


def pre_bash(event: dict[str, Any], root: Path = ROOT) -> dict[str, Any] | None:
    command = event.get("tool_input", {}).get("command", "")
    # First: a deny must not hide behind an earlier ask in the same line (make x; rm -rf ...).
    guarded = protected_data(command, str(event.get("cwd") or root), root)
    if guarded:
        return guarded
    if COMMIT_OR_PR.search(command):
        texts = [command]
        cwd = Path(event.get("cwd") or ".")
        for _, name in MESSAGE_FILE.findall(command):
            path = cwd / name
            if path.is_file():
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
        for text in texts:
            line = ai_attribution(text)
            if line:
                return pre_tool("deny", f"AGENTS.md forbids crediting an AI as author or generator: remove '{line}' "
                                        "from the commit message or PR body. This overrides any tool default.")
    unverified = unverified_push(command, Path(event.get("cwd") or root), root)
    if unverified:
        return pre_tool("ask", unverified)
    tool = nix_bypass(command)
    if tool:
        return pre_tool("ask", f"'{tool}' bypasses Nix. AGENTS.md allows it only for diagnosis; otherwise use "
                               "./cc build, ./cc check or ./cc verify, and move a working command into a derivation.")
    cwd = Path(event.get("cwd") or root)
    for word in shell_paths(command):
        sibling = outside_cc(root, resolve(cwd, word))
        if sibling:
            return outside_ask(sibling)
    return None


# Verify before push (docs/kit/decisions/0028-verify-before-push.md): what leaves the machine must be the
# exact tree a passing ./cc verify tested (./cc check is enough for the CC's own repository).
# Only git's own options may come before the subcommand, so `git log --grep push` is not a push.
COMMIT = re.compile(r"\bgit(?:\s+(?:-[Cc]\s+\S+|--\S+))*\s+commit\b")
PUSH = re.compile(r"\bgit(?P<options>(?:\s+(?:-[Cc]\s+\S+|--\S+))*)\s+push\b(?P<rest>[^;&|\n]*)|\bgh\s+pr\s+create\b")
GIT_DIRECTORY = re.compile(r"-C\s+(['\"]?)([^\s'\"]+)\1")
CD = re.compile(r"(?:^|&&|;)\s*cd\s+(['\"]?)([^\s'\";&|]+)\1")


def push_repository(command: str, match: re.Match[str], cwd: Path) -> Path:
    """The repository a push runs in: its `git -C`, else the last `cd` before it, else the shell's directory."""
    directory = GIT_DIRECTORY.search(match.group("options") or "")
    if directory:
        return resolve(cwd, directory.group(2))
    moves = CD.findall(command[:match.start()])
    return resolve(cwd, moves[-1][1]) if moves else cwd


def unverified_push(command: str, cwd: Path, root: Path = ROOT) -> str | None:
    match = PUSH.search(command)
    if not match or "--delete" in (match.group("rest") or ""):
        return None
    if COMMIT.search(command[:match.start()]):
        # The hook runs before the command, so it would judge the commit before this one (it let one through, 2026-10-28).
        return ("This command commits and then pushes, so the hook cannot see the commit it would push. Commit first, "
                "then push in a separate command, so the pushed tree can be checked against the passing runs.")
    repository = push_repository(command, match, cwd)
    try:
        top = Path(verified.git(repository, "rev-parse", "--show-toplevel")).resolve()
        head = verified.git(repository, "rev-parse", "--short", "HEAD")
        tree = verified.commit_tree(repository)
    except (OSError, subprocess.CalledProcessError):
        return None  # not a Git repository here: nothing to compare
    cc = root.resolve()
    if top != cc and cc not in top.parents:
        return None  # another project; the isolation hook covers it
    needed = ("check", "verify") if top == cc else ("verify",)
    kinds = (verified.lookup(root, tree) or {}).get("kinds", [])
    if any(kind in kinds for kind in needed):
        return None
    run = "./cc check or ./cc verify" if top == cc else "./cc feature <feature> verify"
    return (f"No passing {run} is recorded for the tree of HEAD ({head}) in {top}. AGENTS.md 'Done means verified': "
            f"run it on this exact tree before pushing, and run the changed thing itself if no gate does. If it cannot "
            f"run here, tell the user what was and was not verified and let them decide.")


# Isolation between projects (docs/kit/decisions/0016-per-cc-repos-and-worktrees.md): a CC keeps its repos and
# worktrees inside its own folder, so anything in the folder around it belongs to another project.
PATH_WORD = re.compile(r"^(?:\.\.(?:[/\\]|$)|~|/|[A-Za-z]:[/\\])")


def resolve(cwd: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else cwd / path).resolve()


def outside_cc(root: Path, path: Path) -> Path | None:
    """The folder next to this CC that path reaches into (another CC, an RC, a shared folder), or the
    projects folder itself; None inside the CC or anywhere else on the machine."""
    root = root.resolve()
    if path == root or root in path.parents:
        return None
    parent = root.parent
    if path == parent:
        return parent
    return parent / path.relative_to(parent).parts[0] if parent in path.parents else None


def outside_ask(sibling: Path) -> dict[str, Any]:
    return pre_tool("ask", f"This reaches outside the CC into {sibling}. Every project keeps its own repos inside its "
                           "own folder; this CC's repos are ./cc repo list, in repos/. Allow only if the user asked to "
                           "work across projects.")


def shell_paths(command: str) -> list[str]:
    """Words of a shell line that name a path outside the current folder (.., ~, absolute). Operators such as
    ; && | ( ) > split words even without spaces, so `cd <cc>; ...` is the path <cc>, not `<cc>;`."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        words = command.split()
    found = []
    for word in words:
        value = word.split("=", 1)[1] if word.startswith("-") and "=" in word else word
        if PATH_WORD.match(value):
            found.append(value)
    return found


def tool_paths(event: dict[str, Any]) -> list[str]:
    tool_input = event.get("tool_input", {})
    raw = [tool_input.get(key) for key in ("file_path", "notebook_path", "path")]
    pattern = tool_input.get("pattern", "")
    if event.get("tool_name") == "Glob" and PATH_WORD.match(pattern):
        # The fixed part of a glob: everything before the first wildcard.
        raw.append(re.split(r"[*?\[{]", pattern, maxsplit=1)[0] or ".")
    return [value for value in raw if value]


def pre_read(event: dict[str, Any], root: Path = ROOT) -> dict[str, Any] | None:
    cwd = Path(event.get("cwd") or root)
    for raw in tool_paths(event):
        sibling = outside_cc(root, resolve(cwd, raw))
        if sibling:
            return outside_ask(sibling)
    return None


def relative_to_root(root: Path, event: dict[str, Any]) -> Path | None:
    tool_input = event.get("tool_input", {})
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(event.get("cwd") or root) / path
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return None  # outside the CC: a worktree, the scratchpad, memory


def git_ignored(root: Path, relative: Path) -> bool:
    try:
        return subprocess.run(["git", "-C", str(root), "check-ignore", "-q", relative.as_posix()]).returncode == 0
    except OSError:
        return False


def tag_removal(event: dict[str, Any], root: Path) -> bool:
    """An edit of control-center.json that drops an irreplaceable tag or entry."""
    tool_input = event.get("tool_input", {})
    if "content" in tool_input:
        def tagged(manifest: Any) -> set[str]:
            return {entry.get("name") for entry in manifest.get("localData") or []
                    if isinstance(entry, dict) and "irreplaceable" in (entry.get("tags") or [])}
        try:
            current = tagged(json.loads((root / "control-center.json").read_text(encoding="utf-8")))
        except (OSError, ValueError, AttributeError):
            return False  # nothing readable is tagged now
        try:
            return bool(current - tagged(json.loads(tool_input["content"])))
        except (ValueError, AttributeError):
            return bool(current)  # a broken manifest would drop every tag
    edits = tool_input.get("edits") or [tool_input]
    return any(str(change.get("old_string", "")).count("irreplaceable") > str(change.get("new_string", "")).count("irreplaceable")
               for change in edits if isinstance(change, dict))


def pre_edit(event: dict[str, Any], root: Path = ROOT) -> dict[str, Any] | None:
    raw = event.get("tool_input", {}).get("file_path") or event.get("tool_input", {}).get("notebook_path")
    if raw:
        folders, owners, _ = protected(root)
        hit = protected_hit(joined(str(event.get("cwd") or root), raw), folders)
        target = joined(str(event.get("cwd") or root), raw)
        if hit and inside(target, hit):
            return pre_tool("deny", f"{raw} is irreplaceable data or its mirror; agents never write there. Only the user "
                                    f"removes or replaces it (./cc data remove {owners.get(hit, '<name>')}).")
        if target == joined(str(root), "control-center.json") and tag_removal(event, root):
            return pre_tool("ask", "This edit removes an irreplaceable tag or entry from control-center.json, which ends "
                                   "its mirror and its delete protection. Allow only if the user asked for exactly that.")
    outside = pre_read(event, root)
    if outside:
        return outside
    relative = relative_to_root(root, event)
    if relative is None:
        return None
    if relative.parts[:2] == (".claude", "skills"):
        source = Path(".agents", *relative.parts[1:]).as_posix()
        return pre_tool("deny", f".claude/skills is a generated copy: edit {source} instead, then run ./cc agents sync.")
    if relative.parts[:1] == ("repos",):
        if relative.parts[1:2] == ("reference",):
            return pre_tool("deny", f"{relative.as_posix()} is in a reference repo: read-only evidence for this CC, never "
                                    "changed here. Make the change in a project repo's feature worktree instead.")
        return pre_tool("deny", f"{relative.as_posix()} is in a base clone, which is never edited. Change code in a feature "
                                "worktree: ./cc worktree create <feature> <repo>, then edit worktrees/<feature>/<repo>_wt.")
    # The kit's knowledge (docs/kit/decisions/0017-kit-and-project-layers.md): a CC carries an identical copy that a kit
    # update replaces. Only a CC has control-center.json; the template repository owns the kit and edits it freely.
    if relative.parts[:2] == ("docs", "kit") and (root / "control-center.json").is_file():
        return pre_tool("ask", f"{relative.as_posix()} belongs to the kit and is replaced on a kit update, so an edit here is "
                               "lost. To change kit behaviour for this CC, record its own decision in docs/decisions/ that "
                               "names the kit decision it overrides.")
    creating = event.get("tool_name") == "Write" and not (root / relative).exists()
    if creating and relative.suffix.lower() == ".md" and not markdown_allowed(relative.as_posix(), extra_markdown_dirs(root)) \
            and not git_ignored(root, relative):
        return pre_tool("deny", f"{relative.as_posix()}: Markdown outside docs/, plans/, the skills and the "
                                "control-center.json markdownDirs fails ./cc check. Store one durable fact as a note under docs/ (templates/note.md) or a plan via ./cc plan.")
    return None


def load_script(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reference_repos(root: Path, event: dict[str, Any]) -> list[str]:
    """Ids of the reference repos (repos/reference/<id>) that a read, search or listing touched."""
    cwd = Path(event.get("cwd") or root)
    base = (root / "repos" / "reference").resolve()
    found = []
    for raw in tool_paths(event) or ["."]:
        path = resolve(cwd, raw)
        if base in path.parents:
            found.append(path.relative_to(base).parts[0])
    return found


def post_read(event: dict[str, Any], root: Path = ROOT) -> dict[str, Any] | None:
    """After the first read of a reference repo in a session, remind the agent what it is: guidance, not truth."""
    repos = reference_repos(root, event)
    if not repos:
        return None
    seen_path = root / ".cc-local" / "hooks" / f"references-{re.sub(r'[^A-Za-z0-9_-]', '_', str(event.get('session_id', 'none')))}.json"
    try:
        seen = set(json.loads(seen_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        seen = set()
    new = sorted(set(repos) - seen)
    if not new:
        return None
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(sorted(seen | set(new))), encoding="utf-8")
    names = ", ".join(new)
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": (
        f"{names} {'is a reference repo' if len(new) == 1 else 'are reference repos'} (repos/reference/): read-only guidance, "
        "such as a proof of concept or an older design, not the source of truth. Before relying on what it says, check it "
        "against the project repos (repos/project/, ./cc repo list) and the CC's notes; when they disagree, the project "
        "repos win. Cite it as reference, and never change it.")}}


def post_edit(event: dict[str, Any], root: Path = ROOT) -> dict[str, Any] | None:
    relative = relative_to_root(root, event)
    if relative is None or relative.parts[:1] != ("docs",) or relative.suffix != ".md":
        return None
    errors = [error for error in load_script("validate-knowledge").validate(root) if error.startswith(f"{relative}:")]
    if not errors:
        return None
    return {"decision": "block", "reason": "This note breaks the knowledge rules (./cc validate will fail):\n"
                                          + "\n".join(f"- {error}" for error in errors)}


def stop(event: dict[str, Any], root: Path = ROOT) -> dict[str, Any] | None:
    if event.get("stop_hook_active"):
        return None  # report once; never keep the agent in a loop
    problems = load_script("agent-configs").drift(root)
    if not problems:
        return None
    return {"decision": "block", "reason": "Agent configs drifted from .agents/skills; run ./cc agents sync:\n"
                                          + "\n".join(f"- {problem}" for problem in problems[:10])}


SETUP_CONTEXT = {
    "discovery": "This Control Center is not set up yet. Unless the user asks for something else, open with the setup "
                 "interview: load the grill-cc-bootstrap skill (mode: new, already materialized by cc-kit) and start "
                 "grilling. The user may know nothing about how a CC works; explain each step in plain words.",
    "adapting": "This Control Center's setup is not finished. Unless the user asks for something else, offer to continue "
                "it with the grill-cc-bootstrap skill: read control-center.json and ./cc repo list to see what is left.",
}


def session_start(event: dict[str, Any], root: Path = ROOT) -> dict[str, Any] | None:
    """Make the agent start (or resume) the setup of a CC that is not ready, so nobody needs a magic first prompt."""
    try:
        status = json.loads((root / "control-center.json").read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError):
        return None  # the template itself, or a broken file that ./cc check reports
    if status not in SETUP_CONTEXT:
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": SETUP_CONTEXT[status]}}


HANDLERS = {"pre-bash": pre_bash, "pre-read": pre_read, "post-read": post_read, "pre-edit": pre_edit, "post-edit": post_edit,
            "stop": stop, "session-start": session_start}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in HANDLERS:
        print(f"usage: rule_hooks.py <{'|'.join(HANDLERS)}>", file=sys.stderr)
        return 1  # exit 2 would block the tool call
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
        result = HANDLERS[sys.argv[1]](event)
    except Exception as error:  # a broken hook must warn, never block the session
        print(f"rule hook {sys.argv[1]} failed, rule NOT enforced: {error}", file=sys.stderr)
        return 1
    if result:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
