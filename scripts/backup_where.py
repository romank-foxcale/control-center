#!/usr/bin/env python3
"""./cc backup where | ./cc backup restore <name> [--all]: where this CC's backups are, and how to get data back.

where reads control-center.json, the backup folders, the mirror records, .cc-local/hooks/backup-locations.json and
the remote-tracking ref of cc-backup; it uses no network, so it also works in a CC cloned again from GitHub. It exits
1 when an irreplaceable entry has no complete mirror: compact a WSL disk only after it exits 0.
restore copies the files of an entry's last complete sync back from its mirror (--all: also the orphans), and only
into a missing or empty folder. Why and limits: docs/kit/decisions/0030-disk-management.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup import BRANCH, STAMP, data_root  # noqa: E402
from disk import KIT_STATE, WALK_SECONDS, Runner, expand, human, run, tree_size, wsl_disk_folder  # noqa: E402

STALE_DAYS = 7
LARGE_UNDECLARED = 2**30
GITHUB = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?/?$")


def github_link(url: str) -> str | None:
    match = GITHUB.match(url.strip())
    return f"https://github.com/{match.group(1)}/{match.group(2)}/tree/{BRANCH}" if match else None


def days(stamp: str) -> float:
    return (time.time() - time.mktime(time.strptime(stamp, "%Y%m%d-%H%M%S"))) / 86400


def load(root: Path) -> dict:
    try:
        return json.loads((root / "control-center.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def locations(root: Path) -> dict:
    try:
        return json.loads((root / ".cc-local" / "hooks" / "backup-locations.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def configured_base(manifest: dict, env: dict[str, str], runner: Runner) -> Path | None:
    path = (manifest.get("backup") or {}).get("path")
    return expand(path, env, runner) if path else None


def backup_folders(root: Path, manifest: dict, env: dict[str, str], runner: Runner) -> list[Path]:
    folders = {Path(path) for path in locations(root).get("backups", [])}
    base = configured_base(manifest, env, runner)
    if base:
        folders.add(base / (manifest.get("name") or root.name))
    return sorted(folders)


def mirrors(root: Path, manifest: dict, name: str, env: dict[str, str], runner: Runner) -> list[Path]:
    """Every folder that may hold this entry's mirror: those backup used, and the one the settings point to now."""
    found = {Path(path) for path in (locations(root).get("mirrors") or {}).get(name, [])}
    base = configured_base(manifest, env, runner)
    if base or (manifest.get("backup") or {}).get("dataPath"):
        try:
            found.add(data_root(manifest, base or Path("/"), env, runner) / (manifest.get("name") or root.name)
                      / "data" / name)
        except ValueError:
            pass
    return sorted(found)


def record(mirror: Path) -> tuple[dict | None, str | None]:
    path = mirror.with_name(f"{mirror.name}.json")
    if not path.exists():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError):
        return None, f"record is unreadable: {path}"


def best(root: Path, manifest: dict, name: str, env: dict[str, str], runner: Runner) -> tuple[Path, dict] | None:
    """The mirror whose last complete sync is newest."""
    complete = [(found["complete"]["time"], mirror, found) for mirror in mirrors(root, manifest, name, env, runner)
                for found in [record(mirror)[0]] if found and found.get("complete")]
    return max(complete)[1:] if complete else None


def windows(path: Path, env: dict[str, str], runner: Runner) -> str:
    if not env.get("WSL_DISTRO_NAME"):
        return ""
    native = runner(["wslpath", "-w", str(path)])
    return f" (Windows: {native.strip()})" if native else ""


def place_warnings(path: Path, env: dict[str, str], runner: Runner) -> list[str]:
    """A copy on the WSL disk's own drive does not survive that drive; one in OneDrive leaves the machine."""
    if not env.get("WSL_DISTRO_NAME"):
        return []
    found = []
    drive = re.match(r"/mnt/([a-z])(/|$)", str(path))
    disk_folder = wsl_disk_folder(env, runner)
    if drive and disk_folder and disk_folder[:1].lower() == drive.group(1):
        found.append(f"on drive {drive.group(1).upper()}:, the same drive as the WSL disk file; set backup.dataPath "
                     "to another drive to survive a drive failure")
    for variable in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        value = runner(["cmd.exe", "/c", f"echo %{variable}%"])
        if not value or "%" in value:
            continue
        converted = runner(["wslpath", "-u", value.strip()])
        synced = Path(converted.strip()) if converted else None
        if synced and (path == synced or synced in path.parents):
            found.append(f"inside OneDrive ({value.strip()}), so it leaves this machine")
    return found


