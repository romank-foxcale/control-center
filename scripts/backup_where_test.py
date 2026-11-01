#!/usr/bin/env python3
"""Test ./cc backup where and ./cc backup restore on a fixture CC backed up by the real ./cc backup. where names every
backup folder, the GitHub link, each data entry's mirror state and restore command, and warns about a mirror that is
not complete, stale, on the WSL disk's drive or in OneDrive, and about large undeclared data. restore gives back
exactly the files of the last complete sync, never writes into a folder with files, and refuses unsafe mirrors."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup  # noqa: E402
import backup_where  # noqa: E402
import disk  # noqa: E402

REG = "\r\n".join([
    r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Lxss\{bbbb}",
    "    DistributionName    REG_SZ    Ubuntu",
    r"    BasePath    REG_SZ    \\?\C:\Users\me\AppData\Local\wsl\{bbbb}",
])


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(directory), *arguments],
                          capture_output=True, text=True, check=True).stdout.strip()


def hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def runner(command: list[str]) -> str | None:
    if command[:2] == ["wslpath", "-w"]:
        return "C:\\fake" + command[2].replace("/", "\\")
    if command[:2] == ["wslpath", "-u"] and command[2] == "C:\\Users\\me\\OneDrive":
        return "/mnt/c/Users/me/OneDrive"
    if command[:1] == ["reg.exe"]:
        return REG
    if command == ["cmd.exe", "/c", "echo %OneDrive%"]:
        return "C:\\Users\\me\\OneDrive"
    return None


class Where(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.root = self.temp / "cc"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        (self.root / ".gitignore").write_text("/.cc-local/\n")
        self.manifest = {"name": "my-cc", "backup": {"path": "~/b"}, "localData": [
            {"name": "embeddings", "path": ".cc-local/data/embeddings", "budgetGB": 6, "rebuild": None,
             "rebuildNeeds": "9 GPU hours on EC2", "tags": ["irreplaceable", "sensitive"]},
            {"name": "features", "path": ".cc-local/data/features", "budgetGB": 1, "rebuild": "flar-replay",
             "rebuildNeeds": "nothing"}]}
        self.write_manifest()
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "first")
        self.source = self.root / ".cc-local/data/embeddings"
        (self.source / "deep").mkdir(parents=True)
        (self.source / "a.parquet").write_bytes(b"a" * 100)
        (self.source / "deep/b.parquet").write_bytes(b"b" * 50)
        self.home = self.temp / "home"
        self.mirror = self.home / "b/my-cc/data/embeddings"

    def write_manifest(self) -> None:
        (self.root / "control-center.json").write_text(json.dumps(self.manifest))

    def backup(self, *arguments: str) -> str:
        out = io.StringIO()
        status = backup.main([str(self.root), *arguments], {"HOME": str(self.home)}, lambda command: None, out)
        self.assertIn(status, (0, 1), out.getvalue())
        return out.getvalue()

    def where(self, **env: str) -> tuple[int, str]:
        out = io.StringIO()
        status = backup_where.main([str(self.root), "where"], {"HOME": str(self.home), **env}, runner, out)
        return status, out.getvalue()

    def restore(self, *arguments: str) -> tuple[int, str]:
        out = io.StringIO()
        status = backup_where.main([str(self.root), "restore", *arguments], {"HOME": str(self.home)}, runner, out)
        return status, out.getvalue()

    def record_path(self) -> Path:
        return self.home / "b/my-cc/data/embeddings.json"

    def test_where_names_backups_mirrors_and_restore_commands(self) -> None:
        self.backup()
        status, text = self.where()
        self.assertEqual(status, 0, text)
        [stamp] = [path.name for path in (self.home / "b/my-cc").iterdir() if backup.STAMP.fullmatch(path.name)]
        self.assertIn(f"{self.home / 'b/my-cc'}: newest {stamp}", text)
        self.assertIn("restore: git clone cc.bundle <dir>", text)
        self.assertIn("embeddings (irreplaceable, sensitive): complete sync", text)
        self.assertIn(f"mirror {self.mirror}: 2 files, 150 B, 0 orphans", text)
        self.assertIn("compared by size and time, not checksums", text)
        self.assertIn("restore: ./cc backup restore embeddings", text)
        self.assertIn("features: not backed up; rebuild with ./cc run flar-replay, needs nothing", text)
        self.assertIn("this CC has no origin remote", text)
        self.assertIn("other sync clients", text)  # the kit cannot see them, and says so

    def test_where_finds_every_destination_and_github(self) -> None:
        self.backup()
        self.backup("--to", str(self.temp / "elsewhere"))
        git(self.root, "remote", "add", "origin", "git@github.com:me/my-cc.git")
        status, text = self.where()
        self.assertIn(str(self.temp / "elsewhere/my-cc"), text)
        self.assertIn(f"mirror {self.temp / 'elsewhere/my-cc/data/embeddings'}", text)
        self.assertIn("https://github.com/me/my-cc/tree/cc-backup", text)
        self.assertIn("no cc-backup branch known here", text)

    def test_github_links(self) -> None:
        for url in ("https://github.com/me/my-cc.git", "https://github.com/me/my-cc", "git@github.com:me/my-cc.git",
                    "ssh://git@github.com/me/my-cc.git"):
            with self.subTest(url=url):
                self.assertEqual(backup_where.github_link(url), "https://github.com/me/my-cc/tree/cc-backup")
        for url in ("/home/me/Projects/my-cc", "https://gitlab.com/me/my-cc.git", "git@github-work:me/my-cc.git"):
            with self.subTest(url=url):
                self.assertIsNone(backup_where.github_link(url))
        self.backup()
        git(self.root, "remote", "add", "origin", "/home/me/Projects/my-cc")
        self.assertIn("origin /home/me/Projects/my-cc is not a GitHub URL", self.where()[1])

    def test_missing_incomplete_and_stale_mirrors_are_warned(self) -> None:
        status, text = self.where()
        self.assertEqual(status, 1, text)
        self.assertIn("warning: embeddings has no complete mirror", text)
        self.backup()
        record = json.loads(self.record_path().read_text())
        self.record_path().write_text(json.dumps({**record, "state": "incomplete"}))
        status, text = self.where()
        self.assertEqual(status, 0, text)  # the last complete sync is still there
        self.assertIn("the newest sync stopped before it was complete", text)
        record["complete"]["time"] = "20200101-000000"
        self.record_path().write_text(json.dumps(record))
        status, text = self.where()
        self.assertEqual(status, 0, text)
        self.assertIn("warning: embeddings: the last complete sync is older than 7 days", text)
        self.record_path().write_text("{broken")
        status, text = self.where()
        self.assertEqual(status, 1, text)
        self.assertIn("record is unreadable", text)

    def test_drive_and_onedrive_warnings(self) -> None:
        env = {"WSL_DISTRO_NAME": "Ubuntu"}
        warnings = backup_where.place_warnings(Path("/mnt/c/Users/me/cc-backups/my-cc/data/embeddings"), env, runner)
        self.assertEqual(warnings, ["on drive C:, the same drive as the WSL disk file; set backup.dataPath to "
                                    "another drive to survive a drive failure"])
        warnings = backup_where.place_warnings(Path("/mnt/c/Users/me/OneDrive/cc/data/embeddings"), env, runner)
        self.assertIn("inside OneDrive (C:\\Users\\me\\OneDrive), so it leaves this machine", warnings)
        self.assertEqual(backup_where.place_warnings(Path("/mnt/e/cc/data/embeddings"), env, runner), [])
        self.assertEqual(backup_where.place_warnings(Path("/mnt/c/x"), {}, runner), [])  # not WSL: nothing to compare

    def test_windows_paths_on_wsl(self) -> None:
        self.backup()
        status, text = self.where(WSL_DISTRO_NAME="Ubuntu")
        self.assertIn(f"C:\\fake{str(self.mirror).replace('/', chr(92))}", text)

    def test_large_undeclared_data_is_warned(self) -> None:
        (self.root / ".cc-local/opensearch").mkdir(parents=True)
        (self.root / ".cc-local/opensearch/index").write_bytes(b"x" * 300)
        (self.root / ".cc-local/runs").mkdir()
        (self.root / ".cc-local/runs/big").write_bytes(b"x" * 300)  # kit state is backed up: no warning
        self.backup()
        with unittest.mock.patch.object(backup_where, "LARGE_UNDECLARED", 200):
            text = self.where()[1]
        self.assertIn("warning: .cc-local/opensearch (300 B) is not declared in localData and is in no backup", text)
        self.assertNotIn(".cc-local/runs", text)

    def test_restore_gives_the_last_complete_sync(self) -> None:
        self.backup()
        (self.source / "a.parquet").unlink()
        (self.source / "c.parquet").write_bytes(b"c")
        self.backup()  # a.parquet stays in the mirror as an orphan
        expected = hashes(self.source)
        status, text = self.restore("embeddings")
        self.assertEqual(status, 1, text)
        self.assertIn("has files; restore writes only into a missing or empty folder", text)
        shutil.rmtree(self.source)
        status, text = self.restore("embeddings")
        self.assertEqual(status, 0, text)
        self.assertEqual(hashes(self.source), expected)
        self.assertIn("restored 2 files", text)
        shutil.rmtree(self.source)
        self.source.mkdir()
        status, text = self.restore("embeddings", "--all")
        self.assertEqual(status, 0, text)
        self.assertEqual(set(hashes(self.source)), {"a.parquet", "c.parquet", "deep/b.parquet"})
        self.assertEqual((self.mirror / "c.parquet").stat().st_mtime_ns // 10**9,
                         (self.source / "c.parquet").stat().st_mtime_ns // 10**9)

    def test_restore_uses_the_newest_complete_mirror(self) -> None:
        self.backup()
        (self.source / "new.parquet").write_bytes(b"n")
        self.backup("--to", str(self.temp / "elsewhere"))
        older = json.loads(self.record_path().read_text())
        older["complete"]["time"] = "20200101-000000"
        self.record_path().write_text(json.dumps(older))
        shutil.rmtree(self.source)
        status, text = self.restore("embeddings")
        self.assertEqual(status, 0, text)
        self.assertIn(f"from {self.temp / 'elsewhere/my-cc/data/embeddings'}", text)
        self.assertTrue((self.source / "new.parquet").is_file())

    def test_a_first_sync_that_never_completed(self) -> None:
        self.backup()
        self.record_path().write_text(json.dumps({"state": "incomplete", "started": "20261101-000000", "complete": None}))
        status, text = self.where()
        self.assertEqual(status, 1, text)
        self.assertIn("a sync started 20261101-000000 and never completed", text)
        self.assertIn("embeddings has no complete mirror", self.restore("embeddings")[1])

    def test_restore_refuses_unsafe_or_missing_mirrors(self) -> None:
        status, text = self.restore("features")
        self.assertEqual(status, 1, text)
        self.assertIn("features has no complete mirror", text)
        self.assertIn("unknown localData entry", self.restore("nope")[1])
        self.backup()
        shutil.rmtree(self.source)
        (self.mirror / "a.parquet").unlink()
        status, text = self.restore("embeddings")
        self.assertEqual(status, 1, text)
        self.assertIn("missing in the mirror: a.parquet", text)
        self.assertFalse(self.source.exists())  # nothing written
        (self.mirror / "a.parquet").symlink_to(self.temp / "outside")
        status, text = self.restore("embeddings")
        self.assertEqual(status, 1, text)
        self.assertIn("a.parquet is a symlink", text)
        self.assertFalse(self.source.exists())

    def test_doctor_summary(self) -> None:
        self.assertEqual(backup_where.mirror_summary(self.root, self.manifest, {"HOME": str(self.home)}, runner),
                         ["  embeddings: no complete mirror"])
        self.backup()
        [line] = backup_where.mirror_summary(self.root, self.manifest, {"HOME": str(self.home)}, runner)
        self.assertRegex(line, r"^  embeddings: complete sync \d{8}-\d{6}, 0 days old, 2 files, 150 B$")
        lines = disk.data_section(self.root, self.manifest, {"HOME": str(self.home)}, runner)
        self.assertEqual(lines[-1], "  details, links and restore commands: ./cc backup where")


if __name__ == "__main__":
    unittest.main()
