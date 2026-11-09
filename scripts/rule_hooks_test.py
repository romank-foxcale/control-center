#!/usr/bin/env python3
"""Test the rule hooks: each hard rule has a case it must stop and a look-alike it must let through."""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import hostos
import rule_hooks
import verified
from rule_hooks import ai_attribution, markdown_allowed, nix_bypass, post_edit, pre_bash, pre_edit, stop

SCRIPT = Path(__file__).resolve().parent / "rule_hooks.py"


def decision(result: dict | None) -> str | None:
    return result["hookSpecificOutput"]["permissionDecision"] if result else None


def bash(command: str, cwd: Path | None = None) -> str | None:
    return decision(pre_bash({"tool_input": {"command": command}, "cwd": str(cwd or ".")}))


def check_attribution(base: Path) -> None:
    trailer = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
    assert ai_attribution(f"Fix parser\n\n{trailer}\n") == trailer
    assert ai_attribution("Fix parser\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)") == \
        "Generated with [Claude Code](https://claude.com/claude-code)"
    assert ai_attribution(f'git commit -m "Fix" -m "{trailer}"') == trailer, "the trailer alone, without -m"
    assert ai_attribution("Fix parser\n\nCo-authored-by: Jane Doe <jane@example.com>") is None, "human co-authors are fine"
    assert ai_attribution("Block 'Generated with Claude' footers in PR bodies") is None, "prose about the rule is fine"

    assert bash(f'git commit -m "$(cat <<\'EOF\'\nFix parser\n\n{trailer}\nEOF\n)"') == "deny", "heredoc message"
    assert bash(f'git -C ../worktrees/f/api_wt commit -m "Fix" -m "{trailer}"') == "deny", "second -m argument"
    assert bash('gh pr create --title T --body "Summary\n\n🤖 Generated with Claude Code"') == "deny", "PR body"
    (base / "msg.txt").write_text(f"Fix parser\n\n{trailer}\n", encoding="utf-8")
    assert bash("git commit -F msg.txt", base) == "deny", "message file"
    assert bash('git commit -m "Fix parser"') is None
    assert bash(f"grep -rn '{trailer}' docs") is None, "not a commit"


def check_nix_bypass() -> None:
    for command, tool in (("cmake --build build", "cmake"), ("cd api && make test", "make"),
                          ("CC=clang ninja -C out", "ninja"), ("docker build -t x .", "docker build"),
                          ("docker compose build api", "docker compose build"), ("python3 -m pytest tests", "python -m pytest"),
                          ("sudo /usr/bin/cmake ..", "cmake"), ("ls | pytest -q", "pytest"), ("npm test", "npm test")):
        assert nix_bypass(command) == tool, (command, nix_bypass(command))
        assert bash(command) == "ask", command
    for command in ("./cc build api", "nix build .#api", "cmake-format CMakeLists.txt", "git log -- Makefile",
                    "docker run --rm x", "npm install", "cat make.log", "echo 'cmake is banned'"):
        assert nix_bypass(command) is None, (command, nix_bypass(command))


def check_markdown_allowlist() -> None:
    for path in ("README.md", "docs/hacks/x.md", "plans/active/p.md", ".agents/skills/s/SKILL.md", "templates/note.md"):
        assert markdown_allowed(path), path
    for path in ("NOTES.md", "scripts/README.md", "templates/sub/x.md", "docs.md", "nix/projects/api.md"):
        assert not markdown_allowed(path), path
    assert markdown_allowed("research/plan/01.md", ("research/",))
    assert not markdown_allowed("research-old/01.md", ("research/",)), "a declared folder is a folder, not a prefix"


def edit(root: Path, path: str, tool: str = "Write") -> dict:
    return {"tool_name": tool, "tool_input": {"file_path": str(root / path)}, "cwd": str(root)}


