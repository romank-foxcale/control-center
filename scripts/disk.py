#!/usr/bin/env python3
"""./cc doctor --disk: where this CC's disk space goes. Read-only: it locks, builds, collects and writes nothing.

Sections: the Nix store, this CC's GC roots, .cc-local (kit state, declared data, everything else), caches, the
three nix.conf keys that bound store growth, the nixpkgs pin against the kit's, and on WSL the distro's disk file.
It never prints a whole nix.conf: a user nix.conf can hold a GitHub token. Why and limits:
docs/kit/decisions/0030-disk-management.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

# Folders and files the kit's scripts keep under .cc-local/; ./cc gc and doctor never call them data.
KIT_STATE = ("runs", "runs-archive", "threads", "verified", "lessons", "hooks", "eval", "prs-seen.json")
NIX_KEYS = ("min-free", "max-free", "auto-optimise-store")
SCRIPT_SUFFIXES = {".py", ".sh", ".ipynb", ".r", ".sql"}
UNDECLARED_BYTES = 100 * 2**20
WALK_SECONDS = 20.0

Runner = Callable[[list[str]], "str | None"]


def run(command: list[str]) -> str | None:
    """stdout of a command, or None when it is missing, fails or hangs."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def human(size: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(size) < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError


def tree_size(path: Path, deadline: float) -> tuple[int, bool]:
    """Bytes under path without following links, and whether the walk finished before the deadline."""
    total, stack = 0, [path]
    while stack:
        if time.monotonic() > deadline:
            return total, False
        current = stack.pop()
        try:
            if current.is_symlink() or not current.is_dir():
                total += current.lstat().st_size
                continue
            stack.extend(current.iterdir())
        except OSError:
            continue
    return total, True


def measured(path: Path) -> tuple[int, str]:
    size, finished = tree_size(path, time.monotonic() + WALK_SECONDS)
    return size, human(size) if finished else f"more than {human(size)} (stopped after {WALK_SECONDS:.0f} s)"


def store_section(runner: Runner) -> list[str]:
    lines = ["Nix store"]
    info = runner(["nix", "path-info", "--all", "--json"])
    if info is None:
        lines.append("  size: unknown (nix path-info failed)")
    else:
        paths = json.loads(info)
        values = paths.values() if isinstance(paths, dict) else paths
        lines.append(f"  size: {human(sum((value or {}).get('narSize', 0) for value in values))} in {len(paths)} paths")
    free = runner(["df", "-B1", "--output=avail", "/nix"])
    if free and free.split()[-1].isdigit():
        lines.append(f"  free on the store's file system: {human(int(free.split()[-1]))}")
    lines.append("  ./cc gc lists what a collection would delete")
    return lines


def cc_roots(root: Path, runner: Runner) -> list[tuple[str, str]] | None:
    """(link, store path) of every GC root inside this CC, or None when Nix cannot list them."""
    output = runner(["nix-store", "--gc", "--print-roots"])
    if output is None:
        return None
    # Each line is "<link>" -> <store path>; Nix quotes the link.
    return [(link, target) for link, target in re.findall(r'^"?(.+?)"? -> (\S+)$', output, re.MULTILINE)
            if link.startswith(f"{root}/")]


def roots_section(root: Path, runner: Runner) -> list[str]:
    lines = ["GC roots in this CC"]
    found = cc_roots(root, runner)
    if found is None:
        return lines + ["  unknown (nix-store --gc --print-roots failed)"]
    for link, target in found:
        lines.append(f"  {Path(link).relative_to(root)} -> {target}")
    return lines + ([] if found else ["  none"])


def local_section(root: Path, manifest: dict) -> list[str]:
    local = root / ".cc-local"
    lines = [".cc-local"]
    if not local.is_dir():
        return lines + ["  absent"]
    declared = {Path(entry["path"]).parts[2]: entry for entry in manifest.get("localData", [])}
    state = sum(tree_size(local / name, time.monotonic() + WALK_SECONDS)[0] for name in KIT_STATE if (local / name).exists())
    lines.append(f"  kit state: {human(state)} (back it up; never delete)")
    for folder, entry in sorted(declared.items()):
        path = local / "data" / folder
        size, text = measured(path) if path.exists() else (0, "none")
        budget = entry["budgetGB"] * 2**30
        note = f"over its {entry['budgetGB']} GB budget" if size > budget else f"budget {entry['budgetGB']} GB"
        rebuild = f"rebuild: ./cc run {entry['rebuild']}" if entry.get("rebuild") else "no rebuild app"
        lines.append(f"  data {entry['name']}: {text}, {note}; {rebuild}; a rebuild needs: {entry['rebuildNeeds']}")
    data = local / "data"
    others = [path for path in sorted(local.iterdir()) if path.name not in (*KIT_STATE, "data")]
    others += [path for path in sorted(data.iterdir()) if path.name not in declared] if data.is_dir() else []
    for path in others:
        size, text = measured(path)
        if size >= UNDECLARED_BYTES or text.startswith("more"):
            lines.append(f"  undeclared {path.relative_to(local)}: {text}; declare it in localData or move it")
    scripts = [path for folder in others if folder.is_dir() for path in folder.rglob("*")
               if path.suffix.lower() in SCRIPT_SUFFIXES and path.is_file()]
    scripts += [path for path in others if path.is_file() and path.suffix.lower() in SCRIPT_SUFFIXES]
    for path in scripts[:20]:
        lines.append(f"  script outside a repository: {path.relative_to(local)} (git does not track it)")
    if len(scripts) > 20:
        lines.append(f"  ... and {len(scripts) - 20} more scripts")
    return lines


