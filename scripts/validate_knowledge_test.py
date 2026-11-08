#!/usr/bin/env python3
"""Test the review-lesson fields of validate-knowledge.py: a complete lesson passes, and each missing or
malformed lesson field is reported, while notes outside docs/lessons/ and its index need none of them."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("validate_knowledge", HERE / "validate-knowledge.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)

FAILURES: list[str] = []
LESSON = {"repos": "\n  - api", "occurrences": " 3", "last_seen": " 2026-10-01", "route": " gate"}


def check(name: str, condition: bool, detail: object = "") -> None:
    if not condition:
        FAILURES.append(f"{name}: {detail}")


def note(path: Path, identifier: str, extra: dict[str, str]) -> None:
    fields = "".join(f"{key}:{value}\n" for key, value in extra.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {identifier}\ntitle: T\nstatus: active\nsummary: S\nverified_at: 2026-10-13\n{fields}"
        "evidence:\n  - https://github.com/o/api/pull/1#discussion_r1\nrelations:\n  - docs/index.md\n---\n\n# T\n",
        encoding="utf-8",
    )


def errors_for(lesson: dict[str, str], where: str = "docs/lessons/x.md") -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        skeleton(root, cc=True)
        note(root / where, "lessons.x", lesson)
        return validator.validate(root)


def skeleton(root: Path, cc: bool) -> None:
    """The required files and two indexes; a CC has control-center.json, the template does not."""
    for name in ("README.md", "AGENTS.md", "flake.nix", *(["control-center.json"] if cc else [])):
        (root / name).write_text("x\n", encoding="utf-8")
    note(root / "docs/index.md", "index", {})
    note(root / "docs/lessons/index.md", "lessons.index", {})
    (root / "plans/active").mkdir(parents=True)
    (root / "plans/active/.gitkeep").touch()


def project_layer_errors(cc: bool, git: bool, tracked: list[str], untracked: list[str] = ()) -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        skeleton(root, cc)
        for name in (*tracked, *untracked):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# a plan\n", encoding="utf-8") if name.startswith("plans/") else note(path, path.stem, LESSON)
        if git:
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "-A", "--", ".", *(f":!{name}" for name in untracked)], check=True)
        return [error for error in validator.validate(root) if "project layer" in error]


def check_template_ships_an_empty_project_layer() -> None:
    plan, lesson = "plans/active/review-lessons.md", "docs/lessons/close-handles.md"
    found = project_layer_errors(cc=False, git=True, tracked=[plan, lesson])
    check("template: a tracked plan and a tracked note are reported",
          len(found) == 2 and any(plan in error for error in found) and any(lesson in error for error in found), found)
    check("template: an untracked working plan is fine", project_layer_errors(cc=False, git=True, tracked=[], untracked=[plan]) == [])
    check("template: indexes and .gitkeep are fine", project_layer_errors(cc=False, git=True, tracked=[]) == [])
    check("template copy without .git (Nix) is checked too", len(project_layer_errors(cc=False, git=False, tracked=[plan])) == 1)
    check("a CC keeps its own plans and notes", project_layer_errors(cc=True, git=True, tracked=[plan, lesson]) == [])


def commit(root: Path, day: str, files: dict[str, str]) -> None:
    """Write the files and commit them with author and committer date `day`."""
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    stamp = f"{day}T12:00:00+00:00"
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", day], check=True, env=env)


def dated_note(evidence: list[str], verified_at: str = "2026-03-10", status: str = "active") -> str:
    paths = "".join(f"  - {path}\n" for path in evidence)
    return (f"---\nid: n\ntitle: T\nstatus: {status}\nsummary: S\nverified_at: {verified_at}\n"
            f"evidence:\n{paths}relations:\n  - docs/index.md\n---\n\n# T\n")


def check_stale_evidence() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "cc"
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        commit(root, "2026-03-01", {".gitignore": "repos/\n", "a.py": "1", "b.py": "1", "lib/x.py": "1",
                                    "repos/r/y.py": "1"})
        commit(root, "2026-03-10", {"b.py": "2"})
        commit(root, "2026-03-20", {"a.py": "2", "lib/x.py": "2", "repos/r/y.py": "2"})
        commit(root, "2026-03-25", {
            "docs/n.md": dated_note(["a.py", "b.py", "lib", "repos/r/y.py", "https://example.com/a.py"]),
            "docs/old.md": dated_note(["a.py"], status="superseded"),
            "docs/fresh.md": dated_note(["a.py"], verified_at="2026-03-20"),
        })
        found = validator.stale_evidence(root)
        expected = ["docs/n.md: evidence 'a.py' changed on 2026-03-20, after verified_at 2026-03-10",
                    "docs/n.md: evidence 'lib' changed on 2026-03-20, after verified_at 2026-03-10"]
        check("evidence changed after verified_at is reported; same day, ignored, URL, superseded are not",
              found == expected, found)
        check("staleness is a warning, never a validation error",
              not any("changed on" in error for error in validator.validate(root)), validator.validate(root))

        shallow = Path(temporary) / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", root.as_uri(), str(shallow)], check=True)
        check("a shallow clone is skipped: every file would look changed on its one commit",
              validator.stale_evidence(shallow) == [], validator.stale_evidence(shallow))

        copy = Path(temporary) / "copy"
        shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".git"))
        check("a copy without .git (Nix) is skipped", validator.stale_evidence(copy) == [], validator.stale_evidence(copy))

        commit(root, "2026-03-26", {"docs/kit/k.md": dated_note(["a.py"])})
        kit = "docs/kit/k.md: evidence 'a.py' changed on 2026-03-20, after verified_at 2026-03-10"
        check("the template, which owns docs/kit, gets kit notes too", kit in validator.stale_evidence(root),
              validator.stale_evidence(root))
        commit(root, "2026-03-27", {"control-center.json": "{}"})
        check("a CC skips docs/kit: it never edits the kit, so it could not act on the warning",
              validator.stale_evidence(root) == expected, validator.stale_evidence(root))


def check_outdated_requirements() -> None:
    with tempfile.TemporaryDirectory() as directory:  # no .git: every file counts, as in a Nix source copy
        root = Path(directory)
        files = {
            ".agents/skills/x/SKILL.md": "# X\n\nRun it.\nNix is required for this.\n",
            "README.md": "build and gc need the optional Nix extra.\n",
            "docs/kit/decisions/0032-old.md": "---\nid: old\nstatus: superseded\n---\n\nWSL is required.\n",
            "plans/active/p.md": "Today `./cc` runs inside WSL.\n",
        }
        for name, content in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(content, encoding="utf-8")
        found = validator.outdated_requirements(root)
        check("a planted 'Nix is required' in a skill is reported with its line",
              len(found) == 1 and found[0].startswith(".agents/skills/x/SKILL.md:4:"), found)


def main() -> int:
    check_outdated_requirements()
    check_stale_evidence()
    check_template_ships_an_empty_project_layer()
    check("a complete lesson passes", errors_for(LESSON) == [], errors_for(LESSON))
    for field, value, expected in (
        ("repos", "", "'repos' must list"),
        ("occurrences", " two", "'occurrences' must be a positive integer"),
        ("occurrences", " 0", "'occurrences' must be a positive integer"),
        ("last_seen", " yesterday", "'last_seen' must use"),
        ("route", " lint", "'route' must be one of gate, note"),
    ):
        found = errors_for({**LESSON, field: value})
        check(f"{field}={value.strip() or 'missing'} is reported", any(expected in error for error in found), found)
    missing = errors_for({})
    check("a bare lesson reports all four fields", len(missing) == 4, missing)
    check("a note outside docs/lessons needs no lesson fields", errors_for({}, "docs/hacks/x.md") == [], errors_for({}, "docs/hacks/x.md"))
    for failure in FAILURES:
        print(f"FAIL {failure}")
    print("validate-knowledge tests: " + ("ok" if not FAILURES else f"{len(FAILURES)} failed"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
