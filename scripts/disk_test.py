#!/usr/bin/env python3
"""Test ./cc doctor --disk on a fixture CC with fake nix, df, reg.exe and wslpath output. The report changes no file,
never shows a token from nix.conf, keeps kit state apart from data, picks the disk file of this distro only, and
never calls data safe to delete."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import disk  # noqa: E402

TOKEN = "ghp_CANARY0123456789"
REG = "\r\n".join([
    r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Lxss\{aaaa}",
    "    DistributionName    REG_SZ    docker-desktop",
    r"    BasePath    REG_SZ    C:\Users\me\AppData\Local\Docker\wsl\main",
    "",
    r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Lxss\{bbbb}",
    "    DistributionName    REG_SZ    Ubuntu",
    r"    BasePath    REG_SZ    \\?\C:\Users\me\AppData\Local\wsl\{bbbb}",
])


def snapshot(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


class Report(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.root = self.temp / "cc"
        local = self.root / ".cc-local"
        for folder in ("runs/r1", "verified", "data/embeddings", "data/stray", "opensearch", "flar"):
            (local / folder).mkdir(parents=True)
        (local / "runs/r1/events.jsonl").write_bytes(b"x" * 1000)
        (local / "prs-seen.json").write_bytes(b"x" * 24)
        (local / "data/embeddings/a.parquet").write_bytes(b"x" * 3000)
        (local / "data/stray/b.bin").write_bytes(b"x" * 500)
        (local / "opensearch/index").write_bytes(b"x" * 2000)
        (local / "flar/analyse.py").write_text("print(1)\n")
        (self.root / "nix").mkdir()
        (self.root / "nix/kit-nixpkgs.json").write_text(json.dumps({"rev": "b" * 40}))
        (self.root / "flake.lock").write_text(json.dumps({"nodes": {"nixpkgs": {"locked": {"rev": "a" * 40}}}}))
        (self.root / "control-center.json").write_text(json.dumps({
            "localData": [{"name": "embeddings", "path": ".cc-local/data/embeddings", "budgetGB": 2e-6,
                           "rebuild": "embed", "rebuildNeeds": "EC2 GPU host"}],
            "localCaches": [{"name": "uv", "path": "$XDG_CACHE_HOME/uv"},
                            {"name": "gate", "path": "%LOCALAPPDATA%\\di-cc"}],
        }))
        self.home = self.temp / "home"
        (self.home / ".config/nix").mkdir(parents=True)
        (self.home / ".config/nix/nix.conf").write_text(f"access-tokens = github.com={TOKEN}\nmin-free = 1G\n")
        (self.home / ".cache/uv").mkdir(parents=True)
        (self.home / ".cache/uv/wheel").write_bytes(b"x" * 4096)
        self.vhdx = self.temp / "mnt/c/wsl/ext4.vhdx"
        self.vhdx.parent.mkdir(parents=True)
        self.vhdx.write_bytes(b"x" * 5 * 2**20)
        self.windows = self.temp / "mnt/c/local"
        (self.windows / "di-cc").mkdir(parents=True)
        self.commands: list[list[str]] = []
        self.pushed: str | None = None

    def runner(self, command: list[str]) -> str | None:
        self.commands.append(command)
        if command[:2] == ["nix", "path-info"]:
            return json.dumps({"/nix/store/a": {"narSize": 2**30}, "/nix/store/b": {"narSize": 2**30}})
        if command[:2] == ["nix-store", "--gc"]:
            return (f'"{self.root}/result" -> /nix/store/aaa-cc\n'
                    f'"{self.temp}/other_CC/result" -> /nix/store/bbb-other\n'
                    f'"{self.root}-old/result" -> /nix/store/ddd-sibling\n'
                    '"/proc/1/maps" -> /nix/store/ccc\n')
        if command[0] == "cmd.exe":
            return "C:\\Users\\me\\AppData\\Local\r\n"
        if command[:2] == ["wslpath", "-u"] and command[2] == "C:\\Users\\me\\AppData\\Local":
            return f"{self.windows}\n"
        if command[:2] == ["wslpath", "-w"]:
            return "C:\\Users\\me\\AppData\\Local\\di-cc\n"
        if command[0] == "git" and command[-1] == "refs/remotes/origin/cc-backup":
            return self.pushed
        if "powershell.exe" in command:
            return "3221225472" if "CC_PATH=C:\\Users\\me\\AppData\\Local\\di-cc" in command else None
        if command[:3] == ["nix", "config", "show"]:
            return {"min-free": "1073741824\n", "max-free": f"{TOKEN}\n"}.get(command[3], "false\n")
        if command[0] == "df":
            return "Avail\n" + str(7 * 2**30) + "\n"
        if command[0] == "reg.exe":
            return REG
        if command[0] == "wslpath":
            return f"{self.vhdx}\n" if "{bbbb}" in command[2] else "/nowhere\n"
        return None

    def report(self, **env: str) -> str:
        out = io.StringIO()
        disk.report(self.root, {"HOME": str(self.home), "XDG_CACHE_HOME": str(self.home / ".cache"), **env}, self.runner, out)
        return out.getvalue()

    def test_changes_no_file_and_runs_no_mutating_command(self) -> None:
        before = snapshot(self.temp)
        self.report(WSL_DISTRO_NAME="Ubuntu")
        self.assertEqual(snapshot(self.temp), before)
        for command in self.commands:
            self.assertNotIn(command[:2], (["nix", "build"], ["nix", "flake"], ["nix-collect-garbage", "-d"]))
            self.assertNotIn("--delete", command)

    def test_never_shows_a_token(self) -> None:
        text = self.report()
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("access-tokens", text)
        self.assertIn("  min-free: 1073741824 (user 1G, daemon", text)
        self.assertIn("  max-free: unknown", text)

    def test_state_data_and_undeclared(self) -> None:
        with unittest.mock.patch.object(disk, "UNDECLARED_BYTES", 1000):
            text = self.report()
        self.assertIn("  kit state: 1.0 KiB (back it up; never delete)", text)  # 1000 + 24 bytes
        self.assertIn("  data embeddings: 2.9 KiB, over its 2e-06 GB budget; rebuild: ./cc run embed; "
                      "a rebuild needs: EC2 GPU host", text)
        self.assertIn("  undeclared opensearch: 2.0 KiB;", text)
        self.assertNotIn("undeclared data/stray", text)  # 500 B, under the threshold
        self.assertNotIn("undeclared runs", text)
        self.assertIn("  script outside a repository: flar/analyse.py", text)
        self.assertNotIn("safe to delete", text.lower())

    def test_roots_of_this_cc_only(self) -> None:
        text = self.report()
        self.assertIn("  result -> /nix/store/aaa-cc", text)
        self.assertNotIn("bbb-other", text)
        self.assertNotIn("ddd-sibling", text)  # cc-old/ starts with the same characters as cc/
        self.assertNotIn("/proc", text)

    def test_store_cache_and_pin(self) -> None:
        text = self.report()
        self.assertIn("  size: 2.0 GiB in 2 paths", text)
        self.assertIn("  uv: 4.0 KiB (", text)
        self.assertIn("aaaaaaaaaaaa differs from the kit's recommended bbbbbbbbbbbb", text)
        (self.root / "nix/kit-nixpkgs.json").write_text(json.dumps({"rev": "a" * 40}))
        self.assertIn("aaaaaaaaaaaa, the kit's recommended rev", self.report())
        (self.root / "nix/kit-nixpkgs.json").unlink()
        self.assertIn("aaaaaaaaaaaa; nix/kit-nixpkgs.json is missing", self.report())

    def test_backup_age(self) -> None:
        self.assertIn("  no backup.path in control-center.json", self.report())
        manifest = json.loads((self.root / "control-center.json").read_text())
        (self.root / "control-center.json").write_text(json.dumps({**manifest, "name": "my-cc", "backup": {"path": "$HOME/b"}}))
        self.assertIn("  none yet in $HOME/b", self.report())
        for stamp in ("20261001-090000", "20261030-120000", ".20261031-000000.partial"):
            (self.home / "b/my-cc" / stamp).mkdir(parents=True)
        self.assertIn(f"  newest 20261030-120000, ", self.report())
        self.assertIn(f"2 kept in {self.home / 'b/my-cc'}", self.report())
        self.assertIn("  on GitHub: no cc-backup branch known here", self.report())
        self.pushed = "Backup 20261031-120000\n"
        self.assertIn("  on GitHub: Backup 20261031-120000 in origin's cc-backup branch", self.report())

    def test_windows_cache_is_measured_by_windows(self) -> None:
        self.assertIn("  gate: %LOCALAPPDATA%\\di-cc cannot be resolved here", self.report())
        self.assertIn(f"  gate: 3.0 GiB ({self.windows / 'di-cc'})", self.report(WSL_DISTRO_NAME="Ubuntu"))

    def test_wsl_disk_of_this_distro(self) -> None:
        self.assertNotIn("WSL distro", self.report())
        text = self.report(WSL_DISTRO_NAME="Ubuntu")
        self.assertIn("  disk file: 5.0 MiB (C:\\Users\\me\\AppData\\Local\\wsl\\{bbbb}\\ext4.vhdx)", text)
        self.assertIn("  free inside the distro: 7.0 GiB", text)
        self.assertIn("  free on the Windows drive: 7.0 GiB", text)
        self.assertIn("does not shrink", text)
        self.assertNotIn("compact", text)
        self.assertIn("  disk file: not found", self.report(WSL_DISTRO_NAME="Debian"))

    def test_slow_walk_says_so(self) -> None:
        with unittest.mock.patch.object(disk, "WALK_SECONDS", -1.0):
            text = self.report()
        self.assertIn("more than", text)


if __name__ == "__main__":
    unittest.main()
