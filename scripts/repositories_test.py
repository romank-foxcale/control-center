#!/usr/bin/env python3
"""Test ./cc repo add by URL, set-branch, remove and the reference kind against local bare Git remotes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
# Commands run from a neutral folder, so git never finds the repository the test was started from.
NEUTRAL = Path(tempfile.gettempdir())


def run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(arguments, capture_output=True, text=True)
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
    try:
        path.write_text(path.read_text() + "x") if path.is_file() else (path / "new.txt").write_text("x")
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
    assert os.access(clone / ".git", os.W_OK), ".git stays writable so fetching works"

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


IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def check_new_repo(base: Path, root: Path) -> None:
    """./cc repo new starts a project with no remote; set-remote connects it later, but only to a remote that is empty
    or already holds the local history (docs/kit/decisions/0031-new-project-cc.md)."""
    environment = {**os.environ, **IDENTITY}
    no_identity = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    empty_config = base / "empty.gitconfig"
    empty_config.write_text("")
    no_identity.update(GIT_CONFIG_GLOBAL=str(empty_config), GIT_CONFIG_NOSYSTEM="1")

    def repo(*arguments: str, check: bool = True, env: dict[str, str] = environment) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(SCRIPTS / "repositories.py"), str(root), *arguments],
                                capture_output=True, text=True, cwd=NEUTRAL, env=env)
        if check and result.returncode != 0:
            raise AssertionError(f"{arguments} failed:\n{result.stdout}{result.stderr}")
        return result

    clone = root / "repos" / "project" / "app"
    refused = repo("new", "app", "--targets", "linux", "--stack", "python", check=False, env=no_identity)
    assert refused.returncode != 0 and "user.name" in refused.stderr, refused.stderr
    assert not clone.exists(), "nothing is created without a git identity"
    escaped = repo("new", "../x", "--targets", "linux", "--stack", "python", check=False)
    assert escaped.returncode != 0 and not (root / "repos" / "x").exists() and not (root / "x").exists()

    created = repo("new", "app", "--targets", "linux", "--stack", "python")
    assert "only copy of its history" in created.stdout and "last backup: never" in created.stdout, created.stdout
    descriptor = json.loads((root / "catalog/repositories/app.json").read_text())
    assert descriptor["remote"] is None and descriptor["status"] == "discovered" and descriptor["defaultBranch"] == "main"
    assert run("git", "-C", str(clone), "rev-list", "--count", "HEAD").stdout.strip() == "1", "one first commit"
    assert (clone / ".gitattributes").read_bytes() == b"* text=auto eol=lf\n"
    assert (root / "nix" / "placeholders" / "app" / "CC_PLACEHOLDER").is_file()
    assert repo("new", "app", "--targets", "linux", "--stack", "python", check=False).returncode != 0, "id exists"
    (root / "repos" / "project" / "used").mkdir(parents=True)
    (root / "repos" / "project" / "used" / "file.txt").write_text("x")
    assert repo("new", "used", "--targets", "linux", "--stack", "python", check=False).returncode != 0, "folder not empty"
    assert "none (local)" in repo("list").stdout
    assert "no remote yet" in repo("update", "app").stdout
    sys.path.insert(0, str(SCRIPTS))
    import repositories

    noted = repositories.attention(root, nix_found=False)
    assert len(noted) == 1 and "app has no remote" in noted[0], noted

    assert repo("set-status", "app", "blocked", check=False).returncode != 0, "blocked needs a reason"
    repo("set-status", "app", "blocked", "--reason", "no native gate yet")
    assert json.loads((root / "catalog/repositories/app.json").read_text())["blockedReason"] == "no native gate yet"
    assert not any("blocked" in line for line in repositories.attention(root, nix_found=False))
    assert any("app is blocked (no native gate yet), but this host has Nix" in line
               for line in repositories.attention(root, nix_found=True)), "blocked never hides a host that can gate it"
    repo("set-status", "app", "discovered")
    assert "blockedReason" not in json.loads((root / "catalog/repositories/app.json").read_text())
    assert repo("set-branch", "app", "develop", check=False).returncode != 0, "set-branch checks local branches"
    repo("set-branch", "app", "main")

    # set-remote: an unrelated remote (a README made on the website) and one on another branch are refused, and the
    # clone and the catalog stay as they were; an empty remote is accepted.
    unrelated = bare_remote(base, "unrelated", "main")
    other_branch = bare_remote(base, "other", "trunk")
    for remote in (unrelated, other_branch):
        result = repo("set-remote", "app", remote, check=False)
        assert result.returncode != 0 and "empty remote" in result.stderr, result.stderr
        assert json.loads((root / "catalog/repositories/app.json").read_text())["remote"] is None
        assert run("git", "-C", str(clone), "remote", check=False).stdout.strip() == "", "origin removed again"
    empty = base / "empty.git"
    run("git", "init", "-q", "--bare", str(empty))
    repo("set-remote", "app", empty.as_uri())
    assert json.loads((root / "catalog/repositories/app.json").read_text())["remote"] == empty.as_uri()
    assert run("git", "-C", str(clone), "remote", "get-url", "origin").stdout.strip() == empty.as_uri()
    run("git", "-C", str(clone), "push", "-q", "origin", "main")

    # A remote that already holds the local history is accepted too, and a retry after a failure is safe.
    run("git", "-C", str(clone), "remote", "remove", "origin")
    descriptor = json.loads((root / "catalog/repositories/app.json").read_text())
    descriptor["remote"] = None
    (root / "catalog/repositories/app.json").write_text(json.dumps(descriptor))
    run("git", "-C", str(clone), "remote", "add", "origin", empty.as_uri())  # as if the last run stopped after this
    repo("set-remote", "app", empty.as_uri())
    assert json.loads((root / "catalog/repositories/app.json").read_text())["remote"] == empty.as_uri()

    removed = repo("new", "spare", "--targets", "linux", "--stack", "python")
    assert removed.returncode == 0
    assert "only copy of its history" in repo("remove", "spare").stdout, "remove warns that the clone is the only copy"


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
        assert (root / "repos" / "reference" / "acme-shop-poc" / ".git").exists(), "remove never deletes the base clone"

        # The kept clone is now outside the catalog: verify and doctor must say so.
        strays = repo("strays", check=False)
        assert strays.returncode != 0 and "acme-shop-poc" in strays.stderr, strays.stderr
        run("chmod", "-R", "u+w", str(root / "repos" / "reference" / "acme-shop-poc"))
        shutil.rmtree(root / "repos" / "reference" / "acme-shop-poc")
        assert repo("strays").returncode == 0, "repos/ matches the catalog again"
        (root / "repos" / "scratch").mkdir()
        assert "scratch" in repo("strays", check=False).stderr, "only project/ and reference/ belong in repos/"
        (root / "repos" / "scratch").rmdir()
        check_new_repo(base, root)
    print("repositories test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
