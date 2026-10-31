#!/usr/bin/env python3
"""Test ./cc relock with a fake `nix flake lock` on a lock with several inputs. Only the nixpkgs node may change;
when Nix also moves another input, fails, or a plain lock moves the rev again, flake.lock is put back byte for
byte. S1 in plans/active/disk-size.md ran the real override; this test covers what relock does with the result."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relock  # noqa: E402

OLD, KIT, BRANCH_TIP = "a" * 40, "b" * 40, "c" * 40


def lock(nixpkgs_rev: str, api_rev: str = "1" * 40) -> dict:
    return {"nodes": {
        "api": {"locked": {"owner": "acme", "repo": "api", "rev": api_rev, "type": "github"},
                "original": {"owner": "acme", "ref": "develop", "repo": "api", "type": "github"}},
        "nixpkgs_2": {"locked": {"lastModified": 1, "narHash": f"sha256-{nixpkgs_rev[:4]}", "owner": "NixOS",
                                 "repo": "nixpkgs", "rev": nixpkgs_rev, "type": "github"},
                      "original": {"owner": "NixOS", "ref": "nixos-26.05", "repo": "nixpkgs", "type": "github"}},
        "root": {"inputs": {"api": "api", "nixpkgs": "nixpkgs_2"}},
    }, "root": "root", "version": 7}


class Relock(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "nix").mkdir()
        (self.root / "nix/kit-nixpkgs.json").write_text(json.dumps({"rev": KIT}))
        self.text = json.dumps(lock(OLD), indent=2) + "\n"
        (self.root / "flake.lock").write_text(self.text)
        self.after_override = lock(KIT)
        self.after_plain = lock(KIT)
        self.commands: list[list[str]] = []

    def runner(self, command: list[str]) -> str | None:
        self.commands.append(command)
        if command[:3] != ["nix", "flake", "lock"]:
            return None
        result = self.after_override if "--override-input" in command else self.after_plain
        if result is None:
            return None
        (self.root / "flake.lock").write_text(json.dumps(result, indent=2) + "\n")
        return ""

    def relock(self) -> tuple[int, str]:
        out = io.StringIO()
        return relock.main([str(self.root)], self.runner, out), out.getvalue()

    def lock_text(self) -> str:
        return (self.root / "flake.lock").read_text()

    def test_moves_only_nixpkgs(self) -> None:
        status, text = self.relock()
        self.assertEqual(status, 0, text)
        self.assertEqual(json.loads(self.lock_text()), lock(KIT))
        self.assertEqual(self.commands[0], ["nix", "flake", "lock", str(self.root), "--override-input", "nixpkgs",
                                            f"github:NixOS/nixpkgs/{KIT}"])
        self.assertEqual(self.commands[1], ["nix", "flake", "lock", str(self.root)])
        self.assertIn("nixpkgs aaaaaaaaaaaa -> bbbbbbbbbbbb", text)
        self.assertIn("Not committed", text)

    def test_already_at_the_kit_rev(self) -> None:
        (self.root / "flake.lock").write_text(json.dumps(lock(KIT)))
        self.assertEqual(self.relock()[0], 0)
        self.assertEqual(self.commands, [])

    def test_puts_the_lock_back(self) -> None:
        moved = lock(KIT, api_rev="2" * 40)
        cases = {
            "another input moved": ({"after_override": moved, "after_plain": moved}, "other than nixpkgs"),
            "the override failed": ({"after_override": None}, "--override-input nixpkgs"),
            "a plain lock moved the rev": ({"after_plain": lock(BRANCH_TIP)}, "did not stay"),
            "the plain lock failed": ({"after_plain": None}, "failed after the override"),
        }
        for name, (results, reason) in cases.items():
            with self.subTest(name):
                self.setUp()
                for attribute, value in results.items():
                    setattr(self, attribute, value)
                status, text = self.relock()
                self.assertEqual(status, 1)
                self.assertIn(reason, text)
                self.assertIn("flake.lock is back as it was", text)
                self.assertEqual(self.lock_text(), self.text)

    def test_missing_kit_file_changes_nothing(self) -> None:
        (self.root / "nix/kit-nixpkgs.json").unlink()
        status, text = self.relock()
        self.assertEqual((status, self.commands, self.lock_text()), (1, [], self.text))
        self.assertIn("nothing was changed", text)


if __name__ == "__main__":
    unittest.main()
