#!/usr/bin/env python3
"""Test ./cc backup on a real Git CC: a restore from the backup gives the same history, untracked files and
.cc-local state, without data/ or bulky undeclared folders; a destination inside the CC, or inside the WSL
distro, is refused before anything is written; only the newest `keep` backups stay."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup  # noqa: E402


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(directory), *arguments],
                          capture_output=True, text=True, check=True).stdout.strip()


def hashes(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file() and ".git" not in path.parts}


class Backup(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.root = self.temp / "cc"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        (self.root / ".gitignore").write_text("/.cc-local/\n/repos/\n")
        (self.root / "control-center.json").write_text(json.dumps({"name": "my-cc", "backup": {"path": "~/b", "keep": 2}}))
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "first")
        git(self.root, "switch", "-q", "-c", "side")
        (self.root / "side.txt").write_text("only on a branch")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "side")
        git(self.root, "switch", "-q", "main")
        (self.root / "plans").mkdir()
        (self.root / "plans/active.md").write_text("untracked plan")
        (self.root / "control-center.json").write_text(json.dumps({"name": "my-cc", "backup": {"path": "~/b", "keep": 2}}) + "\n")
        (self.root / "staged.py").write_text("added to the index, never committed")
        git(self.root, "add", "staged.py")
        (self.root / "repos").mkdir()
        (self.root / "repos/big.bin").write_bytes(b"ignored clone")
        local = self.root / ".cc-local"
        for folder in ("runs/r1", "threads", "saved", "data/embeddings", "opensearch"):
            (local / folder).mkdir(parents=True)
        (local / "runs/r1/events.jsonl").write_text("event\n" * 50)  # kit state over the size limit is still kept
        (local / "threads/api-1.json").write_text("{}")
        (local / "saved/fix.patch").write_text("patch")
        (local / "prs-seen.json").write_text("{}")
        (local / "data/embeddings/a.parquet").write_bytes(b"x" * 100)
        (local / "opensearch/index").write_bytes(b"x" * 300)
        self.home = self.temp / "home"

    def backup(self, *arguments: str, **env: str) -> tuple[int, str]:
        out = io.StringIO()
        with unittest.mock.patch.object(backup, "UNDECLARED_BYTES", 200):
            status = backup.main([str(self.root), *arguments], {"HOME": str(self.home), **env}, lambda _: None, out)
        return status, out.getvalue()

    def test_restore_gives_the_same_cc(self) -> None:
        status, text = self.backup()
        self.assertEqual(status, 0, text)
        [made] = list((self.home / "b/my-cc").iterdir())
        restored = self.temp / "restored"
        subprocess.run(["git", "clone", "-q", str(made / "cc.bundle"), str(restored)], check=True)
        self.assertEqual(git(restored, "log", "--format=%s", "origin/side"), "side\nfirst")
        for archive in ("changes.tar.gz", "state.tar.gz"):
            with tarfile.open(made / archive) as opened:
                opened.extractall(restored, filter="data")
        expected = {name: digest for name, digest in hashes(self.root).items()
                    if not name.startswith(("repos/", ".cc-local/data/", ".cc-local/opensearch/"))}
        self.assertEqual({name: digest for name, digest in hashes(restored).items() if name != "side.txt"}, expected)
        self.assertIn("not backed up: .cc-local/data/", text)
        self.assertIn("not backed up: .cc-local/opensearch: 300 B", text)

    def test_keeps_the_newest(self) -> None:
        folder = self.home / "b/my-cc"
        for stamp in ("20200101-000000", "20210101-000000", "20220101-000000"):
            (folder / stamp).mkdir(parents=True)
        (folder / "notes").mkdir()
        self.backup()
        names = sorted(path.name for path in folder.iterdir())
        self.assertEqual(names[0], "20220101-000000")
        self.assertEqual(len(names), 3)  # two backups kept, and the folder that is not a backup
        self.assertEqual(names[-1], "notes")

    def test_refused_destinations_write_nothing(self) -> None:
        for to, env, reason in ((str(self.root / "b"), {}, "inside the CC"),
                                ("/home/u/b", {"WSL_DISTRO_NAME": "Ubuntu"}, "inside this WSL distro"),
                                ("/mnt/wsl/b", {"WSL_DISTRO_NAME": "Ubuntu"}, "inside this WSL distro")):
            with self.subTest(to=to):
                before = hashes(self.temp)
                status, text = self.backup("--to", to, **env)
                self.assertEqual(status, 1)
                self.assertIn(reason, text)
                self.assertIn("nothing was written", text)
                self.assertEqual(hashes(self.temp), before)
        (self.root / "control-center.json").write_text(json.dumps({"name": "my-cc"}))
        self.assertIn("pass --to DIR or set backup.path", self.backup()[1])


if __name__ == "__main__":
    unittest.main()
