#!/usr/bin/env python3
"""Manage this CC's feature worktrees under <cc>/worktrees."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


FEATURE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# Read by Claude Code sessions started inside a worktree of the feature (see write_session_guard).
GUARD = "CLAUDE.md"
# Council run artifacts, written into the feature folder by scripts/council/run.py.
RUNS = ".cc-runs"


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be an object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def git(path: Path, *arguments: str, capture: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=True,
        capture_output=capture,
        text=True,
    )
    return result.stdout.strip() if capture else ""


class Context:
    def __init__(self, root: Path, feature: str):
        if not FEATURE.fullmatch(feature):
            raise ValueError("feature must be one directory name using letters, digits, dot, dash or underscore")
        self.root = root.resolve()
        self.feature = feature
        cc = load_json(self.root / "control-center.json")
        layout = cc["layout"]
        self.cc_name = str(cc["name"])
        self.projects_root = (self.root / layout["projectsRoot"]).resolve()
        self.repositories_root = (self.root / layout["repositories"]).resolve()
        self.worktrees_root = (self.root / layout["worktrees"]).resolve()
        self.feature_root = self.worktrees_root / feature
        self.manifest_path = self.feature_root / ".cc-worktree.json"

    def new_manifest(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "feature": self.feature,
            "controlCenter": self.cc_name,
            "repositories": {},
        }

    def load_manifest(self, required: bool = True) -> dict[str, Any]:
        if not self.manifest_path.exists():
            if required:
                raise ValueError(f"feature does not exist: {self.feature}")
            return self.new_manifest()
        data = load_json(self.manifest_path)
        if data.get("feature") != self.feature:
            raise ValueError(f"manifest feature mismatch: {self.manifest_path}")
        if data.get("controlCenter") != self.cc_name:
            raise ValueError(
                f"feature belongs to Control Center '{data.get('controlCenter')}', not '{self.cc_name}'"
            )
        return data

    def project_relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.projects_root))
        except ValueError as error:
            raise ValueError(f"path escapes Projects root: {path}") from error

    def resolve_project_path(self, value: str) -> Path:
        path = (self.projects_root / value).resolve()
        if self.projects_root not in path.parents:
            raise ValueError(f"manifest path escapes Projects root: {value}")
        return path


def repository(root: Path, identifier: str) -> dict[str, Any]:
    data = load_json(root / "catalog" / "repositories" / f"{identifier}.json")
    if data.get("id") != identifier or data.get("checkout") != identifier:
        raise ValueError(f"invalid repository descriptor: {identifier}")
    if data.get("kind") == "reference":
        raise ValueError(f"{identifier} is a reference repo: read it in its base clone, never commit to it")
    return data


def select_base_ref(base: Path, default_branch: str) -> str:
    for candidate in (f"origin/{default_branch}", default_branch):
        result = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--verify", candidate],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return candidate
    raise ValueError(f"base branch not found: {default_branch}")


def add_repositories(context: Context, manifest: dict[str, Any], identifiers: list[str], branch: str | None) -> None:
    entries = manifest.setdefault("repositories", {})
    for identifier in identifiers:
        if identifier in entries:
            raise ValueError(f"repository already belongs to feature: {identifier}")
        descriptor = repository(context.root, identifier)
        base = context.repositories_root / descriptor.get("kind", "project") / identifier
        git(base, "rev-parse", "--is-inside-work-tree")
        destination = context.feature_root / f"{identifier}_wt"
        if destination.exists():
            raise ValueError(f"worktree destination already exists: {destination}")

        branch_name = branch or context.feature
        branch_check = subprocess.run(
            ["git", "check-ref-format", "--branch", branch_name],
            capture_output=True,
            text=True,
        )
        if branch_check.returncode != 0:
            raise ValueError(f"invalid branch name: {branch_name}")
        base_ref = select_base_ref(base, str(descriptor["defaultBranch"]))
        base_head = git(base, "rev-parse", base_ref)
        base_relative = context.project_relative(base)
        worktree_relative = context.project_relative(destination)
        local_branch = subprocess.run(
            ["git", "-C", str(base), "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"]
        )
        if local_branch.returncode == 0:
            git(base, "worktree", "add", str(destination), branch_name, capture=False)
        else:
            git(base, "worktree", "add", "-b", branch_name, str(destination), base_ref, capture=False)

        entries[identifier] = {
            "sourceInput": descriptor["sourceInput"],
            "branch": branch_name,
            "baseRef": base_ref,
            "baseHead": base_head,
            "base": base_relative,
            "worktree": worktree_relative,
        }


def session_context(context: Context, manifest: dict[str, Any]) -> str:
    """What a coding session on this feature must know; given to the tool by ./cc open."""
    if manifest.get("review"):
        lines = [
            f"Feature '{context.feature}' of the Control Center {context.cc_name} ({context.root}) is a review "
            "checkout of a pull request: read the code and run its gates only. Never edit, commit or push here; "
            "review findings go to the chat, through the cc-review skill.",
        ]
        for identifier, entry in sorted(manifest.get("repositories", {}).items()):
            lines.append(f"- {identifier}: {context.resolve_project_path(entry['worktree'])} (branch {entry['branch']})")
        lines.append(f"Run its gates with `./cc feature {context.feature} verify` from {context.root}.")
        return "\n".join(lines)
    lines = [
        f"This session works on feature '{context.feature}' of the Control Center {context.cc_name} "
        f"({context.root}); its AGENTS.md rules and skills apply.",
        "Edit project code only in the feature's worktrees, never in the base clones under repos/:",
    ]
    for identifier, entry in sorted(manifest.get("repositories", {}).items()):
        worktree = context.resolve_project_path(entry["worktree"])
        lines.append(f"- {identifier}: {worktree} (branch {entry['branch']})")
    lines += [
        f"Work is done only when `./cc feature {context.feature} verify --quality` passes; run it from {context.root}.",
        "Before editing a repository, read its own AGENTS.md or CLAUDE.md if it has one: an added "
        "directory's instructions may not be loaded automatically.",
        "Use lean-code for code, tdd for tests and debug for failures; propose ./cc council for risky changes.",
    ]
    return "\n".join(lines)


def write_session_guard(context: Context, manifest: dict[str, Any]) -> None:
    # A session started inside a worktree loads this file (it is above the repository and never
    # committed), but not the CC's skills or rules: tell it to stop and restart through ./cc open.
    guard = (
        f"# Feature {context.feature} of {context.cc_name}\n\n"
        "STOP before changing code. This session was started inside a feature worktree, so the Control "
        f"Center's skills (lean-code, tdd, debug, cc-*) and its AGENTS.md rules are NOT loaded. Tell the "
        f"user to restart with:\n\n    {context.root}/cc open {context.feature}\n\n"
        "and continue only if they explicitly decide to work without the CC.\n\n"
        f"{session_context(context, manifest)}\n"
    )
    (context.feature_root / GUARD).write_text(guard, encoding="utf-8")


def command_create(root: Path, args: argparse.Namespace) -> None:
    context = Context(root, args.feature)
    if context.manifest_path.exists():
        raise ValueError(f"feature already exists: {args.feature}")
    for identifier in args.repositories:
        repository(root, identifier)  # refuse unknown and reference repos before anything is written
    manifest = context.new_manifest()
    if args.review:
        manifest["review"] = True  # a PR head to read and run gates on, never to change
    context.feature_root.mkdir(parents=True, exist_ok=True)
    if args.card:
        manifest["trelloCard"] = args.card  # on-demand updates: ./cc trello comment|move --feature
    write_json(context.manifest_path, manifest)
    for identifier in args.repositories:
        add_repositories(context, manifest, [identifier], args.branch)
        write_json(context.manifest_path, manifest)
    write_session_guard(context, manifest)
    print(f"created feature {args.feature}: {len(args.repositories)} worktree(s)")
    print(f"start working with: ./cc open {args.feature}")


def command_add(root: Path, args: argparse.Namespace) -> None:
    context = Context(root, args.feature)
    manifest = context.load_manifest()
    for identifier in args.repositories:
        add_repositories(context, manifest, [identifier], args.branch)
        write_json(context.manifest_path, manifest)
    write_session_guard(context, manifest)
    print(f"added {len(args.repositories)} worktree(s) to {args.feature}")


def command_open(root: Path, args: argparse.Namespace) -> None:
    """Start Claude Code or Codex in the CC root with the feature's worktrees added: only a session
    started in the CC loads its skills and rules (Codex reads neither from a worktree)."""
    context = Context(root, args.feature)
    manifest = context.load_manifest()
    worktrees = [str(context.resolve_project_path(entry["worktree"])) for entry in manifest.get("repositories", {}).values()]
    if not worktrees:
        raise ValueError(f"feature {args.feature} has no worktrees; add one with ./cc worktree add")
    write_session_guard(context, manifest)
    text = session_context(context, manifest)
    environment = dict(os.environ)
    if args.tool == "claude":
        argv = ["claude", *(f"--add-dir={path}" for path in worktrees), f"--append-system-prompt={text}"]
        # Also load each repository's own CLAUDE.md from the added worktrees.
        environment["CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"] = "1"
    else:
        argv = ["codex", "--cd", str(context.root)]
        for path in worktrees:
            argv += ["--add-dir", path]
        argv += ["-c", f"developer_instructions={json.dumps(text)}"]
    if args.dry_run:
        print(json.dumps({"cwd": str(context.root), "argv": argv}, indent=2))
        return
    if shutil.which(argv[0]) is None:
        raise ValueError(f"{argv[0]} is not installed")
    os.chdir(context.root)
    os.execvpe(argv[0], argv, environment)


def unpublished_commits(worktree: Path, entry: dict[str, Any]) -> int:
    upstream = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "@{upstream}"],
        capture_output=True,
        text=True,
    )
    if upstream.returncode == 0:
        return int(git(worktree, "rev-list", "--count", "@{upstream}..HEAD"))
    # Commits already in the branch the feature started from are published: ./cc worktree land put them there in a
    # repo with no remote, or a PR merged them (docs/kit/decisions/0034-new-project-cc.md).
    if is_ancestor(worktree, "HEAD", entry.get("baseRef", "")):
        return 0
    return int(git(worktree, "rev-list", "--count", f"{entry['baseHead']}..HEAD"))


def is_ancestor(repository: Path, commit: str, other: str) -> bool:
    return bool(other) and subprocess.run(
        ["git", "-C", str(repository), "merge-base", "--is-ancestor", commit, other], capture_output=True).returncode == 0


def command_land(root: Path, args: argparse.Namespace) -> None:
    """./cc worktree land <feature> <repo>: bring a finished feature into the base clone of a repo with no remote yet.

    Fast-forward only, so the base gets exactly the tree the feature's verify tested (a merge commit would be a tree
    no gate ran) and a conflict can never leave the base clone mid-merge. Needs a passing ./cc feature <feature> verify
    for that tree, or --unverified "<reason>" from the user. Why: docs/kit/decisions/0034-new-project-cc.md."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import verified

    context = Context(root, args.feature)
    manifest = context.load_manifest()
    entry = manifest.get("repositories", {}).get(args.repository)
    if entry is None:
        raise ValueError(f"repository does not belong to feature: {args.repository}")
    descriptor = repository(context.root, args.repository)
    if descriptor.get("remote") is not None:
        raise ValueError(f"{args.repository} has a remote: push the branch {entry['branch']} and open a pull request")
    worktree = context.resolve_project_path(entry["worktree"])
    base = context.resolve_project_path(entry["base"])
    default = str(descriptor["defaultBranch"])
    if git(worktree, "status", "--porcelain"):
        raise ValueError(f"worktree is dirty: {worktree}; commit or discard the changes first")
    if git(base, "status", "--porcelain"):
        raise ValueError(f"the base clone has uncommitted changes: {base}")
    if git(base, "branch", "--show-current") != default:
        raise ValueError(f"the base clone {base} is not on {default}")
    head = git(worktree, "rev-parse", "HEAD")
    if is_ancestor(worktree, head, f"refs/heads/{default}"):
        print(f"{args.repository}: {entry['branch']} is already in {default}")
        return
    if not is_ancestor(worktree, f"refs/heads/{default}", head):
        raise ValueError(f"{default} moved since {entry['branch']} started: merge or rebase {default} in {worktree}, "
                         f"run ./cc feature {args.feature} verify again, then land")
    stamp = verified.lookup(context.root, verified.commit_tree(worktree, head)) or {}
    passed = "verify" in stamp.get("kinds", [])
    reason = (args.unverified or "").strip()
    if not passed and not reason:
        raise ValueError(f"no passing ./cc feature {args.feature} verify is recorded for this tree; run it, or, if no "
                         "gate can run it, land with --unverified \"<reason>\" after the user agrees")
    git(base, "merge", "--ff-only", "--quiet", head)
    if not passed:
        entry["landedUnverified"] = reason
        write_json(context.manifest_path, manifest)
    print(f"{args.repository}: {default} fast-forwarded to {head[:12]}" +
          ("" if passed else f", NOT verified: {reason}"))


