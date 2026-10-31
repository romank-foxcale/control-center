#!/usr/bin/env python3
"""./cc relock: move this CC's locked nixpkgs to the kit's recommended rev (nix/kit-nixpkgs.json).

CCs on one machine that lock the same nixpkgs share one copy of every tool in the Nix store. Only the nixpkgs node
of flake.lock changes: if Nix changes any other node, or a plain `nix flake lock` moves the rev again, the old
flake.lock is put back. It never commits; the CC must pass ./cc check (and ./cc verify when it has project gates)
first. Why and limits: docs/kit/decisions/0030-disk-management.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from disk import Runner  # noqa: E402
from store_gc import run  # noqa: E402  (no timeout: the first fetch of a nixpkgs rev takes minutes)


def nixpkgs_node(lock: dict) -> str:
    """Name of the node the flake's own `nixpkgs` input points at."""
    return lock["nodes"][lock["root"]]["inputs"]["nixpkgs"]


def others(lock: dict, node: str) -> dict:
    return {name: value for name, value in lock["nodes"].items() if name != node}


def main(argv: list[str], runner: Runner = run, out=sys.stdout) -> int:
    root = Path(argv[0]).resolve()
    path = root / "flake.lock"
    try:
        kit = json.loads((root / "nix" / "kit-nixpkgs.json").read_text(encoding="utf-8"))["rev"]
        text = path.read_text(encoding="utf-8")
        lock = json.loads(text)
        node = nixpkgs_node(lock)
        locked, original = lock["nodes"][node]["locked"], lock["nodes"][node]["original"]
    except (OSError, KeyError, json.JSONDecodeError) as error:
        print(f"error: cannot read the nixpkgs pin ({error}); nothing was changed", file=out)
        return 1
    if locked.get("rev") == kit:
        print(f"nixpkgs is already at the kit's recommended {kit[:12]}; nothing to do", file=out)
        return 0
    if original.get("type") != "github":
        print(f"error: nixpkgs comes from a {original.get('type')} input, not github; nothing was changed", file=out)
        return 1

    def restore(reason: str) -> int:
        path.write_text(text, encoding="utf-8")
        print(f"error: {reason}; flake.lock is back as it was", file=out)
        return 1

    target = f"github:{original['owner']}/{original['repo']}/{kit}"
    if runner(["nix", "flake", "lock", str(root), "--override-input", "nixpkgs", target]) is None:
        return restore(f"nix flake lock --override-input nixpkgs {target} failed")
    # A plain lock is what every ./cc check|build|run does first: the pin must survive it.
    if runner(["nix", "flake", "lock", str(root)]) is None:
        return restore("nix flake lock failed after the override")
    new = json.loads(path.read_text(encoding="utf-8"))
    if new["nodes"][nixpkgs_node(new)]["locked"].get("rev") != kit:
        return restore("the rev did not stay after a plain nix flake lock")
    if nixpkgs_node(new) != node or others(new, node) != others(lock, node) or new["root"] != lock["root"]:
        return restore("Nix changed flake.lock nodes other than nixpkgs")
    print(f"flake.lock: nixpkgs {locked.get('rev', '?')[:12]} -> {kit[:12]}; no other node changed", file=out)
    print("Not committed. Run ./cc check (and ./cc verify if this CC has project gates) before the commit; "
          "a new nixpkgs can change tool versions and fixed hashes.", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
