#!/usr/bin/env python3
"""The one place that knows how Windows differs from Linux and macOS for processes and paths.

Other scripts call these helpers and never branch on os.name or sys.platform for the same jobs:
- alive(pid): does a process run? os.kill(pid, 0) is not a probe on Windows (signal 0 is CTRL_C_EVENT).
- resolve_exe(name): the full path of a command; on Windows it also finds npm-style .cmd shims, which
  CreateProcess does not look up by a bare name.
- start(argv) / stop_tree(process): start a process so that its whole tree can be stopped later; on
  Windows killing a .cmd shim kills only cmd.exe and leaves its children running.
- long_path(path): one canonical spelling of a path (no 8.3 short names, symlinks resolved).
- cc_command(root): how a script starts the CC's own CLI without the extensionless shim.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

WINDOWS = os.name == "nt"

if WINDOWS:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.GetLongPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    _kernel32.GetLongPathNameW.restype = wintypes.DWORD

    _QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _ACCESS_DENIED = 5


def alive(pid: int) -> bool:
    """True while a process with this pid runs. A pid of 0 or less is never alive."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if WINDOWS:
        handle = _kernel32.OpenProcess(_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # The process exists but belongs to someone we may not query.
            return ctypes.get_last_error() == _ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            return bool(_kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == _STILL_ACTIVE
        finally:
            _kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def resolve_exe(name: str) -> str | None:
    """The full path of a command found on PATH (PATHEXT included on Windows), or None."""
    return shutil.which(name)


def start(argv: list[str], **popen: Any) -> subprocess.Popen:
    """Popen whose whole process tree stop_tree can stop. argv[0] is resolved on PATH first."""
    executable = resolve_exe(argv[0]) or argv[0]
    if WINDOWS:
        popen["creationflags"] = popen.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen["start_new_session"] = True
    return subprocess.Popen([executable, *argv[1:]], **popen)


def stop_tree(process: subprocess.Popen) -> None:
    """Stop a process started with start() and every process it started. Safe to call twice."""
    if process.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def long_path(path: str | os.PathLike[str]) -> Path:
    """The canonical absolute spelling of a path: symlinks resolved and, on Windows, 8.3 short names expanded.

    Works for paths that do not exist yet: the longest existing prefix is expanded, the rest kept as given.
    """
    resolved = Path(os.path.realpath(path))
    if not WINDOWS:
        return resolved
    head, tail = resolved, []
    while not head.exists() and head.parent != head:
        head, tail = head.parent, [head.name, *tail]
    size = _kernel32.GetLongPathNameW(str(head), None, 0)
    if size:
        buffer = ctypes.create_unicode_buffer(size)
        if _kernel32.GetLongPathNameW(str(head), buffer, size):
            head = Path(buffer.value)
    return head.joinpath(*tail)


def cc_command(root: Path) -> list[str]:
    """The argv prefix that runs this CC's CLI with the current interpreter, on every OS."""
    return [sys.executable, str(Path(root) / "scripts" / "cc.py")]
