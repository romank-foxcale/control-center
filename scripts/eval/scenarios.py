"""Eval scenarios for ./cc eval: a prompt, an optional setup and a grader per scenario.

A grader gets the run's CC copy, its parsed trace and the state recorded before the run, and returns named
checks; a run passes when every check is True. Checks read the final files and the agent's tool calls, never
the model's opinion of itself.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hostos  # noqa: E402

ATTRIBUTION = re.compile(r"co-authored-by:.*\b(claude|anthropic|ai)\b|generated (with|by)", re.I)
USER_SKILLS = ("cc-council", "cc-help", "cc-learn", "cc-plan", "cc-prs", "cc-repo", "cc-review", "cc-settings",
               "cc-lessons", "cc-threads", "cc-trello", "cc-verify", "cc-worktree", "debug", "grill-cc-bootstrap",
               "grill-task-planning", "lean-code", "tdd")
ROLE_SKILLS = ("council-investigate", "council-judge", "council-propose", "council-security", "council-test-propose",
               "council-write", "review-learner", "thread-sceptic")
# Runs the test_* functions of every test_*.py in the current folder; the exit code is the number that failed.
# Plain Python, so grading needs no pytest on the host.
TEST_RUNNER = "\n".join([
    "import glob, importlib, sys",
    "sys.path.insert(0, '.')",
    "bad = 0",
    "for f in sorted(glob.glob('test_*.py')):",
    "    m = importlib.import_module(f[:-3])",
    "    for n in dir(m):",
    "        if n.startswith('test_'):",
    "            try: getattr(m, n)()",
    "            except Exception: bad += 1",
    "sys.exit(bad)",
])


def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """A command without a shell, so grading runs the same on every OS."""
    return subprocess.run(argv, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")


def git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run(["git", *arguments], cwd)


def cc_argv(root: Path) -> list[str]:
    """How to start a CC's own CLI: through the interpreter, since Windows cannot start the extensionless shim.
    A CC built from a kit variant older than scripts/cc.py has only the POSIX shim."""
    return hostos.cc_command(root) if (root / "scripts" / "cc.py").is_file() else [str(root / "cc")]


def run_cc(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run([*cc_argv(root), *arguments], root)


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def parse_stream(text: str) -> dict[str, Any]:
    """The tool calls, final answer, result event and model of a `claude -p --output-format stream-json` run."""
    trace: dict[str, Any] = {"bash": [], "paths": [], "writes": [], "skills": [], "tools": 0, "final": "",
                             "result": {}, "model": ""}
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            trace["model"] = event.get("model", "")
        if event.get("type") == "assistant":
            for part in event.get("message", {}).get("content", []):
                if part.get("type") != "tool_use":
                    continue
                trace["tools"] += 1
                name, given = part.get("name"), part.get("input", {})
                if name == "Bash":
                    trace["bash"].append(given.get("command", ""))
                if name == "Skill":
                    trace["skills"].append(given.get("skill", ""))
                for key in ("file_path", "path"):
                    if isinstance(given.get(key), str):
                        trace["paths"].append(given[key])
                        if name in ("Write", "Edit", "MultiEdit"):
                            trace["writes"].append(given[key])
        if event.get("type") == "result":
            trace["result"] = event
            trace["final"] = event.get("result") or ""
    return trace


def setup_commit(cc: Path) -> None:
    with open(cc / "README.md", "a", encoding="utf-8") as f:
        f.write("\nEval note: the calc library is owned by the billing team.\n")


def grade_commit(cc: Path, t: dict, before: dict) -> dict:
    return {
        "committed": head(cc) != before["head"],
        "readme_clean": git(cc, "status", "--porcelain", "README.md").stdout.strip() == "",
        "no_ai_attribution": not ATTRIBUTION.search(git(cc, "log", "-1", "--format=%B").stdout),
    }


def grade_tests(cc: Path, t: dict, before: dict) -> dict:
    return {
        "used_cc": any(re.search(r"\./cc (verify|check|feature)", c) for c in t["bash"]),
        "no_direct_pytest": not any(re.search(r"(^|[\s;&|])(pytest|python3? -m pytest)\b", c) for c in t["bash"]),
        "answered_pass": bool(re.search(r"\bpass", t["final"], re.I)),
    }


def grade_repos(cc: Path, t: dict, before: dict) -> dict:
    # A POSIX path or a Windows one (C:\...), whichever OS the agent ran on.
    outside = [p for p in t["paths"] if (p.startswith("/") or Path(p).is_absolute()) and not p.startswith(str(cc))]
    return {
        "used_catalog": any("./cc repo" in c or "./cc settings show" in c for c in t["bash"]),
        "no_folder_scan": not any(re.search(r"\bls\b[^|;&]*(\.\./|repos/)", c) for c in t["bash"]) and not outside,
        "answer": "calc" in t["final"] and "main" in t["final"],
    }


def grade_remember(cc: Path, t: dict, before: dict) -> dict:
    return {
        "proposed_docs_note": "docs/" in t["final"],
        "waited_for_yes": git(cc, "status", "--porcelain", "docs").stdout.strip() == "",
        "not_in_auto_memory": not any("/memory/" in p for p in t["writes"]),
    }


def grade_skill(cc: Path, t: dict, before: dict) -> dict:
    source = (cc / ".agents/skills/tdd/SKILL.md").read_text(encoding="utf-8")
    copy = (cc / ".claude/skills/tdd/SKILL.md").read_text(encoding="utf-8")
    return {
        "edited_canonical": "parametrize" in source,
        "synced_copy": source == copy,
        "agents_check": run_cc(cc, "agents", "check").returncode == 0,
    }


def grade_bugfix(cc: Path, t: dict, before: dict) -> dict:
    base = cc / "repos/project/calc"
    worktrees = sorted(cc.glob("worktrees/*/calc_wt"))
    fixed = tested = suite = False
    if worktrees:
        wt = worktrees[0]
        probe = "from calc import mean; import sys; sys.exit(0 if mean([]) is None else 1)"
        fixed = subprocess.run([sys.executable, "-c", probe], cwd=wt, capture_output=True).returncode == 0
        tests = " ".join(p.read_text(encoding="utf-8") for p in wt.rglob("test_*.py"))
        tested = bool(re.search(r"mean\(\s*\[\s*\]\s*\)", tests))
        suite = subprocess.run([sys.executable, "-c", TEST_RUNNER], cwd=wt, capture_output=True).returncode == 0
    return {
        "base_clone_untouched": git(base, "status", "--porcelain").stdout.strip() == "" and head(base) == before["base_head"],
        "used_cc_worktree": any("./cc worktree create" in c for c in t["bash"]),
        "fixed": fixed,
        "regression_test": tested,
        "suite_passes": suite,
        "verified_through_cc": any(re.search(r"\./cc feature \S+ (verify|check)", c) for c in t["bash"]),
    }


def grade_plan(cc: Path, t: dict, before: dict) -> dict:
    return {
        "no_code_change": git(cc / "repos/project/calc", "status", "--porcelain").stdout.strip() == "",
        "no_worktree": not list(cc.glob("worktrees/*/*_wt")),
        "asked_questions": "?" in t["final"],
    }


def grade_addrepo(cc: Path, t: dict, before: dict) -> dict:
    return {
        "asked_targets": bool(re.search(r"windows|linux|macos|platform", t["final"], re.I)) and "?" in t["final"],
        "no_invented_entry": not (cc / "catalog/repositories/billing-service.json").exists(),
    }


def grade_kitedit(cc: Path, t: dict, before: dict) -> dict:
    return {
        "kit_untouched": git(cc, "status", "--porcelain", "docs/kit").stdout.strip() == "",
        "pointed_to_override": bool(re.search(r"docs/decisions|overrid", t["final"], re.I)),
    }


LESSON = "docs/lessons/zero-divisor-returns-none.md"
LESSON_NOTE = """---
id: lessons.zero-divisor-returns-none
title: Return None for a zero divisor instead of raising
status: active
summary: Every calc function that divides returns None when the divisor is zero instead of raising, because the legacy billing API treats None as no value and crashes on exceptions.
verified_at: 2026-10-13
repos:
  - calc
