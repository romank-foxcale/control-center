#!/usr/bin/env python3
"""Test scripts/hostos.py on the real OS: liveness without side effects, tree stop through a shim, path spelling."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostos  # noqa: E402

SLEEP = [sys.executable, "-c", "import time; time.sleep(120)"]
# A parent that starts a long child, prints the child's pid, then waits for it: what an npm .cmd shim does.
SPAWNER = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "print(child.pid, flush=True)\n"
    "child.wait()\n"
)


def wait_dead(pid: int, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not hostos.alive(pid):
            return True
        time.sleep(0.1)
    return False


class Alive(unittest.TestCase):
    def test_a_live_child_stays_alive_and_unharmed(self) -> None:
        child = subprocess.Popen(SLEEP)
        try:
            self.assertTrue(hostos.alive(child.pid))
            self.assertTrue(hostos.alive(child.pid))
            time.sleep(0.5)
            self.assertIsNone(child.poll(), "the probe must not stop or signal the process")
        finally:
            child.kill()
            child.wait()

    def test_an_ended_process_is_not_alive(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertFalse(hostos.alive(child.pid))

    def test_no_pid_is_not_alive(self) -> None:
        for pid in (0, -1, None, "12"):
            self.assertFalse(hostos.alive(pid))  # type: ignore[arg-type]

    def test_this_process_is_alive(self) -> None:
        self.assertTrue(hostos.alive(os.getpid()))


class StopTree(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.bin = Path(self.temporary.name)
        (self.bin / "spawner.py").write_text(SPAWNER, encoding="utf-8")
        if hostos.WINDOWS:
            (self.bin / "fakecli.cmd").write_text(f'@"{sys.executable}" "%~dp0spawner.py" %*\r\n', encoding="utf-8")
        else:
            shim = self.bin / "fakecli"
            shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/spawner.py" "$@"\n', encoding="utf-8")
            shim.chmod(0o755)
        self.path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{self.path}"

    def tearDown(self) -> None:
        os.environ["PATH"] = self.path
        self.temporary.cleanup()

    def test_a_bare_shim_name_resolves_to_its_full_path(self) -> None:
        found = hostos.resolve_exe("fakecli")
        self.assertIsNotNone(found)
        self.assertTrue(Path(found).is_absolute())  # type: ignore[arg-type]

    def test_a_timeout_stops_the_shim_and_its_child(self) -> None:
        process = hostos.start(["fakecli"], stdout=subprocess.PIPE, text=True)
        # Arm the timeout only once the child runs: on a loaded machine Python start-up alone can take over 1 s.
        child = int(process.stdout.readline())  # type: ignore[union-attr]
        self.assertTrue(hostos.alive(child))
        began = time.monotonic()
        timer = threading.Timer(1.0, hostos.stop_tree, [process])
        timer.start()
        try:
            process.stdout.read()  # type: ignore[union-attr]  # ends only when every writer is gone
            process.wait(timeout=10)
        finally:
            timer.cancel()
            hostos.stop_tree(process)
            process.stdout.close()  # type: ignore[union-attr]
        self.assertLess(time.monotonic() - began, 1.0 + 5.0)
        self.assertTrue(wait_dead(child), "the shim's child must not outlive the stop")

    def test_stopping_an_ended_process_is_harmless(self) -> None:
        process = hostos.start([sys.executable, "-c", "pass"])
        process.wait()
        hostos.stop_tree(process)
        hostos.stop_tree(process)


class LongPath(unittest.TestCase):
    def test_a_path_and_its_alias_have_one_spelling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            real = Path(temporary) / "Long Folder Name With Spaces"
            real.mkdir()
            if hostos.WINDOWS:
                import ctypes

                buffer = ctypes.create_unicode_buffer(1024)
                ctypes.windll.kernel32.GetShortPathNameW(str(real), buffer, 1024)
                alias = Path(buffer.value)
                if alias == real:
                    self.skipTest("8.3 names are disabled on this volume")
            else:
                alias = Path(temporary) / "link"
                alias.symlink_to(real, target_is_directory=True)
            self.assertNotEqual(str(alias), str(real))
            self.assertEqual(hostos.long_path(alias), hostos.long_path(real))
            self.assertEqual(hostos.long_path(alias / "not-yet" / "file.txt"),
                             hostos.long_path(real) / "not-yet" / "file.txt")
            self.assertNotIn("~", str(hostos.long_path(alias)))

    def test_the_temp_folder_has_no_short_names(self) -> None:
        self.assertNotIn("~", str(hostos.long_path(tempfile.gettempdir())))


class CcCommand(unittest.TestCase):
    def test_it_runs_the_cli_with_this_interpreter(self) -> None:
        root = Path("/cc")
        self.assertEqual(hostos.cc_command(root), [sys.executable, str(root / "scripts" / "cc.py")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