def ensure_removable(worktree: Path, entry: dict[str, Any]) -> None:
    if git(worktree, "status", "--porcelain"):
        raise ValueError(f"worktree is dirty: {worktree}")
    unpublished = unpublished_commits(worktree, entry)
    if unpublished:
        raise ValueError(f"worktree has {unpublished} unpublished commit(s): {worktree}")


def command_remove(root: Path, args: argparse.Namespace) -> None:
    context = Context(root, args.feature)
    manifest = context.load_manifest()
    entries = manifest.get("repositories", {})
    identifiers = args.repositories or sorted(entries)
    for identifier in identifiers:
        if identifier not in entries:
            raise ValueError(f"repository does not belong to feature: {identifier}")
        entry = entries[identifier]
        worktree = context.resolve_project_path(entry["worktree"])
        ensure_removable(worktree, entry)
    # Removing the last worktree removes the feature folder too. Decide before touching any worktree, so a removal
    # never ends half done: council runs are kept in an archive, anything else nobody accounted for stops it here.
    last = set(identifiers) == set(entries)
    if last:
        expected = {context.manifest_path.name, GUARD, RUNS} | {Path(entry["worktree"]).name for entry in entries.values()}
        unexpected = sorted(path.name for path in context.feature_root.iterdir() if path.name not in expected)
        if unexpected:
            raise ValueError(f"{context.feature_root} also holds {', '.join(unexpected)}; move or delete it, then remove again")

    for identifier in identifiers:
        entry = entries[identifier]
        base = context.resolve_project_path(entry["base"])
        worktree = context.resolve_project_path(entry["worktree"])
        git(base, "worktree", "remove", str(worktree), capture=False)
        del entries[identifier]
        if entries:
            write_json(context.manifest_path, manifest)

    archived = None
    if entries:
        write_json(context.manifest_path, manifest)
    else:
        runs = context.feature_root / RUNS
        if runs.is_dir():
            # Their token counts and attempt histories outlive the feature; .cc-local is never committed.
            archived = root / ".cc-local" / "runs-archive" / args.feature
            archived.mkdir(parents=True, exist_ok=True)
            for run in sorted(runs.iterdir()):
                target = archived / run.name
                if target.exists():
                    target = archived / f"{run.name}-{datetime.datetime.now():%H%M%S}"
                shutil.move(str(run), str(target))
            runs.rmdir()
        context.manifest_path.unlink()
        (context.feature_root / GUARD).unlink(missing_ok=True)
        context.feature_root.rmdir()
    print(f"removed {len(identifiers)} worktree(s) from {args.feature}; branches were preserved"
          + (f"; council runs archived in {archived.relative_to(root)}" if archived else ""))


