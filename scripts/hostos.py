#!/usr/bin/env python3
"""The one place that knows how Windows differs from Linux and macOS for processes and paths.

Other scripts call these helpers and never branch on os.name or sys.platform for the same jobs:
- alive(pid): does a process run? os.kill(pid, 0) is not a probe on Windows (signal 0 is CTRL_C_EVENT).
- resolve_exe(name): the full path of a command; on Windows it also finds npm-style .cmd shims, which
  CreateProcess does not look up by a bare name.
- start(argv) / stop_tree(process): start a process so that its whole tree can be stopped later; on
  Windows killing a .cmd shim kills only cmd.exe and leaves its children running.
- long_path(path): one canonical spelling of a path (no 8.3 short names, symlinks resolved).
- native_spelling(text): a Git Bash, Cygwin or WSL drive path as Windows spells it; unchanged elsewhere.
- write_protect(folder, read_only): make a folder tree refuse writes; chmod only marks Windows files read-only and
  leaves folders open, so Windows gets a deny-write ACL instead.
- rmtree(path): shutil.rmtree that clears a read-only flag or folder mode and retries, as Windows needs.
- find_process(pattern): pgrep -f, also where Windows has no pgrep.
- terminal(): the files of the controlling terminal (/dev/tty, or CONIN$ and CONOUT$).
- cc_command(root): how a script starts the CC's own CLI without the extensionless shim.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import stat
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


_DRIVE_ROOT = re.compile(r"^/(?:mnt/|cygdrive/)?([A-Za-z])(?:/(.*))?$")


def native_spelling(text: str) -> str:
    """On Windows, /c/x (Git Bash), /cygdrive/c/x and /mnt/c/x (WSL) become C:\\x; any other text is unchanged."""
    match = _DRIVE_ROOT.match(text) if WINDOWS else None
    if not match:
        return text
    return f"{match.group(1).upper()}:\\" + (match.group(2) or "").replace("/", "\\")


# Windows rights a protected folder denies to Everyone: write and append data, write attributes, delete. icacls
# adds SYNCHRONIZE to every ACE it writes, and a denied SYNCHRONIZE makes the files unreadable, so the deny ACE
# is written through .NET, which leaves SYNCHRONIZE out of deny rules. icacls removes it again.
_EVERYONE = "*S-1-1-0"
_WRITE_RIGHTS = "(OI)(CI)(WD,AD,WEA,WA,D,DC)"
_DENY_SCRIPT = (
    "$item = Get-Item -LiteralPath $env:CC_PROTECT -Force; $acl = $item.GetAccessControl('Access'); "
    "$rule = New-Object System.Security.AccessControl.FileSystemAccessRule("
    "(New-Object System.Security.Principal.SecurityIdentifier 'S-1-1-0'), "
    "'WriteData,AppendData,WriteExtendedAttributes,WriteAttributes,Delete,DeleteSubdirectoriesAndFiles', "
    "'ContainerInherit,ObjectInherit', 'None', 'Deny'); $acl.AddAccessRule($rule); $item.SetAccessControl($acl)"
)


def _icacls(*arguments: str) -> None:
    subprocess.run(["icacls", *arguments, "/Q"], check=True, capture_output=True)


def _deny_writes(folder: Path) -> None:
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _DENY_SCRIPT], check=True,
                   capture_output=True, env={**os.environ, "CC_PROTECT": str(folder)})


def _user_sid() -> str:
    output = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], check=True, capture_output=True, text=True).stdout
    return output.strip().split(",")[-1].strip('"')


def _writable_modes(folder: Path, keep: str, read_only: bool) -> None:
    for directory, folders, files in os.walk(folder):
        folders[:] = [name for name in folders if not (directory == str(folder) and name == keep)]
        for name in [*files, *folders]:
            path = Path(directory) / name
            if not path.is_symlink():
                mode = path.stat().st_mode
                path.chmod(mode & ~0o222 if read_only else mode | 0o200)
    mode = folder.stat().st_mode
    folder.chmod(mode & ~0o222 if read_only else mode | 0o200)


def write_protect(folder: str | os.PathLike[str], read_only: bool, keep: str = ".git") -> None:
    """Make every file and folder under folder refuse writes, or accept them again; folder/keep stays writable.

    POSIX: clear the write bits. Windows: an inherited deny-write ACE for Everyone on folder, and an explicit allow for
    the current user on folder/keep, which wins over the inherited deny. Undoing also clears read-only flags that an
    older chmod (or WSL) left, so a later delete or rename does not fail with WinError 5."""
    folder = Path(folder)
    if not WINDOWS:
        _writable_modes(folder, keep, read_only)
        return
    kept = folder / keep
    grant = f"*{_user_sid()}:{_WRITE_RIGHTS}"
    if read_only:
        if kept.exists():
            _icacls(str(kept), "/grant", grant)
        _deny_writes(folder)
        return
    _icacls(str(folder), "/remove:d", _EVERYONE)
    if kept.exists():
        _icacls(str(kept), "/remove:g", grant.split(":")[0])
    _writable_modes(folder, keep, read_only=False)


def rmtree(path: str | os.PathLike[str], ignore_errors: bool = False) -> None:
    """shutil.rmtree that, on a permission error, makes the entry and its folder writable and tries once more:
    Windows refuses to delete a file with the read-only flag, POSIX one in a folder without the write bit."""
    def retry(function: Any, name: str, error: BaseException) -> None:
        if isinstance(error, PermissionError):
            for target in (os.path.dirname(name), name):
                try:
                    mode = os.lstat(target).st_mode
                    if not stat.S_ISLNK(mode):
                        os.chmod(target, mode | stat.S_IWRITE | (0o700 if stat.S_ISDIR(mode) else 0o600))
                except OSError:
                    pass
            try:
                function(name)
                return
            except OSError:
                if not ignore_errors:
                    raise
        elif not ignore_errors:
            raise error

    shutil.rmtree(path, onexc=retry)


def windows_command_lines(powershell: str = "powershell.exe") -> list[str] | None:
    """Command lines of all Windows processes (from WSL too, through powershell.exe), or None when listing fails."""
    try:
        result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command",
                                 "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"],
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.splitlines() if result.returncode == 0 else None


def find_process(pattern: str) -> int | None:
    """pgrep -f's exit status for a regular expression: 0 a command line matches, 1 none, other an error; None when
    processes cannot be listed. Windows has no pgrep, so its process list is searched instead."""
    if WINDOWS:
        lines = windows_command_lines()
        if lines is None:
            return None
        return 0 if any(re.search(pattern, line) for line in lines) else 1
    try:
        return subprocess.run(["pgrep", "-f", pattern], capture_output=True, timeout=30).returncode
    except (OSError, subprocess.TimeoutExpired):
        return None


def terminal() -> tuple[str, str]:
    """The files to read a typed line from and to write a prompt to on the controlling terminal."""
    return ("CONIN$", "CONOUT$") if WINDOWS else ("/dev/tty", "/dev/tty")


def cc_command(root: Path) -> list[str]:
    """The argv prefix that runs this CC's CLI with the current interpreter, on every OS."""
    return [sys.executable, str(Path(root) / "scripts" / "cc.py")]