def history_lines(root: Path, manifest: dict, env: dict[str, str], runner: Runner) -> list[str]:
    lines = [f"Backups of {manifest.get('name') or root.name} (Git history, uncommitted files, kit state, clones)"]
    for folder in backup_folders(root, manifest, env, runner):
        stamps = sorted(path.name for path in folder.iterdir() if STAMP.fullmatch(path.name)) if folder.is_dir() else []
        if not stamps:
            lines.append(f"  {folder}: no backups{windows(folder, env, runner)}")
            continue
        lines.append(f"  {folder}: newest {stamps[-1]}, {days(stamps[-1]):.0f} days old, {len(stamps)} kept"
                     f"{windows(folder, env, runner)}")
        lines += [f"    warning: {text}" for text in place_warnings(folder, env, runner)]
    if len(lines) == 1:
        lines.append("  none known: set backup.path in control-center.json and run ./cc backup")
    lines.append("  restore: git clone cc.bundle <dir>, then tar -xzf changes.tar.gz, state.tar.gz and "
                 "worktree-changes.tar.gz inside it; git clone repos/<clone>.bundle for each project clone")
    try:
        url = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True,
                             check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return lines + ["  GitHub: this CC has no origin remote, so nothing is pushed"]
    link = github_link(url)
    if not link:
        return lines + [f"  GitHub: origin {url} is not a GitHub URL"]
    pushed = runner(["git", "-C", str(root), "log", "-1", "--format=%cs %s", f"refs/remotes/origin/{BRANCH}"])
    state = f"last known: {pushed.strip()}" if pushed else f"no {BRANCH} branch known here; ./cc backup --push sends one"
    return lines + [f"  GitHub: {link} ({state})",
                    f"    restore: git fetch origin {BRANCH} && git archive FETCH_HEAD | tar -x -C <dir>"]


def data_lines(root: Path, manifest: dict, env: dict[str, str], runner: Runner) -> tuple[list[str], bool]:
    lines, missing = ["Data (.cc-local/data, localData in control-center.json)"], False
    for entry in manifest.get("localData") or []:
        if not isinstance(entry, dict) or "name" not in entry:
            continue
        name, tags = entry["name"], entry.get("tags") or []
        if "irreplaceable" not in tags:
            rebuild = f"./cc run {entry['rebuild']}" if entry.get("rebuild") else "no rebuild app"
            lines.append(f"  {name}: not backed up; rebuild with {rebuild}, needs {entry.get('rebuildNeeds')}")
            continue
        chosen = best(root, manifest, name, env, runner)
        if chosen:
            complete = chosen[1]["complete"]
            lines.append(f"  {name} ({', '.join(tags)}): complete sync {complete['time']}, "
                         f"{days(complete['time']):.0f} days old")
        else:
            lines.append(f"  {name} ({', '.join(tags)}): no complete mirror")
        for mirror in mirrors(root, manifest, name, env, runner):
            found, error = record(mirror)
            if error:
                lines.append(f"    mirror {mirror}: {error}")
            elif found and found.get("complete"):
                complete = found["complete"]
                lines.append(f"    mirror {mirror}: {len(complete['files'])} files, {human(complete['bytes'])}, "
                             f"{complete['orphans']} orphans ({human(complete['orphanBytes'])}){windows(mirror, env, runner)}")
                if found.get("state") != "complete":
                    lines.append(f"      the newest sync stopped before it was complete; restore uses the sync of "
                                 f"{complete['time']}")
            elif found:
                lines.append(f"    mirror {mirror}: a sync started {found.get('started')} and never completed")
            else:
                continue
            lines += [f"      warning: {text}" for text in place_warnings(mirror, env, runner)]
        if chosen:
            lines.append("    compared by size and time, not checksums")
            lines.append(f"    restore: ./cc backup restore {name}")
            if days(chosen[1]["complete"]["time"]) > STALE_DAYS:
                lines.append(f"    warning: {name}: the last complete sync is older than {STALE_DAYS} days; run ./cc backup")
        else:
            missing = True
            lines.append(f"    warning: {name} has no complete mirror; run ./cc backup and read its errors")
    return (lines if len(lines) > 1 else []), missing


