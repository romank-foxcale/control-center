#!/usr/bin/env python3
"""./cc backup [--to DIR] [--push]: copy what only this machine has of the CC to a folder outside the CC.

One backup is <DIR>/<cc name>/<timestamp>/ with
- cc.bundle: every branch and tag of the CC's history;
- changes.tar.gz: files Git does not track or that differ from HEAD, without ignored ones (plans live here in the kit);
- state.tar.gz: .cc-local without data/ and without other folders over 100 MiB, which are listed instead;
- repos/<clone>.bundle: every branch of each project clone, so worktree commits that are on no remote survive;
- worktree-changes.tar.gz: each feature worktree's files that differ from its HEAD or are untracked.
On WSL the folder must be on a Windows drive (/mnt/...): a copy inside the distro's own disk file is lost with it.
With --push (or backup.push), the same files then go as one parentless commit to the branch cc-backup of the CC's
origin, only if GitHub says that repository is private. Each localData entry tagged irreplaceable is then mirrored
to <dataPath or DIR>/<cc name>/data/<entry>/: new or changed files are copied, nothing is ever deleted there, and
<entry>.json records the file list of the last complete sync. Data is never pushed.
Why and limits: docs/kit/decisions/0030-disk-management.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from disk import KIT_STATE, UNDECLARED_BYTES, WALK_SECONDS, Runner, expand, human, run, tree_size  # noqa: E402

STAMP = re.compile(r"\d{8}-\d{6}")
BRANCH = "cc-backup"
# GitHub refuses files over 100 MB in a push.
MAX_FILE_BYTES = 95 * 10**6


def git(directory: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(["git", "-C", str(directory), *arguments], capture_output=True, text=True, check=True,
                          env=env).stdout.strip()


def clones(root: Path) -> list[Path]:
    """Project clones that have at least one branch."""
    base = root / "repos" / "project"
    return [path for path in sorted(base.iterdir()) if (path / ".git").exists()
            and git(path, "for-each-ref", "--count=1", "refs/heads")] if base.is_dir() else []


def worktrees(root: Path) -> list[Path]:
    return [path for path in sorted((root / "worktrees").glob("*/*_wt")) if (path / ".git").exists()]

def destination(root: Path, manifest: dict, to: str | None, env: dict[str, str], runner: Runner) -> Path:
    if to is None:
        configured = (manifest.get("backup") or {}).get("path")
        if not configured:
            raise ValueError("no destination: pass --to DIR or set backup.path in control-center.json")
        resolved = expand(configured, env, runner)
        if resolved is None:
            raise ValueError(f"backup.path {configured} cannot be resolved here")
    else:
        resolved = Path(to).expanduser()
    resolved = resolved.resolve()
    if resolved == root or root in resolved.parents:
        raise ValueError(f"{resolved} is inside the CC; a backup must live outside it")
    if env.get("WSL_DISTRO_NAME") and not re.match(r"/mnt/[a-z]/", f"{resolved}/"):
        raise ValueError(f"{resolved} is inside this WSL distro's disk file; choose a folder on a Windows drive (/mnt/c/...)")
    return resolved


def changed_files(root: Path) -> list[str]:
    """Files that differ from HEAD (staged or not) and untracked files that are not ignored."""
    names: set[str] = set()
    for command in (["diff", "-z", "--name-only", "HEAD"], ["ls-files", "-z", "--others", "--exclude-standard"]):
        output = subprocess.run(["git", "-C", str(root), *command], capture_output=True, text=True, check=True).stdout
        names.update(output.split("\0"))
    return sorted(name for name in names if name and (root / name).is_file())


def state_members(local: Path) -> tuple[list[Path], list[str]]:
    """Top-level entries of .cc-local to archive, and those left out with the reason."""
    members, skipped = [], []
    for path in sorted(local.iterdir()) if local.is_dir() else []:
        if path.name == "data":
            skipped.append("data/: declared rebuildable data")
            continue
        size, finished = tree_size(path, time.monotonic() + WALK_SECONDS)
        if path.name not in KIT_STATE and (size >= UNDECLARED_BYTES or not finished):
            skipped.append(f"{path.name}: {human(size)}{'' if finished else '+'}, over {human(UNDECLARED_BYTES)}; "
                           "declare it in localData or copy it yourself")
            continue
        members.append(path)
    return members, skipped


def prune(folder: Path, keep: int) -> list[Path]:
    backups = sorted(path for path in folder.iterdir() if path.is_dir() and STAMP.fullmatch(path.name))
    old = backups[:-keep] if len(backups) > keep else []
    for path in old:
        shutil.rmtree(path)
    return old


def pgrep(pattern: str) -> int | None:
    """pgrep's exit status for a pattern: 0 found, 1 none, other an error; None when pgrep is missing."""
    try:
        return subprocess.run(["pgrep", "-f", pattern], capture_output=True, timeout=30).returncode
    except (OSError, subprocess.TimeoutExpired):
        return None