def check_edits(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("/.notes/\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "OLD.md").write_text("tracked before the rule\n")

    result = pre_edit(edit(root, ".claude/skills/tdd/SKILL.md", "Edit"), root)
    assert decision(result) == "deny" and ".agents/skills/tdd/SKILL.md" in result["hookSpecificOutput"]["permissionDecisionReason"]
    assert decision(pre_edit(edit(root, "NOTES.md"), root)) == "deny", "a new stray Markdown file"
    assert pre_edit(edit(root, "docs/hacks/x.md"), root) is None
    assert pre_edit(edit(root, ".notes/scratch.md"), root) is None, "ignored local notes are not tracked"
    assert pre_edit(edit(root, "scripts/OLD.md"), root) is None, "an existing file is the gate's business"
    assert pre_edit(edit(root, "scripts/x.py"), root) is None
    assert decision(pre_edit(edit(root, "research/a.md"), root)) == "deny", "undeclared folder"
    (root / "control-center.json").write_text(json.dumps({"markdownDirs": ["research"]}))
    assert pre_edit(edit(root, "research/a.md"), root) is None, "a folder the CC declares in markdownDirs"
    assert decision(pre_edit(edit(root, "NOTES.md"), root)) == "deny", "declaring one folder allows only that folder"
    (root / "control-center.json").write_text("not json")
    assert decision(pre_edit(edit(root, "research/a.md"), root)) == "deny", "a broken manifest declares nothing"
    assert pre_edit({"tool_name": "Write", "tool_input": {"file_path": str(root.parent.parent / "elsewhere.md")}}, root) is None, \
        "outside the CC: worktrees, scratchpad, memory"

    # The files every hook runs through: a broken one blocks every call and only the user can repair it.
    for name in ("pyproject.toml", "uv.lock", ".uv-pin", "cc", "cc.cmd", "cc.ps1", "scripts/hook_guard.py"):
        for tool in ("Edit", "Write"):
            asked = pre_edit(edit(root, name, tool), root)
            assert decision(asked) == "ask" and "only the user can repair" in \
                asked["hookSpecificOutput"]["permissionDecisionReason"], (name, tool, asked)
    relative = {"tool_name": "Edit", "tool_input": {"file_path": "../cc.cmd"}, "cwd": str(root / "scripts")}
    assert decision(pre_edit(relative, root)) == "ask", "a relative spelling is the same file"
    if os.path.normcase("A") == "a":
        assert decision(pre_edit(edit(root, "PyProject.TOML", "Edit"), root)) == "ask", "letter case on Windows"
    for name in ("scripts/hook_guard_test.py", "scripts/cc", "docs/uv.lock", "cc.py", "scripts/rule_hooks.py"):
        assert decision(pre_edit(edit(root, name, "Edit"), root)) != "ask", name


def check_notes_and_drift() -> None:
    """Run against a copy of this CC so the real validator and drift check are exercised."""
    source = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "cc"
        files = subprocess.run(["git", "-C", str(source), "ls-files", "--cached", "--others", "--exclude-standard"],
                               capture_output=True, text=True).stdout.splitlines()
        if not files:  # the Nix check copy has no .git
            files = [str(path.relative_to(source)) for path in source.rglob("*") if path.is_file()]
        for name in files:
            target = root / name
            if (source / name).is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((source / name).read_bytes())

        assert post_edit(edit(root, "docs/index.md", "Edit"), root) is None, "a valid note passes"
        bad = root / "docs" / "hacks" / "bad.md"
        bad.write_text("# No frontmatter\n", encoding="utf-8")
        result = post_edit(edit(root, "docs/hacks/bad.md"), root)
        assert result and "missing YAML frontmatter" in result["reason"], result
        assert post_edit(edit(root, "plans/active/p.md"), root) is None, "plans are not notes"

        assert stop({}, root) is None, "no drift, no message"
        (root / ".agents" / "skills" / "tdd" / "SKILL.md").write_text("changed\n", encoding="utf-8")
        result = stop({}, root)
        assert result and result["decision"] == "block" and "tdd" in result["reason"], result
        assert stop({"stop_hook_active": True}, root) is None, "never loop the agent"


def check_isolation(base: Path) -> None:
    """Reaching into another project next to the CC asks; the CC's own repos and the rest of the machine do not."""
    projects = base / "Projects"
    root = projects / "alpha_CC"
    (root / "repos" / "shop").mkdir(parents=True)
    (projects / "beta_CC").mkdir()
    reading = lambda tool, cwd=root, **tool_input: decision(rule_hooks.pre_read(
        {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd)}, root))
    assert reading("Read", file_path=str(projects / "beta_CC" / "AGENTS.md")) == "ask", "another CC, absolute"
    assert reading("Read", file_path="../beta_CC/AGENTS.md") == "ask", "another CC, relative"
    assert reading("Grep", pattern="def main", path="../repos") == "ask", "the old shared folder"
    assert reading("Glob", pattern="../*_CC/**/*.json") == "ask", "listing the projects folder"
    assert reading("Read", file_path=str(projects)) == "ask", "the projects folder itself"
    assert reading("Read", file_path="repos/shop/README.md") is None, "this CC's own clone"
    assert reading("Read", file_path="../../alpha_CC/docs/index.md", cwd=root / "repos" / "shop") is None, "back into the CC"
    assert reading("Read", file_path=str(base / "elsewhere" / "notes.md")) is None, "outside Projects: scratchpad, memory"
    assert reading("Grep", pattern="../beta_CC") is None, "a Grep pattern is text, not a path"

    write = {"tool_name": "Write", "tool_input": {"file_path": "../beta_CC/docs/x.md"}, "cwd": str(root)}
    assert decision(rule_hooks.pre_edit(write, root)) == "ask", "writing into another CC"

    shell = lambda command: decision(rule_hooks.pre_bash({"tool_input": {"command": command}, "cwd": str(root)}, root))
    for command in ("cat ../beta_CC/AGENTS.md", "ls ..", "grep -rn token ../repos", f"git -C {projects / 'beta_CC'} log",
                    "rg --path=../beta_CC foo"):
        assert shell(command) == "ask", command
    for command in ("git status", "cat repos/shop/README.md", "./cc repo list", "ls /tmp", "cat ~/.bashrc"):
        assert shell(command) is None, command
    # Shell punctuation glued to a path is not part of it: cd into the CC itself, then work there.
    for command in (f"cd {root}; ./cc repo list", f"cd {root}&& ls", f"cd {root}|| exit 1", f"(cd {root})",
                    f"ls {root}>/dev/null"):
        assert shell(command) is None, command
    for command in (f"cd {projects / 'beta_CC'}; ls", f"cat ../beta_CC/AGENTS.md;", "true&&cat ../beta_CC/x"):
        assert shell(command) == "ask", command
    # The hook cannot tell a path argument from text; asking about one echo is the accepted cost.
    assert shell("echo ../beta_CC") == "ask"


def check_reference_repos(base: Path) -> None:
    """Base clones are never edited, and a reference repo reminds the agent once per session what it is."""
    root = base / "ref" / "alpha_CC"
    (root / "repos" / "reference" / "shop-poc" / "src").mkdir(parents=True)
    (root / "repos" / "project" / "shop-backend").mkdir(parents=True)
    edit_at = lambda path: rule_hooks.pre_edit({"tool_name": "Edit", "tool_input": {"file_path": str(root / path)}, "cwd": str(root)}, root)
    denied = edit_at("repos/reference/shop-poc/src/app.cs")
    assert decision(denied) == "deny" and "reference repo" in denied["hookSpecificOutput"]["permissionDecisionReason"]
    denied = edit_at("repos/project/shop-backend/pom.xml")
    assert decision(denied) == "deny" and "feature worktree" in denied["hookSpecificOutput"]["permissionDecisionReason"]
    assert edit_at("worktrees/f/shop-backend_wt/pom.xml") is None, "worktrees are where code changes"

    def read(session: str, **tool_input):
        tool = "Grep" if "pattern" in tool_input else "Read"
        result = rule_hooks.post_read({"tool_name": tool, "tool_input": tool_input, "cwd": str(root), "session_id": session}, root)
        return result["hookSpecificOutput"]["additionalContext"] if result else None

    note = read("s1", file_path="repos/reference/shop-poc/src/app.cs")
    assert note and "shop-poc is a reference repo" in note and "project repos win" in note, note
    assert read("s1", pattern="Order", path="repos/reference/shop-poc") is None, "once per repo per session"
    assert read("s2", file_path=str(root / "repos/reference/shop-poc/src/app.cs")), "a new session is reminded again"
    assert read("s1", file_path="repos/project/shop-backend/pom.xml") is None, "project repos need no reminder"


def check_post_read_is_cheap(base: Path) -> None:
    """post-read runs after every read: with no reference repo in the CC it returns before any path or file work."""
    root = base / "cheap" / "alpha_CC"
    root.mkdir(parents=True)
    event = {"tool_name": "Read", "tool_input": {"file_path": "repos/reference/x/a.py"}, "cwd": str(root), "session_id": "s"}

    def no_work(*arguments: object, **keywords: object) -> None:
        raise AssertionError("post-read resolved a path although no reference repo exists")
    real_resolve = rule_hooks.resolve
    rule_hooks.resolve = no_work  # type: ignore[assignment]
    try:
        assert rule_hooks.post_read(event, root) is None, "no repos/reference folder"
        (root / "repos" / "reference").mkdir(parents=True)
        assert rule_hooks.post_read(event, root) is None, "an empty repos/reference folder"
    finally:
        rule_hooks.resolve = real_resolve  # type: ignore[assignment]
    assert not (root / ".cc-local").exists(), "nothing written"
    (root / "repos" / "reference" / "x").mkdir()
    assert rule_hooks.post_read(event, root), "with a reference repo the reminder still comes"


def check_unusual_paths(base: Path) -> None:
    """UNC shares, another drive letter and non-ASCII names: no gate raises on them, and a protected path spelled that
    way is still refused."""
    root = base / "unusual" / "Проект_CC"
    data = root / ".cc-local" / "data" / "эмбеддинги"
    data.mkdir(parents=True)
    (root / ".cc-local" / "hooks").mkdir()
    (root / "repos" / "reference" / "пример").mkdir(parents=True)
    (root.parent / "другой_CC").mkdir()
    (root / "control-center.json").write_text(json.dumps({"localData": [
        {"name": "emb", "path": ".cc-local/data/эмбеддинги", "tags": ["irreplaceable"]}]}, ensure_ascii=False),
        encoding="utf-8")
    unc, drive = "\\\\cc-test-server\\share\\cc-backups\\emb", "Q:\\cc-backups\\emb"
    (root / ".cc-local/hooks/backup-locations.json").write_text(json.dumps({"mirrors": {"emb": [unc, drive]}}),
                                                                 encoding="utf-8")

    def edit(path: str) -> str | None:
        return decision(rule_hooks.pre_edit({"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": str(root)}, root))

    def read(path: str) -> str | None:
        return decision(rule_hooks.pre_read({"tool_name": "Read", "tool_input": {"file_path": path}, "cwd": str(root)}, root))

    def shell(command: str) -> str | None:
        return decision(rule_hooks.pre_bash({"tool_input": {"command": command}, "cwd": str(root)}, root))

    for path in (f"{unc}\\part.parquet", f"{drive}\\part.parquet", str(data / "часть.parquet")):
        assert edit(path) == "deny", path
    for path in (unc, drive, str(data)):
        assert shell(f"rm -rf '{path}'") == "deny", path
        assert shell(f"Remove-Item -Recurse -Force '{path}'") == "deny", path
    assert edit(str(root / "repos" / "reference" / "пример" / "файл.py")) == "deny", "a reference repo with a non-ASCII name"
    assert read(str(root.parent / "другой_CC" / "заметки.md")) == "ask", "another project with a non-ASCII name"
    for path in ("\\\\cc-test-server\\share\\other\\x.txt", "Q:\\elsewhere\\x.py", str(root / "docs" / "заметка.txt")):
        assert edit(path) is None, path
        assert read(path) is None, path
        assert shell(f"cat '{path}'") is None, path
    assert read("\\\\cc-test-server\\share") is None and shell("ls \\\\cc-test-server\\share") is None


def check_cli() -> None:
    """Hooks run with onFailure block: any exit but 0 or 2 blocks the call. A gate that fails blocks on purpose with
    exit 2 and says why; a failing non-gate hook reports through systemMessage and exits 0, so it never traps the
    session (a blocking Stop hook could loop)."""
    event = json.dumps({"tool_input": {"command": "cmake .."}})
    result = subprocess.run([sys.executable, str(SCRIPT), "pre-bash"], input=event.encode(), capture_output=True)
    assert result.returncode == 0 and json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
    allowed = subprocess.run([sys.executable, str(SCRIPT), "pre-bash"], input=b'{"tool_input": {"command": "ls"}}',
                             capture_output=True)
    assert allowed.returncode == 0 and allowed.stdout == b"", "silence lets the normal permission flow decide"
    broken = subprocess.run([sys.executable, str(SCRIPT), "pre-bash"], input=b"not json", capture_output=True)
    assert broken.returncode == 2 and b"pre-bash failed, so the call is blocked" in broken.stderr, broken.stderr

    # Each handler made to raise, as a bug in it would.
    failing = ("import sys; sys.path.insert(0, sys.argv[1]); import rule_hooks\n"
               "def broken(event):\n    raise RuntimeError('boom')\n"
               "rule_hooks.HANDLERS[sys.argv[2]] = broken\nsys.argv = ['rule_hooks.py', sys.argv[2]]\n"
               "raise SystemExit(rule_hooks.main())")
    for event_name in rule_hooks.HANDLERS:
        ran = subprocess.run([sys.executable, "-c", failing, str(SCRIPT.parent), event_name], input='{"tool_input": {}}',
                             capture_output=True, text=True)
        if event_name in ("pre-bash", "pre-read", "pre-edit"):
            assert ran.returncode == 2 and ran.stdout == "", (event_name, ran)
            assert ran.stderr.strip() == f"rule hook {event_name} failed, so the call is blocked: boom. Fix " \
                                         "scripts/rule_hooks.py or run ./cc doctor.", (event_name, ran.stderr)
        else:
            assert ran.returncode == 0, (event_name, ran)
            assert json.loads(ran.stdout) == {"systemMessage": f"rule hook {event_name} failed: boom"}, (event_name, ran)
    # A gate with no tool call to check (empty stdin, no tool_input object) blocks; it used to allow, since {} has
    # nothing to refuse. The reporting hooks keep reading such an event as before.
    for event_name in ("pre-bash", "pre-read", "pre-edit"):
        for payload in (b"", b"{}", b'{"tool_input": "rm -rf /"}', b'{"tool_input": null}', b"[]"):
            ran = subprocess.run([sys.executable, str(SCRIPT), event_name], input=payload, capture_output=True)
            assert ran.returncode == 0, (event_name, payload, ran)
            output = json.loads(ran.stdout)["hookSpecificOutput"]
            assert output["permissionDecision"] == "deny" and output["permissionDecisionReason"] == \
                "the hook got no tool call to check, so the call is blocked", (event_name, payload, output)
    quiet = subprocess.run([sys.executable, str(SCRIPT), "post-read"], input=b"", capture_output=True)
    assert quiet.returncode == 0 and quiet.stdout == b"", quiet
    for arguments in ([], ["no-such-event"], ["pre-bash", "extra"]):
        usage = subprocess.run([sys.executable, str(SCRIPT), *arguments], input="{}", capture_output=True, text=True)
        assert usage.returncode == 2 and usage.stderr.startswith("usage: rule_hooks.py <pre-bash|"), (arguments, usage)


def short_name(path: Path) -> str | None:
    """The 8.3 spelling Windows gives an existing path (C:\\Users\\KOZYRE~1\\...), or None where there is none."""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    size = windll.kernel32.GetShortPathNameW(str(path), None, 0)
    buffer = ctypes.create_unicode_buffer(size)
    windll.kernel32.GetShortPathNameW(str(path), buffer, size)
    return buffer.value if buffer.value and buffer.value != str(path) else None


def link_folder(link: Path, target: Path) -> None:
    """A symlink to a folder, or a junction where Windows refuses symlinks (no Developer Mode)."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))


def spellings(folder: Path, tail: str, link: Path | None = None) -> list[str]:
    """Every spelling this machine accepts for folder/tail: as given, 8.3 short names, the other slash, the other
    letter case (only where the file system ignores case), Git Bash /c/... (only where it maps to a drive), through a
    link that points at folder, and relative with .. from the folder next to it."""
    plain = str(folder / tail)
    found = [plain, plain.replace("\\", "/")]
    short = short_name(folder)
    if short:
        found += [str(Path(short) / tail), str(Path(short) / tail).upper()]
    swapped = str(folder).swapcase()
    if os.path.exists(swapped) and os.path.samefile(swapped, folder):
        found.append(str(Path(swapped) / tail))
    drive = re.match(r"^([A-Za-z]):[\\/](.*)$", plain)
    if drive:
        bash = f"/{drive.group(1).lower()}/{drive.group(2).replace(chr(92), '/')}"
        if hostos.native_spelling(bash) != bash:
            found.append(bash)
    if link:
        found.append(str(link / tail))
    return list(dict.fromkeys(found))


def check_path_spellings(base: Path) -> None:
    """A protected action is refused with every spelling of its path and in Bash, PowerShell and cmd forms; a valid
    action passes with every spelling. On Windows the fixture folders get 8.3 names and links are junctions."""
    projects = base / "Long Projects Folder"
    root = projects / "alpha_control_CC"
    poc = root / "repos" / "reference" / "shop-poc-reference"
    (poc / "src").mkdir(parents=True)
    (root / "worktrees" / "feature" / "api_wt").mkdir(parents=True)
    (projects / "beta_control_CC" / "docs").mkdir(parents=True)
    emb = root / ".cc-local" / "data" / "embeddings-store"
    emb.mkdir(parents=True)
    (emb / "part.parquet").write_bytes(b"x")
    (root / "control-center.json").write_text(json.dumps({"name": "alpha", "localData": [
        {"name": "emb", "path": ".cc-local/data/embeddings-store", "tags": ["irreplaceable"]}]}), encoding="utf-8")
    links = base / "links"
    links.mkdir()
    link_folder(links / "poc", poc)
    link_folder(links / "emb", emb)
    link_folder(links / "beta", projects / "beta_control_CC")

    def edit(path: str, tool: str = "Write") -> str | None:
        return decision(rule_hooks.pre_edit({"tool_name": tool, "tool_input": {"file_path": path}, "cwd": str(root)}, root))

    def shell(command: str) -> str | None:
        return decision(rule_hooks.pre_bash({"tool_input": {"command": command}, "cwd": str(root)}, root))

    def read(path: str) -> str | None:
        return decision(rule_hooks.pre_read({"tool_name": "Read", "tool_input": {"file_path": path}, "cwd": str(root)}, root))

    for path in spellings(poc, "src/App.cs", links / "poc"):
        assert edit(path) == "deny", f"writing into a reference repo: {path}"
    for path in spellings(emb, "part.parquet", links / "emb"):
        assert edit(path) == "deny", f"writing into irreplaceable data: {path}"
    for path in spellings(emb, "", links / "emb"):
        path = path.rstrip("\\/")
        unquoted = [] if " " in path else [f"rm -rf {path}", f'cmd /c "rmdir /s /q {path}"']
        for command in (*unquoted, f"rm -rf '{path}'", f'Remove-Item -LiteralPath "{path}" -Recurse -Force',
                        f"Remove-Item -Recurse -Force -Path '{path}'", f"& Remove-Item '{path}'",
                        f"cmd.exe /c rd /s /q \"{path}\"", f"powershell -NoProfile -Command \"Remove-Item -Recurse '{path}'\"",
                        f"pwsh -c 'Remove-Item -Recurse \"{path}\"'", f"Set-Content -Path '{path}/part.parquet' -Value x",
                        f"Move-Item '{path}' C:/elsewhere", f"Rename-Item '{path}' old"):
            assert shell(command) == "deny", command
    for path in spellings(projects / "beta_control_CC", "docs/x.md", links / "beta"):
        assert read(path) == "ask", f"another project: {path}"
        assert shell(f"Get-Content -LiteralPath '{path}'") == "ask", path
    for path in spellings(root / "worktrees" / "feature" / "api_wt", "App.cs"):
        assert edit(path) is None, f"a worktree is where code changes: {path}"
        assert read(path) is None, path
    for path in spellings(emb, "part.parquet", links / "emb"):
        assert shell(f"Get-Content -LiteralPath '{path}'") in (None, "ask"), f"reading data is fine: {path}"
        assert shell(f"Copy-Item '{path}' C:/tmp/part.parquet") in (None, "ask"), path


def check_command_paths() -> None:
    """Paths in PowerShell and cmd lines keep their backslashes; Windows tool names match in any case and suffix."""
    for command, tool in ((r"C:\Tools\CMake\bin\CMake.EXE --build build", "cmake"), (r"& 'C:\Program Files\x\ninja.exe'", "ninja"),
                          ("py -m pytest tests", "python -m pytest"), (r"Set-Location C:\x; msbuild app.sln", "msbuild")):
        assert nix_bypass(command) == tool, (command, nix_bypass(command))
    assert rule_hooks.shell_paths(r"git -C C:\Users\me\beta_CC log") == [r"C:\Users\me\beta_CC"]
    assert rule_hooks.shell_paths(r"Get-ChildItem -LiteralPath 'D:\data folder\x'") == [r"D:\data folder\x"]
    assert rule_hooks.shell_paths("cat '/tmp/a b'") == ["/tmp/a b"]


def check_attributed_commits(base: Path) -> None:
    repo = base / "repo"
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "Base\n\nCo-Authored-By: Claude <noreply@anthropic.com>"], check=True)
    start = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "Clean"], check=True)
    assert rule_hooks.attributed_commits(repo, start) == [], "commits before the base are not this branch's"
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "Feature\n\nCo-Authored-By: GPT-5 <x@openai.com>"], check=True)
    found = rule_hooks.attributed_commits(repo, start)
    assert len(found) == 1 and "GPT-5" in found[0], found


def check_verify_before_push(base: Path) -> None:
    """A push asks unless a passing run stamped the exact tree of HEAD; the CC accepts ./cc check, a project needs verify."""
    cc = base / "push" / "alpha_CC"
    worktree = cc / "worktrees" / "f" / "api_wt"
    outside = base / "push" / "beta"

    def commit(repository: Path, name: str, text: str) -> None:
        (repository / name).write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", name], check=True)
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", text],
                       check=True)

    for repository in (cc, worktree, outside):
        repository.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        commit(repository, "a.txt", "one")

    def push(command: str, cwd: Path = cc) -> dict | None:
        return pre_bash({"tool_input": {"command": command}, "cwd": str(cwd)}, cc)

    asked = push("git push origin main")
    assert decision(asked) == "ask" and "./cc check or ./cc verify" in asked["hookSpecificOutput"]["permissionDecisionReason"], asked
    assert decision(push('gh pr create --title T --body B')) == "ask", "opening a PR pushes too"
    verified.record(cc, cc, "check")
    assert push("git push origin main") is None, "./cc check passed on this tree"

    (cc / "a.txt").write_text("two", encoding="utf-8")
    (cc / "untracked.txt").write_text("Nix never sees it", encoding="utf-8")
    verified.record(cc, cc, "check")
    commit(cc, "a.txt", "two")
    assert push("git push") is None, "a run on uncommitted changes covers the commit made from them"
    commit(cc, "a.txt", "three")
    assert decision(push("git push")) == "ask", "an edit after the run is not verified"

    assert decision(push(f"git -C {worktree} push")) == "ask", "git -C names the repository"
    verified.record(cc, worktree, "check")
    asked = push(f"cd {worktree} && git push -u origin f")
    assert decision(asked) == "ask" and "feature <feature> verify" in asked["hookSpecificOutput"]["permissionDecisionReason"], \
        "a project repo needs verify: ./cc check runs no target gate"
    verified.record(cc, worktree, "verify")
    assert push("git push -u origin f", worktree) is None
    assert verified.lookup(cc, verified.commit_tree(worktree))["kinds"] == ["check", "verify"], "kinds accumulate"

    asked = push('git commit -q -m "Fix" && git push')
    assert decision(asked) == "ask" and "separate command" in asked["hookSpecificOutput"]["permissionDecisionReason"], \
        "the hook runs before the commit, so it cannot judge what this push sends, even when HEAD is verified"
    assert decision(push('git -c user.name=t commit -m x; git -C . push')) == "ask", "with git options too"
    later = push('git push && git commit -m "next"')
    assert "separate command" not in (later or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", ""), \
        "a commit after the push changes nothing it sent; HEAD alone decides"
    assert push("git log --grep push") is None, "push as an argument is not a push"
    assert push("git push origin --delete old-branch") is None, "deleting a remote branch sends no code"
    assert push("git push", outside) is None, "another project is the isolation hook's business"
    assert push("git push", base) is None, "not a Git repository"

    # A host without Nix says why a project's gates cannot run here (decision 0033); with Nix it does not.
    real_resolve = hostos.resolve_exe
    for nix, says in ((None, True), ("/usr/bin/nix", False)):
        hostos.resolve_exe = lambda name, nix=nix: nix if name == "nix" else real_resolve(name)
        try:
            commit(worktree, "a.txt", f"unverified {nix}")
            reason = push("git push", worktree)["hookSpecificOutput"]["permissionDecisionReason"]
            assert ("this host has no nix, so the nix-defined project gates cannot run here" in reason.lower() and "0033" in reason) == says, (nix, reason)
            commit(cc, "a.txt", f"unverified {nix}")
            reason = push("git push")["hookSpecificOutput"]["permissionDecisionReason"]
            assert "0033" not in reason, "the CC's own push needs only ./cc check, which runs without Nix"
        finally:
            hostos.resolve_exe = real_resolve


def check_nix_part_before_push(base: Path) -> None:
    """Pushed commits that change a *.nix file, a flake.lock or catalog/ need a stamp whose run included the Nix part;
    other changes need only the stamp. What is pushed: since the upstream's merge base, else every file of HEAD."""
    cc = base / "nixpush" / "alpha_CC"
    remote = base / "nixpush" / "origin.git"
    cc.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(cc)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git = ["git", "-C", str(cc), "-c", "user.name=t", "-c", "user.email=t@t"]

    def commit(name: str, text: str) -> None:
        (cc / name).parent.mkdir(parents=True, exist_ok=True)
        (cc / name).write_text(text, encoding="utf-8")
        subprocess.run([*git, "add", name], check=True)
        subprocess.run([*git, "commit", "-q", "-m", text], check=True)

    def push() -> str | None:
        result = pre_bash({"tool_input": {"command": "git push"}, "cwd": str(cc)}, cc)
        return result["hookSpecificOutput"]["permissionDecisionReason"] if result else None

    commit("a.txt", "one")
    verified.record(cc, cc, "check")
    assert push() is None, "no remote yet: every file of HEAD is pushed, and none is a Nix input"
    commit("flake.nix", "{ }")
    verified.record(cc, cc, "check")
    reason = push()
    assert reason and "nix flake check did not run" in reason and "flake.nix" in reason, reason
    verified.record(cc, cc, "check", nix=False)
    assert push(), "a run that skipped Nix does not cover a Nix change"
    verified.record(cc, cc, "check", nix=True)
    assert push() is None, "the Nix part ran on this tree"

    subprocess.run([*git, "remote", "add", "origin", str(remote)], check=True)
    subprocess.run([*git, "push", "-q", "-u", "origin", "main"], check=True, capture_output=True)
    commit("a.txt", "two")
    verified.record(cc, cc, "check")
    assert push() is None, "flake.nix is already upstream; only a.txt is pushed"
    for name in ("sub/flake.lock", "catalog/repos.json", "nix/projects/api.nix"):
        commit(name, f"{name} changed")
        verified.record(cc, cc, "check")
        reason = push()
        assert reason and name in reason, (name, reason)
    for name in ("docs/flake.nix.md", "catalogue/x.json", "flake.locked"):
        commit(name, "look-alike")
    verified.record(cc, cc, "check")
    reason = push()
    assert reason and "catalog/repos.json" in reason and "flake.locked" not in reason and "catalogue" not in reason \
        and "docs/flake.nix.md" not in reason, reason


def check_stamp_nix_field(base: Path) -> None:
    """The stamp keeps "nix" when a run gives it; an old stamp without the key reads as unknown; true stays true."""
    root = base / "stamps"
    root.mkdir()
    tree = "0" * 40
    (verified.stamps(root)).mkdir(parents=True)
    (verified.stamps(root) / tree).write_text(json.dumps({"kinds": ["check"], "repository": "x", "at": "t"}), encoding="utf-8")
    assert "nix" not in verified.lookup(root, tree), "an old stamp stays readable: no key means unknown"
    verified.record(root, root, "check", tree=tree)
    assert "nix" not in verified.lookup(root, tree), "no value given, none invented"
    verified.record(root, root, "check", tree=tree, nix=False)
    assert verified.lookup(root, tree)["nix"] is False
    verified.record(root, root, "verify", tree=tree, nix=True)
    verified.record(root, root, "check", tree=tree, nix=False)
    verified.record(root, root, "check", tree=tree)
    assert verified.lookup(root, tree) == {**verified.lookup(root, tree), "kinds": ["check", "verify"], "nix": True}

    repository = base / "stamps-cli"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "a.txt"], check=True)
    cli = [sys.executable, str(SCRIPT.with_name("verified.py")), str(root), "record", str(repository)]
    assert subprocess.run([*cli, "check"], capture_output=True).returncode == 0
    stamped = verified.lookup(root, verified.working_tree(repository))
    assert stamped["kinds"] == ["check"] and "nix" not in stamped, stamped
    assert subprocess.run([*cli, "verify", "--nix"], capture_output=True).returncode == 0
    assert verified.lookup(root, verified.working_tree(repository))["nix"] is True
    assert subprocess.run([*cli, "verify", "--maybe"], capture_output=True).returncode == 2


