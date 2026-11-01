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
import os
import shutil
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

    # Irreplaceable data: one mirror per entry outside the stamp folders, never deleted from, with a record.

    def tag(self, tags: list[str], **entry: object) -> None:
        manifest = {"name": "my-cc", "backup": {"path": "~/b", "keep": 2},
                    "localData": [{"name": "embeddings", "path": ".cc-local/data/embeddings", "budgetGB": 6,
                                   "rebuild": None, "rebuildNeeds": "9 GPU hours", "tags": tags, **entry}]}
        (self.root / "control-center.json").write_text(json.dumps(manifest))

    @property
    def source(self) -> Path:
        return self.root / ".cc-local/data/embeddings"

    @property
    def mirror(self) -> Path:
        return self.home / "b/my-cc/data/embeddings"

    def record(self) -> dict:
        return json.loads((self.home / "b/my-cc/data/embeddings.json").read_text())

    def test_mirror_copies_changes_and_never_deletes(self) -> None:
        self.tag(["irreplaceable"])
        status, text = self.backup()
        self.assertEqual(status, 0, text)
        self.assertIn("data embeddings: mirrored 1 files, 1 copied", text)
        self.assertEqual(hashes(self.mirror), hashes(self.source))
        self.assertEqual(self.record()["state"], "complete")
        self.assertEqual(set(self.record()["complete"]["files"]), {"a.parquet"})
        self.assertIn("data embeddings: mirrored 1 files, 0 copied", self.backup()[1])
        (self.source / "a.parquet").write_bytes(b"y" * 101)
        (self.source / "deep/b.parquet").parent.mkdir()
        (self.source / "deep/b.parquet").write_bytes(b"b")
        self.assertIn("mirrored 2 files, 2 copied", self.backup()[1])
        (self.source / "a.parquet").unlink()
        status, text = self.backup()
        self.assertEqual(status, 0, text)
        self.assertIn("mirrored 1 files, 0 copied, 1 orphans (101 B)", text)
        self.assertEqual((self.mirror / "a.parquet").read_bytes(), b"y" * 101)  # never deleted
        self.assertEqual(set(self.record()["complete"]["files"]), {"deep/b.parquet"})
        # prune keeps 2 stamp folders and never touches the mirror next to them
        self.assertEqual(sorted(path.name for path in (self.home / "b/my-cc").iterdir())[-1:], ["data"])
        self.assertEqual(len([path for path in (self.home / "b/my-cc").iterdir() if backup.STAMP.fullmatch(path.name)]), 2)

    def test_size_or_whole_second_change_copies(self) -> None:
        self.tag(["irreplaceable"])
        self.backup()
        path = self.source / "a.parquet"
        later = (path.stat().st_mtime_ns // 10**9 + 5) * 10**9  # a whole second
        path.write_bytes(b"z" * 100)  # same size, later time
        os.utime(path, ns=(later, later))
        self.assertIn("1 copied", self.backup()[1])
        path.write_bytes(b"z" * 99)  # other size, same time
        os.utime(path, ns=(later, later))
        self.assertIn("1 copied", self.backup()[1])
        self.assertEqual((self.mirror / "a.parquet").read_bytes(), b"z" * 99)
        os.utime(path, ns=(later + 400_000_000,) * 2)  # sub-second only: /mnt/c drops it
        self.assertIn("0 copied", self.backup()[1])

    def test_only_irreplaceable_entries_are_mirrored(self) -> None:
        for tags in ([], ["sensitive"]):
            with self.subTest(tags=tags):
                self.tag(tags)
                status, text = self.backup()
                self.assertEqual(status, 0, text)
                self.assertNotIn("data embeddings", text)
                self.assertFalse(self.mirror.exists())

    def test_data_path_moves_the_mirror(self) -> None:
        manifest = json.loads(json.dumps({"name": "my-cc", "backup": {"path": "~/b", "dataPath": "~/d"}}))
        self.tag(["irreplaceable", "sensitive"])
        manifest["localData"] = json.loads((self.root / "control-center.json").read_text())["localData"]
        (self.root / "control-center.json").write_text(json.dumps(manifest))
        self.assertEqual(self.backup()[0], 0)
        self.assertTrue((self.home / "d/my-cc/data/embeddings/a.parquet").is_file())
        self.assertFalse(self.mirror.exists())

    def test_quiesce_skips_unless_pgrep_finds_nothing(self) -> None:
        self.tag(["irreplaceable"], quiesce=["opensearch"])
        for code, reason in ((0, "opensearch is running"), (2, "cannot check for opensearch (pgrep exit 2)"),
                             (None, "cannot check for opensearch (pgrep not found)")):
            with self.subTest(code=code), unittest.mock.patch.object(backup, "pgrep", return_value=code):
                status, text = self.backup()
                self.assertEqual(status, 1, text)
                self.assertIn(f"error: data embeddings not mirrored: {reason}", text)
                self.assertFalse(self.mirror.exists())
                self.assertIn("backup: ", text)  # the stamp backup is still complete
        with unittest.mock.patch.object(backup, "pgrep", return_value=1):
            self.assertEqual(self.backup()[0], 0)

    def test_writer_during_the_copy_leaves_it_incomplete(self) -> None:
        self.tag(["irreplaceable"], quiesce=["opensearch"])
        with unittest.mock.patch.object(backup, "pgrep", return_value=1):
            self.backup()
        first = self.record()["complete"]
        with unittest.mock.patch.object(backup, "pgrep", side_effect=[1, 0]):
            status, text = self.backup()
        self.assertEqual(status, 1, text)
        self.assertIn("error: data embeddings not mirrored: during the copy: opensearch is running", text)
        self.assertEqual(self.record()["state"], "incomplete")
        self.assertEqual(self.record()["complete"], first)  # the last complete sync is kept

    def test_windows_writers_are_checked_on_wsl(self) -> None:
        with unittest.mock.patch.object(backup, "pgrep", return_value=1), \
                unittest.mock.patch.object(backup, "windows_command_lines", return_value=["py.exe ai-vectors-load x"]):
            self.assertEqual(backup.busy(["ai-vectors-load"], {"WSL_DISTRO_NAME": "Ubuntu"}),
                             "ai-vectors-load is running (Windows)")
            self.assertIsNone(backup.busy(["ai-vectors-load"], {}))
        with unittest.mock.patch.object(backup, "pgrep", return_value=1), \
                unittest.mock.patch.object(backup, "windows_command_lines", return_value=None):
            self.assertEqual(backup.busy(["x"], {"WSL_DISTRO_NAME": "Ubuntu"}), "cannot list Windows processes")

    def test_failure_halfway_leaves_it_incomplete(self) -> None:
        self.tag(["irreplaceable"])
        self.backup()
        first = self.record()["complete"]
        for name in ("b", "c"):
            (self.source / f"{name}.parquet").write_bytes(name.encode())
        real = backup.shutil.copy2
        calls = []

        def failing(source, target):
            calls.append(source)
            if len(calls) == 2:
                Path(target).write_bytes(b"half")  # a partial copy, as a full disk leaves it
                raise OSError(28, "No space left on device")
            return real(source, target)

        with unittest.mock.patch.object(backup.shutil, "copy2", side_effect=failing):
            status, text = self.backup()
        self.assertEqual(status, 1, text)
        self.assertIn("error: data embeddings not mirrored: No space left on device", text)
        self.assertEqual(self.record()["state"], "incomplete")
        self.assertEqual(self.record()["complete"], first)
        self.assertEqual([path.name for path in self.mirror.rglob("*.cc-tmp")], [])

    def test_unsafe_sources_are_skipped(self) -> None:
        self.tag(["irreplaceable"])
        (self.source / "A.parquet").write_bytes(b"upper")
        (self.source / "a.PARQUET").write_bytes(b"lower")
        status, text = self.backup()
        self.assertEqual(status, 1, text)
        self.assertIn("names differ only in case", text)
        self.assertFalse(self.mirror.exists())
        for path in (self.source / "A.parquet", self.source / "a.PARQUET"):
            path.unlink()
        outside = self.temp / "outside.txt"
        outside.write_text("not data")
        (self.source / "link").symlink_to(outside)
        status, text = self.backup()
        self.assertEqual(status, 0, text)
        self.assertFalse((self.mirror / "link").exists())
        self.assertNotIn("link", self.record()["complete"]["files"])
        shutil.rmtree(self.source)
        status, text = self.backup()
        self.assertEqual(status, 1, text)
        self.assertIn("error: data embeddings not mirrored: .cc-local/data/embeddings is missing", text)

    def test_too_little_space_skips(self) -> None:
        self.tag(["irreplaceable"])
        usage = shutil.disk_usage(self.temp)._replace(free=50)
        with unittest.mock.patch.object(backup.shutil, "disk_usage", return_value=usage):
            status, text = self.backup()
        self.assertEqual(status, 1, text)
        self.assertIn("needs 100 B, 50 B free", text)
        self.assertFalse(self.mirror.exists())

    def test_a_broken_record_is_reported_and_replaced(self) -> None:
        self.tag(["irreplaceable"])
        self.backup()
        (self.home / "b/my-cc/data/embeddings.json").write_text('{"state": "comp')
        status, text = self.backup()
        self.assertEqual(status, 0, text)
        self.assertIn("previous record was unreadable", text)
        self.assertEqual(self.record()["state"], "complete")

    def test_drive_must_be_mounted(self) -> None:
        mounts = "C:\\134 /mnt/c 9p rw 0 0\nnone /mnt/wsl tmpfs rw 0 0\n"
        self.assertTrue(backup.drive_mounted(Path("/mnt/c/Users/me/cc"), mounts))
        self.assertFalse(backup.drive_mounted(Path("/mnt/e/cc"), mounts))
        self.assertFalse(backup.drive_mounted(Path("/mnt/wsl/cc"), mounts))
        self.assertTrue(backup.drive_mounted(Path("/home/me/cc"), mounts))  # not a drive path: nothing to check

    def test_data_never_reaches_the_push(self) -> None:
        self.remote()
        self.tag(["irreplaceable", "sensitive"])
        marker = b"SENSITIVE-MARKER-7f3a"
        (self.source / "secret.bin").write_bytes(marker * 3)
        status, text = self.backup("--push")
        self.assertEqual(status, 0, text)
        bare = self.temp / "remote.git"
        blobs = subprocess.run(["git", "-C", str(bare), "rev-list", "--objects", "cc-backup"], capture_output=True,
                               text=True, check=True).stdout.split()
        pushed = b"".join(subprocess.run(["git", "-C", str(bare), "cat-file", "-p", obj], capture_output=True).stdout
                          for obj in blobs if len(obj) == 40)
        found = [marker in pushed]
        with tarfile.open(fileobj=io.BytesIO(subprocess.run(["git", "-C", str(bare), "archive", "cc-backup"],
                                                            capture_output=True, check=True).stdout)) as outer:
            for member in outer:
                data = outer.extractfile(member).read() if member.isfile() else b""
                if member.name.endswith(".tar.gz"):
                    with tarfile.open(fileobj=io.BytesIO(data)) as inner:
                        found += [marker in inner.extractfile(m).read() for m in inner if m.isfile()]
                if member.name.endswith(".bundle"):
                    clone = self.temp / f"scan-{len(found)}"
                    (self.temp / "b.bundle").write_bytes(data)
                    subprocess.run(["git", "clone", "-q", "--mirror", str(self.temp / "b.bundle"), str(clone)], check=True)
                    found.append(marker.decode() in subprocess.run(
                        ["git", "-C", str(clone), "log", "--all", "-p", "--binary"], capture_output=True, text=True,
                        errors="replace").stdout)
        self.assertEqual(found.count(True), 0)
        self.assertGreater(len(found), 3)
        self.assertTrue((self.mirror / "secret.bin").is_file())

    def test_locations_are_remembered(self) -> None:
        self.tag(["irreplaceable"])
        self.backup()
        self.backup("--to", str(self.temp / "elsewhere"))
        locations = json.loads((self.root / ".cc-local/hooks/backup-locations.json").read_text())
        self.assertEqual(locations["backups"], sorted([str((self.home / "b/my-cc").resolve()),
                                                       str((self.temp / "elsewhere/my-cc").resolve())]))
        self.assertEqual(locations["mirrors"]["embeddings"], sorted([
            str(self.mirror.resolve()), str((self.temp / "elsewhere/my-cc/data/embeddings").resolve())]))

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
