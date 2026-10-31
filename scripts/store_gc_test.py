#!/usr/bin/env python3
"""Test ./cc gc on a fixture CC with fake nix-store, ps and nix-collect-garbage. A dry run changes nothing; --apply
removes only symlinks into /nix/store named result or result-N that Nix lists as roots, never a real folder named
result and never anything in .cc-local; while a Nix build runs, --apply changes nothing."""

from __future__ import annotations

import hashlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store_gc as cc_gc  # noqa: E402

COLLECT = ("removing old generations of profile /home/u/.local/state/nix/profiles/profile\n"
           "finding garbage collector roots...\n"
           "9849 store paths deleted, 1234.00 MiB freed\n")


def snapshot(root: Path) -> dict[str, str]:
    """Every entry with its link target or content hash, so a removed or changed link shows."""
    return {str(path.relative_to(root)): os.readlink(path) if path.is_symlink()
            else hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
            for path in sorted(root.rglob("*"))}


class Gc(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()) / "cc"
        (self.root / "worktrees/f/api_wt").mkdir(parents=True)
        (self.root / ".cc-local/runs").mkdir(parents=True)
        (self.root / ".cc-local/runs/state.json").write_text("{}")
        (self.root / "result").symlink_to("/nix/store/aaa-check")
        (self.root / "result-1").symlink_to("/nix/store/bbb-app")
        (self.root / "worktrees/f/api_wt/result").symlink_to("/nix/store/ccc-build")
        (self.root / ".cc-local/result").symlink_to("/nix/store/ddd-kept")
        (self.root / "result-2").mkdir()  # a real folder with the same name pattern
        (self.root / "result-2/keep.txt").write_text("mine")
        (self.root / "result-3").symlink_to("/tmp/elsewhere")
        (self.root / "gcroot").symlink_to("/nix/store/eee-named")
        listed = ["result", "result-1", "worktrees/f/api_wt/result", ".cc-local/result", "result-2", "result-3", "gcroot"]
        self.roots = "".join(f'"{self.root / name}" -> /nix/store/{index}-x\n' for index, name in enumerate(listed))
        self.ps = "  1 /usr/bin/bash\n"
        self.commands: list[list[str]] = []

    def runner(self, command: list[str]) -> str | None:
        self.commands.append(command)
        if command[:2] == ["nix-store", "--gc"]:
            return self.roots
        if command[0] == "ps":
            return self.ps
        if command[0] == "nix-collect-garbage":
            return COLLECT.replace("deleted", "would be deleted") if "--dry-run" in command else COLLECT
        return None

    def gc(self, *arguments: str) -> tuple[int, str]:
        out = io.StringIO()
        return cc_gc.main([str(self.root), *arguments], self.runner, out), out.getvalue()

    def collects(self) -> list[list[str]]:
        return [command for command in self.commands if command[0] == "nix-collect-garbage"]

    def test_dry_run_changes_nothing(self) -> None:
        before = snapshot(self.root)
        status, text = self.gc()
        self.assertEqual(status, 0)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(self.collects(), [["nix-collect-garbage", "--delete-older-than", "14d", "--dry-run"]])
        self.assertIn("would remove", text)
        self.assertIn("This was a dry run", text)

    def test_apply_removes_only_store_result_links(self) -> None:
        before = snapshot(self.root)
        status, text = self.gc("--apply", "--days", "30")
        self.assertEqual(status, 0)
        after = snapshot(self.root)
        self.assertEqual(sorted(set(before) - set(after)), ["result", "result-1", "worktrees/f/api_wt/result"])
        for name in after:
            self.assertEqual(after[name], before[name])
        self.assertEqual(self.collects(), [["nix-collect-garbage", "--delete-older-than", "30d"]])
        self.assertIn("  .cc-local/result: inside .cc-local", text)
        self.assertIn("  result-2: not a symlink into /nix/store", text)
        self.assertIn("  result-3: not a symlink into /nix/store", text)
        self.assertIn("  gcroot: not a result link", text)
        self.assertIn("  /home/u/.local/state/nix/profiles/profile", text)
        self.assertIn("9849 store paths deleted, 1234.00 MiB freed", text)
        self.assertIn("store-wide", text)

    def test_apply_refuses_while_nix_builds(self) -> None:
        before = snapshot(self.root)
        for args in ("/nix/store/x-nix-2.35/bin/nix flake check /cc --keep-going", "nix build .#api", "nix-build default.nix"):
            with self.subTest(args=args):
                self.ps = f"  1 /usr/bin/bash\n 42 {args}\n"
                self.commands.clear()
                status, text = self.gc("--apply")
                self.assertEqual(status, 1)
                self.assertIn("nothing was changed", text)
                self.assertEqual(snapshot(self.root), before)
                self.assertEqual(self.collects(), [])

    def test_open_dev_shell_does_not_block(self) -> None:
        self.ps = "  1 /usr/bin/bash\n 43 nix develop\n 44 /usr/bin/python3 nix_build_helper.py\n"
        self.assertEqual(self.gc("--apply")[0], 0)

    def test_failed_root_listing_changes_nothing(self) -> None:
        self.roots = None
        before = snapshot(self.root)
        status, text = self.gc("--apply")
        self.assertEqual((status, snapshot(self.root), self.collects()), (1, before, []))
        self.assertIn("nothing was changed", text)


if __name__ == "__main__":
    unittest.main()
