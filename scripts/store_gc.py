#!/usr/bin/env python3
"""./cc gc [--apply] [--days N]: free Nix store space without touching CC state.

A dry run by default. With --apply it removes this CC's `result` links (symlinks into /nix/store that Nix lists
as GC roots, never inside .cc-local) and runs nix-collect-garbage --delete-older-than Nd as the user. Collection is
store-wide: it also frees what other CCs no longer root. Why and limits: docs/kit/decisions/0030-disk-management.md.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from disk import Runner, cc_roots  # noqa: E402

LINK_NAME = re.compile(r"result(-\d+)?")
# Nix commands that build or check while a collection deletes paths. An open `nix develop` shell does not block:
# it can stay open for days, and its temporary roots protect what it uses.
BUSY = re.compile(r"(^|/)(nix\s+(build|flake|run)\b|nix-build\b)")


def run(command: list[str]) -> str | None:
    """stdout and stderr of a command, or None when it is missing or fails: nix-collect-garbage names the profiles
    it prunes on stderr. No timeout: a collection of a large store takes minutes."""
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def removable(root: Path, runner: Runner) -> tuple[list[Path], list[str]] | None:
    """This CC's result links that gc may remove, and the roots it keeps with the reason."""
    roots = cc_roots(root, runner)
    if roots is None:
        return None
    links, kept = [], []
    for link, target in roots:
        path = Path(link)
        relative = path.relative_to(root)
        if relative.parts[0] == ".cc-local":
            kept.append(f"{relative}: inside .cc-local")
        elif not LINK_NAME.fullmatch(path.name):
            kept.append(f"{relative}: not a result link")
        elif not path.is_symlink() or not os.readlink(path).startswith("/nix/store/"):
            kept.append(f"{relative}: not a symlink into /nix/store")
        else:
            links.append(path)
    return links, kept


def busy(runner: Runner) -> list[str]:
    """Nix commands of this user that are running now."""
    output = runner(["ps", "-u", str(os.getuid()), "-o", "pid=,args="]) or ""
    own = {os.getpid(), os.getppid()}
    return [line.strip() for line in output.splitlines()
            if BUSY.search(line.strip().split(maxsplit=1)[-1]) and int(line.split()[0]) not in own]


def main(argv: list[str], runner: Runner = run, out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(prog="./cc gc", description=__doc__.split("\n")[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--apply", action="store_true", help="remove the links and collect; without it, only show")
    parser.add_argument("--days", type=int, default=14, help="keep profile generations newer than this (default 14)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.days < 1:
        parser.error("--days must be 1 or more")

    found = removable(root, runner)
    if found is None:
        print("error: nix-store --gc --print-roots failed; nothing was changed", file=out)
        return 1
    links, kept = found
    running = busy(runner)
    if running and args.apply:
        print("error: Nix commands are running; nothing was changed. Run ./cc gc --apply when they end:", file=out)
        print("\n".join(f"  {line}" for line in running), file=out)
        return 1

    verb = "removing" if args.apply else "would remove"
    print(f"Result links in this CC ({verb}):", file=out)
    print("\n".join(f"  {link.relative_to(root)} -> {os.readlink(link)}" for link in links) or "  none", file=out)
    if kept:
        print("Roots kept:\n" + "\n".join(f"  {line}" for line in kept), file=out)
    if args.apply:
        for link in links:
            link.unlink()

    command = ["nix-collect-garbage", "--delete-older-than", f"{args.days}d", *([] if args.apply else ["--dry-run"])]
    print(f"$ {' '.join(command)}", file=out, flush=True)
    output = runner(command)
    if output is None:
        print("error: nix-collect-garbage failed", file=out)
        return 1
    profiles = re.findall(r"removing old generations of profile (\S+)", output)
    summary = [line for line in output.splitlines() if re.search(r"store paths (would be )?deleted|freed", line)]
    print(f"Profiles checked for generations older than {args.days} days:", file=out)
    print("\n".join(f"  {profile}" for profile in profiles) or "  none", file=out)
    print("\n".join(summary) or "no summary from nix-collect-garbage", file=out)
    print("Note: collection is store-wide, for every CC and profile of this user. The next check downloads or builds "
          "again what it needs, so private flake inputs need a working GitHub token in nix.conf.", file=out)
    if not args.apply:
        if running:
            print("Nix commands are running now; --apply waits until they end.", file=out)
        print("This was a dry run; the result links above still root their paths. Run ./cc gc --apply to act.", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
