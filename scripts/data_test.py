#!/usr/bin/env python3
"""Test ./cc data remove: it deletes a data entry (and with --mirror its mirrors and records) only after a person
types <cc name>/<entry name> on the terminal. Without a terminal, inside Claude Code, or with any other answer it
deletes nothing. One test drives the real script through a pseudo-terminal (on Windows: a new console whose input
buffer a helper types into, since Windows has no pty)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup  # noqa: E402
import data  # noqa: E402
import hostos  # noqa: E402

try:
    import pty
except ImportError:  # Windows: no termios
    pty = None

# Types a line into the input buffer of another process's console, as a person at that console would.
TYPER = r"""
import ctypes, sys, time
from ctypes import wintypes
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.restype = wintypes.HANDLE
class Key(ctypes.Structure):
    _fields_ = [("down", wintypes.BOOL), ("repeat", wintypes.WORD), ("key", wintypes.WORD), ("scan", wintypes.WORD),
                ("char", wintypes.WCHAR), ("state", wintypes.DWORD)]
class Record(ctypes.Structure):
    _fields_ = [("kind", wintypes.WORD), ("event", Key)]
for _ in range(100):
    if kernel32.AttachConsole(int(sys.argv[1])):
        break
    time.sleep(0.1)
else:
    sys.exit(3)
handle = kernel32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
enter = chr(13)
records = [Record(1, Key(down, 1, 0x0D if char == enter else 0, 0, char, 0))
           for char in sys.argv[2] + enter for down in (1, 0)]
