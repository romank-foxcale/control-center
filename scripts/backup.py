#!/usr/bin/env python3
"""./cc backup [--to DIR]: copy what only this machine has of the CC to a folder outside the CC.

One backup is <DIR>/<cc name>/<timestamp>/ with
- cc.bundle: `git bundle --all` of the CC's history (a CC may have no remote);
- changes.tar.gz: files Git does not track or that differ from HEAD, without ignored ones (plans live here in the kit);
- state.tar.gz: .cc-local without data/ and without other folders over 100 MiB, which are listed instead.
On WSL the folder must be on a Windows drive (/mnt/...): a copy inside the distro's own disk file is lost with it.
It never uploads. Why and limits: docs/kit/decisions/0030-disk-management.md.
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


def main(argv: list[str], env: dict[str, str] | None = None, runner: Runner = run, out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(prog="./cc backup", description=__doc__.split("\n")[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--to", help="destination folder; default backup.path in control-center.json")
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
    partial = folder / f".{stamp}.partial"
    partial.mkdir(parents=True)
    # Written under a hidden name and renamed at the end: an interrupted run leaves the earlier backups as they are.
    try:
        subprocess.run(["git", "-C", str(root), "bundle", "create", "-q", str(partial / "cc.bundle"), "--all"],
                       check=True, capture_output=True)
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
    for line in skipped:
        print(f"  not backed up: .cc-local/{line}", file=out)
    for path in removed:
        print(f"  removed older backup {path.name} (keeping {keep})", file=out)
    print("  restore: git clone cc.bundle <dir>, then tar -xzf changes.tar.gz and state.tar.gz inside it", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
