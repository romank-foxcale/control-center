#!/usr/bin/env python3
"""Test ./cc prs with a fake gh: only catalog repos are queried, newest first, new and updated PRs are marked."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "prs.py"

# The fake gh logs which repo it was asked about and answers from PULLS (a JSON file the test rewrites).
FAKE_GH = f"""#!{sys.executable}
import json, os, sys
if sys.argv[1:3] == ["api", "user"]:
    print("me")
    sys.exit(0)
if sys.argv[1:3] == ["api", "graphql"]:  # review threads: none here; threads_test.py covers them
    print(json.dumps({{"data": {{"repository": {{"pullRequest": {{"number": 1, "title": "t", "author": {{"login": "dev"}}}}}}}}}}))
    sys.exit(0)
repo = sys.argv[sys.argv.index("--repo") + 1]
if sys.argv[1:3] == ["pr", "view"]:
    print(json.dumps(json.load(open(os.environ["FAKE_GH_PULLS"]))["view"]))
    sys.exit(0)
if sys.argv[1:3] == ["pr", "diff"]:  # raw bytes, as gh gives them: the Shift-JIS line is not UTF-8
    sys.stdout.buffer.write(b"diff --git a/api.py b/api.py\\n+# \\x93\\xfa\\x96\\x7b\\n")
    sys.exit(0)
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(repo + "\\n")
pulls = json.load(open(os.environ["FAKE_GH_PULLS"]))
if repo not in pulls:
    sys.exit("could not resolve to a Repository")
