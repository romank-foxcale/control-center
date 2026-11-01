#!/usr/bin/env python3
"""Test the rule hooks: each hard rule has a case it must stop and a look-alike it must let through."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

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


def check_cli() -> None:
    event = json.dumps({"tool_input": {"command": "cmake .."}})
    result = subprocess.run([sys.executable, str(SCRIPT), "pre-bash"], input=event.encode(), capture_output=True)
    assert result.returncode == 0 and json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
    allowed = subprocess.run([sys.executable, str(SCRIPT), "pre-bash"], input=b'{"tool_input": {"command": "ls"}}',
                             capture_output=True)
    assert allowed.returncode == 0 and allowed.stdout == b"", "silence lets the normal permission flow decide"
    broken = subprocess.run([sys.executable, str(SCRIPT), "pre-bash"], input=b"not json", capture_output=True)
    assert broken.returncode == 1 and b"NOT enforced" in broken.stderr, "a broken hook warns and never blocks (exit 2)"


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

    assert "grill-cc-bootstrap" in context("discovery") and "not set up yet" in context("discovery")
    assert "grill-cc-bootstrap" in context("adapting") and "continue" in context("adapting")
    assert context("ready") is None, "a ready CC starts like any session"
    assert context(None) is None, "the template has no control-center.json and is never bootstrapped"


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

    started = time.monotonic()
    for _ in range(100):
        run("ls -la .cc-local/data/emb && rm -rf /tmp/x")
    assert time.monotonic() - started < 5, "the hook must stay fast: no subprocess per call"


def main() -> int:
    check_nix_bypass()
    check_markdown_allowlist()
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        check_attribution(base)
        check_edits(base / "cc")
        check_attributed_commits(base)
        check_verify_before_push(base)
        check_stamp_ignores_cached_stats(base)
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