def windows_command_lines() -> list[str] | None:
    """Command lines of Windows processes; WSL pgrep does not see Python started from Windows (data-tags S2)."""
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                                 "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"],
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.splitlines() if result.returncode == 0 else None


def busy(patterns: list[str], env: dict[str, str]) -> str | None:
    """Why the entry must not be copied now, or None when no writer runs. Only "no match" counts as clear."""
    for pattern in patterns:
        code = pgrep(pattern)
        if code == 0:
            return f"{pattern} is running"
        if code != 1:
            return f"cannot check for {pattern} ({'pgrep not found' if code is None else f'pgrep exit {code}'})"
    if patterns and env.get("WSL_DISTRO_NAME"):
        lines = windows_command_lines()
        if lines is None:
            return "cannot list Windows processes"
        for pattern in patterns:
            if any(re.search(pattern, line) for line in lines):
                return f"{pattern} is running (Windows)"
    return None


def drive_mounted(path: Path, mounts: str | None = None) -> bool:
    """A /mnt/<x>/ path must be on a mounted Windows drive: an unmounted /mnt/e is a plain folder inside the WSL disk."""
    match = re.match(r"(/mnt/[^/]+)(/|$)", str(path))
    if not match:
        return True
    if mounts is None:
        mounts = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
    return any(fields[1] == match.group(1) and fields[2] in ("9p", "drvfs", "virtiofs")
               for fields in (line.split() for line in mounts.splitlines()) if len(fields) > 2)


