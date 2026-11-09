#!/usr/bin/env python3
"""Manage tracked repository descriptors and this CC's base clones in repos/<kind>/<id>."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import hostos


ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
STATUSES = {"discovered", "adapted", "verified", "blocked"}
# project: built, verified and changed through feature worktrees. reference: read-only guidance, never committed to.
KINDS = ("project", "reference")
# Platforms a repository ships on; each one needs its own gate in the adapter.
TARGETS = ("windows", "linux", "macos")


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be an object")
    return data


def layout(root: Path) -> tuple[Path, Path]:
    manifest = load_json(root / "control-center.json")
    projects_root = (root / manifest["layout"]["projectsRoot"]).resolve()
    repositories_root = (root / manifest["layout"]["repositories"]).resolve()
    return projects_root, repositories_root


def base_clone(root: Path, identifier: str, kind: str | None = None) -> Path:
    """repos/<kind>/<id>: the folder says what a clone is, in every path an agent reads
    (docs/kit/decisions/0016-per-cc-repos-and-worktrees.md)."""
    if kind is None:
        path = descriptor_path(root, identifier)
        kind = load_json(path).get("kind", "project") if path.is_file() else "project"
    if kind not in KINDS:
        raise ValueError(f"unknown repository kind: {kind}")
    return layout(root)[1] / kind / identifier


def protect(checkout: Path, read_only: bool) -> None:
    """Make a reference clone's files and folders read-only on disk, or writable again for an update.

    The OS then refuses every writer (Claude, Codex, Cursor, a person), not only the ones that follow rules.
    .git stays writable, so fetching still works. On Windows this is a deny-write ACL, since a read-only flag
    neither closes folders nor survives a delete (hostos.write_protect)."""
    hostos.write_protect(checkout, read_only, keep=".git")


def descriptor_path(root: Path, identifier: str) -> Path:
    if not ID.fullmatch(identifier):
        raise ValueError("repository id must use lowercase hyphen-case")
    return root / "catalog" / "repositories" / f"{identifier}.json"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_targets(values: list[str]) -> list[str]:
    targets = {value.strip().lower() for item in values for value in item.split(",") if value.strip()}
    if not targets:
        raise ValueError(f"at least one target is required: {', '.join(TARGETS)}")
    unknown = sorted(targets - set(TARGETS))
    if unknown:
        raise ValueError(f"unsupported target(s): {', '.join(unknown)}; use {', '.join(TARGETS)}")
    return [target for target in TARGETS if target in targets]


def parse_stack(values: list[str]) -> list[str]:
    # Languages are open-ended (java, python, typescript, csharp, cpp, rust, ...); keep the given order.
    stack = list(dict.fromkeys(value.strip().lower() for item in values for value in item.split(",") if value.strip()))
    if not stack:
        raise ValueError("at least one stack entry is required, e.g. java or python")
    invalid = [entry for entry in stack if not ID.fullmatch(entry)]
    if invalid:
        raise ValueError(f"stack entries must use lowercase hyphen-case: {', '.join(invalid)}")
    return stack


def is_git_checkout(path: Path) -> bool:
    if not path.is_dir():
        return False
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def canonical_remote(value: str, base: Path) -> str:
    if "://" in value or re.match(r"^[^/@]+@[^:]+:", value):
        return value.rstrip("/")
    path = Path(value)
    return str((path if path.is_absolute() else base / path).resolve())


def require_remote(checkout: Path, expected: str, root: Path) -> None:
    if not is_git_checkout(checkout):
        return
    result = subprocess.run(
        ["git", "-C", str(checkout), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    )
    actual = result.stdout.strip()
    if canonical_remote(actual, root) != canonical_remote(expected, root):
        raise ValueError(f"base checkout remote mismatch: expected {expected}, found {actual}")


def is_url(value: str) -> bool:
    return "://" in value or bool(re.match(r"^[^/@]+@[^:]+:", value))


def id_from_remote(remote: str) -> str:
    """https://github.com/org/Foo-Bar.git -> foo-bar"""
    name = re.split(r"[/:]", remote.rstrip("/"))[-1].removesuffix(".git")
    identifier = "-".join(re.findall(r"[a-z0-9]+", name.lower()))
    if not identifier:
        raise ValueError(f"cannot derive a repository id from {remote}; pass --id")
    return identifier


