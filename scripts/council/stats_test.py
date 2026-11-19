#!/usr/bin/env python3
"""Test ./cc council stats on run folders whose expected numbers are counted by hand: who won each proposal council,
what the user picked instead, and the tokens each provider used in the last 7 days."""

from __future__ import annotations

import io
import json
import os
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import stats

CLAUDE, GPT, OLD_GPT, GROK = ("claude", "claude-opus-5-5"), ("codex", "gpt-6.1-sol"), ("codex", "gpt-6.0"), ("cursor", "grok-4.7-high")
NOW = 1_800_000_000.0
DAY = 86_400


def run(folder: Path, kind: str, labels: dict[str, str], decision: str | None, models: dict[str, tuple[str, str]],
        pick: str | None = None) -> None:
    calls = [{"step": f"proposal-{role}", "role": role, "provider": p, "model": m} for role, (p, m) in models.items()]
    calls.append({"step": "verdict", "role": "judge", "provider": GROK[0], "model": GROK[1]})
    state = {"id": folder.name, "kind": kind, "labels": labels, "calls": calls,
             **({"decision": decision} if decision else {}), **({"pick": pick} if pick else {})}
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")


def log(path: Path, first: str, last: str, age_days: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"$ {first}\n\nsay: working\ntool: read a.py\n{last}\n", encoding="utf-8")
    os.utime(path, (NOW - age_days * DAY, NOW - age_days * DAY))


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "control-center.json").write_text(json.dumps({"layout": {"worktrees": "worktrees"}}), encoding="utf-8")
        both = {"proposer-claude": CLAUDE, "proposer-gpt": GPT}
        # Coding, counted by hand: GPT wins r1, Claude wins r2 (but the user picked GPT's), r5 rejected, r6 unparsed,
        # and r7 ran when proposer-gpt was still gpt-6.0: the win belongs to gpt-6.0, whatever the catalog says now.
        run(root / "worktrees/f1/.cc-runs/r1", "coding", {"A": "proposer-claude", "B": "proposer-gpt"}, "B", both)
        run(root / ".cc-local/runs-archive/f0/r2", "coding", {"A": "proposer-gpt", "B": "proposer-claude"}, "B", both, pick="a")
        run(root / "worktrees/f1/.cc-runs/r5", "coding", {"A": "proposer-gpt", "B": "proposer-claude"}, "REJECT", both)
        run(root / "worktrees/f1/.cc-runs/r6", "coding", {"A": "proposer-claude", "B": "proposer-gpt"}, "UNPARSED", both)
        run(root / "worktrees/f2/.cc-runs/r7", "coding", {"A": "proposer-gpt", "B": "proposer-claude"}, "A",
            {"proposer-claude": CLAUDE, "proposer-gpt": OLD_GPT})
        run(root / ".cc-local/runs/r3", "debug", {"A": "investigator-claude", "B": "investigator-gpt"}, "HYBRID",
            {"investigator-claude": CLAUDE, "investigator-gpt": GPT})
        run(root / ".cc-local/runs/r4", "risk", {"A": "risk-claude", "B": "risk-gpt"}, "GO",
            {"risk-claude": CLAUDE, "risk-gpt": GPT})
        run(root / "worktrees/f1/.cc-runs/r8", "coding", {"A": "proposer-claude", "B": "proposer-gpt"}, None, both)

        wins = stats.wins(root)
        assert set(wins) == {"coding", "debug"}, "risk verdicts are not proposal wins"
        coding = wins["coding"]
        assert coding["models"] == {
            "claude claude-opus-5-5": {"entered": 6, "won": 1, "picked": 0},
            "codex gpt-6.1-sol": {"entered": 5, "won": 1, "picked": 1},
            "codex gpt-6.0": {"entered": 1, "won": 1, "picked": 0},
        }, coding["models"]
        assert (coding["runs"], coding["hybrid"], coding["reject"], coding["open"]) == (6, 0, 1, 2), coding
        assert coding["judges"] == ["cursor grok-4.7-high"], "the judge model is shown next to its win rates"
        debug = wins["debug"]
        assert debug["hybrid"] == 1 and all(row["won"] == 0 for row in debug["models"].values()), debug

        # Tokens: every call log in every run folder, by its CLI and the usage line it ends with.
        usage = "usage: {} new input, {} cached input, {} output tokens"
        log(root / "worktrees/f1/.cc-runs/r1/logs/proposal-claude.log", "claude --print --model claude-opus-5-5 --tools Read",
            usage.format(100, 50, 10), 1)
        log(root / ".cc-local/runs-archive/f0/r2/logs/write-0.log", "codex exec --model gpt-6.1-sol --sandbox workspace-write",
            usage.format(200, 0, 20), 2)
        log(root / ".cc-local/runs/20261118-threads-api-7/logs/sceptic.log",
            "cursor-agent --print --trust --workspace /tmp/x --model grok-4.7-high", usage.format(300, 0, 30), 3)
        log(root / ".cc-local/runs/r3/logs/verdict.log", "cursor-agent --print --model grok-4.7-high",
            "usage: not reported (the call ended before its CLI reported tokens)", 0.5)
        log(root / ".cc-local/runs/r3/logs/old.log", "claude --print --model claude-opus-5-5", usage.format(9000, 0, 900), 10)
        log(root / ".cc-local/runs/r3/logs/verify.log", "./cc feature f verify api --quality", "ok", 1)

        used = stats.provider_usage(root, days=7, now=NOW)
        assert used == {
            "claude": {"calls": 1, "input": 100, "cached": 50, "output": 10, "unreported": 0},
            "codex": {"calls": 1, "input": 200, "cached": 0, "output": 20, "unreported": 0},
            "cursor": {"calls": 2, "input": 300, "cached": 0, "output": 30, "unreported": 1},
        }, used
        assert stats.provider_usage(root, days=30, now=NOW)["claude"]["input"] == 9100, "the window decides"

        out = io.StringIO()
        with redirect_stdout(out):
            assert stats.main([str(root), "--now", str(NOW)]) == 0
        text = out.getvalue()
        for expected in ("coding: 6 runs", "codex gpt-6.1-sol", "1 of 5 won (20%)", "judge: cursor grok-4.7-high",
                         "1 user pick", "cursor", "at least", "not counted: chat sessions"):
            assert expected in text, (expected, text)
        assert json.loads(_json(root))["usage"]["codex"]["input"] == 200
    print("stats test passed")
    return 0


def _json(root: Path) -> str:
    out = io.StringIO()
    with redirect_stdout(out):
        stats.main([str(root), "--now", str(NOW), "--json"])
    return out.getvalue()


if __name__ == "__main__":
    raise SystemExit(main())