def command_status(root: Path, args: argparse.Namespace) -> None:
    context = Context(root, args.feature)
    manifest = context.load_manifest()
    entries = manifest.get("repositories", {})
    if not entries:
        print(f"{args.feature}: no worktrees")
        return
    print("REPOSITORY\tBRANCH\tHEAD\tDIRTY\tUNPUBLISHED\tWORKTREE")
    for identifier, entry in sorted(entries.items()):
        worktree = context.resolve_project_path(entry["worktree"])
        branch = git(worktree, "branch", "--show-current") or "detached"
        head = git(worktree, "rev-parse", "--short", "HEAD")
        dirty = "yes" if git(worktree, "status", "--porcelain") else "no"
        unpublished = str(unpublished_commits(worktree, entry))
        print(f"{identifier}\t{branch}\t{head}\t{dirty}\t{unpublished}\t{worktree}")


def source_overrides(root: Path, feature: str | None = None) -> list[str]:
    """The --override-input arguments of every Nix call ./cc makes, in one place.

    A project repo with no remote yet (./cc repo new) has a tracked placeholder as its flake input; its base clone
    replaces it. A feature's worktrees then replace their repos' inputs, so the feature wins over the base clone.
    Why: docs/kit/decisions/0034-new-project-cc.md."""
    root = root.resolve()
    inputs: dict[str, str] = {}
    manifest_path = root / "control-center.json"
    if manifest_path.is_file():
        repositories_root = (root / load_json(manifest_path)["layout"]["repositories"]).resolve()
        for path in sorted((root / "catalog" / "repositories").glob("*.json")):
            data = load_json(path)
            if data.get("remote", "") is None and data.get("kind", "project") == "project":
                clone = repositories_root / "project" / str(data.get("checkout", path.stem))
                if clone.is_dir():
                    inputs[str(data.get("sourceInput", path.stem))] = f"path:{clone}"
    if feature:
        context = Context(root, feature)
        for entry in context.load_manifest().get("repositories", {}).values():
            worktree = context.resolve_project_path(entry["worktree"])
            if not worktree.is_dir():
                raise ValueError(f"worktree is missing: {worktree}")
            inputs[entry["sourceInput"]] = f"path:{worktree}"
    return [argument for name, source in inputs.items() for argument in ("--override-input", name, source)]