def check_stamp_ignores_cached_stats(base: Path) -> None:
    """A same-size edit in the clock tick of an index entry that Git did not smudge: Git itself rehashes it because
    the entry is racily clean, so the stamp must see the edit too. A copied index lost that (flaky in CI, 2026-10-28)."""
    repository = base / "racy"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    for key, value in (("core.checkStat", "minimal"), ("core.trustctime", "false"), ("user.name", "t"), ("user.email", "t@t")):
        subprocess.run(["git", "-C", str(repository), "config", key, value], check=True)
    (repository / "a.txt").write_text("one", encoding="utf-8")
    tick = time.time_ns() - 10 * 10**9
    os.utime(repository / "a.txt", ns=(tick, tick))  # older than the index Git writes next: not smudged
    subprocess.run(["git", "-C", str(repository), "add", "a.txt"], check=True)
    # With core.filemode false (a Windows drive) the executable bit lives only in the index.
    subprocess.run(["git", "-C", str(repository), "config", "core.filemode", "false"], check=True)
    (repository / "run.sh").write_text("echo run\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "--chmod=+x", "run.sh"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-q", "-m", "one"], check=True)
    (repository / "a.txt").write_text("two", encoding="utf-8")
    for path in (repository / "a.txt", repository / ".git" / "index"):
        os.utime(path, ns=(tick, tick))
    status = subprocess.run(["git", "-C", str(repository), "status", "--porcelain"], capture_output=True, text=True,
                            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout.strip()
    assert status == "M a.txt", f"the setup must be one Git itself sees as edited: {status!r}"
    edited = subprocess.run(["git", "-C", str(repository), "hash-object", "a.txt"], capture_output=True, text=True).stdout.strip()
    stamped = verified.working_tree(repository)
    assert verified.git(repository, "rev-parse", f"{stamped}:a.txt") == edited, "the stamp hashes content, not cached stats"
    assert verified.git(repository, "ls-tree", stamped, "run.sh").startswith("100755"), "modes come from the index, not the disk"


def check_kit_layer(base: Path) -> None:
    """In a CC, docs/kit belongs to the kit: an edit asks first and points to an override decision. The template edits it."""
    def edit_at(root: Path, path: str, tool: str = "Edit"):
        return rule_hooks.pre_edit({"tool_name": tool, "tool_input": {"file_path": str(root / path)}, "cwd": str(root)}, root)

    cc = base / "kit" / "alpha_CC"
    (cc / "docs" / "kit" / "rules").mkdir(parents=True)
    (cc / "control-center.json").write_text("{}", encoding="utf-8")
    for path, tool in (("docs/kit/rules/worktrees.md", "Edit"), ("docs/kit/decisions/0099-new.md", "Write")):
        asked = edit_at(cc, path, tool)
        reason = asked["hookSpecificOutput"]["permissionDecisionReason"] if asked else ""
        assert decision(asked) == "ask" and "docs/decisions/" in reason and "overrides" in reason, (path, asked)
    assert edit_at(cc, "docs/decisions/0001-own.md", "Write") is None, "a CC's own decision is not the kit"
    assert edit_at(cc, "docs/kitchen/notes.md") is None, "only the docs/kit folder itself"

    template = base / "kit" / "template"
    (template / "docs" / "kit" / "rules").mkdir(parents=True)
    assert edit_at(template, "docs/kit/rules/worktrees.md") is None, "the template repository owns the kit and edits it"


def check_session_start(base: Path) -> None:
    """A CC that is not set up yet makes the agent open with the setup interview; a ready CC and the template stay quiet."""
    def context(status: str | None) -> str | None:
        root = base / "start" / str(status)
        root.mkdir(parents=True, exist_ok=True)
        if status:
            (root / "control-center.json").write_text(json.dumps({"status": status}), encoding="utf-8")
        result = rule_hooks.session_start({}, root)
        return result["hookSpecificOutput"]["additionalContext"] if result else None

    real_resolve, real_run = hostos.resolve_exe, subprocess.run
    hostos.resolve_exe = lambda name: None if name == "claude" else real_resolve(name)
    try:
        assert "grill-cc-bootstrap" in context("discovery") and "not set up yet" in context("discovery")
        assert "grill-cc-bootstrap" in context("adapting") and "continue" in context("adapting")
        assert context("ready") is None, "a ready CC starts like any session"
        assert context(None) is None, "the template has no control-center.json and is never bootstrapped"
        assert rule_hooks.session_start({}, base / "start" / "ready") is None, "no claude on PATH: nothing to say"
    finally:
        hostos.resolve_exe = real_resolve

    # Fail-closed hooks ("onFailure": "block") need Claude Code 2.1.295; an older one gets a warning.
    def started(output: str | Exception, status: str = "ready") -> dict | None:
        calls = []

        def fake_run(argv: list[str], **options: object) -> subprocess.CompletedProcess:
            calls.append((argv, options.get("timeout")))
            if isinstance(output, Exception):
                raise output
            return subprocess.CompletedProcess(argv, 0, output, "")
        hostos.resolve_exe = lambda name: "/opt/claude" if name == "claude" else real_resolve(name)
        subprocess.run = fake_run  # type: ignore[assignment]
        try:
            result = rule_hooks.session_start({}, base / "start" / status)
        finally:
            hostos.resolve_exe, subprocess.run = real_resolve, real_run  # type: ignore[assignment]
        assert calls == [(["/opt/claude", "--version"], 3)], calls
        return result

    for old in ("2.1.294 (Claude Code)", "2.0.999 (Claude Code)", "1.9.400"):
        warning = (started(old) or {}).get("systemMessage", "")
        assert "2.1.295 or later" in warning and "may not apply" in warning, (old, warning)
    for new in ("2.1.295 (Claude Code)", "2.1.318 (Claude Code)", "2.2.0 (Claude Code)", "10.0.0", "", "garbage"):
        assert started(new) is None, new
    assert started(subprocess.TimeoutExpired("claude", 3)) is None, "a slow claude is skipped"
    assert started(OSError("gone")) is None, "a claude that cannot start is skipped"
    both = started("2.1.200 (Claude Code)", "discovery")
    assert "systemMessage" in both and "grill-cc-bootstrap" in both["hookSpecificOutput"]["additionalContext"], both

    hostos.resolve_exe = lambda name: None if name == "claude" else real_resolve(name)
    try:
        assert "Next step" not in context("adapting"), "no new repo: the plain continue offer"

        # A new repo (./cc repo new) waits for its first feature: the session names that step and the open feature.
        root = base / "start" / "adapting"
        (root / "catalog" / "repositories").mkdir(parents=True)
        (root / "catalog" / "repositories" / "app.json").write_text(json.dumps(
            {"id": "app", "kind": "project", "remote": None, "status": "discovered"}), encoding="utf-8")
        assert "first feature on the new repo(s) app" in context("adapting") and "Open features" not in context("adapting")
        (root / "worktrees" / "start-app").mkdir(parents=True)
        (root / "worktrees" / "start-app" / ".cc-worktree.json").write_text("{}", encoding="utf-8")
        assert "Open features: start-app" in context("adapting"), context("adapting")
    finally:
        hostos.resolve_exe = real_resolve


def check_protected_data(base: Path) -> None:
    """Irreplaceable data and its mirrors: an agent's delete, move-away or overwrite is denied in Bash and PowerShell
    forms, before any ask; reading, listing and backing up pass. The mirror list comes from the locations file, so it
    holds when the manifest is broken; removing the tag asks."""
    root = base / "protected" / "di_CC"
    entry = root / ".cc-local" / "data" / "emb"
    entry.mkdir(parents=True)
    (entry / "part.parquet").write_bytes(b"x")
    (root / ".cc-local" / "data" / "features").mkdir()
    (root / ".cc-local" / "hooks").mkdir()
    manifest = {"name": "di_CC", "localData": [
        {"name": "emb", "path": ".cc-local/data/emb", "tags": ["irreplaceable", "sensitive"],
         "purge": ["ai-embeddings-purge"]},
        {"name": "features", "path": ".cc-local/data/features", "purge": ["ai-features-purge"]}]}
    (root / "control-center.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    mirror = "/mnt/c/Users/u/cc-backups/di_CC/data/emb"
    (root / ".cc-local/hooks/backup-locations.json").write_text(json.dumps(
        {"backups": ["/mnt/c/Users/u/cc-backups/di_CC"], "mirrors": {"emb": [mirror]}}), encoding="utf-8")

    def run(command: str, cwd: Path = root) -> str | None:
        return decision(rule_hooks.pre_bash({"tool_input": {"command": command}, "cwd": str(cwd)}, root))

    denied = [
        "rm -rf .cc-local/data/emb", "rm -rf .cc-local", "rm .cc-local/data/emb/part.parquet", f"rm -rf {root}",
        "find .cc-local -delete", "find .cc-local/data -name '*.parquet' -delete",
        "find .cc-local/data -name x -exec rm {} +", "git clean -fdx", "git clean -xdf", "git clean -fdX",
        "cd .cc-local/data && rm -rf *", "cd .cc-local/data/emb; rm -rf .", "cd /tmp && rm -rf " + str(entry),
        r"cmd.exe /c rmdir /s /q C:\Users\u\cc-backups\di_CC\data\emb",
        r"Remove-Item -Recurse -Force C:\Users\u\cc-backups\di_CC\data\emb",
        "Remove-Item -Recurse -Force .cc-local/data/emb", r"del C:\Users\u\cc-backups\di_CC\data\emb.json",
        f"rm {mirror}.json", "python3 -c \"import shutil; shutil.rmtree('.cc-local/data/emb')\"",
        "bash -c 'rm -rf .cc-local/data/emb'", ": > .cc-local/data/emb/part.parquet",
        f"echo x > {mirror}.json", "echo x >> .cc-local/data/emb/part.parquet",
        "cp /tmp/x .cc-local/data/emb/part.parquet", "mv .cc-local/data/emb /tmp/old", "mv /tmp/part.parquet .cc-local/data/emb/",
        "cp -r /tmp/data .cc-local","mv /tmp/x .cc-local/data/emb/part.parquet",
        f"rsync -a --delete /tmp/empty/ {mirror}/", "shred -u .cc-local/data/emb/part.parquet",
        "truncate -s 0 .cc-local/data/emb/part.parquet", "./cc data remove emb", "cc data remove emb --mirror",
        "script -qc './cc data remove emb' /dev/null", "./cc run ai-embeddings-purge",
        "make x; rm -rf .cc-local/data/emb", "git push; rm -rf .cc-local/data/emb",
        "D=.cc-local/data/emb; rm -rf $D", "export D=.cc-local/data; rm -rf ${D}/emb",
    ]
    for command in denied:
        assert run(command) == "deny", command
    allowed = [
        "ls -la .cc-local/data/emb", "du -sh .cc-local", "./cc backup", "./cc backup where", "./cc backup restore emb",
        "rm -rf .cc-local/data/features", "rm -rf /tmp/x", "git clean -fd", "./cc run ai-features-purge",
        "cat .cc-local/data/emb/meta.json > /tmp/meta.json", "cp .cc-local/data/emb/part.parquet /tmp/",
        "mv /tmp/new.parquet .cc-local/data/emb/", "cp -r /tmp/new .cc-local","python3 -c \"import pandas; pandas.read_parquet('.cc-local/data/emb')\"",
        f"ls {mirror}", "rm -rf .cc-local/data/embeddings-old", "echo data remove",
    ]
    for command in allowed:
        assert run(command) in (None, "ask"), command
    assert run("git clean -fdx", root / "worktrees" / "f" / "api_wt") is None, "a worktree's own clean is its business"
    reason = rule_hooks.pre_bash({"tool_input": {"command": "rm -rf .cc-local/data/emb"}, "cwd": str(root)}, root)
    assert "./cc data remove emb" in reason["hookSpecificOutput"]["permissionDecisionReason"], reason

    def edit(path: str, tool: str = "Write", **tool_input: str) -> str | None:
        event = {"tool_name": tool, "tool_input": {"file_path": path, **tool_input}, "cwd": str(root)}
        return decision(rule_hooks.pre_edit(event, root))

    assert edit(str(entry / "part.parquet"), content="x") == "deny"
    assert edit(str(entry / "new.json"), content="x") == "deny"
    assert edit(f"{mirror}.json", "Edit", old_string="a", new_string="b") == "deny"
    assert edit(r"C:\Users\u\cc-backups\di_CC\data\emb\x.bin", content="x") == "deny"
    assert edit(str(root / ".cc-local/data/features/x.bin"), content="x") is None
    without = {**manifest, "localData": [{**manifest["localData"][0], "tags": ["sensitive"]}, manifest["localData"][1]]}
    assert edit(str(root / "control-center.json"), content=json.dumps(without)) == "ask", "dropping the tag asks"
    assert edit(str(root / "control-center.json"), content=json.dumps({**manifest, "localData": []})) == "ask"
    assert edit(str(root / "control-center.json"), content="{broken") == "ask"
    assert edit(str(root / "control-center.json"), "Edit", old_string='"irreplaceable",', new_string="") == "ask"
    assert edit(str(root / "control-center.json"), content=json.dumps({**manifest, "status": "ready"})) is None
    assert edit(str(root / "control-center.json"), "Edit", old_string='"di_CC"', new_string='"di-cc"') is None

    (root / "control-center.json").write_text("{broken", encoding="utf-8")
    assert run(f"rm -rf {mirror}") == "deny", "the locations file still protects the mirror"

    # The hook must stay fast: no subprocess per call. Counted, not timed: a parallel ./cc check loads the machine
    # enough on Windows to push 100 in-process calls past a tight wall-clock limit.
    def no_subprocess(*arguments: object, **keywords: object) -> None:
        raise AssertionError(f"the hook started a subprocess: {arguments[:1]}")
    started, real_popen = time.monotonic(), subprocess.Popen
    subprocess.Popen = no_subprocess  # type: ignore[assignment,misc]
    try:
        for _ in range(100):
            run("ls -la .cc-local/data/emb && rm -rf /tmp/x")
    finally:
        subprocess.Popen = real_popen  # type: ignore[misc]
    assert time.monotonic() - started < 30, "100 hook calls took over 30 s"


def main() -> int:
    check_nix_bypass()
    check_command_paths()
    check_markdown_allowlist()
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        check_path_spellings(base)
        check_attribution(base)
        check_edits(base / "cc")
        check_attributed_commits(base)
        check_verify_before_push(base)
        check_nix_part_before_push(base)
        check_stamp_nix_field(base)
        check_stamp_ignores_cached_stats(base)
        check_post_read_is_cheap(base)
        check_unusual_paths(base)
        check_isolation(base)
        check_reference_repos(base)
        check_kit_layer(base)
        check_session_start(base)
        check_protected_data(base)
    check_notes_and_drift()
    check_cli()
    print("rule hooks test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
