#!/usr/bin/env python3
"""./cc changes: a compact change map of one catalog pull request.

collect gathers the deterministic facts (stats, file classes, symbols, test names) and the source-only diff that
an agent reads; the agent fills map.json; render checks map.json against the facts and fills a fixed HTML
template. The agent never writes HTML. Why: docs/kit/decisions/0032-change-maps.md"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prs  # noqa: E402  (slug and gh are shared with ./cc prs)

HERE = Path(__file__).resolve().parent / "change_map"
CLASSES = ("source", "config", "test", "docs", "generated")
VIEW_FIELDS = "number,title,url,body,baseRefName,headRefName,additions,deletions,commits"

GENERATED_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Cargo.lock", "go.sum",
                   "flake.lock", "Gemfile.lock", "composer.lock", "Pipfile.lock"}
CONFIG_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".nix", ".env", ".properties", ".xml"}
CONFIG_NAMES = {"Dockerfile", "Makefile", "Justfile", ".gitignore", ".dockerignore", "requirements.txt"}
DOCS_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}
TEST_NAME = re.compile(r"(^test_.*\.py$|_test\.\w+$|\.(test|spec)\.\w+$|Tests?\.(java|kt|cs|swift)$)")
SYMBOL = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:pub\s+)?(?:async\s+)?"
                    r"(?:def|class|function|func|fn|interface|struct|enum|type)\s+([A-Za-z_]\w*)")
TEST_CASE = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)|^\s*(?:it|test)\(\s*['\"`](.+?)['\"`]")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)
LIMITS = {"intent": 600, "title": 80, "why": 600, "case": 200, "before": 300, "after": 300, "note": 300}


def classify(path: str) -> str:
    parts = path.split("/")
    name = parts[-1]
    suffix = Path(name).suffix.lower()
    if path.startswith(".claude/skills/") or name in GENERATED_NAMES or name.endswith((".min.js", ".snap")) \
            or "__snapshots__" in parts:
        return "generated"
    if TEST_NAME.search(name) or any(part in {"tests", "test", "__tests__", "spec"} for part in parts[:-1]):
        return "test"
    if name in CONFIG_NAMES or path.startswith(".github/"):
        return "config"
    if suffix in DOCS_SUFFIXES or "docs" in parts[:-1]:
        return "docs"
    if suffix in CONFIG_SUFFIXES:
        return "config"
    return "source"


def split_diff(diff: str) -> dict[str, str]:
    """Each file's block of a unified diff, by its new path (old path for a deletion)."""
    blocks: dict[str, str] = {}
    for block in re.split(r"(?m)^(?=diff --git )", diff):
        header = re.match(r"diff --git a/(.+?) b/(.+)$", block, re.M)
        if not header:
            continue
        deleted = re.search(r"(?m)^\+\+\+ /dev/null$", block)
        blocks[header.group(1) if deleted else header.group(2)] = block if block.endswith("\n") else block + "\n"
    return blocks


def hunk_ranges(block: str) -> list[list[int]]:
    """New-file line ranges each hunk covers; a deleted file reports its old lines."""
    deleted = re.search(r"(?m)^\+\+\+ /dev/null$", block)
    ranges = []
    for match in HUNK.finditer(block):
        start, length = (match.group(1), match.group(2)) if deleted else (match.group(3), match.group(4))
        first, count = int(start), int(length) if length is not None else 1
        ranges.append([first, max(first, first + count - 1)])
    return ranges


def changed_lines(block: str, sign: str) -> list[str]:
    return [line[1:] for line in block.splitlines() if line.startswith(sign) and not line.startswith(sign * 3)]


def symbols(block: str) -> dict[str, list[str]]:
    added = {m.group(1) for line in changed_lines(block, "+") if (m := SYMBOL.match(line))}
    removed = {m.group(1) for line in changed_lines(block, "-") if (m := SYMBOL.match(line))}
    return {"added": sorted(added - removed), "removed": sorted(removed - added), "changed": sorted(added & removed)}