def remote_default_branch(remote: str) -> str:
    """The branch the remote's HEAD points to: main, develop, ..."""
    result = subprocess.run(["git", "ls-remote", "--symref", "--", remote, "HEAD"], capture_output=True, text=True, timeout=60)
    match = re.search(r"^ref: refs/heads/(\S+)\tHEAD", result.stdout, re.MULTILINE)
    if result.returncode != 0 or not match:
        raise ValueError(f"cannot read the default branch of {remote}: {result.stderr.strip() or 'no HEAD'}; pass --branch")
    return match.group(1)


def remote_has_branch(remote: str, branch: str) -> bool | None:
    """True or False when the remote answered, None when it could not be reached."""
    result = subprocess.run(
        ["git", "ls-remote", "--heads", "--", remote, f"refs/heads/{branch}"], capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def features_using(root: Path, identifier: str) -> list[str]:
    manifest = load_json(root / "control-center.json")
    worktrees_root = (root / manifest["layout"]["worktrees"]).resolve()
    return sorted(
        path.parent.name
        for path in worktrees_root.glob("*/.cc-worktree.json")
        if identifier in load_json(path).get("repositories", {})
    )


def command_add(root: Path, args: argparse.Namespace) -> None:
    # ./cc repo add <url> derives the id; ./cc repo add <id> --remote <url> names it.
    if is_url(args.repository):
        args.remote = args.remote or args.repository
        args.id = args.id or id_from_remote(args.repository)
    else:
        args.id = args.repository
    if not args.remote:
        raise ValueError("give the repository URL: ./cc repo add <url> or ./cc repo add <id> --remote <url>; "
                         "to start a new project repo with no remote, run ./cc repo new <id>")
    reference = args.kind == "reference"
    targets = parse_targets(args.targets) if args.targets or not reference else []
    stack = parse_stack(args.stack) if args.stack or not reference else []
    path = descriptor_path(root, args.id)
    if path.exists():
        raise ValueError(f"repository already exists: {args.id}")
    if Path(args.remote).is_absolute():
        raise ValueError("remote must be a portable Git URL, not an absolute local path")
    args.branch = args.branch or remote_default_branch(args.remote)

    checkout = base_clone(root, args.id, args.kind)
    if args.clone:
        checkout.parent.mkdir(parents=True, exist_ok=True)
        if checkout.exists() and not is_git_checkout(checkout):
            raise ValueError(f"checkout exists but is not a Git worktree: {checkout}")
        if not checkout.exists():
            subprocess.run(
                ["git", "clone", "--branch", args.branch, "--", args.remote, str(checkout)],
                check=True,
            )
        require_remote(checkout, args.remote, root)
        if args.kind == "reference":
            protect(checkout, read_only=True)
    elif checkout.exists():
        if not is_git_checkout(checkout):
            raise ValueError(f"checkout exists but is not a Git worktree: {checkout}")
        require_remote(checkout, args.remote, root)

    write_json(
        path,
        {
            "schemaVersion": 1,
            "id": args.id,
            "checkout": args.id,
            "remote": args.remote,
            "defaultBranch": args.branch,
            "kind": args.kind,
            "role": args.role or args.kind,
            "targets": targets,
            "stack": stack,
            "sourceInput": args.source_input or args.id,
            "adapter": args.adapter or f"nix/projects/{args.id}.nix",
            "status": "discovered",
        },
    )
    state = "present" if is_git_checkout(checkout) else "absent"
    print(f"added {args.id} ({args.kind}, branch {args.branch}): base checkout {state} at {checkout}")


def last_backup(root: Path) -> str | None:
    """The newest ./cc backup stamp in any folder ./cc backup recorded, or None (docs/kit/decisions/0030-disk-management.md)."""
    try:
        known = json.loads((root / ".cc-local" / "hooks" / "backup-locations.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    stamps = [entry.name for folder in known.get("backups", []) if Path(folder).is_dir()
              for entry in Path(folder).iterdir() if entry.is_dir() and re.fullmatch(r"\d{8}-\d{6}", entry.name)]
    return max(stamps, default=None)


def only_copy_warning(root: Path, identifier: str) -> str:
    """A repo with no remote has its whole history in one ignored folder (risk R1 of plans new-project-cc)."""
    stamp = last_backup(root)
    when = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if stamp else "never"
    return (f"warning: {identifier} has no remote: repos/project/{identifier} is the only copy of its history on this "
            f"disk; last backup: {when}. Run ./cc backup --to <folder> (or set backup.path), or add a remote with "
            f"./cc repo set-remote {identifier} <url>")


def attention(root: Path, nix_found: bool) -> list[str]:
    """What ./cc doctor and ./cc verify point out about the catalog: each repo with no remote is the only copy of its
    history (R1), and a blocked repo may be gated on a host that has Nix, so blocked never reads as tested (R5)."""
    lines = []
    for path in sorted((root / "catalog" / "repositories").glob("*.json")):
        data = load_json(path)
        identifier = str(data.get("id", path.stem))
        if data.get("kind", "project") != "project":
            continue
        if data.get("remote", "") is None and is_git_checkout(base_clone(root, identifier, "project")):
            lines.append(only_copy_warning(root, identifier))
        if data.get("status") == "blocked" and nix_found:
            lines.append(f"note: {identifier} is blocked ({data.get('blockedReason', 'no reason given')}), but this host "
                         f"has Nix: if its adapter declares gates, run ./cc verify {identifier} and set it verified")
    return lines


def command_attention(root: Path, _: argparse.Namespace) -> None:
    for line in attention(root, hostos.resolve_exe("nix") is not None):
        print(line)


def git_identity_missing(root: Path) -> list[str]:
    """What git lacks to make the first commit: user.name and user.email, from the environment or the config."""
    missing = []
    for key, variables in (("user.name", ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME")),
                           ("user.email", ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"))):
        if all(os.environ.get(variable) for variable in variables):
            continue
        found = subprocess.run(["git", "config", "--get", key], cwd=root, capture_output=True, text=True)
        if found.returncode != 0 or not found.stdout.strip():
            missing.append(key)
    return missing


def command_new(root: Path, args: argparse.Namespace) -> None:
    """./cc repo new <id>: start a new project repo with no remote yet (docs/kit/decisions/0034-new-project-cc.md).

    git init in repos/project/<id> with one first commit (worktrees need a base) that holds a .gitattributes with
    LF line ends (Windows checkouts with core.autocrlf would give WSL gates CRLF), a descriptor with remote null, and a
    tracked placeholder source in nix/placeholders/<id> that ./cc overrides with the base clone on every Nix call."""
    path = descriptor_path(root, args.id)  # checks the id before anything is written
    if path.exists():
        raise ValueError(f"repository already exists: {args.id}")
    targets = parse_targets(args.targets)
    stack = parse_stack(args.stack)
    checkout = base_clone(root, args.id, "project")
    if checkout.exists() and any(checkout.iterdir()):
        raise ValueError(f"{checkout} already exists and is not empty; add an existing repo with ./cc repo add <url>")
    missing = git_identity_missing(root)
    if missing:
        raise ValueError(f"git has no {' and no '.join(missing)} for the first commit; set them with "
                         "git config --global user.name \"<name>\" and git config --global user.email <email>")
    checkout.mkdir(parents=True, exist_ok=True)
    git = ["git", "-C", str(checkout)]
    subprocess.run(["git", "init", "-q", "-b", args.branch, str(checkout)], check=True)
    (checkout / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")
    subprocess.run([*git, "add", ".gitattributes"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", f"Start {args.id}"], check=True)
    placeholder = root / "nix" / "placeholders" / args.id / "CC_PLACEHOLDER"
    placeholder.parent.mkdir(parents=True, exist_ok=True)
    placeholder.write_bytes(
        f"{args.id} has no remote yet. Its flake input points here, and ./cc overrides it with repos/project/{args.id}; "
        "the adapter refuses this placeholder, so no gate passes without the real code.\n".encode())
    write_json(
        path,
        {
            "schemaVersion": 1,
            "id": args.id,
            "checkout": args.id,
            "remote": None,
            "defaultBranch": args.branch,
            "kind": "project",
            "role": args.role or "project",
            "targets": targets,
            "stack": stack,
            "sourceInput": args.id,
            "adapter": f"nix/projects/{args.id}.nix",
            "status": "discovered",
        },
    )
    print(f"created {args.id} (project, branch {args.branch}, no remote) at {checkout}")
    print(f"flake input for the adapter: {args.id} = {{ url = \"path:./nix/placeholders/{args.id}\"; flake = false; }};")
    print(only_copy_warning(root, args.id))


def stray_clones(root: Path) -> list[str]:
    """Folders in this CC's repos/ that its catalog does not list: a clone nobody declared is out of scope
    (docs/kit/decisions/0016-per-cc-repos-and-worktrees.md). Nix checks cannot see the ignored folder, so verify
    and doctor ask this instead. The kit template has no control-center.json and no clones, so no strays."""
    if not (root / "control-center.json").is_file():
        return []
    _, repositories_root = layout(root)
    if not repositories_root.is_dir():
        return []
    kinds = {path.stem: load_json(path).get("kind", "project") for path in (root / "catalog" / "repositories").glob("*.json")}
    strays = []
    for entry in sorted(repositories_root.iterdir()):
        if entry.name not in KINDS:
            strays.append(entry.name)
            continue
        strays += [f"{entry.name}/{clone.name}" for clone in sorted(entry.iterdir()) if kinds.get(clone.name) != entry.name]
    return strays


def command_strays(root: Path, _: argparse.Namespace) -> None:
    strays = stray_clones(root)
    if strays:
        raise ValueError(f"repos/ holds clones the catalog does not list under that kind: {', '.join(strays)}; "
                         "add them with ./cc repo add <url>, fix the kind with ./cc repo set-kind, or delete them")
    print("repos/ matches the catalog")


def command_update(root: Path, args: argparse.Namespace) -> None:
    """Bring base clones up to date: fetch every one, and fast-forward a reference clone's files, lifting its
    read-only protection only for the pull."""
    identifiers = args.ids or sorted(path.stem for path in (root / "catalog" / "repositories").glob("*.json"))
    for identifier in identifiers:
        data = load_json(descriptor_path(root, identifier))
        checkout = base_clone(root, identifier, data.get("kind", "project"))
        if not is_git_checkout(checkout):
            print(f"{identifier}: no base clone at {checkout}; clone it with ./cc repo add --clone")
            continue
        if data.get("remote") is None:
            print(f"{identifier}: no remote yet, nothing to fetch")
            continue
        if data.get("kind") == "reference":
            protect(checkout, read_only=False)
            try:
                subprocess.run(["git", "-C", str(checkout), "pull", "--ff-only", "--quiet"], check=True)
            finally:
                protect(checkout, read_only=True)
            print(f"{identifier}: reference clone fast-forwarded and read-only again")
        else:
            subprocess.run(["git", "-C", str(checkout), "fetch", "--prune", "--quiet", "origin"], check=True)
            print(f"{identifier}: fetched; new feature worktrees start from origin/{data.get('defaultBranch', 'main')}")


def command_list(root: Path, _: argparse.Namespace) -> None:
    directory = root / "catalog" / "repositories"
    rows: list[tuple[str, ...]] = []
    for path in sorted(directory.glob("*.json")):
        data = load_json(path)
        identifier = str(data.get("id", path.stem))
        present = "yes" if is_git_checkout(base_clone(root, identifier, data.get("kind", "project"))) else "no"
        targets = ",".join(data.get("targets") or []) or "?"
        stack = ",".join(data.get("stack") or []) or "?"
        rows.append(
            (identifier, str(data.get("kind", "project")), str(data.get("defaultBranch", "?")), str(data.get("status", "?")),
             targets, stack, present, "none (local)" if data.get("remote", "?") is None else str(data.get("remote", "?")))
        )
    if not rows:
        print("no repositories")
        return
    print("ID\tKIND\tBRANCH\tSTATUS\tTARGETS\tSTACK\tBASE\tREMOTE")
    for row in rows:
        print("\t".join(row))


def command_show(root: Path, args: argparse.Namespace) -> None:
    path = descriptor_path(root, args.id)
    print(path.read_text(encoding="utf-8"), end="")


def command_status(root: Path, args: argparse.Namespace) -> None:
    if args.status not in STATUSES:
        raise ValueError(f"unsupported status: {args.status}")
    # A blocked repo says why, so a ready CC never reads as tested (docs/kit/decisions/0034-new-project-cc.md).
    if args.status == "blocked" and not (args.reason or "").strip():
        raise ValueError(f"say why it is blocked: ./cc repo set-status {args.id} blocked --reason \"<why>\"")
    if args.status != "blocked" and args.reason:
        raise ValueError("--reason is only for the blocked status")
    path = descriptor_path(root, args.id)
    data = load_json(path)
    data["status"] = args.status
    if args.status == "blocked":
        data["blockedReason"] = args.reason.strip()
    else:
        data.pop("blockedReason", None)
    write_json(path, data)
    print(f"{args.id}: {args.status}" + (f" ({data['blockedReason']})" if args.status == "blocked" else ""))


def command_targets(root: Path, args: argparse.Namespace) -> None:
    targets = parse_targets(args.targets)
    path = descriptor_path(root, args.id)
    data = load_json(path)
    # Verification covered the previous targets only; a changed set must be verified again.
    reset = data.get("targets") != targets and data.get("status") == "verified"
    data["targets"] = targets
    if reset:
        data["status"] = "adapted"
    write_json(path, data)
    print(f"{args.id}: targets {', '.join(targets)}")
    if reset:
        print(f"{args.id}: status reset to adapted; run ./cc verify {args.id}, then set-status verified")


def command_stack(root: Path, args: argparse.Namespace) -> None:
    stack = parse_stack(args.stack)
    path = descriptor_path(root, args.id)
    data = load_json(path)
    data["stack"] = stack
    write_json(path, data)
    print(f"{args.id}: stack {', '.join(stack)}")


def command_remove(root: Path, args: argparse.Namespace) -> None:
    path = descriptor_path(root, args.id)
    if not path.exists():
        raise ValueError(f"not in the catalog: {args.id}")
    features = features_using(root, args.id)
    if features:
        raise ValueError(f"{args.id} is used by feature(s) {', '.join(features)}; remove those worktrees first")
    data = load_json(path)
    checkout = base_clone(root, args.id, data.get("kind", "project"))
    if data.get("kind") == "reference" and checkout.is_dir():
        protect(checkout, read_only=False)  # a clone outside the catalog is a stray to delete, not evidence to keep
    path.unlink()
    print(f"removed {args.id} from the catalog")
    if data.get("remote") is None and is_git_checkout(checkout):
        print(only_copy_warning(root, args.id))
    if is_git_checkout(checkout):
        print(f"note: the base clone {checkout} is kept, writable, and ./cc repo strays reports it; delete it yourself "
              "if the repo is gone for good")
    adapter = data.get("adapter")
    if adapter and (root / adapter).is_file():
        print(f"note: {adapter} and its flake input still exist; remove them if the repo is gone for good")


def command_branch(root: Path, args: argparse.Namespace) -> None:
    path = descriptor_path(root, args.id)
    data = load_json(path)
    if data.get("remote") is None:
        checkout = base_clone(root, args.id, data.get("kind", "project"))
        local = subprocess.run(["git", "-C", str(checkout), "rev-parse", "--verify", "--quiet", f"refs/heads/{args.branch}"],
                               capture_output=True, text=True)
        if local.returncode != 0:
            raise ValueError(f"{checkout} has no branch {args.branch}")
        data["defaultBranch"] = args.branch
        write_json(path, data)
        print(f"{args.id}: default branch {args.branch}")
        return
    found = remote_has_branch(str(data["remote"]), args.branch)
    if found is False:
        raise ValueError(f"{data['remote']} has no branch {args.branch}")
    data["defaultBranch"] = args.branch
    write_json(path, data)
    print(f"{args.id}: default branch {args.branch}")
    if found is None:
        print(f"warning: could not reach {data['remote']} to confirm the branch exists")


def command_remote(root: Path, args: argparse.Namespace) -> None:
    if not is_url(args.remote):
        raise ValueError("remote must be a portable Git URL")
    path = descriptor_path(root, args.id)
    data = load_json(path)
    if data.get("remote") is None:
        connect_remote(root, args.id, data, args.remote)
        data["remote"] = args.remote
        write_json(path, data)  # last: a failure before this leaves the catalog as it was, so a retry is safe
        print(f"{args.id}: remote {args.remote}; push with git -C repos/project/{args.id} push -u origin "
              f"{data['defaultBranch']}, then features use the normal push and PR flow")
        return
    data["remote"] = args.remote
    write_json(path, data)
    print(f"{args.id}: remote {args.remote}")
    try:
        require_remote(base_clone(root, args.id, data.get("kind", "project")), args.remote, root)
    except ValueError as error:
        print(f"warning: {error}; update the clone with git remote set-url origin {args.remote}")


def connect_remote(root: Path, identifier: str, data: dict[str, Any], remote: str) -> None:
    """Give a repo made with ./cc repo new its origin. The remote must be empty, or its default branch must already be in
    the local history: a remote made with a README would make new features start from that unrelated commit (R6)."""
    checkout = base_clone(root, identifier, data.get("kind", "project"))
    if not is_git_checkout(checkout):
        raise ValueError(f"no base clone at {checkout}")
    git = ["git", "-C", str(checkout)]
    existing = subprocess.run([*git, "remote", "get-url", "origin"], capture_output=True, text=True)
    if existing.returncode == 0 and canonical_remote(existing.stdout.strip(), root) != canonical_remote(remote, root):
        raise ValueError(f"{checkout} already has origin {existing.stdout.strip()}; remove it or use that URL")
    if existing.returncode != 0:
        subprocess.run([*git, "remote", "add", "origin", "--", remote], check=True)
    try:
        heads = subprocess.run([*git, "ls-remote", "--heads", "origin"], capture_output=True, text=True, timeout=60)
        if heads.returncode != 0:
            raise ValueError(f"cannot reach {remote}: {heads.stderr.strip()}")
        if heads.stdout.strip():
            branch = data["defaultBranch"]
            subprocess.run([*git, "fetch", "--quiet", "origin"], check=True, timeout=300)
            remote_branch = f"refs/remotes/origin/{branch}"
            if subprocess.run([*git, "rev-parse", "--verify", "--quiet", remote_branch], capture_output=True).returncode:
                raise ValueError(f"{remote} is not empty and has no branch {branch}; use an empty remote")
            if subprocess.run([*git, "merge-base", "--is-ancestor", remote_branch, f"refs/heads/{branch}"]).returncode:
                raise ValueError(f"{remote} has commits that are not in {identifier}'s history (for example a README made "
                                 "on the website); use an empty remote")
    except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        if existing.returncode != 0:
            subprocess.run([*git, "remote", "remove", "origin"], capture_output=True)
        raise


def command_kind(root: Path, args: argparse.Namespace) -> None:
    path = descriptor_path(root, args.id)
    data = load_json(path)
    if args.kind == "project" and not (data.get("targets") and data.get("stack")):
        raise ValueError(f"a project repo needs targets and a stack: run set-targets and set-stack on {args.id} first")
    old_kind = data.get("kind", "project")
    if args.kind == old_kind:
        print(f"{args.id}: already {args.kind}")
        return
    if args.kind == "reference" and features_using(root, args.id):
        raise ValueError(f"{args.id} is used by feature(s) {', '.join(features_using(root, args.id))}; remove those worktrees first")
    # The clone moves with the kind: repos/<kind>/<id> is what tells agents what it is.
    source, destination = base_clone(root, args.id, old_kind), base_clone(root, args.id, args.kind)
    if source.exists():
        if destination.exists():
            raise ValueError(f"{destination} already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if old_kind == "reference":
            protect(source, read_only=False)
        source.rename(destination)
        if args.kind == "reference":
            protect(destination, read_only=True)
        print(f"moved the base clone to {destination}" + (" (read-only)" if args.kind == "reference" else ""))
    data["kind"] = args.kind
    write_json(path, data)
    print(f"{args.id}: {args.kind}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("root", type=Path)
    subparsers = result.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add")
    add.add_argument("repository", help="the Git URL, or the id when --remote is given")
    add.add_argument("--id", help="catalog id; derived from the URL by default")
    add.add_argument("--remote")
    add.add_argument("--kind", choices=KINDS, default="project", help="reference: read-only guidance, never committed to")
    add.add_argument("--role", help="what the repo is, e.g. backend API; defaults to the kind")
    add.add_argument("--targets", nargs="+", default=[], help=f"platforms it ships on: {', '.join(TARGETS)}")
    add.add_argument("--stack", nargs="+", default=[], help="languages, e.g. java, python, typescript, csharp")
    add.add_argument("--branch", help="main branch; read from the remote by default")
    add.add_argument("--source-input")
    add.add_argument("--adapter")
    add.add_argument("--clone", action="store_true")
    add.set_defaults(handler=command_add)

    new = subparsers.add_parser("new", help="start a new project repo with no remote yet: git init in repos/project/<id>")
    new.add_argument("id")
    new.add_argument("--targets", nargs="+", default=[], help=f"platforms it ships on: {', '.join(TARGETS)}")
    new.add_argument("--stack", nargs="+", default=[], help="languages, e.g. java, python, typescript, csharp")
    new.add_argument("--branch", default="main", help="main branch (default: main)")
    new.add_argument("--role", help="what the repo is, e.g. backend API")
    new.set_defaults(handler=command_new)

    listing = subparsers.add_parser("list")
    listing.set_defaults(handler=command_list)

    show = subparsers.add_parser("show")
    show.add_argument("id")
    show.set_defaults(handler=command_show)

    status = subparsers.add_parser("set-status")
    status.add_argument("id")
    status.add_argument("status")
    status.add_argument("--reason", help="why the repo is blocked; required for blocked")
    status.set_defaults(handler=command_status)

    targets = subparsers.add_parser("set-targets")
    targets.add_argument("id")
    targets.add_argument("targets", nargs="+", help=f"platforms it ships on: {', '.join(TARGETS)}")
    targets.set_defaults(handler=command_targets)

    stack = subparsers.add_parser("set-stack")
    stack.add_argument("id")
    stack.add_argument("stack", nargs="+", help="languages, e.g. java, python, typescript, csharp")
    stack.set_defaults(handler=command_stack)

    remove = subparsers.add_parser("remove", help="drop from the catalog; the base clone is kept")
    remove.add_argument("id")
    remove.set_defaults(handler=command_remove)

    branch = subparsers.add_parser("set-branch", help="the main branch new feature worktrees start from")
    branch.add_argument("id")
    branch.add_argument("branch")
    branch.set_defaults(handler=command_branch)

    remote = subparsers.add_parser("set-remote")
    remote.add_argument("id")
    remote.add_argument("remote")
    remote.set_defaults(handler=command_remote)

    kind = subparsers.add_parser("set-kind")
    kind.add_argument("id")
    kind.add_argument("kind", choices=KINDS)
    kind.set_defaults(handler=command_kind)
    strays = subparsers.add_parser("strays", help="fail when repos/ holds a clone the catalog does not list")
    strays.set_defaults(handler=command_strays)
    attention_parser = subparsers.add_parser("attention", help="repos with no remote and blocked repos worth a look")
    attention_parser.set_defaults(handler=command_attention)
    update = subparsers.add_parser("update", help="fetch base clones; fast-forward reference clones, keeping them read-only")
    update.add_argument("ids", nargs="*")
    update.set_defaults(handler=command_update)
    return result


def main() -> int:
    args = parser().parse_args()
    root = args.root.resolve()
    try:
        args.handler(root, args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
