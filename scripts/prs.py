#!/usr/bin/env python3
"""./cc prs: open pull requests of this CC's catalog repositories, newest first, marking the ones not seen before.

Only catalog repos are queried: repos/ is never scanned, so a stray clone there is never listed."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostos  # noqa: E402

FIELDS = "number,title,author,createdAt,updatedAt,isDraft,reviewDecision,url,baseRefName,headRefName"
GITHUB = re.compile(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?/?$")
TRELLO_CARD = re.compile(r"https://trello\.com/c/[A-Za-z0-9]+(?:/[^\s)\]>]*)?")
DETAIL_FIELDS = "number,title,author,body,url,baseRefName,headRefName,isDraft,reviewDecision,additions,deletions,files,comments"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def catalog(root: Path) -> dict[str, dict[str, Any]]:
    return {path.stem: load_json(path) for path in sorted((root / "catalog" / "repositories").glob("*.json"))}


def age(stamp: str, now: datetime) -> str:
    seconds = int((now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{seconds // size} {unit}"
    return "now"


def review(pr: dict[str, Any]) -> str:
    if pr.get("isDraft"):
        return "draft"
    return {"APPROVED": "approved", "CHANGES_REQUESTED": "changes requested", "REVIEW_REQUIRED": "review required"}.get(
        pr.get("reviewDecision") or "", "-"
    )


def gh_path() -> str:
    """gh resolved on PATH, so a gh.cmd (a test fake on Windows) is found as well as gh.exe."""
    return hostos.resolve_exe("gh") or "gh"


def fetch(slug: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [gh_path(), "pr", "list", "--repo", slug, "--state", "open", "--limit", "100", "--json", FIELDS],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"gh pr list failed for {slug}")
    return json.loads(result.stdout or "[]")


def slug(root: Path, identifier: str) -> str:
    repos = catalog(root)
    if identifier not in repos:
        raise ValueError(f"not in the catalog: {identifier}; see ./cc repo list")
    if repos[identifier].get("remote", "") is None:
        raise ValueError(f"{identifier}: no remote yet, so it has no pull requests; see ./cc repo set-remote")
    match = GITHUB.search(str(repos[identifier].get("remote", "")))
    if not match:
        raise ValueError(f"{identifier}: not a GitHub remote")
    return f"{match.group(1)}/{match.group(2)}"


def gh(*arguments: str) -> str:
    result = subprocess.run([gh_path(), *arguments], capture_output=True, text=True, encoding="utf-8", timeout=120)
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or f"gh {' '.join(arguments)} failed")
    return result.stdout


def show(root: Path, identifier: str, number: int, diff: bool) -> str:
    """One PR with what a review needs: description, files, linked Trello cards and, with --diff, the diff."""
    repository = slug(root, identifier)
    pr = json.loads(gh("pr", "view", str(number), "--repo", repository, "--json", DETAIL_FIELDS))
    texts = [pr.get("body") or ""] + [comment.get("body") or "" for comment in pr.get("comments") or []]
    cards = list(dict.fromkeys(card.rstrip(".,;:!?") for text in texts for card in TRELLO_CARD.findall(text)))
    files = pr.get("files") or []
    lines = [
        f"{identifier} #{pr['number']}  {pr['title']}",
        f"  {(pr.get('author') or {}).get('login', '?')} · {pr.get('baseRefName')} <- {pr.get('headRefName')} · {review(pr)} · {pr.get('url')}",
        f"  trello: {', '.join(cards) if cards else 'none linked'}",
        f"\nfiles ({len(files)}, +{pr.get('additions', 0)} -{pr.get('deletions', 0)}):",
        *(f"  +{entry.get('additions', 0)} -{entry.get('deletions', 0)}  {entry.get('path')}" for entry in files),
        "\ndescription:",
        (pr.get("body") or "").strip() or "(empty)",
    ]
    if diff:
        lines += ["\ndiff:", gh("pr", "diff", str(number), "--repo", repository).rstrip()]
    return "\n".join(lines)


def git(path: Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(path), *arguments], capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or f"git {' '.join(arguments)} failed in {path}")
    return result.stdout.strip()


def checkout(root: Path, identifier: str, number: int) -> str:
    """Put a PR's head into a feature worktree, so its gates can run: ./cc feature pr-<repo>-<n> verify.

    The review branch tracks the fetched PR ref: re-running fast-forwards it to the PR's latest push,
    and ./cc worktree remove sees nothing unpublished."""
    repository = slug(root, identifier)
    manifest = load_json(root / "control-center.json")
    kind = load_json(root / "catalog" / "repositories" / f"{identifier}.json").get("kind", "project")
    base = (root / manifest["layout"]["repositories"]).resolve() / kind / identifier
    if not (base / ".git").exists():
        raise ValueError(f"no base clone at {base}; clone it first: git clone <remote> {base}")
    pr = json.loads(gh("pr", "view", str(number), "--repo", repository, "--json", "number,body,headRefName"))
    tracking, branch, feature = f"refs/remotes/origin/pr/{number}", f"review/pr-{number}", f"pr-{identifier}-{number}"
    git(base, "fetch", "--quiet", "origin", f"+refs/pull/{number}/head:{tracking}")
    worktree = (root / manifest["layout"]["worktrees"]).resolve() / feature / f"{identifier}_wt"
    if worktree.exists():
        if git(worktree, "status", "--porcelain"):
            raise ValueError(f"{worktree} has local changes; commit or discard them first")
        git(worktree, "merge", "--ff-only", "--quiet", "@{upstream}")
        return f"updated {feature} to the PR's latest push ({git(worktree, 'rev-parse', '--short', 'HEAD')})"
    if subprocess.run(["git", "-C", str(base), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode:
        git(base, "branch", "--quiet", "--track", branch, tracking)
    cards = TRELLO_CARD.findall(pr.get("body") or "")
    command = [sys.executable, str(Path(__file__).resolve().parent / "worktrees.py"), str(root), "create", feature, identifier, "--branch", branch, "--review"]
    if cards:
        command += ["--card", cards[0].rstrip(".,;:!?")]
    created = subprocess.run(command, capture_output=True, text=True)
    if created.returncode != 0:
        raise ValueError(created.stderr.strip() or created.stdout.strip())
    return (f"checked out {identifier} #{number} as feature {feature} ({worktree})\n"
            f"run its gates: ./cc feature {feature} verify\nclean up: ./cc worktree remove {feature}")


def collect(root: Path, requested: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    repos = catalog(root)
    unknown = [identifier for identifier in requested if identifier not in repos]
    if unknown:
        raise ValueError(f"not in the catalog: {', '.join(unknown)}; see ./cc repo list")
    seen_path = root / ".cc-local" / "prs-seen.json"
    seen: dict[str, str] = load_json(seen_path) or {}
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for identifier in requested or sorted(repos):
        if repos[identifier].get("remote", "") is None:
            problems.append(f"{identifier}: no remote yet; skipped")
            continue
        remote = str(repos[identifier].get("remote", ""))
        match = GITHUB.search(remote)
        if not match:
            problems.append(f"{identifier}: not a GitHub remote ({remote}); skipped")
            continue
        try:
            pulls = fetch(f"{match.group(1)}/{match.group(2)}")
        except (RuntimeError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
            problems.append(f"{identifier}: {error}")
            continue
        for pr in pulls:
            key = f"{identifier}#{pr['number']}"
            rows.append({
                "repo": identifier,
                "number": pr["number"],
                "title": pr["title"],
                "author": (pr.get("author") or {}).get("login", "?"),
                "createdAt": pr["createdAt"],
                "age": age(pr["createdAt"], now),
                "review": review(pr),
                "base": pr.get("baseRefName", ""),
                "head": pr.get("headRefName", ""),
                "url": pr.get("url", ""),
                "seen": "new" if key not in seen else "updated" if seen[key] != pr["updatedAt"] else "",
            })
            seen[key] = pr["updatedAt"]
    rows.sort(key=lambda row: row["createdAt"], reverse=True)
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(seen, indent=2) + "\n", encoding="utf-8")
    return rows, problems


def mark_threads(root: Path, rows: list[dict[str, Any]], problems: list[str]) -> None:
    """Fill each row's threads field: what in the PR's review conversation waits on the user."""
    import threads  # imports this module, so it is loaded only when needed

    for row in rows:
        row["threads"] = ""
    if not rows:
        return
    try:
        me = threads.login()
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        problems.append(f"review threads not checked: {error}")
        return

    def mark(row: dict[str, Any]) -> None:
        try:
            row["threads"] = threads.waiting_mark(root, row["repo"], slug(root, row["repo"]), row["number"], me)
        except (ValueError, OSError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
            problems.append(f"{row['repo']} #{row['number']}: review threads not checked: {error}")

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        list(pool.map(mark, rows))


def main() -> int:
    if sys.argv[2:3] in (["threads"], ["judge"]):
        import threads

        return threads.main(sys.argv[1:])
    if sys.argv[2:3] == ["learn"]:
        import lessons

        return lessons.main(sys.argv[1:])
    if sys.argv[2:3] in (["show"], ["checkout"]):
        detail = argparse.ArgumentParser(prog=f"./cc prs {sys.argv[2]}")
        detail.add_argument("root", type=Path)
        detail.add_argument("command")
        detail.add_argument("repository", help="catalog id")
        detail.add_argument("number", type=int)
        detail.add_argument("--diff", action="store_true", help="show: also print the diff")
        args = detail.parse_args()
        try:
            if args.command == "checkout":
                print(checkout(args.root.resolve(), args.repository, args.number))
            else:
                print(show(args.root.resolve(), args.repository, args.number, args.diff))
        except (ValueError, OSError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        return 0
    parser = argparse.ArgumentParser(prog="./cc prs")
    parser.add_argument("root", type=Path)
    parser.add_argument("repositories", nargs="*", help="catalog ids; all catalog repos by default")
    parser.add_argument("--json", action="store_true", help="machine-readable, for skills")
    args = parser.parse_args()
    try:
        rows, problems = collect(args.root.resolve(), args.repositories)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    mark_threads(args.root.resolve(), rows, problems)
    for problem in problems:
        print(f"warning: {problem}", file=sys.stderr)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif not rows:
        print("no open pull requests")
    else:
        print("SEEN\tREPO\tPR\tTITLE\tAUTHOR\tAGE\tREVIEW\tTHREADS\tURL")
        for row in rows:
            print("\t".join([row["seen"] or "-", row["repo"], f"#{row['number']}", row["title"], row["author"], row["age"], row["review"],
                             row["threads"] or "-", row["url"]]))
    # Every repo failing means the listing is empty for the wrong reason.
    return 1 if problems and not rows and len(problems) == len(args.repositories or catalog(args.root.resolve())) else 0


if __name__ == "__main__":
    raise SystemExit(main())
