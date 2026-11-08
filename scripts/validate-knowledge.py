#!/usr/bin/env python3
"""Validate the repository's small, linked Markdown knowledge graph."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from rule_hooks import extra_markdown_dirs, markdown_allowed


REQUIRED_FILES = ("README.md", "AGENTS.md", "flake.nix", "docs/index.md")
REQUIRED_FIELDS = ("id", "title", "status", "summary", "verified_at")
ALLOWED_STATUSES = {"active", "accepted", "draft", "superseded", "archived"}
MARKDOWN_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
LESSONS = "docs/lessons"
ROUTES = {"note", "gate"}


def parse_frontmatter(path: Path, text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ValueError("unclosed YAML frontmatter") from error

    scalars: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    current_list: str | None = None
    for line in lines[1:end]:
        field = re.match(r"^([a-z_]+):(?:\s*(.*))?$", line)
        if field:
            name, value = field.group(1), (field.group(2) or "").strip().strip('"')
            current_list = name if not value else None
            if value:
                scalars[name] = value
            else:
                lists[name] = []
            continue
        item = re.match(r"^\s+-\s+(.+?)\s*$", line)
        if item and current_list:
            lists[current_list].append(item.group(1).strip().strip('"'))
    return scalars, lists


def local_target(root: Path, note: Path, raw: str, from_root: bool) -> Path | None:
    target = raw.split("#", 1)[0].strip().strip("<>")
    if not target or target.startswith(("http://", "https://", "mailto:")):
        return None
    base = root if from_root else note.parent
    return (base / target).resolve()


def repository_files(root: Path) -> list[str]:
    """Files Git would commit: tracked and untracked, never ignored. A Nix source copy has no .git
    and holds only tracked files, so a plain walk is exact there."""
    if (root / ".git").exists():
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True,
        )
        if listed.returncode == 0:
            return listed.stdout.splitlines()
    return [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()]


def stray_markdown(root: Path) -> list[str]:
    extra = extra_markdown_dirs(root)
    return [
        f"{name}: Markdown outside docs/, plans/, the skills and control-center.json markdownDirs; "
        "store it as a note under docs/"
        for name in repository_files(root)
        if name.lower().endswith(".md") and not markdown_allowed(name, extra)
    ]


def lesson_errors(relative: Path, scalars: dict[str, str], lists: dict[str, list[str]]) -> list[str]:
    """A review lesson (templates/lesson.md) says which catalog repos it covers, how often and when reviewers
    last raised it, and whether it stays a note or should become a gate check."""
    errors = []
    if not lists.get("repos"):
        errors.append(f"{relative}: 'repos' must list at least one catalog repo")
    if not scalars.get("occurrences", "").isdigit() or int(scalars["occurrences"]) < 1:
        errors.append(f"{relative}: 'occurrences' must be a positive integer")
    if not DATE.fullmatch(scalars.get("last_seen", "")):
        errors.append(f"{relative}: 'last_seen' must use YYYY-MM-DD")
    if scalars.get("route") not in ROUTES:
        errors.append(f"{relative}: 'route' must be one of {', '.join(sorted(ROUTES))}")
    return errors


PROJECT_LAYER = ("plans/", "docs/decisions/", "docs/debt/", "docs/hacks/", "docs/projects/", "docs/glossary/", "docs/lessons/")
EMPTY_MARKERS = {".gitkeep", "index.md"}


def project_layer_in_template(root: Path) -> list[str]:
    """The template (no control-center.json) ships each CC's own layer empty: only .gitkeep and the indexes are
    tracked there (docs/kit/decisions/0017-kit-and-project-layers.md). A working plan may stay untracked."""
    if (root / "control-center.json").exists():
        return []
    if (root / ".git").exists():
        listed = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True)
        names = listed.stdout.splitlines() if listed.returncode == 0 else []
    else:  # a Nix source copy holds only tracked files
        names = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()]
    return [f"{name}: the template ships the project layer empty; keep this out of the kit (git rm --cached)"
            for name in names if name.startswith(PROJECT_LAYER) and Path(name).name not in EMPTY_MARKERS]


MAINTAINED = {"active", "accepted", "draft"}


def last_changes(root: Path, paths: set[str]) -> dict[str, str]:
    """The last commit date (YYYY-MM-DD) of each path, a file or a directory, from one walk of the history.
    Paths Git does not track (ignored, untracked) get no date."""
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--format=@%cs", "--name-only", "--no-renames", "--", *sorted(paths)],
        capture_output=True, text=True,
    )
    dates: dict[str, str] = {}
    day = ""
    for line in log.stdout.splitlines() if log.returncode == 0 else []:
        if line.startswith("@"):
            day = line[1:]
        elif line:
            for path in paths - dates.keys():
                if line == path or line.startswith(path.rstrip("/") + "/"):
                    dates[path] = day
    return dates


def stale_evidence(root: Path) -> list[str]:
    """Warnings for maintained notes whose in-repo evidence was committed after the note's verified_at. Many such
    changes leave the note true, so this asks for a look, never fails (docs/kit/decisions/0023-stale-evidence-warning.md).
    Skipped without Git history: a Nix source copy has no .git, and a shallow clone dates every file to its one commit."""
    root = root.resolve()
    if not (root / ".git").exists():
        return []
    shallow = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],
                             capture_output=True, text=True)
    if shallow.returncode != 0 or shallow.stdout.strip() != "false":
        return []

    # A CC never edits docs/kit, so only the template, which owns the kit, is warned about kit notes.
    kit = root / "docs/kit" if (root / "control-center.json").exists() else None
    claims: list[tuple[str, str, str]] = []
    for note in sorted((root / "docs").rglob("*.md")):
        if kit and note.is_relative_to(kit):
            continue
        try:
            scalars, lists = parse_frontmatter(note, note.read_text(encoding="utf-8"))
        except ValueError:
            continue
        verified_at = scalars.get("verified_at", "")
        if scalars.get("status") not in MAINTAINED or not DATE.fullmatch(verified_at):
            continue
        for value in lists.get("evidence", []):
            target = local_target(root, note, value, from_root=True)
            if target and target.exists() and target.is_relative_to(root):
                claims.append((note.relative_to(root).as_posix(), target.relative_to(root).as_posix(), verified_at))

    dates = last_changes(root, {path for _, path, _ in claims}) if claims else {}
    return [f"{note}: evidence '{path}' changed on {dates[path]}, after verified_at {verified_at}"
            for note, path, verified_at in claims if dates.get(path, "") > verified_at]


# Claims that the CC as a whole needs Nix or WSL. Since 0033 the core runs natively and Nix is an optional extra, so such
# a line misleads every agent that reads it. Superseded notes keep their history.
OUTDATED_REQUIREMENTS = re.compile(
    r"\b(?:Nix|WSL) is (?:required|mandatory)\b"
    r"|\bNix (?:is|becomes) a mandatory prerequisite\b"
    r"|\b[Rr]equires (?:Nix|WSL)\b"
    r"|(?:\./cc|\bthe CC|\bcc check|\bcc verify|\bcc\.cmd)`? (?:requires|needs) (?:Nix|WSL)\b"
    r"|\bforwards to WSL\b"
    r"|\bruns `?\./cc`? inside WSL\b"
)


def outdated_requirements(root: Path) -> list[str]:
    """Lines in committed Markdown that still say the CC needs Nix or WSL (docs/kit/decisions/0033-native-core-nix-optional.md)."""
    errors = []
    for name in repository_files(root):
        # Plans are working notes that record the old state on purpose.
        if not name.lower().endswith(".md") or name.startswith("plans/") or not (root / name).is_file():
            continue
        text = (root / name).read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?m)^status: superseded$", text[:2000]):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if match := OUTDATED_REQUIREMENTS.search(line):
                errors.append(f"{name}:{number}: says the CC needs Nix or WSL (\"{match.group(0)}\"); since decision 0033 "
                              "the core runs natively and Nix is an optional extra")
    return errors


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    root = root.resolve()

    for name in REQUIRED_FILES:
        if not (root / name).exists():
            errors.append(f"{name}: required file is missing")

    notes = sorted((root / "docs").rglob("*.md")) if (root / "docs").exists() else []
    identifiers: dict[str, Path] = {}

    for note in notes:
        relative = note.relative_to(root)
        text = note.read_text(encoding="utf-8")
        try:
            scalars, lists = parse_frontmatter(note, text)
        except ValueError as error:
            errors.append(f"{relative}: {error}")
            continue

        if len(text.splitlines()) > 220 and scalars.get("kind", "note") != "research-reference":
            errors.append(f"{relative}: note exceeds 220 lines; split it")

        for field in REQUIRED_FIELDS:
            if not scalars.get(field):
                errors.append(f"{relative}: missing frontmatter field '{field}'")

        identifier = scalars.get("id")
        if identifier:
            if identifier in identifiers:
                errors.append(
                    f"{relative}: duplicate id '{identifier}' also used by {identifiers[identifier].relative_to(root)}"
                )
            identifiers[identifier] = note

        status = scalars.get("status")
        if status and status not in ALLOWED_STATUSES:
            errors.append(f"{relative}: unsupported status '{status}'")

        if relative.parent.as_posix() == LESSONS and note.name != "index.md":
            errors.extend(lesson_errors(relative, scalars, lists))

        verified_at = scalars.get("verified_at")
        if verified_at and not DATE.fullmatch(verified_at):
            errors.append(f"{relative}: verified_at must use YYYY-MM-DD")

        for field in ("evidence", "relations"):
            values = lists.get(field, [])
            if not values:
                errors.append(f"{relative}: '{field}' must contain at least one path")
            for value in values:
                target = local_target(root, note, value, from_root=True)
                if target and not target.exists():
                    errors.append(f"{relative}: broken {field} path '{value}'")

        for raw_link in MARKDOWN_LINK.findall(text):
            target = local_target(root, note, raw_link, from_root=False)
            if target and not target.exists():
                errors.append(f"{relative}: broken Markdown link '{raw_link}'")

    if not notes:
        errors.append("docs/: no Markdown notes found")
    errors.extend(stray_markdown(root))
    errors.extend(project_layer_in_template(root))
    errors.extend(outdated_requirements(root))
    return errors


def print_stale(root: Path) -> None:
    warnings = stale_evidence(root)
    if warnings:
        print(f"warning: {len(warnings)} evidence paths changed after their note was verified; "
              "check each note and refresh verified_at (or correct it):", file=sys.stderr)
        for warning in warnings:
            print(f"- {warning}", file=sys.stderr)


def main() -> int:
    arguments = sys.argv[1:]
    stale_only = "--stale" in arguments
    arguments = [argument for argument in arguments if argument != "--stale"]
    root = Path(arguments[0] if arguments else ".")
    print_stale(root)
    if stale_only:
        return 0
    errors = validate(root)
    if errors:
        print("knowledge validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    count = len(list((root / "docs").rglob("*.md")))
    print(f"knowledge validation passed: {count} notes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
