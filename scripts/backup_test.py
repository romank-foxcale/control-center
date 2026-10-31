#!/usr/bin/env python3
"""Test ./cc backup on a real Git CC: a restore from the backup gives the same history, untracked files and
.cc-local state, without data/ or bulky undeclared folders, plus every branch of each project clone and the changed
files of feature worktrees; a destination inside the CC, or inside the WSL distro, is refused before anything is
written; only the newest `keep` backups stay. With --push, a bare repository stands in for GitHub: the pushed tree
equals the local backup, cc-backup keeps one commit, the CC's HEAD, index, working tree and branches do not change,
and a remote that is not private, missing, or a file over the size limit stops the push but keeps the local backup."""

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
        git(self.root, "config", "user.name", "t")
        git(self.root, "config", "user.email", "t@t")
        (self.root / ".gitignore").write_text("/.cc-local/\n/repos/\n/worktrees/\n")
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
        clone = self.root / "repos/project/api"
        clone.mkdir(parents=True)
        git(clone, "init", "-q", "-b", "main")
        (clone / "app.py").write_text("v1")
        git(clone, "add", ".")
        git(clone, "commit", "-q", "-m", "api first")
        self.worktree = self.root / "worktrees/feat/api_wt"
        git(clone, "worktree", "add", "-q", "-b", "feat", str(self.worktree))
        (self.worktree / "app.py").write_text("v2")
        git(self.worktree, "commit", "-q", "-am", "unpushed feature commit")
        (self.worktree / "app.py").write_text("v3, not committed")
        (self.worktree / "notes.txt").write_text("untracked in the worktree")
        (self.root / "repos/project/empty").mkdir()
        git(self.root / "repos/project/empty", "init", "-q")  # a clone with no branch is skipped, not an error
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

        self.visibility: str | None = "PRIVATE\n"

    def runner(self, command: list[str]) -> str | None:
        return self.visibility if command[:3] == ["gh", "repo", "view"] else None

    def backup(self, *arguments: str, **env: str) -> tuple[int, str]:
        out = io.StringIO()
        with unittest.mock.patch.object(backup, "UNDECLARED_BYTES", 200):
            status = backup.main([str(self.root), *arguments], {"HOME": str(self.home), **env}, self.runner, out)
        return status, out.getvalue()

    def remote(self) -> Path:
        bare = self.temp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        git(self.root, "remote", "add", "origin", str(bare))
        return bare

    def cc_state(self) -> tuple[str, ...]:
        return (git(self.root, "rev-parse", "HEAD"), git(self.root, "ls-files", "--stage"),
                git(self.root, "status", "--porcelain", "--untracked-files=all"),
                git(self.root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"))

    def test_clones_and_worktrees(self) -> None:
        status, text = self.backup()
        self.assertEqual(status, 0, text)
        self.assertIn("project clones: 1 bundles; worktrees: 2 changed files", text)
        [made] = list((self.home / "b/my-cc").iterdir())
        self.assertEqual(sorted(path.name for path in (made / "repos").iterdir()), ["api.bundle"])
        restored = self.temp / "api"
        subprocess.run(["git", "clone", "-q", str(made / "repos/api.bundle"), str(restored)], check=True)
        self.assertEqual(git(restored, "rev-parse", "origin/feat"), git(self.worktree, "rev-parse", "HEAD"))
        with tarfile.open(made / "worktree-changes.tar.gz") as opened:
            files = {member.name: opened.extractfile(member).read().decode() for member in opened if member.isfile()}
        self.assertEqual(files, {"worktrees/feat/api_wt/app.py": "v3, not committed",
                                 "worktrees/feat/api_wt/notes.txt": "untracked in the worktree"})

    def test_push_sends_the_backup_and_leaves_the_cc_alone(self) -> None:
        bare = self.remote()
        before = self.cc_state()
        status, text = self.backup("--push")
        self.assertEqual(status, 0, text)
        self.assertIn("pushed: origin cc-backup", text)
        self.assertEqual(self.cc_state(), before)
        [made] = list((self.home / "b/my-cc").iterdir())
        pushed = self.temp / "pushed"
        pushed.mkdir()
        archive = subprocess.run(["git", "-C", str(bare), "archive", "cc-backup"], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as opened:
            opened.extractall(pushed, filter="data")
        self.assertEqual(hashes(pushed), hashes(made))
        self.assertEqual(git(bare, "rev-list", "--count", "cc-backup"), "1")
        first = git(bare, "rev-parse", "cc-backup")
        self.assertEqual(self.backup("--push")[0], 0)  # usually in the same second: it waits for its own stamp
        self.assertEqual(git(bare, "rev-list", "--count", "cc-backup"), "1")
        self.assertNotEqual(git(bare, "rev-parse", "cc-backup"), first)
        self.assertEqual(git(bare, "for-each-ref", "--format=%(refname)"), "refs/heads/cc-backup")
        self.assertEqual(self.cc_state(), before)
        # The first push left refs/remotes/origin/cc-backup; the next backup must not carry the previous one.
        self.assertTrue(git(self.root, "rev-parse", "--verify", "refs/remotes/origin/cc-backup"))
        newest = sorted((self.home / "b/my-cc").iterdir())[-1]
        self.assertNotIn("cc-backup", git(self.root, "bundle", "list-heads", str(newest / "cc.bundle")))

    def test_push_follows_the_setting(self) -> None:
        bare = self.remote()
        manifest = {"name": "my-cc", "backup": {"path": "~/b", "keep": 2, "push": True}}
        (self.root / "control-center.json").write_text(json.dumps(manifest))
        self.assertEqual(self.backup("--no-push")[0], 0)
        self.assertEqual(git(bare, "for-each-ref"), "")
        self.assertEqual(self.backup()[0], 0)
        self.assertEqual(git(bare, "for-each-ref", "--format=%(refname)"), "refs/heads/cc-backup")

    def test_refused_pushes_keep_the_local_backup(self) -> None:
        def refused(reason: str) -> None:
            status, text = self.backup("--push")
            self.assertEqual(status, 1, text)
            self.assertIn(reason, text)
            self.assertIn("the local backup above is complete", text)
            self.assertTrue(any(path.is_dir() and (path / "cc.bundle").is_file()
                                for path in (self.home / "b/my-cc").iterdir()))

        refused("this CC has no origin remote")
        bare = self.remote()
        for visibility in ("PUBLIC\n", "INTERNAL\n", None):
            self.visibility = visibility
            refused("is not a private GitHub repository")
        self.visibility = "PRIVATE\n"
        with unittest.mock.patch.object(backup, "MAX_FILE_BYTES", 10):
            refused("which GitHub refuses")
        self.assertEqual(git(bare, "for-each-ref"), "")

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
                    if not name.startswith(("repos/", "worktrees/", ".cc-local/data/", ".cc-local/opensearch/"))}
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