def windows_path(variable: str, runner: Runner) -> Path | None:
    """%VARIABLE% from Windows as a WSL path, or None outside WSL."""
    value = runner(["cmd.exe", "/c", f"echo %{variable}%"])
    if not value or "%" in value:
        return None
    converted = runner(["wslpath", "-u", value.strip()])
    return Path(converted.strip()) if converted else None


def windows_size(path: Path, runner: Runner) -> str:
    """Size of a Windows folder, measured by Windows: a walk through /mnt/ reads about 1 MiB of metadata per second."""
    native = runner(["wslpath", "-w", str(path)])
    if not native:
        return "unknown (wslpath failed)"
    script = ("$s = (Get-ChildItem -LiteralPath $env:CC_PATH -Recurse -File -Force -ErrorAction SilentlyContinue | "
              "Measure-Object Length -Sum).Sum; [Console]::Out.Write([int64]$s)")
    size = runner(["env", f"CC_PATH={native.strip()}", "WSLENV=CC_PATH/w", "powershell.exe", "-NoProfile",
                   "-NonInteractive", "-Command", script])
    return human(int(size)) if size and size.strip().isdigit() else "unknown (powershell.exe failed)"


def expand(path: str, env: dict[str, str], runner: Runner) -> Path | None:
    for variable in ("LOCALAPPDATA", "USERPROFILE"):
        token = f"%{variable}%"
        if path.startswith(token):
            base = windows_path(variable, runner) if "WSL_DISTRO_NAME" in env else None
            return base.joinpath(*re.split(r"[\\/]", path[len(token):].strip("\\/"))) if base else None
    home = env.get("HOME", "~")
    cache = env.get("XDG_CACHE_HOME") or f"{home}/.cache"
    if path.startswith("~/"):
        path = f"{home}/{path[2:]}"
    return Path(path.replace("$XDG_CACHE_HOME", cache).replace("$HOME", home)).expanduser()


def caches_section(manifest: dict, env: dict[str, str], runner: Runner) -> list[str]:
    caches = [("kit gate venvs", "$XDG_CACHE_HOME/control-center")]
    if "WSL_DISTRO_NAME" in env:
        caches.append(("kit gate venvs (Windows)", "%LOCALAPPDATA%\\control-center"))
    caches += [(entry["name"], entry["path"]) for entry in manifest.get("localCaches", [])]
    lines = ["Caches"]
    for name, declared in caches:
        path = expand(declared, env, runner)
        if path is None:
            lines.append(f"  {name}: {declared} cannot be resolved here")
        elif not path.exists():
            lines.append(f"  {name}: none ({path})")
        elif declared.startswith("%"):
            lines.append(f"  {name}: {windows_size(path, runner)} ({path})")
        else:
            lines.append(f"  {name}: {measured(path)[1]} ({path})")
    return lines