def data_root(manifest: dict, base: Path, env: dict[str, str], runner: Runner) -> Path:
    configured = (manifest.get("backup") or {}).get("dataPath")
    if not configured:
        return base
    drive = re.match(r"([A-Za-z]):[\\/](.*)", configured)
    if drive:
        return Path(f"/mnt/{drive.group(1).lower()}") / drive.group(2).replace("\\", "/")
    resolved = configured if configured.startswith("/mnt/") else expand(configured, env, runner)
    if resolved is None:
        raise ValueError(f"backup.dataPath {configured} cannot be resolved here")
    return Path(resolved).resolve()


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.cc-tmp")
    temporary.write_text(json.dumps(value, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def same_file(source: os.stat_result, target: Path) -> bool:
    """Equal size and equal whole seconds: /mnt/c keeps whole seconds only (data-tags S1)."""
    try:
        found = target.stat()
    except OSError:
        return False
    return found.st_size == source.st_size and found.st_mtime_ns // 10**9 == source.st_mtime_ns // 10**9


def mirror(root: Path, entry: dict, folder: Path, stamp: str, env: dict[str, str]) -> str:
    """Mirror one irreplaceable entry into folder/<name>/: copy new or changed files, never delete. The record
    folder/<name>.json keeps the file list of the last complete sync, which a restore uses. Raises ValueError."""
    source = root / entry["path"]
    if not source.is_dir():
        raise ValueError(f"{entry['path']} is missing")
    reason = busy(entry.get("quiesce", []), env)
    if reason:
        raise ValueError(reason)
    files: dict[str, os.stat_result] = {}
    for base, _, names in os.walk(source):  # os.walk does not follow symlinked folders
        for name in names:
            path = Path(base) / name
            if not path.is_symlink():
                files[path.relative_to(source).as_posix()] = path.stat()
    lowered: dict[str, list[str]] = {}
    for name in files:
        lowered.setdefault(name.lower(), []).append(name)
    clashes = [names for names in lowered.values() if len(names) > 1]
    if clashes:
        raise ValueError(f"names differ only in case, which a Windows drive merges: {', '.join(sorted(clashes[0]))}")
    target = folder / entry["name"]
    record_path = folder / f"{entry['name']}.json"
    needed = [name for name, stat in sorted(files.items()) if not same_file(stat, target / name)]
    size = sum(files[name].st_size for name in needed)
    existing = folder
    while not existing.exists():
        existing = existing.parent
    free = shutil.disk_usage(existing).free
    if size > free:
        raise ValueError(f"needs {human(size)}, {human(free)} free at {folder}")
    note = ""
    previous: dict = {}
    if record_path.exists():
        try:
            previous = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            note = "; previous record was unreadable, starting a new one"
    target.mkdir(parents=True, exist_ok=True)
    write_json(record_path, {"state": "incomplete", "started": stamp, "complete": previous.get("complete")})
    for name in needed:
        destination_file = target / name
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination_file.with_name(f".{destination_file.name}.cc-tmp")
        try:
            shutil.copy2(source / name, temporary)
            for attempt in range(5):  # Defender or the indexer may hold the target for a moment
                try:
                    os.replace(temporary, destination_file)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.5 * (attempt + 1))
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise ValueError(error.strerror or str(error)) from None
    reason = busy(entry.get("quiesce", []), env)
    if reason:
        raise ValueError(f"during the copy: {reason}; the copy may be inconsistent")
    orphans = [path for path in target.rglob("*") if path.is_file() and not path.name.endswith(".cc-tmp")
               and path.relative_to(target).as_posix() not in files]
    orphan_bytes = sum(path.stat().st_size for path in orphans)
    complete = {"time": stamp, "bytes": sum(stat.st_size for stat in files.values()), "orphans": len(orphans),
                "orphanBytes": orphan_bytes,
                "files": {name: [stat.st_size, stat.st_mtime_ns // 10**9] for name, stat in sorted(files.items())}}
    write_json(record_path, {"state": "complete", "started": stamp, "complete": complete})
    return (f"mirrored {len(files)} files, {len(needed)} copied, {len(orphans)} orphans ({human(orphan_bytes)}) "
            f"to {target}{note}")


def remember(root: Path, folder: Path, mirrors: dict[str, Path]) -> None:
    """.cc-local/hooks/backup-locations.json: every backup folder and mirror used, also with --to, so ./cc backup
    where finds them and the hooks protect mirrors without resolving Windows paths."""
    path = root / ".cc-local" / "hooks" / "backup-locations.json"
    try:
        known = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        known = {}
    backups = set(known.get("backups", [])) | {str(folder)}
    entries = {name: set(paths) for name, paths in (known.get("mirrors") or {}).items()}
    for name, target in mirrors.items():
        entries.setdefault(name, set()).add(str(target))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"backups": sorted(backups), "mirrors": {name: sorted(paths) for name, paths in sorted(entries.items())}})


def push(root: Path, backup: Path, stamp: str, runner: Runner) -> str:
    """Push the backup folder as one parentless commit to origin's cc-backup; return the commit. The CC's HEAD,
    index, working tree and branches stay as they are: the commit is built with a temporary index."""
    try:
        url = git(root, "remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        raise ValueError("this CC has no origin remote") from None
    visibility = (runner(["gh", "repo", "view", url, "--json", "visibility", "--jq", ".visibility"]) or "").strip()
    if visibility != "PRIVATE":
        raise ValueError(f"origin {url} is not a private GitHub repository (gh says '{visibility or 'unknown'}')")
    large = [path for path in backup.rglob("*") if path.is_file() and path.stat().st_size > MAX_FILE_BYTES]
    if large:
        raise ValueError(f"{large[0].relative_to(backup)} is over {MAX_FILE_BYTES // 10**6} MB, which GitHub refuses")
    index = backup.parent / f".{stamp}.index"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    git_dir = git(root, "rev-parse", "--absolute-git-dir")
    try:
        git(backup, f"--git-dir={git_dir}", f"--work-tree={backup}", "add", "--force", "--all", ".", env=env)
        tree = git(root, "write-tree", env=env)
    finally:
        index.unlink(missing_ok=True)
    commit = git(root, "commit-tree", tree, "-m", f"Backup {stamp}")
    try:
        git(root, "push", "--quiet", "--force", "origin", f"{commit}:refs/heads/{BRANCH}")
    except subprocess.CalledProcessError as error:
        raise ValueError(f"git push failed: {error.stderr.strip()}; if it asks for credentials, run gh auth setup-git "
                         "(and gh auth refresh -s workflow when a workflow file is refused)") from None
    return commit


def main(argv: list[str], env: dict[str, str] | None = None, runner: Runner = run, out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(prog="./cc backup", description=__doc__.split("\n")[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--to", help="destination folder; default backup.path in control-center.json")
    parser.add_argument("--push", action=argparse.BooleanOptionalAction, default=None,
                        help=f"also push to origin's {BRANCH} branch; default backup.push in control-center.json")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env
    root = args.root.resolve()
    try:
        manifest = json.loads((root / "control-center.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    try:
        base = destination(root, manifest, args.to, env, runner)
    except ValueError as error:
        print(f"error: {error}; nothing was written", file=out)
        return 1

    folder = base / (manifest.get("name") or root.name)
    # Irreplaceable data goes to one mirror per entry, outside every stamp folder: prune and --push never see it.
    irreplaceable = [entry for entry in manifest.get("localData") or []
                     if isinstance(entry, dict) and "irreplaceable" in (entry.get("tags") or [])]
    data, data_error = None, None
    if irreplaceable:
        try:
            data = data_root(manifest, base, env, runner) / folder.name / "data"
            if env.get("WSL_DISTRO_NAME") and not drive_mounted(data):
                raise ValueError(f"{data} is not on a mounted Windows drive")
        except ValueError as error:
            data_error = error
    # Written before the state archive, so the backup also says where its mirrors are.
    remember(root, folder, {entry["name"]: data / entry["name"] for entry in irreplaceable} if data else {})

    stamp = time.strftime("%Y%m%d-%H%M%S")
    while (folder / stamp).exists():  # two runs in one second
        time.sleep(0.2)
        stamp = time.strftime("%Y%m%d-%H%M%S")
    partial = folder / f".{stamp}.partial"
    partial.mkdir(parents=True)
    # Written under a hidden name and renamed at the end: an interrupted run leaves the earlier backups as they are.
    try:
        # Not --all: refs/remotes/origin/cc-backup would put the previous backup inside this one.
        subprocess.run(["git", "-C", str(root), "bundle", "create", "-q", str(partial / "cc.bundle"), "--branches",
                        "--tags", "HEAD"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "bundle", "verify", "-q", str(partial / "cc.bundle")],
                       check=True, capture_output=True)
        changes = changed_files(root)
        with tarfile.open(partial / "changes.tar.gz", "w:gz") as archive:
            for name in changes:
                archive.add(root / name, arcname=name)
        members, skipped = state_members(root / ".cc-local")
        with tarfile.open(partial / "state.tar.gz", "w:gz") as archive:
            for path in members:
                archive.add(path, arcname=f".cc-local/{path.name}")
        bundled = clones(root)
        for clone in bundled:
            target = partial / "repos" / f"{clone.name}.bundle"
            target.parent.mkdir(exist_ok=True)
            git(clone, "bundle", "create", "-q", str(target), "--branches", "HEAD")
            git(clone, "bundle", "verify", "-q", str(target))
        worktree_files = [(tree / name).relative_to(root) for tree in worktrees(root) for name in changed_files(tree)]
        with tarfile.open(partial / "worktree-changes.tar.gz", "w:gz") as archive:
            for name in worktree_files:
                archive.add(root / name, arcname=str(name))
    except (OSError, subprocess.CalledProcessError) as error:
        shutil.rmtree(partial, ignore_errors=True)
        detail = getattr(error, "stderr", "") or error
        print(f"error: backup failed, earlier backups are unchanged: {str(detail).strip()}", file=out)
        return 1
    final = folder / stamp
    partial.rename(final)
    keep = (manifest.get("backup") or {}).get("keep", 5)
    removed = prune(folder, keep)

    size = sum(path.stat().st_size for path in final.iterdir())
    print(f"backup: {final} ({human(size)})", file=out)
    print(f"  history: cc.bundle, {len(changes)} untracked or changed files, {len(members)} .cc-local entries", file=out)
    print(f"  project clones: {len(bundled)} bundles; worktrees: {len(worktree_files)} changed files", file=out)
    for line in skipped:
        print(f"  not backed up: .cc-local/{line}", file=out)
    for path in removed:
        print(f"  removed older backup {path.name} (keeping {keep})", file=out)
    print("  restore: git clone cc.bundle <dir>, then tar -xzf changes.tar.gz, state.tar.gz and "
          "worktree-changes.tar.gz inside it; git clone repos/<clone>.bundle for each project clone", file=out)

    status = 0
    for entry in irreplaceable:
        try:
            if data is None:
                raise ValueError(data_error)
            line = mirror(root, entry, data, stamp, env)
        except ValueError as error:
            print(f"error: data {entry['name']} not mirrored: {error}", file=out)
            status = 1
            continue
        print(f"data {entry['name']}: {line}", file=out)

    if not (args.push if args.push is not None else (manifest.get("backup") or {}).get("push", False)):
        return status
    try:
        commit = push(root, final, stamp, runner)
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"error: not pushed: {getattr(error, 'stderr', '') or error}; the local backup above is complete", file=out)
        return 1
    print(f"pushed: origin {BRANCH} {commit[:12]}, one commit that replaces the previous one", file=out)
    print(f"  restore from GitHub: git fetch origin {BRANCH} && git archive FETCH_HEAD | tar -x -C <dir>", file=out)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