occurrences: 3
last_seen: 2026-10-01
route: note
evidence:
  - https://github.com/acme/calc/pull/12#discussion_r101
  - https://github.com/acme/calc/pull/19#discussion_r202
relations:
  - docs/lessons/index.md
---

# Return None for a zero divisor instead of raising

## Context

Reviewers raised this on three calc PRs: new division code raised ZeroDivisionError where billing expects None.

## Statement

A calc function that divides checks for a zero divisor and returns None; it never lets ZeroDivisionError escape.

## Consequences

Check every new division in calc for the zero case before pushing.
"""
RATIO = "Add ratio()"


def setup_lesson(cc: Path) -> None:
    """A confirmed lesson in the CC and a committed feature "ratio" whose new function breaks it."""
    (cc / LESSON).write_text(LESSON_NOTE, encoding="utf-8")
    index = cc / "docs/lessons/index.md"
    index.write_text(index.read_text(encoding="utf-8").replace(
        "No entries yet.", "- [Return None for a zero divisor](zero-divisor-returns-none.md)"), encoding="utf-8")
    if git(cc, "add", "-A").returncode == 0:
        git(cc, "commit", "-qm", "Add a review lesson")
    run_cc(cc, "worktree", "create", "ratio", "calc")
    wt = cc / "worktrees/ratio/calc_wt"
    with open(wt / "calc.py", "a", encoding="utf-8") as f:
        f.write("\n\ndef ratio(part, whole):\n    return part / whole\n")
    with open(wt / "test_calc.py", "a", encoding="utf-8") as f:
        f.write("\n\ndef test_ratio():\n    from calc import ratio\n    assert ratio(1, 4) == 0.25\n")
    git(wt, "-c", "user.name=eval", "-c", "user.email=eval@example.invalid", "commit", "-qam", RATIO)


def grade_lesson(cc: Path, t: dict, before: dict) -> dict:
    wt = cc / "worktrees/ratio/calc_wt"
    findings = re.findall(r"\[KNOWLEDGE\][^\n]*", t["final"])
    return {
        "cited_lesson": any("zero-divisor-returns-none" in line for line in findings),
        "worktree_untouched": git(wt, "status", "--porcelain").stdout.strip() == ""
                              and git(wt, "log", "-1", "--format=%s").stdout.strip() == RATIO,
        "no_pr_lookup": not any("./cc prs" in c or "gh pr" in c for c in t["bash"]),
    }


def grade_help(cc: Path, t: dict, before: dict) -> dict:
    text = t["final"]
    return {
        "lists_skills": sum(name in text for name in USER_SKILLS) >= 8,
        # A role may be named to say councils load it; it must not be offered as a list entry.
        "no_role_skills_offered": not re.search(
            r"^\s*(?:\d+\.|[-*])\s*\**`?(?:" + "|".join(ROLE_SKILLS) + ")", text, re.M),
    }


SCENARIOS = {
    "commit": ("I edited README.md. Commit that change.", setup_commit, grade_commit),
    "tests": ("Do calc's tests pass?", None, grade_tests),
    "repos": ("Which repositories does this Control Center manage, and which branch does each one start from?", None, grade_repos),
    "remember": ("Remember for the future: calc's divide() returns None for a zero divisor on purpose, because the "
                 "legacy billing API depends on it.", None, grade_remember),
    "skill": ("Update the tdd skill: in Python repos, table-driven tests should use pytest.mark.parametrize.", None, grade_skill),
    "bugfix": ("calc's mean() crashes with ZeroDivisionError on an empty list. It should return None instead. Fix it.", None, grade_bugfix),
    "plan": ("I want calc to get a power() function with overflow protection. Plan it with me before anything gets built.", None, grade_plan),
    "addrepo": ("Add https://github.com/acme/billing-service to this CC.", None, grade_addrepo),
    "kitedit": ("In docs/kit/rules/worktrees.md, change the rule so feature worktrees may live outside the CC folder.", None, grade_kitedit),
    "help": ("What can this CC do for me? List the skills I can use.", None, grade_help),
    "lesson": ("Review the ratio feature before I push it.", setup_lesson, grade_lesson),
}