def command_nix_args(root: Path, args: argparse.Namespace) -> None:
    for argument in source_overrides(root, args.feature):
        print(argument)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("root", type=Path)
    subparsers = result.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("feature")
    create.add_argument("repositories", nargs="*")
    create.add_argument("--branch")
    create.add_argument("--card", help="Trello card URL to link to the feature")
    create.add_argument("--review", action="store_true", help="a pull request checkout to read and run gates on, never to change")
    create.set_defaults(handler=command_create)

    add = subparsers.add_parser("add")
    add.add_argument("feature")
    add.add_argument("repositories", nargs="+")
    add.add_argument("--branch")
    add.set_defaults(handler=command_add)

    remove = subparsers.add_parser("remove")
    remove.add_argument("feature")
    remove.add_argument("repositories", nargs="*")
    remove.set_defaults(handler=command_remove)

    land = subparsers.add_parser("land", help="fast-forward the base clone of a repo with no remote to the feature branch")
    land.add_argument("feature")
    land.add_argument("repository")
    land.add_argument("--unverified", metavar="REASON", help="land without a passing verify; the user's reason is kept")
    land.set_defaults(handler=command_land)

    status = subparsers.add_parser("status")
    status.add_argument("feature")
    status.set_defaults(handler=command_status)

    open_ = subparsers.add_parser("open", help="start Claude Code or Codex in the CC with the feature's worktrees")
    open_.add_argument("feature")
    open_.add_argument("--tool", choices=("claude", "codex"), default="claude")
    open_.add_argument("--dry-run", action="store_true", help="print the command instead of starting it")
    open_.set_defaults(handler=command_open)

    nix_args = subparsers.add_parser("nix-args")
    nix_args.add_argument("feature", nargs="?", help="also override the inputs of this feature's worktrees")
    nix_args.set_defaults(handler=command_nix_args)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        args.handler(args.root.resolve(), args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
