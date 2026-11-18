#!/usr/bin/env python3
"""./cc council stats: how often each model's proposal wins, and the tokens each provider used lately.

Wins come from the state.json of proposal councils (coding, testing, debug). The model of a proposal is the one its
run recorded in calls[], never today's catalog, so a role moved to another model keeps its old wins apart. A user's
--pick is counted apart from the judge's choice. Tokens come from every call log of every run folder (councils,
risk audits, thread judging), by the CLI on the log's first line and the usage line it ends with; a call that never
reported is counted as unknown, never as zero. Chat sessions and ./cc prs learn (temporary logs) are not counted.
Why: docs/kit/decisions/0034-council-load-balance.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import providers

PROPOSAL_KINDS = ("coding", "testing", "debug")
CLIS = {"claude": "claude", "codex": "codex", "cursor-agent": "cursor"}


def run_folders(root: Path) -> Iterable[Path]:
    """Every run folder: the CC's own runs, archived feature runs, and runs of open features."""
    try:
        layout = json.loads((root / "control-center.json").read_text(encoding="utf-8")).get("layout", {})
    except (OSError, ValueError):
        layout = {}
    worktrees = root / layout.get("worktrees", "worktrees")
    yield from (root / ".cc-local" / "runs").glob("*/")
    yield from (root / ".cc-local" / "runs-archive").glob("*/*/")
    yield from worktrees.glob("*/.cc-runs/*/")


def wins(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for folder in run_folders(root):
        try:
            state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        labels = state.get("labels") or {}
        if state.get("kind") not in PROPOSAL_KINDS or not labels:
            continue
        model = {call["role"]: f"{call['provider']} {call['model']}" for call in state.get("calls", []) if "role" in call}
        kind = result.setdefault(state["kind"], {"runs": 0, "hybrid": 0, "reject": 0, "open": 0, "models": {}, "judges": []})
        kind["runs"] += 1
        judge = next((f"{c['provider']} {c['model']}" for c in state.get("calls", []) if c.get("step") == "verdict"), None)
        if judge and judge not in kind["judges"]:
            kind["judges"].append(judge)
        for role in labels.values():
            kind["models"].setdefault(model.get(role, f"? {role}"), {"entered": 0, "won": 0, "picked": 0})["entered"] += 1
        decision = state.get("decision")
        if decision in labels:
            kind["models"][model.get(labels[decision], f"? {labels[decision]}")]["won"] += 1
        elif decision == "HYBRID":
            kind["hybrid"] += 1
        elif decision == "REJECT":
            kind["reject"] += 1
        else:
            kind["open"] += 1  # unparsed, or the run has not reached a verdict
        pick = str(state.get("pick") or "").upper()
        if pick in labels:
            kind["models"][model.get(labels[pick], f"? {labels[pick]}")]["picked"] += 1
    return result


def provider_usage(root: Path, days: float = 7, now: float | None = None) -> dict[str, dict[str, int]]:
    """Tokens per provider from the call logs finished in the last `days` days (by the log's modification time)."""
    since = (now if now is not None else time.time()) - days * 86_400
    totals: dict[str, dict[str, int]] = {}
    for folder in run_folders(root):
        for log in folder.glob("logs/*.log"):
            try:
                if log.stat().st_mtime < since:
                    continue
                with log.open(encoding="utf-8", errors="replace") as stream:
                    first = stream.readline().split()
            except OSError:
                continue
            provider = CLIS.get(first[1]) if len(first) > 1 and first[0] == "$" else None
            if provider is None:
                continue  # not a model call (a verify log, for example)
            row = totals.setdefault(provider, {"calls": 0, "input": 0, "cached": 0, "output": 0, "unreported": 0})
            row["calls"] += 1
            used = providers.usage(log)
            if used is None:
                row["unreported"] += 1
                continue
            for key in ("input", "cached", "output"):
                row[key] += used[key]
    return totals


def report(won: dict[str, dict[str, Any]], used: dict[str, dict[str, int]], days: float) -> str:
    lines = ["Proposal wins (the judge's choice; a user's --pick is counted apart)"]
    for kind, data in sorted(won.items()):
        lines.append(f"{kind}: {data['runs']} runs, judge: {', '.join(data['judges']) or 'unknown'}; "
                     f"{data['hybrid']} hybrid, {data['reject']} rejected, {data['open']} open or unparsed")
        for name, row in sorted(data["models"].items(), key=lambda item: (-item[1]["won"], item[0])):
            share = f" ({100 * row['won'] / row['entered']:.0f}%)" if row["entered"] else ""
            picks = f", {row['picked']} user pick{'s' if row['picked'] != 1 else ''}" if row["picked"] else ""
            lines.append(f"  {name:<32} {row['won']} of {row['entered']} won{share}{picks}")
    if not won:
        lines.append("  no proposal council runs yet")
    lines += ["", f"Tokens in the last {days:g} days (council, risk and thread run logs)"]
    for provider, row in sorted(used.items()):
        total = providers.describe({key: row[key] for key in ("input", "cached", "output")})
        missing = f"; {row['unreported']} did not report" if row["unreported"] else ""
        lines.append(f"  {provider:<8} {row['calls']} calls, {'at least ' if missing else ''}{total}{missing}")
    if not used:
        lines.append("  no model calls")
    lines.append("not counted: chat sessions, and ./cc prs learn calls (their logs are temporary)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="./cc council stats")
    parser.add_argument("root", type=Path)
    parser.add_argument("--days", type=float, default=7, help="token window in days (default 7)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--now", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    won, used = wins(args.root), provider_usage(args.root, args.days, args.now)
    if args.json:
        print(json.dumps({"wins": won, "usage": used, "days": args.days}, indent=2))
    else:
        print(report(won, used, args.days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