def undeclared_lines(root: Path) -> list[str]:
    local, lines = root / ".cc-local", []
    for path in sorted(local.iterdir()) if local.is_dir() else []:
        if path.name in KIT_STATE or path.name == "data":
            continue
        size, finished = tree_size(path, time.monotonic() + WALK_SECONDS)
        if size >= LARGE_UNDECLARED or not finished:
            lines.append(f"  warning: .cc-local/{path.name} ({human(size)}{'' if finished else '+'}) is not declared in "
                         "localData and is in no backup")
    return lines


def where(root: Path, env: dict[str, str], runner: Runner, out) -> int:
    manifest = load(root)
    data, missing = data_lines(root, manifest, env, runner)
    lines = history_lines(root, manifest, env, runner) + data + undeclared_lines(root)
    lines.append("Local copies stay on this machine's drives as far as the kit can see; it cannot see other sync "
                 "clients (Dropbox, backup agents), so check that no folder above is synced if data is sensitive")
    print("\n".join(lines), file=out)
    return 1 if missing else 0


def mirror_summary(root: Path, manifest: dict, env: dict[str, str], runner: Runner) -> list[str]:
    lines = []
    for entry in manifest.get("localData") or []:
        if isinstance(entry, dict) and "irreplaceable" in (entry.get("tags") or []):
            chosen = best(root, manifest, entry["name"], env, runner)
            if not chosen:
                lines.append(f"  {entry['name']}: no complete mirror")
                continue
            complete = chosen[1]["complete"]
            lines.append(f"  {entry['name']}: complete sync {complete['time']}, {days(complete['time']):.0f} days old, "
                         f"{len(complete['files'])} files, {human(complete['bytes'])}")
    return lines


def restore(root: Path, name: str, everything: bool, env: dict[str, str], runner: Runner, out) -> int:
    manifest = load(root)
    entry = next((item for item in manifest.get("localData") or [] if isinstance(item, dict) and item.get("name") == name), None)
    if entry is None:
        print(f"error: unknown localData entry: {name}", file=out)
        return 1
    chosen = best(root, manifest, name, env, runner)
    if not chosen:
        print(f"error: {name} has no complete mirror; nothing to restore", file=out)
        return 1
    mirror, found = chosen
    target = root / entry["path"]
    if target.exists() and any(target.iterdir()):
        print(f"error: {target} has files; restore writes only into a missing or empty folder", file=out)
        return 1
    names = set(found["complete"]["files"])
    if everything:
        names |= {path.relative_to(mirror).as_posix() for path in mirror.rglob("*")
                  if (path.is_file() or path.is_symlink()) and not path.name.endswith(".cc-tmp")}
    # Checked before anything is written: a restore never leaves half a folder because of a bad mirror.
    unsafe = sorted(item for item in names if (mirror / item).is_symlink())
    if unsafe:
        print(f"error: {unsafe[0]} is a symlink in the mirror; restore refuses it", file=out)
        return 1
    absent = sorted(item for item in names if not (mirror / item).is_file())
    if absent:
        print(f"error: missing in the mirror: {', '.join(absent[:5])}{' ...' if len(absent) > 5 else ''}", file=out)
        return 1
    for item in sorted(names):
        destination = target / item
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mirror / item, destination)
    print(f"restored {len(names)} files from {mirror} (sync {found['complete']['time']}) to {target}", file=out)
    return 0


def main(argv: list[str], env: dict[str, str] | None = None, runner: Runner = run, out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(prog="./cc backup", description=__doc__.split("\n")[0])
    parser.add_argument("root", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("where", help="where every backup and data mirror is, with restore commands")
    restoring = commands.add_parser("restore", help="copy a data entry back from its mirror")
    restoring.add_argument("name")
    restoring.add_argument("--all", action="store_true", help="also the orphans: files gone from the CC since")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env
    root = args.root.resolve()
    if args.command == "where":
        return where(root, env, runner, out)
    return restore(root, args.name, args.all, env, runner, out)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