def test_names(block: str) -> list[str]:
    return [next(group for group in m.groups() if group) for line in changed_lines(block, "+") if (m := TEST_CASE.match(line))]


def output_dir(root: Path, repository: str, number: int) -> Path:
    return root / ".cc-local" / "changes" / f"{repository}-{number}"


def skeleton() -> dict[str, Any]:
    return {"intent": "", "groups": [{"title": "", "files": [], "why": "", "before_after": [{"case": "", "before": "", "after": ""}]}],
            "reading_order": [], "risks": [{"ref": "", "note": ""}], "reclass": {}, "diagram": None}


def collect(root: Path, repository: str, number: int) -> str:
    slug = prs.slug(root, repository)
    view = json.loads(prs.gh("pr", "view", str(number), "--repo", slug, "--json", VIEW_FIELDS))
    blocks = split_diff(prs.gh("pr", "diff", str(number), "--repo", slug))
    files = []
    for path, block in blocks.items():
        files.append({"path": path, "class": classify(path),
                      "additions": len(changed_lines(block, "+")), "deletions": len(changed_lines(block, "-")),
                      "status": "deleted" if re.search(r"(?m)^\+\+\+ /dev/null$", block)
                      else "added" if re.search(r"(?m)^--- /dev/null$", block) else "modified",
                      "hunks": hunk_ranges(block), "symbols": symbols(block),
                      "tests": test_names(block) if classify(path) == "test" else []})
    facts = {"repo": repository, "number": number, "title": view.get("title", ""), "url": view.get("url", ""),
             "base": view.get("baseRefName", ""), "head": view.get("headRefName", ""), "body": (view.get("body") or "").strip(),
             "additions": view.get("additions", 0), "deletions": view.get("deletions", 0),
             "commits": [(c.get("messageHeadline") or "").strip() for c in view.get("commits") or []], "files": files}
    out = output_dir(root, repository, number)
    out.mkdir(parents=True, exist_ok=True)
    (out / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    patch = "".join(blocks[f["path"]] for f in files if f["class"] in ("source", "config"))
    (out / "source.patch").write_text(patch, encoding="utf-8")
    map_path = out / "map.json"
    kept = map_path.exists() and bool(prs.load_json(map_path).get("intent"))
    if not kept:
        map_path.write_text(json.dumps(skeleton(), indent=2) + "\n", encoding="utf-8")
    counts = {name: sum(1 for f in files if f["class"] == name) for name in CLASSES}
    tests = sum(len(f["tests"]) for f in files)
    return "\n".join([
        f"{repository} #{number}  {facts['title']}",
        f"  {len(files)} files, +{facts['additions']} -{facts['deletions']}, {len(facts['commits'])} commits",
        "  classes: " + ", ".join(f"{name} {count}" for name, count in counts.items() if count),
        f"  source.patch: {len(patch.splitlines())} lines, about {len(patch) // 4 // 1000}k tokens; {tests} test names in facts.json",
        f"  {out / 'facts.json'}", f"  {out / 'source.patch'}",
        f"  {map_path} ({'kept: already filled' if kept else 'skeleton: fill it, then ./cc changes render'})",
    ])


def check(facts: dict[str, Any], change_map: dict[str, Any]) -> list[str]:
    """Everything wrong with map.json, so one fix round covers all of it."""
    problems: list[str] = []
    files = {f["path"]: f for f in facts.get("files", [])}
    reclass = change_map.get("reclass") or {}
    for path, name in reclass.items():
        if path not in files:
            problems.append(f"reclass: {path} is not in this PR")
        if name not in CLASSES:
            problems.append(f"reclass: {path}: unknown class {name!r}; use one of {', '.join(CLASSES)}")

    def limit(field: str, text: Any, where: str) -> None:
        if not isinstance(text, str) or not text.strip():
            problems.append(f"{where}: {field} is empty")
        elif len(text) > LIMITS[field]:
            problems.append(f"{where}: {field} has {len(text)} characters, at most {LIMITS[field]}")

    limit("intent", change_map.get("intent"), "map")
    seen: dict[str, str] = {}
    groups = change_map.get("groups") or []
    if not groups:
        problems.append("map: no groups")
    for index, group in enumerate(groups, 1):
        where = f"group {index}"
        limit("title", group.get("title"), where)
        limit("why", group.get("why"), where)
        if not group.get("files"):
            problems.append(f"{where}: no files")
        for path in group.get("files") or []:
            if path not in files:
                problems.append(f"{where}: {path} is not in this PR")
            elif path in seen:
                problems.append(f"{path} is in two groups: {seen[path]} and {where}")
            else:
                seen[path] = where
        for row in group.get("before_after") or []:
            for field in ("case", "before", "after"):
                limit(field, row.get(field), f"{where} before/after")
    for path, entry in files.items():
        if reclass.get(path, entry["class"]) == "source" and path not in seen:
            problems.append(f"{path} (source) is in no group")

    def ref(text: Any, where: str) -> None:
        path, _, line = str(text).rpartition(":") if re.search(r":\d+$", str(text)) else (str(text), "", "")
        if path not in files:
            problems.append(f"{where}: {text} names a file that is not in this PR")
        elif line and not any(a <= int(line) <= b for a, b in files[path]["hunks"]):
            problems.append(f"{where}: {text} is outside the changed lines of {path}")

    for text in change_map.get("reading_order") or []:
        ref(text, "reading_order")
    for risk in change_map.get("risks") or []:
        ref(risk.get("ref"), "risk")
        limit("note", risk.get("note"), f"risk {risk.get('ref')}")
    diagram = change_map.get("diagram")
    if diagram is not None and not (isinstance(diagram, str) and diagram.strip()):
        problems.append("diagram: Mermaid text or null")
    return problems


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def render(root: Path, repository: str, number: int) -> str:
    out = output_dir(root, repository, number)
    if not (out / "facts.json").exists():
        raise ValueError(f"no facts for {repository} #{number}; run ./cc changes collect {repository} {number} first")
    facts = prs.load_json(out / "facts.json")
    change_map = prs.load_json(out / "map.json")
    problems = check(facts, change_map)
    if problems:
        raise ValueError(f"{out / 'map.json'} needs fixes:\n" + "\n".join(f"  - {p}" for p in problems))
    data = json.dumps({"facts": facts, "map": change_map}, ensure_ascii=False).replace("<", "\\u003c")
    page = (HERE / "index.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", data, 1)
    (out / "index.html").write_text(page, encoding="utf-8")
    if change_map.get("diagram"):
        vendor = out.parent / "vendor"
        vendor.mkdir(exist_ok=True)
        shutil.copyfile(HERE / "vendor" / "mermaid.min.js", vendor / "mermaid.min.js")
    lines = [f"{repository} #{number}  {facts.get('title', '')}", "", change_map["intent"].strip(), ""]
    for index, group in enumerate(change_map["groups"], 1):
        lines.append(f"{index}. {group['title']}  ({len(group['files'])} files)")
    if change_map.get("reading_order"):
        lines += ["", "Read in this order: " + " -> ".join(change_map["reading_order"])]
    for risk in change_map.get("risks") or []:
        lines.append(f"Risk  {risk['ref']}: {risk['note']}")
    page_path = out / "index.html"
    lines += ["", f"page: {page_path}"]
    if is_wsl():
        lines.append(f'open: explorer.exe "$(wslpath -w {page_path})"')
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="./cc changes")
    parser.add_argument("root", type=Path)
    parser.add_argument("command", choices=("collect", "render"))
    parser.add_argument("repository")
    parser.add_argument("number", type=int)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        action = collect if args.command == "collect" else render
        print(action(root, args.repository, args.number))
    except (ValueError, OSError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
