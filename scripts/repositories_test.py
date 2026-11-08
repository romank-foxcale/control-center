#!/usr/bin/env python3
"""Test ./cc repo add by URL, set-branch, remove and the reference kind against local bare Git remotes."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import hostos  # noqa: E402

# Commands run from a neutral folder: git must not find the repository the test was started from (a worktree made
# on Windows is not readable from WSL).
NEUTRAL = Path(tempfile.gettempdir())


def run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(arguments, capture_output=True, text=True, cwd=NEUTRAL)
    if check and result.returncode != 0:
        raise AssertionError(f"{arguments} failed:\n{result.stdout}{result.stderr}")
    return result


def bare_remote(base: Path, name: str, branch: str) -> str:
    """A remote whose HEAD is `branch`, like a GitHub repo whose main branch is develop."""
    source = base / f"{name}-src"
    run("git", "init", "-q", "-b", branch, str(source))
    (source / "README.md").write_text(f"# {name}\n")
    (source / "docs").mkdir()
    (source / "docs" / "notes.md").write_text("notes\n")
    run("git", "-C", str(source), "add", "-A")
    run("git", "-C", str(source), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    remote = base / f"{name}.git"
    run("git", "clone", "-q", "--bare", str(source), str(remote))
    return remote.as_uri()


def writable(path: Path) -> bool:
    """Whether a file accepts a write, or a folder a new file: what the OS allows, not what a mode bit says
    (os.access sees only the read-only flag on Windows, not an ACL)."""
    try:
        if path.is_file():
            path.write_text(path.read_text() + "x")
        else:
            (path / "new.txt").write_text("x")
            (path / "new.txt").unlink()
        return True
    except PermissionError:
        return False


def check_reference_clone(base: Path, root: Path, repo) -> None:
    """A reference clone sits in repos/reference/, nobody can write its files, and update and set-kind
    still work: the protection is lifted only for the moment they need it."""
    clone = root / "repos" / "reference" / "acme-shop-poc"
    readme = clone / "README.md"
    assert readme.is_file(), "cloned with its files"
    assert not writable(readme) and not writable(clone) and not writable(clone / "docs"), "files and folders are read-only"
    assert writable(clone / ".git"), ".git stays writable so fetching works"
    try:
        (clone / "docs" / "new.md").write_text("x")
        raise AssertionError("a new file in a reference clone must be refused")
    except PermissionError:
        pass
    assert readme.read_text() == "# acme-Shop-PoC\n", "protected files stay readable"

    source = base / "acme-Shop-PoC-src"
    (source / "README.md").write_text("# PoC\n\nversion 2\n")
    run("git", "-C", str(source), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "v2")
    run("git", "-C", str(source), "push", "-q", str(base / "acme-Shop-PoC.git"), "main")
    repo("update", "acme-shop-poc")
    assert "version 2" in readme.read_text() and not writable(readme), "update fast-forwards and protects again"

    repo("set-kind", "acme-shop-poc", "project", check=False)  # refused: a project needs targets and a stack
    repo("set-targets", "acme-shop-poc", "linux")
    repo("set-stack", "acme-shop-poc", "csharp")
    repo("set-kind", "acme-shop-poc", "project")
    moved = root / "repos" / "project" / "acme-shop-poc"
    assert not clone.exists() and writable(moved / "README.md"), "a project clone is writable again"
    run("git", "-C", str(moved), "checkout", "-q", "--", ".")
    repo("set-kind", "acme-shop-poc", "reference")
    assert clone.is_dir() and not writable(readme), "back to reference: moved and protected"


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        root = base / "x_CC"
        (root / "catalog" / "repositories").mkdir(parents=True)
        (root / "control-center.json").write_text(json.dumps(
            {"name": "x_CC", "layout": {"projectsRoot": ".", "repositories": "repos", "worktrees": "worktrees"}}))
        repo = lambda *arguments, check=True: run(sys.executable, str(SCRIPTS / "repositories.py"), str(root), *arguments, check=check)
        backend = bare_remote(base, "Acme-Shop-Backend", "develop")
        poc = bare_remote(base, "acme-Shop-PoC", "main")

        # Pasting the link is enough: the id comes from the URL, the main branch from the remote's HEAD.
        repo("add", backend, "--targets", "linux", "--stack", "java")
        descriptor = json.loads((root / "catalog/repositories/acme-shop-backend.json").read_text())
        assert descriptor["id"] == "acme-shop-backend", descriptor
        assert descriptor["defaultBranch"] == "develop", "the main branch is read from the remote, not assumed to be main"
        assert descriptor["kind"] == "project" and descriptor["remote"] == backend

        assert repo("add", poc, check=False).returncode != 0, "a project repo still needs targets and a stack"
        repo("add", poc, "--kind", "reference", "--clone")
        assert json.loads((root / "catalog/repositories/acme-shop-poc.json").read_text())["targets"] == []
        check_reference_clone(base, root, repo)

        listing = repo("list").stdout
        assert "acme-shop-backend\tproject\tdevelop" in listing and "acme-shop-poc\treference\tmain" in listing, listing

        # set-branch refuses a branch the remote does not have, so a typo cannot break ./cc worktree.
        assert repo("set-branch", "acme-shop-backend", "mian", check=False).returncode != 0
        repo("set-branch", "acme-shop-backend", "develop")

        # Reference repos are never checked out as feature worktrees.
        refused = run(sys.executable, str(SCRIPTS / "worktrees.py"), str(root), "create", "demo", "acme-shop-poc", check=False)
        assert refused.returncode != 0 and "reference repo" in refused.stderr, refused.stderr

        # remove keeps the clone and refuses while a feature still uses the repo.
        feature = root / "worktrees" / "demo"
        feature.mkdir(parents=True)
        (feature / ".cc-worktree.json").write_text(json.dumps({"repositories": {"acme-shop-backend": {}}}))
        blocked = repo("remove", "acme-shop-backend", check=False)
        assert blocked.returncode != 0 and "demo" in blocked.stderr, blocked.stderr
        repo("remove", "acme-shop-poc")
        assert not (root / "catalog/repositories/acme-shop-poc.json").exists()
        kept = root / "repos" / "reference" / "acme-shop-poc"
        assert (kept / ".git").exists(), "remove never deletes the base clone"
        assert writable(kept) and writable(kept / "README.md"), "a removed reference clone is writable, so it can be deleted"

        # The kept clone is now outside the catalog: verify and doctor must say so.
        strays = repo("strays", check=False)
        assert strays.returncode != 0 and "acme-shop-poc" in strays.stderr, strays.stderr
        hostos.rmtree(kept)  # git marks pack files read-only, which Windows refuses to delete without help
        assert not kept.exists()
        assert repo("strays").returncode == 0, "repos/ matches the catalog again"
        (root / "repos" / "scratch").mkdir()
        assert "scratch" in repo("strays", check=False).stderr, "only project/ and reference/ belong in repos/"
    print("repositories test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
