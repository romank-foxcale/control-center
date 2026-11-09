#!/usr/bin/env python3
"""Test a feature on a repo with no remote yet: the Nix source overrides of every ./cc Nix call, ./cc worktree land
(fast-forward only, stamp or user reason) and remove after land. Why: docs/kit/decisions/0034-new-project-cc.md."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import verified  # noqa: E402
import worktrees  # noqa: E402

# Commands run from a neutral folder, so git never finds the repository the test was started from.
NEUTRAL = Path(tempfile.gettempdir())
IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(arguments, capture_output=True, text=True, cwd=NEUTRAL, env={**os.environ, **IDENTITY})
    if check and result.returncode != 0:
        raise AssertionError(f"{arguments} failed:\n{result.stdout}{result.stderr}")
    return result


def make_cc(base: Path) -> Path:
    root = base / "x_CC"
    (root / "catalog" / "repositories").mkdir(parents=True)
    (root / "control-center.json").write_text(json.dumps(
        {"schemaVersion": 1, "name": "x_CC", "status": "adapting",
         "layout": {"projectsRoot": ".", "repositories": "repos", "worktrees": "worktrees"}}))
    return root


def commit(worktree: Path, name: str) -> None:
    (worktree / name).write_text(f"{name}\n")
    run("git", "-C", str(worktree), "add", name)
    run("git", "-C", str(worktree), "commit", "-q", "-m", name)


def check_overrides(root: Path, cc) -> None:
    """A repo with no remote is overridden with its base clone; a feature's worktree wins over it; a repo with a
    remote is overridden only inside a feature."""
    remote = root.parent / "api.git"
    run("git", "init", "-q", "--bare", "-b", "main", str(remote))
    seed = root.parent / "api-seed"
    run("git", "init", "-q", "-b", "main", str(seed))
    commit(seed, "README.md")
    run("git", "-C", str(seed), "push", "-q", str(remote), "main")
    cc("repositories.py", "add", remote.as_uri(), "--targets", "linux", "--stack", "go", "--clone")
    clone = (root / "repos" / "project" / "app").resolve()
    assert worktrees.source_overrides(root) == ["--override-input", "app", f"path:{clone}"], worktrees.source_overrides(root)
    cc("worktrees.py", "create", "both", "app", "api")
    feature = (root / "worktrees" / "both").resolve()
    assert worktrees.source_overrides(root, "both") == [
        "--override-input", "app", f"path:{feature / 'app_wt'}",
        "--override-input", "api", f"path:{feature / 'api_wt'}"], worktrees.source_overrides(root, "both")
    printed = cc("worktrees.py", "nix-args").stdout.splitlines()
    assert printed == ["--override-input", "app", f"path:{clone}"], printed
    cc("worktrees.py", "remove", "both")


def check_land(root: Path, cc) -> None:
    base = root / "repos" / "project" / "app"
    cc("worktrees.py", "create", "first", "app")
    cc("worktrees.py", "create", "second", "app")
    first = root / "worktrees" / "first" / "app_wt"
    second = root / "worktrees" / "second" / "app_wt"
    commit(first, "one.txt")
    commit(second, "two.txt")

    refused = cc("worktrees.py", "remove", "first", check=False)
    assert refused.returncode != 0 and "unpublished" in refused.stderr, "remove without land still refuses"
    refused = cc("worktrees.py", "land", "first", "app", check=False)
    assert refused.returncode != 0 and "--unverified" in refused.stderr, "no stamp and no reason: refused"
    (first / "one.txt").write_text("changed\n")
    refused = cc("worktrees.py", "land", "first", "app", "--unverified", "no gate yet", check=False)
    assert refused.returncode != 0 and "dirty" in refused.stderr, refused.stderr
    run("git", "-C", str(first), "checkout", "--", "one.txt")
    (base / "scratch.txt").write_text("x\n")
    refused = cc("worktrees.py", "land", "first", "app", "--unverified", "no gate yet", check=False)
    assert refused.returncode != 0 and "uncommitted" in refused.stderr, refused.stderr
    (base / "scratch.txt").unlink()

    # A stamp of the feature tree is enough, and the base then holds exactly that tree.
    tree = verified.record(root, first, "verify")
    landed = cc("worktrees.py", "land", "first", "app")
    assert "fast-forwarded" in landed.stdout and "NOT verified" not in landed.stdout, landed.stdout
    assert verified.commit_tree(base) == tree, "the base HEAD is the tree the verify tested"
    assert (base / "one.txt").is_file(), "the base working tree follows"
    cc("worktrees.py", "remove", "first")

    # The second feature started before the first landed: not a fast-forward, refused, and the base stays clean.
    head = run("git", "-C", str(base), "rev-parse", "HEAD").stdout
    refused = cc("worktrees.py", "land", "second", "app", "--unverified", "no gate yet", check=False)
    assert refused.returncode != 0 and "moved since" in refused.stderr, refused.stderr
    assert run("git", "-C", str(base), "status", "--porcelain").stdout == "", "the base is not left mid-merge"
    assert run("git", "-C", str(base), "rev-parse", "HEAD").stdout == head
    run("git", "-C", str(second), "rebase", "-q", "main")
    landed = cc("worktrees.py", "land", "second", "app", "--unverified", "no gate yet")
    assert "NOT verified: no gate yet" in landed.stdout, landed.stdout
    manifest = json.loads((root / "worktrees" / "second" / ".cc-worktree.json").read_text())
    assert manifest["repositories"]["app"]["landedUnverified"] == "no gate yet"
    assert "already in main" in cc("worktrees.py", "land", "second", "app").stdout
    cc("worktrees.py", "remove", "second")

    # With a remote, a feature goes through push and a pull request instead.
    remote = root.parent / "app.git"
    run("git", "init", "-q", "--bare", str(remote))
    cc("repositories.py", "set-remote", "app", remote.as_uri())
    cc("worktrees.py", "create", "third", "app")
    commit(root / "worktrees" / "third" / "app_wt", "three.txt")
    refused = cc("worktrees.py", "land", "third", "app", "--unverified", "x", check=False)
    assert refused.returncode != 0 and "has a remote" in refused.stderr, refused.stderr


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = make_cc(Path(directory))
        cc = lambda script, *arguments, check=True: run(sys.executable, str(SCRIPTS / script), str(root), *arguments, check=check)
        cc("repositories.py", "new", "app", "--targets", "linux", "--stack", "python")
        check_overrides(root, cc)
        check_land(root, cc)
    print("worktrees test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
