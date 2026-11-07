#!/usr/bin/env python3
"""./cc migrate-layout: move this CC's clones to repos/<kind>/<id> and its features to worktrees/<feature>,
inside the CC (docs/kit/decisions/0016-per-cc-repos-and-worktrees.md).

It moves a CC from the old shared ../repos and ../worktrees, and nests the flat repos/<id> clones of the
first per-CC layout under repos/project/ and repos/reference/. Only this CC's catalog clones and the
features whose manifest names this CC move. Worktree links are repaired, manifests, council run state and
session guards rewritten, reference clones made read-only, and the layout in control-center.json updated
last. Every check runs before the first move, so a refused run changes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import hostos
import repositories

NEW_LAYOUT = {"projectsRoot": ".", "repositories": "repos", "worktrees": "worktrees", "worktreePattern": "{feature}/{repository}_wt"}
IGNORED = ("/repos/", "/worktrees/")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(directory), *arguments], check=True, capture_output=True, text=True).stdout


def worktree_paths(base: Path) -> list[Path]:
    """Linked worktrees of a base clone, from `git worktree list` (the main checkout excluded)."""
    listed = [Path(line[9:]) for line in git(base, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]
    return [path for path in listed if path.resolve() != base.resolve()]


def shared_with(root: Path, projects: Path, old_repos: Path, identifier: str) -> list[str]:
    """Other CCs next to this one whose catalog also lists the clone in the same shared folder."""
    users = []
    for other in sorted(projects.iterdir()):
        config = other / "control-center.json"
        if other.resolve() == root.resolve() or not config.is_file():
            continue
        try:
            layout = load_json(config)["layout"]
        except (OSError, ValueError, KeyError):
            continue
        if (other / layout["repositories"]).resolve() == old_repos and (other / "catalog" / "repositories" / f"{identifier}.json").is_file():
            users.append(other.name)
    return users


def plan(root: Path) -> dict[str, Any]:
    config = load_json(root / "control-center.json")
    layout = config["layout"]
    shared = {key: layout.get(key) for key in NEW_LAYOUT} != NEW_LAYOUT
    old_repos = (root / layout["repositories"]).resolve()
    old_worktrees = (root / layout["worktrees"]).resolve()
    problems: list[str] = []
    kinds = {path.stem: load_json(path).get("kind", "project") for path in sorted((root / "catalog" / "repositories").glob("*.json"))}

    repos: list[tuple[str, Path, Path]] = []
    for identifier, kind in kinds.items():
        source = old_repos / identifier
        destination = repositories.base_clone(root, identifier, kind) if not shared else root / "repos" / kind / identifier
        if identifier in repositories.KINDS:
            problems.append(f"repo id {identifier} collides with the repos/{identifier}/ kind folder; rename it in the catalog")
            continue
        if not (source / ".git").exists() or source.resolve() == destination.resolve():
            continue
        if shared:
            users = shared_with(root, (root / layout["projectsRoot"]).resolve(), old_repos, identifier)
            if users:
                problems.append(f"{source} is also in the catalog of {', '.join(users)}: clone it into each CC's repos/ by hand")
        if destination.exists():
            problems.append(f"{destination} already exists")
        repos.append((identifier, source, destination))

    features: list[tuple[Path, Path]] = []
    if shared and old_worktrees.is_dir():
        for manifest_path in sorted(old_worktrees.glob("*/.cc-worktree.json")):
            manifest = load_json(manifest_path)
            if manifest.get("controlCenter") != config["name"]:
                continue  # another CC's feature
            source, destination = manifest_path.parent, root / "worktrees" / manifest_path.parent.name
            if destination.exists():
                problems.append(f"{destination} already exists")
            unknown = sorted(set(manifest.get("repositories", {})) - set(kinds))
            if unknown:
                problems.append(f"feature {source.name} uses repos outside the catalog: {', '.join(unknown)}")
            features.append((source, destination))
    return {"config": config, "shared": shared, "old_repos": old_repos, "old_worktrees": old_worktrees, "kinds": kinds,
            "repos": repos, "features": features, "problems": problems}


def move(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(source, destination)
    except OSError:  # another drive: copy, then delete (git's read-only pack files need hostos.rmtree on Windows)
        shutil.copytree(source, destination, symlinks=True)
        hostos.rmtree(source)


def replace_paths(path: Path, pairs: list[tuple[str, str]]) -> None:
    """Replace whole path names only: /repos/shop must not rewrite /repos/shop-backend."""
    text = path.read_text(encoding="utf-8")
    # JSON doubles each backslash of a Windows path, so the escaped spelling is replaced as well.
    escaped = [(json.dumps(old)[1:-1], json.dumps(new)[1:-1]) for old, new in pairs if json.dumps(old)[1:-1] != old]
    for old, new in sorted(pairs + escaped, key=lambda pair: -len(pair[0])):
        # A period ends the path only at the end of a sentence: feature names may contain dots (v1.2).
        text = re.sub(re.escape(old) + r"(?=[/\\\"'\s:,;)]|\.(?:\s|$)|$)", new.replace("\\", "\\\\"), text)
    path.write_text(text, encoding="utf-8")


def migrate(root: Path, dry_run: bool) -> int:
    root = root.resolve()
    steps = plan(root)
    for _, source, destination in steps["repos"]:
        print(f"clone    {source} -> {destination}")
    for source, destination in steps["features"]:
        print(f"feature  {source} -> {destination}")
    if steps["problems"]:
        print("refused, nothing was moved:", file=sys.stderr)
        for problem in steps["problems"]:
            print(f"- {problem}", file=sys.stderr)
        return 1
    if dry_run:
        print("dry run: nothing was moved")
        return 0

    # Where every linked worktree of each clone will be after the moves, so repair can fix both ends.
    feature_moves = {source.resolve(): destination for source, destination in steps["features"]}
    links: dict[Path, list[Path]] = {}
    for _, source, destination in steps["repos"]:
        links[destination] = [
            feature_moves[path.resolve().parent] / path.name if path.resolve().parent in feature_moves else path
            for path in worktree_paths(source)
        ]
    for identifier, source, destination in steps["repos"]:
        if steps["kinds"].get(identifier) == "reference":
            repositories.protect(source, read_only=False)  # protected again below, at its new place
        move(source, destination)
    for source, destination in steps["features"]:
        move(source, destination)
    for base, targets in links.items():
        existing = [str(path) for path in targets if path.exists()]
        if existing:
            git(base, "worktree", "repair", *existing)

    # Rewrite every feature of this CC: manifests name the new places, run state and guards hold absolute paths.
    pairs = [(str(source), str(destination)) for _, source, destination in steps["repos"]]
    pairs += [(str(source), str(destination)) for source, destination in steps["features"]]
    for manifest_path in sorted((root / "worktrees").glob("*/.cc-worktree.json")):
        manifest = load_json(manifest_path)
        if manifest.get("controlCenter") != steps["config"]["name"]:
            continue
        feature = manifest_path.parent
        for identifier, entry in manifest.get("repositories", {}).items():
            kind = steps["kinds"].get(identifier, "project")
            entry["base"] = f"repos/{kind}/{identifier}"
            entry["worktree"] = (feature / f"{identifier}_wt").relative_to(root).as_posix()
            git(feature / f"{identifier}_wt", "rev-parse", "HEAD")  # the worktree still resolves
        write_json(manifest_path, manifest)
        for path in [*feature.glob(".cc-runs/*/state.json"), feature / "CLAUDE.md"]:
            if path.is_file():
                replace_paths(path, pairs)

    for identifier, kind in steps["kinds"].items():
        clone = root / "repos" / kind / identifier
        if kind == "reference" and (clone / ".git").exists():
            repositories.protect(clone, read_only=True)

    config = steps["config"]
    if steps["shared"]:
        config["layout"] = {**config["layout"], **NEW_LAYOUT}
        write_json(root / "control-center.json", config)
        for folder in (steps["old_repos"], steps["old_worktrees"]):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
                print(f"removed the empty shared folder {folder}")
    gitignore = root / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.is_file() else []
    missing = [entry for entry in IGNORED if entry not in lines]
    if missing:
        gitignore.write_text("\n".join(lines + ["", "# Base clones and feature worktrees of this CC.", *missing]) + "\n", encoding="utf-8")
    if not steps["repos"] and not steps["features"]:
        print("already on the per-CC layout: repos/<kind>/<id> and worktrees/; reference clones are read-only")
        return 0
    print(f"moved {len(steps['repos'])} clones and {len(steps['features'])} features; reference clones are read-only. "
          "Run ./cc check and commit control-center.json and .gitignore.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="./cc migrate-layout")
    parser.add_argument("root", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="show what would move, change nothing")
    args = parser.parse_args()
    try:
        return migrate(args.root, args.dry_run)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