written = wintypes.DWORD()
sys.exit(0 if kernel32.WriteConsoleInputW(handle, (Record * len(records))(*records), len(records), ctypes.byref(written)) else 4)
"""


class Remove(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.root = self.temp / "cc"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("/.cc-local/\n")
        (self.root / "control-center.json").write_text(json.dumps({
            "name": "my-cc", "backup": {"path": "~/b"}, "localData": [
                {"name": "emb", "path": ".cc-local/data/emb", "budgetGB": 1, "rebuild": None,
                 "rebuildNeeds": "GPU hours", "tags": ["irreplaceable"]},
                {"name": "features", "path": ".cc-local/data/features", "budgetGB": 1, "rebuild": "replay",
                 "rebuildNeeds": "nothing"}]}))
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t", "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
                       check=True)
        self.source = self.root / ".cc-local/data/emb"
        self.source.mkdir(parents=True)
        (self.source / "a.bin").write_bytes(b"a" * 2048)
        (self.root / ".cc-local/data/features").mkdir()
        (self.root / ".cc-local/data/features/f.bin").write_bytes(b"f")
        self.home = self.temp / "home"
        self.env = {"HOME": str(self.home)}
        backup.main([str(self.root)], self.env, lambda command: None, io.StringIO())
        self.mirror = hostos.long_path(self.home / "b/my-cc/data/emb")  # as output names it: no 8.3 short names
        self.record = self.home / "b/my-cc/data/emb.json"
        self.assertTrue(self.mirror.is_dir() and self.record.is_file())
        self.prompts: list[str] = []

    def remove(self, *arguments: str, answer: str | None = "my-cc/emb", **env: str) -> tuple[int, str]:
        def ask(prompt: str) -> str | None:
            self.prompts.append(prompt)
            return answer
        out = io.StringIO()
        status = data.main([str(self.root), "remove", *arguments], {**self.env, **env}, lambda command: None, out, ask)
        return status, out.getvalue()

    def test_the_exact_name_deletes_the_entry_only(self) -> None:
        status, text = self.remove("emb")
        self.assertEqual(status, 0, text)
        self.assertIn(".cc-local/data/emb (2.0 KiB)", text)
        self.assertEqual(self.prompts, ["Type my-cc/emb to delete it, anything else to stop: "])
        self.assertFalse(self.source.exists())
        self.assertTrue(self.mirror.is_dir() and self.record.is_file())  # the mirror stays without --mirror
        self.assertIn(f"the mirror stays: {self.mirror}", text)

    def test_mirror_goes_too_with_the_flag(self) -> None:
        status, text = self.remove("emb", "--mirror")
        self.assertEqual(status, 0, text)
        self.assertIn(f"and its mirror {self.mirror} (2.0 KiB)", text)
        self.assertFalse(self.source.exists() or self.mirror.exists() or self.record.exists())
        self.assertTrue((self.home / "b/my-cc").is_dir())  # the stamp backups are not touched

    def test_anything_else_deletes_nothing(self) -> None:
        for answer in ("emb", "my-cc/emb ", "MY-CC/EMB", "", "y", None):
            with self.subTest(answer=answer):
                status, text = self.remove("emb", "--mirror", answer=answer)
                self.assertEqual(status, 1, text)
                self.assertIn("nothing was deleted", text)
                self.assertTrue(self.source.is_dir() and self.mirror.is_dir() and self.record.is_file())

    def test_refused_inside_claude_code(self) -> None:
        status, text = self.remove("emb", CLAUDECODE="1")
        self.assertEqual(status, 1, text)
        self.assertIn("an agent cannot run this; the user runs it in their own terminal", text)
        self.assertEqual(self.prompts, [])
        self.assertTrue(self.source.is_dir())

    def test_unknown_and_absent_entries(self) -> None:
        self.assertIn("unknown localData entry: nope", self.remove("nope")[1])
        status, text = self.remove("features", answer="my-cc/features")
        self.assertEqual(status, 0, text)
        self.assertFalse((self.root / ".cc-local/data/features").exists())
        status, text = self.remove("features", answer="my-cc/features")
        self.assertEqual(status, 1, text)
        self.assertIn("nothing to delete", text)
        self.assertEqual(len(self.prompts), 1)

    def test_no_terminal_deletes_nothing(self) -> None:
        self.assertIsNone(data.ask_terminal("x: ", Path(self.temp / "no-such-tty")))
        script = Path(data.__file__)
        # No controlling terminal: a new session on POSIX, no console at all on Windows.
        result = subprocess.run([sys.executable, "-B", str(script), str(self.root), "remove", "emb"],
                                capture_output=True, text=True, stdin=subprocess.DEVNULL, start_new_session=True,
                                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
                                env={**os.environ, **self.env, "CLAUDECODE": ""})
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("no terminal to confirm on", result.stdout)
        self.assertTrue(self.source.is_dir())

    def test_typed_on_a_real_terminal(self) -> None:
        env = {**os.environ, **self.env}
        env.pop("CLAUDECODE", None)
        if pty is None:
            self.typed_on_a_windows_console(env)
            return
        pid, fd = pty.fork()
        if pid == 0:  # the child gets the pseudo-terminal as its controlling terminal, so /dev/tty is it
            os.execve(sys.executable, [sys.executable, "-B", data.__file__, str(self.root), "remove", "emb"], env)
        seen = b""
        try:
            while b"to stop: " not in seen:
                seen += os.read(fd, 1024)
        except OSError:  # the child exited without asking
            self.fail(seen.decode())
        os.write(fd, b"my-cc/emb\n")
        try:
            while chunk := os.read(fd, 1024):
                seen += chunk
        except OSError:
            pass
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0, seen.decode())
        self.assertIn(b"deleted", seen)
        self.assertFalse(self.source.exists())

    def typed_on_a_windows_console(self, env: dict[str, str]) -> None:
        """The script runs in a hidden console of its own; the answer reaches it only as keys typed at that console."""
        hidden = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=0)
        child = subprocess.Popen([sys.executable, "-B", data.__file__, str(self.root), "remove", "emb"], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=hidden)
        try:
            typer = subprocess.run([sys.executable, "-c", TYPER, str(child.pid), "my-cc/emb"],
                                   creationflags=subprocess.DETACHED_PROCESS, timeout=60)
            output, _ = child.communicate(timeout=60)
        finally:
            child.kill()
        self.assertEqual(typer.returncode, 0, "the helper could not type into the console")
        self.assertEqual(child.returncode, 0, output)
        self.assertIn("deleted", output)
        self.assertFalse(self.source.exists())


if __name__ == "__main__":
    unittest.main()