def conf_value(path: Path, key: str) -> str | None:
    """The last value of one key; nothing else of the file leaves this function."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    values = re.findall(rf"^\s*{re.escape(key)}\s*=\s*(\S+)\s*$", text, re.MULTILINE)
    return values[-1] if values else None


def nix_conf_section(env: dict[str, str], runner: Runner) -> list[str]:
    config_home = Path(env.get("XDG_CONFIG_HOME") or f"{env.get('HOME', '~')}/.config").expanduser()
    files = {"user": config_home / "nix" / "nix.conf", "daemon": Path("/etc/nix/nix.conf")}
    lines = ["nix.conf"]
    effective: dict[str, str | None] = {}
    for key in NIX_KEYS:
        shown = runner(["nix", "config", "show", key])
        # Only a number or a boolean is printed, whatever nix returns.
        effective[key] = shown.strip() if shown and re.fullmatch(r"\d+|true|false", shown.strip()) else None
        sources = ", ".join(f"{label} {conf_value(path, key) or 'unset'}" for label, path in files.items())
        lines.append(f"  {key}: {effective[key] or 'unknown'} ({sources})")
    if effective["min-free"] in (None, "0"):
        lines.append("  min-free is 0, so Nix never collects by itself: set min-free and max-free in /etc/nix/nix.conf "
                     "(needs sudo), then restart the Nix daemon; a user nix.conf does not change daemon settings")
    if effective["auto-optimise-store"] != "true":
        lines.append("  auto-optimise-store is off: set it in /etc/nix/nix.conf for new paths; it does not shrink the "
                     "current store (nix-store --optimise does that once)")
    return lines


def pin_section(root: Path) -> list[str]:
    try:
        rev = json.loads((root / "flake.lock").read_text(encoding="utf-8"))["nodes"]["nixpkgs"]["locked"]["rev"]
    except (OSError, KeyError, json.JSONDecodeError):
        return ["nixpkgs pin", "  unknown: flake.lock has no locked nixpkgs"]
    try:
        kit = json.loads((root / "nix" / "kit-nixpkgs.json").read_text(encoding="utf-8"))["rev"]
    except (OSError, KeyError, json.JSONDecodeError):
        return ["nixpkgs pin", f"  {rev[:12]}; nix/kit-nixpkgs.json is missing, so this CC's kit predates the recommended rev"]
    if rev == kit:
        return ["nixpkgs pin", f"  {rev[:12]}, the kit's recommended rev"]
    return ["nixpkgs pin", f"  {rev[:12]} differs from the kit's recommended {kit[:12]}: CCs on other revs store "
            "their own copy of every tool; ./cc relock moves this CC to it"]


def backup_section(root: Path, manifest: dict, env: dict[str, str], runner: Runner) -> list[str]:
    # From the remote-tracking ref the last push or fetch left; no network.
    pushed = runner(["git", "-C", str(root), "log", "-1", "--format=%s", "refs/remotes/origin/cc-backup"])
    remote = f"  on GitHub: {pushed.strip()} in origin's cc-backup branch" if pushed else \
        "  on GitHub: no cc-backup branch known here; ./cc backup --push sends one"
    configured = (manifest.get("backup") or {}).get("path")
    if not configured:
        return ["Backup", "  no backup.path in control-center.json; ./cc backup --to DIR makes one", remote]
    base = expand(configured, env, runner)
    folder = base / (manifest.get("name") or root.name) if base else None
    stamps = sorted(path.name for path in folder.iterdir() if re.fullmatch(r"\d{8}-\d{6}", path.name)) \
        if folder and folder.is_dir() else []
    if not stamps:
        return ["Backup", f"  none yet in {configured}; run ./cc backup", remote]
    age = (time.time() - time.mktime(time.strptime(stamps[-1], "%Y%m%d-%H%M%S"))) / 86400
    return ["Backup", f"  newest {stamps[-1]}, {age:.0f} days old, {len(stamps)} kept in {folder}", remote]


def wsl_section(env: dict[str, str], runner: Runner) -> list[str]:
    distro = env.get("WSL_DISTRO_NAME")
    if not distro:
        return []
    lines = [f"WSL distro {distro}"]
    free = runner(["df", "-B1", "--output=avail", "/"])
    if free and free.split()[-1].isdigit():
        lines.append(f"  free inside the distro: {human(int(free.split()[-1]))}")
    query = runner(["reg.exe", "query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss", "/s"]) or ""
    base = None
    for block in re.split(r"\r?\n(?=HKEY_)", query):
        name = re.search(r"DistributionName\s+REG_SZ\s+(.+?)\s*$", block, re.MULTILINE)
        path = re.search(r"BasePath\s+REG_SZ\s+(.+?)\s*$", block, re.MULTILINE)
        if name and path and name.group(1) == distro:
            base = path.group(1).removeprefix("\\\\?\\")
    if base is None:
        return lines + ["  disk file: not found in the Lxss registry key"]
    converted = runner(["wslpath", "-u", f"{base}\\ext4.vhdx"])
    disk = Path(converted.strip()) if converted else None
    if disk is None or not disk.exists():
        return lines + [f"  disk file: {base}\\ext4.vhdx is not readable"]
    lines.append(f"  disk file: {human(disk.stat().st_size)} ({base}\\ext4.vhdx)")
    windows = runner(["df", "-B1", "--output=avail", str(disk)])
    if windows and windows.split()[-1].isdigit():
        lines.append(f"  free on the Windows drive: {human(int(windows.split()[-1]))}")
    lines.append("  the disk file does not shrink when files inside are deleted; making it smaller is a manual "
                 "Windows administrator step, done only after ./cc backup (see Backup above)")
    return lines


def report(root: Path, env: dict[str, str], runner: Runner = run, out=sys.stdout) -> None:
    try:
        manifest = json.loads((root / "control-center.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    sections = (
        lambda: store_section(runner),
        lambda: roots_section(root, runner),
        lambda: local_section(root, manifest),
        lambda: caches_section(manifest, env, runner),
        lambda: nix_conf_section(env, runner),
        lambda: pin_section(root),
        lambda: backup_section(root, manifest, env, runner),
        lambda: wsl_section(env, runner),
    )
    for section in sections:
        lines = section()
        if lines:
            print("\n".join(lines), file=out, flush=True)


if __name__ == "__main__":
    report(Path(sys.argv[1]).resolve(), dict(os.environ))
