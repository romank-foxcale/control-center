#!/usr/bin/env python3
"""./cc data remove <name> [--mirror]: delete a localData entry's folder (and with --mirror its mirrors and records)
after the user types <cc name>/<entry name> on their own terminal.

This is the only kit command that deletes data a mirror protects. It reads the answer from /dev/tty, not stdin, and
refuses inside Claude Code (CLAUDECODE is set), so an agent cannot confirm it for the user; the hooks also deny it.
Why and limits: docs/kit/decisions/0030-disk-management.md.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup_where import mirrors  # noqa: E402
from disk import Runner, human, run, tree_size  # noqa: E402


def ask_terminal(prompt: str, tty: Path = Path("/dev/tty")) -> str | None:
    """One line typed on the controlling terminal, or None when there is none."""
    try:  # unbuffered bytes: a buffered text handle opened r+ wants to seek, which a terminal cannot
        with os.fdopen(os.open(tty, os.O_RDWR), "r+b", buffering=0) as terminal:
            terminal.write(prompt.encode())
            line = terminal.readline()
    except OSError:
        return None
    return line.decode(errors="replace").rstrip("\r\n") if line else None


def size(path: Path) -> str:
    return human(tree_size(path, time.monotonic() + 60)[0])


def remove(root: Path, name: str, with_mirror: bool, env: dict[str, str], runner: Runner, out,
           ask: Callable[[str], str | None]) -> int:
    if env.get("CLAUDECODE"):
        print("error: an agent cannot run this; the user runs it in their own terminal. Nothing was deleted.", file=out)
        return 1
    try:
        manifest = json.loads((root / "control-center.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    entry = next((item for item in manifest.get("localData") or [] if isinstance(item, dict) and item.get("name") == name), None)
    if entry is None:
        print(f"error: unknown localData entry: {name}", file=out)
        return 1
    source = root / entry["path"]
    copies = [path for path in mirrors(root, manifest, name, env, runner) if path.exists()] if with_mirror else []
    if not source.exists() and not copies:
        print(f"error: nothing to delete for {name}", file=out)
        return 1
    print(f"This deletes {entry['path']} ({size(source)})" if source.exists() else f"{entry['path']} is already gone",
          file=out)
    for path in copies:
        print(f"  and its mirror {path} ({size(path)})", file=out)
    print(f"  {', '.join(entry.get('tags') or ['no tags'])}; rebuild needs: {entry.get('rebuildNeeds')}. "
          "It cannot be undone.", file=out)
    expected = f"{manifest.get('name') or root.name}/{name}"
    answer = ask(f"Type {expected} to delete it, anything else to stop: ")
    if answer is None:
        print("error: no terminal to confirm on; nothing was deleted", file=out)
        return 1
    if answer != expected:
        print(f"error: the answer was not {expected}; nothing was deleted", file=out)
        return 1
    if source.exists():
        shutil.rmtree(source)
        print(f"deleted {source}", file=out)
    for path in copies:
        shutil.rmtree(path)
        path.with_name(f"{path.name}.json").unlink(missing_ok=True)
        print(f"deleted the mirror {path} and its record", file=out)
    if not with_mirror:
        for path in mirrors(root, manifest, name, env, runner):
            if path.exists():
                print(f"  the mirror stays: {path} (./cc backup restore {name} brings the data back)", file=out)
    return 0


def main(argv: list[str], env: dict[str, str] | None = None, runner: Runner = run, out=sys.stdout,
         ask: Callable[[str], str | None] = ask_terminal) -> int:
    parser = argparse.ArgumentParser(prog="./cc data", description=__doc__.split("\n")[0])
    parser.add_argument("root", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    removing = commands.add_parser("remove", help="delete a data entry after you type its full name")
    removing.add_argument("name")
    removing.add_argument("--mirror", action="store_true", help="also delete its mirrors and their records")
    args = parser.parse_args(argv)
    return remove(args.root.resolve(), args.name, args.mirror, dict(os.environ) if env is None else env, runner, out, ask)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
