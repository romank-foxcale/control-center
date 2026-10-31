#!/usr/bin/env python3
"""./cc backup [--to DIR] [--push]: copy what only this machine has of the CC to a folder outside the CC.

One backup is <DIR>/<cc name>/<timestamp>/ with
- cc.bundle: every branch and tag of the CC's history;
- changes.tar.gz: files Git does not track or that differ from HEAD, without ignored ones (plans live here in the kit);
- state.tar.gz: .cc-local without data/ and without other folders over 100 MiB, which are listed instead;
- repos/<clone>.bundle: every branch of each project clone, so worktree commits that are on no remote survive;
- worktree-changes.tar.gz: each feature worktree's files that differ from its HEAD or are untracked.
On WSL the folder must be on a Windows drive (/mnt/...): a copy inside the distro's own disk file is lost with it.
With --push (or backup.push), the same files then go as one parentless commit to the branch cc-backup of the CC's
origin, only if GitHub says that repository is private. Why and limits: docs/kit/decisions/0030-disk-management.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from disk import KIT_STATE, UNDECLARED_BYTES, WALK_SECONDS, Runner, expand, human, run, tree_size  # noqa: E402

STAMP = re.compile(r"\d{8}-\d{6}")
BRANCH = "cc-backup"
# GitHub refuses files over 100 MB in a push.
MAX_FILE_BYTES = 95 * 10**6


def git(directory: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(["git", "-C", str(directory), *arguments], capture_output=True, text=True, check=True,
                          env=env).stdout.strip()


def clones(root: Path) -> list[Path]:
    """Project clones that have at least one branch."""
    base = root / "repos" / "project"
    return [path for path in sorted(base.iterdir()) if (path / ".git").exists()
            and git(path, "for-each-ref", "--count=1", "refs/heads")] if base.is_dir() else []


def worktrees(root: Path) -> list[Path]:
    return [path for path in sorted((root / "worktrees").glob("*/*_wt")) if (path / ".git").exists()]

def destination(root: Path, manifest: dict, to: str | None, env: dict[str, str], runner: Runner) -> Path:
    if to is None:
        configured = (manifest.get("backup") or {}).get("path")
        if not configured:
            raise ValueError("no destination: pass --to DIR or set backup.path in control-center.json")
        resolved = expand(configured, env, runner)
        if resolved is None:
            raise ValueError(f"backup.path {configured} cannot be resolved here")
    else:
        resolved = Path(to).expanduser()
    resolved = resolved.resolve()
    if resolved == root or root in resolved.parents:
        raise ValueError(f"{resolved} is inside the CC; a backup must live outside it")
    if env.get("WSL_DISTRO_NAME") and not re.match(r"/mnt/[a-z]/", f"{resolved}/"):
        raise ValueError(f"{resolved} is inside this WSL distro's disk file; choose a folder on a Windows drive (/mnt/c/...)")
    return resolved


def changed_files(root: Path) -> list[str]:
    """Files that differ from HEAD (staged or not) and untracked files that are not ignored."""
    names: set[str] = set()
    for command in (["diff", "-z", "--name-only", "HEAD"], ["ls-files", "-z", "--others", "--exclude-standard"]):
        output = subprocess.run(["git", "-C", str(root), *command], capture_output=True, text=True, check=True).stdout
        names.update(output.split("\0"))
    return sorted(name for name in names if name and (root / name).is_file())


def state_members(local: Path) -> tuple[list[Path], list[str]]:
    """Top-level entries of .cc-local to archive, and those left out with the reason."""
    members, skipped = [], []
    for path in sorted(local.iterdir()) if local.is_dir() else []:
        if path.name == "data":
            skipped.append("data/: declared rebuildable data")
            continue
        size, finished = tree_size(path, time.monotonic() + WALK_SECONDS)
        if path.name not in KIT_STATE and (size >= UNDECLARED_BYTES or not finished):
            skipped.append(f"{path.name}: {human(size)}{'' if finished else '+'}, over {human(UNDECLARED_BYTES)}; "
                           "declare it in localData or copy it yourself")
            continue
        members.append(path)
    return members, skipped


def prune(folder: Path, keep: int) -> list[Path]:
    backups = sorted(path for path in folder.iterdir() if path.is_dir() and STAMP.fullmatch(path.name))
    old = backups[:-keep] if len(backups) > keep else []
    for path in old:
        shutil.rmtree(path)
    return old


def push(root: Path, backup: Path, stamp: str, runner: Runner) -> str:
    """Push the backup folder as one parentless commit to origin's cc-backup; return the commit. The CC's HEAD,
    index, working tree and branches stay as they are: the commit is built with a temporary index."""
    try:
        url = git(root, "remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        raise ValueError("this CC has no origin remote") from None
    visibility = (runner(["gh", "repo", "view", url, "--json", "visibility", "--jq", ".visibility"]) or "").strip()
    if visibility != "PRIVATE":
        raise ValueError(f"origin {url} is not a private GitHub repository (gh says '{visibility or 'unknown'}')")
    large = [path for path in backup.rglob("*") if path.is_file() and path.stat().st_size > MAX_FILE_BYTES]
    if large:
        raise ValueError(f"{large[0].relative_to(backup)} is over {MAX_FILE_BYTES // 10**6} MB, which GitHub refuses")
    index = backup.parent / f".{stamp}.index"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    git_dir = git(root, "rev-parse", "--absolute-git-dir")
    try:
        git(backup, f"--git-dir={git_dir}", f"--work-tree={backup}", "add", "--force", "--all", ".", env=env)
        tree = git(root, "write-tree", env=env)
    finally:
        index.unlink(missing_ok=True)
    commit = git(root, "commit-tree", tree, "-m", f"Backup {stamp}")
    try:
        git(root, "push", "--quiet", "--force", "origin", f"{commit}:refs/heads/{BRANCH}")
    except subprocess.CalledProcessError as error:
        raise ValueError(f"git push failed: {error.stderr.strip()}; if it asks for credentials, run gh auth setup-git "
                         "(and gh auth refresh -s workflow when a workflow file is refused)") from None
    return commit


def main(argv: list[str], env: dict[str, str] | None = None, runner: Runner = run, out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(prog="./cc backup", description=__doc__.split("\n")[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--to", help="destination folder; default backup.path in control-center.json")
    parser.add_argument("--push", action=argparse.BooleanOptionalAction, default=None,
                        help=f"also push to origin's {BRANCH} branch; default backup.push in control-center.json")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env
    root = args.root.resolve()
    try:
        manifest = json.loads((root / "control-center.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    try:
        base = destination(root, manifest, args.to, env, runner)
    except ValueError as error:
        print(f"error: {error}; nothing was written", file=out)
        return 1

    folder = base / (manifest.get("name") or root.name)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    while (folder / stamp).exists():  # two runs in one second
        time.sleep(0.2)
        stamp = time.strftime("%Y%m%d-%H%M%S")
    partial = folder / f".{stamp}.partial"
    partial.mkdir(parents=True)
    # Written under a hidden name and renamed at the end: an interrupted run leaves the earlier backups as they are.
    try:
        # Not --all: refs/remotes/origin/cc-backup would put the previous backup inside this one.
        subprocess.run(["git", "-C", str(root), "bundle", "create", "-q", str(partial / "cc.bundle"), "--branches",
                        "--tags", "HEAD"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "bundle", "verify", "-q", str(partial / "cc.bundle")],
                       check=True, capture_output=True)
        changes = changed_files(root)
        with tarfile.open(partial / "changes.tar.gz", "w:gz") as archive:
            for name in changes:
                archive.add(root / name, arcname=name)
        members, skipped = state_members(root / ".cc-local")
        with tarfile.open(partial / "state.tar.gz", "w:gz") as archive:
            for path in members:
                archive.add(path, arcname=f".cc-local/{path.name}")
        bundled = clones(root)
        for clone in bundled:
            target = partial / "repos" / f"{clone.name}.bundle"
            target.parent.mkdir(exist_ok=True)
            git(clone, "bundle", "create", "-q", str(target), "--branches", "HEAD")
            git(clone, "bundle", "verify", "-q", str(target))
        worktree_files = [(tree / name).relative_to(root) for tree in worktrees(root) for name in changed_files(tree)]
        with tarfile.open(partial / "worktree-changes.tar.gz", "w:gz") as archive:
            for name in worktree_files:
                archive.add(root / name, arcname=str(name))
    except (OSError, subprocess.CalledProcessError) as error:
        shutil.rmtree(partial, ignore_errors=True)
        detail = getattr(error, "stderr", "") or error
        print(f"error: backup failed, earlier backups are unchanged: {str(detail).strip()}", file=out)
        return 1
    final = folder / stamp
    partial.rename(final)
    keep = (manifest.get("backup") or {}).get("keep", 5)
    removed = prune(folder, keep)

    size = sum(path.stat().st_size for path in final.iterdir())
    print(f"backup: {final} ({human(size)})", file=out)
    print(f"  history: cc.bundle, {len(changes)} untracked or changed files, {len(members)} .cc-local entries", file=out)
    print(f"  project clones: {len(bundled)} bundles; worktrees: {len(worktree_files)} changed files", file=out)
    for line in skipped:
        print(f"  not backed up: .cc-local/{line}", file=out)
    for path in removed:
        print(f"  removed older backup {path.name} (keeping {keep})", file=out)
    print("  restore: git clone cc.bundle <dir>, then tar -xzf changes.tar.gz, state.tar.gz and "
          "worktree-changes.tar.gz inside it; git clone repos/<clone>.bundle for each project clone", file=out)
    if not (args.push if args.push is not None else (manifest.get("backup") or {}).get("push", False)):
        return 0
    try:
        commit = push(root, final, stamp, runner)
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"error: not pushed: {getattr(error, 'stderr', '') or error}; the local backup above is complete", file=out)
        return 1
    print(f"pushed: origin {BRANCH} {commit[:12]}, one commit that replaces the previous one", file=out)
    print(f"  restore from GitHub: git fetch origin {BRANCH} && git archive FETCH_HEAD | tar -x -C <dir>", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
