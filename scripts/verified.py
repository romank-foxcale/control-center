#!/usr/bin/env python3
"""Stamps of passing ./cc check and ./cc verify runs, keyed by the Git tree they tested.

A stamp names the tree, not the commit: a run on uncommitted changes still covers the commit made from
them afterwards, and any later edit gives a new tree with no stamp. The push hook in rule_hooks.py reads
them. Why: docs/kit/decisions/0028-verify-before-push.md
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# What each run proves: ./cc check builds the flake checks only; ./cc verify also runs every target gate.
KINDS = ("check", "verify")


def stamps(root: Path) -> Path:
    return root / ".cc-local" / "verified"


def git(repository: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(["git", "-C", str(repository), *arguments], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def working_tree(repository: Path) -> str:
    """The tree Nix evaluates in `repository`: every tracked file as it is on disk, committed or not.

    The index's entries (paths and modes: with core.filemode false the disk has no executable bit) go into a new
    index without their cached file stats, so `add --update` hashes every file's content. A copy of the index kept
    the stats but lost Git's racy-clean timing, and called a same-size edit in the same clock tick unchanged (flaky
    in CI, 2026-10-28).
    """
    entries = subprocess.run(["git", "-C", str(repository), "ls-files", "--stage", "-z"], check=True, capture_output=True).stdout
    with tempfile.TemporaryDirectory() as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        subprocess.run(["git", "-C", str(repository), "update-index", "-z", "--index-info"], input=entries, check=True,
                       capture_output=True, env=env)
        git(repository, "add", "--update", env=env)
        return git(repository, "write-tree", env=env)


def commit_tree(repository: Path, revision: str = "HEAD") -> str:
    return git(repository, "rev-parse", f"{revision}^{{tree}}")


def record(root: Path, repository: Path, kind: str) -> str:
    """Stamp the tree `repository` has on disk as passed by `kind`; returns the tree."""
    tree = working_tree(repository)
    directory = stamps(root)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = directory / tree
    found = lookup(root, tree)
    kinds = sorted({kind, *(found or {}).get("kinds", [])})
    stamp.write_text(json.dumps({"kinds": kinds, "repository": str(repository),
                                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n", encoding="utf-8")
    return tree


def lookup(root: Path, tree: str) -> dict | None:
    try:
        return json.loads((stamps(root) / tree).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main() -> int:
    if len(sys.argv) != 5 or sys.argv[2] != "record" or sys.argv[4] not in KINDS:
        print(f"usage: verified.py <cc-root> record <repository> <{'|'.join(KINDS)}>", file=sys.stderr)
        return 2
    root, repository = Path(sys.argv[1]).resolve(), Path(sys.argv[3]).resolve()
    try:
        record(root, repository, sys.argv[4])
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"warning: no {sys.argv[4]} stamp for {repository}: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