print(json.dumps(pulls[repo]))
"""


def pr(number: int, created: str, updated: str | None = None, **extra: object) -> dict:
    return {"number": number, "title": f"PR {number}", "author": {"login": "dev"}, "createdAt": created,
            "updatedAt": updated or created, "isDraft": False, "reviewDecision": "REVIEW_REQUIRED",
            "url": f"https://github.com/acme/x/pull/{number}", "baseRefName": "develop", "headRefName": f"f{number}", **extra}


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        root = base / "alpha_CC"
        for identifier, remote in (("shop-frontend", "https://github.com/acme/acme-Shop-frontend.git"),
                                   ("shop-backend", "git@github.com:acme/acme-Shop-backend.git"),
                                   ("shop-internal", "https://gitlab.example.com/shop/internal.git")):
            path = root / "catalog" / "repositories" / f"{identifier}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"id": identifier, "remote": remote}))
        # A clone that is not in the catalog: it must never be queried.
        (root / "repos" / "di-backend" / ".git").mkdir(parents=True)

        bin_dir = base / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(FAKE_GH)
        (bin_dir / "gh").chmod(0o755)
        log, pulls = base / "gh.log", base / "pulls.json"
        pulls.write_text(json.dumps({
            "acme/acme-Shop-frontend": [pr(7, "2026-10-01T10:00:00Z", isDraft=True)],
            "acme/acme-Shop-backend": [pr(3, "2026-10-02T10:00:00Z"), pr(4, "2026-10-07T09:00:00Z")],
        }))
        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_LOG": str(log), "FAKE_GH_PULLS": str(pulls)}

        def listing() -> tuple[list[dict], str]:
            result = subprocess.run([sys.executable, str(SCRIPT), str(root), "--json"], capture_output=True, text=True, env=env)
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout), result.stderr

        rows, warnings = listing()
        assert sorted(set(log.read_text().split())) == ["acme/acme-Shop-backend", "acme/acme-Shop-frontend"], \
            f"only the catalog's GitHub repos are queried: {log.read_text()}"
        assert "shop-internal: not a GitHub remote" in warnings, warnings
        assert "review threads not checked" not in warnings, warnings
        assert all(row["threads"] == "" for row in rows), "no review threads, nothing waits"
        assert [(row["repo"], row["number"]) for row in rows] == [("shop-backend", 4), ("shop-backend", 3), ("shop-frontend", 7)], \
            "newest first across repos"
        assert all(row["seen"] == "new" for row in rows), "everything is new on the first listing"
        assert rows[2]["review"] == "draft" and rows[0]["review"] == "review required"

        # Second listing: nothing new; a PR that changed since is marked updated; a brand-new one is new.
        current = json.loads(pulls.read_text())
        current["acme/acme-Shop-backend"][1]["updatedAt"] = "2026-10-07T12:00:00Z"
        current["acme/acme-Shop-frontend"].append(pr(8, "2026-10-07T11:00:00Z"))
        pulls.write_text(json.dumps(current))
        rows, _ = listing()
        marks = {(row["repo"], row["number"]): row["seen"] for row in rows}
        assert marks == {("shop-frontend", 8): "new", ("shop-backend", 4): "updated", ("shop-backend", 3): "", ("shop-frontend", 7): ""}, marks

        # show: what a review needs, with Trello cards linked from the description or comments.
        current["view"] = {**pr(4, "2026-10-07T09:00:00Z"), "body": "Implements https://trello.com/c/Cd1/7-health.",
                           "comments": [{"body": "see also https://trello.com/c/Xy9"}], "additions": 5, "deletions": 1,
                           "files": [{"path": "api.py", "additions": 5, "deletions": 1}]}
        pulls.write_text(json.dumps(current))
        detail = subprocess.run([sys.executable, str(SCRIPT), str(root), "show", "shop-backend", "4", "--diff"],
                                capture_output=True, text=True, env=env)
        assert detail.returncode == 0, detail.stderr
        assert "trello: https://trello.com/c/Cd1/7-health, https://trello.com/c/Xy9" in detail.stdout, detail.stdout
        assert "+5 -1  api.py" in detail.stdout and "diff --git a/api.py" in detail.stdout, detail.stdout
        assert "+# �" in detail.stdout, f"bytes that are not UTF-8 are replaced, not fatal: {detail.stdout}"
        outside = subprocess.run([sys.executable, str(SCRIPT), str(root), "show", "di-backend", "1"], capture_output=True, text=True, env=env)
        assert outside.returncode != 0 and "not in the catalog" in outside.stderr, "show is limited to the catalog too"

        unknown = subprocess.run([sys.executable, str(SCRIPT), str(root), "di-backend"], capture_output=True, text=True, env=env)
        assert unknown.returncode != 0 and "not in the catalog" in unknown.stderr, "a repo outside the catalog is refused"

        check_checkout(base, root, env)
    print("prs test passed")


def git(path: Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
                            capture_output=True, text=True)
    assert result.returncode == 0, f"git {arguments}: {result.stderr}"
    return result.stdout.strip()


def check_checkout(base: Path, root: Path, env: dict[str, str]) -> None:
    """checkout puts the PR head into a feature worktree, follows later pushes, and cleans up without complaint."""
    (root / "control-center.json").write_text(json.dumps(
        {"name": "alpha_CC", "layout": {"projectsRoot": ".", "repositories": "repos", "worktrees": "worktrees"}}))
    descriptor = root / "catalog/repositories/shop-backend.json"
    descriptor.write_text(json.dumps({**json.loads(descriptor.read_text()), "checkout": "shop-backend", "defaultBranch": "develop",
                                      "sourceInput": "shop-backend"}))
    # GitHub keeps each PR's head at refs/pull/<n>/head; a bare repo plays GitHub.
    author = base / "author"
    git(base, "init", "-q", "-b", "develop", str(author))
    git(author, "commit", "-q", "--allow-empty", "-m", "base")
    origin = base / "origin.git"
    git(base, "clone", "-q", "--bare", str(author), str(origin))
    git(author, "commit", "-q", "--allow-empty", "-m", "pr 4, first push")
    git(author, "push", "-q", str(origin), "HEAD:refs/pull/4/head")
    git(base, "clone", "-q", str(origin), str(root / "repos" / "project" / "shop-backend"))

    def checkout() -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT), str(root), "checkout", "shop-backend", "4"], capture_output=True, text=True, env=env)

    first = checkout()
    assert first.returncode == 0, first.stderr
    worktree = root / "worktrees" / "pr-shop-backend-4" / "shop-backend_wt"
    assert git(worktree, "log", "-1", "--format=%s") == "pr 4, first push", "the worktree is at the PR head"
    manifest = json.loads((root / "worktrees/pr-shop-backend-4/.cc-worktree.json").read_text())
    assert manifest["trelloCard"] == "https://trello.com/c/Cd1/7-health", "the PR's card is linked for cc-trello"
    assert manifest["review"] is True, "a PR checkout is marked as a review checkout"
    guard = (root / "worktrees/pr-shop-backend-4/CLAUDE.md").read_text()
    assert "review checkout" in guard and "Never edit, commit or push" in guard, guard

    git(author, "commit", "-q", "--allow-empty", "-m", "pr 4, second push")
    git(author, "push", "-q", str(origin), "HEAD:refs/pull/4/head")
    second = checkout()
    assert second.returncode == 0 and "updated" in second.stdout, second.stderr
    assert git(worktree, "log", "-1", "--format=%s") == "pr 4, second push", "re-running follows the PR's latest push"

    # A feature that ran a council holds .cc-runs next to its worktrees; a stray file is something nobody accounted for.
    feature_root = root / "worktrees" / "pr-shop-backend-4"
    run = feature_root / ".cc-runs" / "20261011-093830-coding"
    (run / "logs").mkdir(parents=True)
    (run / "state.json").write_text('{"stage": "done"}', encoding="utf-8")
    (run / "logs" / "write-0.log").write_text("usage: 1 new input, 2 cached input, 3 output tokens\n", encoding="utf-8")
    (feature_root / "notes.txt").write_text("draft", encoding="utf-8")

    def remove() -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT.parent / "worktrees.py"), str(root), "remove", "pr-shop-backend-4"],
                              capture_output=True, text=True)

    refused = remove()
    assert refused.returncode != 0 and "notes.txt" in refused.stderr, refused.stderr
    assert worktree.is_dir() and (feature_root / ".cc-worktree.json").is_file(), "refused before any worktree was removed"
    (feature_root / "notes.txt").unlink()

    removed = remove()
    assert removed.returncode == 0, f"the PR's commits are published, so cleanup must not refuse: {removed.stderr}"
    assert not feature_root.exists(), "the feature folder is gone, not left half removed"
    archived = root / ".cc-local" / "runs-archive" / "pr-shop-backend-4" / "20261011-093830-coding"
    assert (archived / "state.json").read_text() == '{"stage": "done"}' and (archived / "logs" / "write-0.log").is_file(), \
        "council runs are archived, not deleted"
    assert "runs-archive" in removed.stdout, removed.stdout
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
