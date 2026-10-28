"""Progress of a council run, computed from its state.json: shared by the runner's ticker, ./cc council status and ./cc settings."""

from __future__ import annotations

import datetime
from typing import Any

TERMINAL = {"done", "rejected", "verify-failed", "blocked-security", "failed"}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# The steps a council walks through; retries repeat a step without moving the bar.
STEPS = {
    "coding": ["propose", "judge", "approval", "write", "verify", "security", "security summary"],
    "testing": ["propose", "judge", "approval", "write", "verify", "security", "security summary"],
    "debug": ["reproduce", "propose", "judge", "approval", "write test", "red check", "fix", "verify", "security", "security summary"],
    "risk": ["assess", "risk summary"],
}


def security_roles(council: dict[str, Any]) -> list[str]:
    """The council's security reviewers: a list, or one role id in catalogs older than the list."""
    value = council.get("security")
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def plan(kind: str, council: dict[str, Any]) -> list[str]:
    skipped = {"approval"} if not council.get("approval") else set()
    reviewers = security_roles(council)
    if not reviewers:
        skipped.add("security")
    if len(reviewers) < 2:
        skipped.add("security summary")  # one reviewer's review is the result
    return [step for step in STEPS.get(kind, []) if step not in skipped]


def seconds_since(stamp: str | None, now: datetime.datetime | None = None) -> int:
    if not stamp:
        return 0
    try:
        start = datetime.datetime.fromisoformat(stamp)
    except ValueError:
        return 0
    return max(0, int(((now or datetime.datetime.now()) - start).total_seconds()))


def clock(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes // 60}:{minutes % 60:02d}:{seconds:02d}" if minutes >= 60 else f"{minutes:02d}:{seconds:02d}"


def bar(state: dict[str, Any]) -> str:
    steps = state.get("plan") or []
    if not steps:
        return ""
    if state.get("stage") == "done":
        done = len(steps)
    else:
        phase = "approval" if state.get("stage") == "awaiting-approval" else state.get("phase")
        done = steps.index(phase) if phase in steps else 0
    return f"{'▰' * done}{'▱' * (len(steps) - done)} {done}/{len(steps)}"


def agents(state: dict[str, Any]) -> str:
    """Agents of the current step: '…' while working, '✓' once they answered."""
    phase_start = state.get("phaseStartedAt", "")
    current = [call for call in state.get("calls", []) if call.get("startedAt", "") >= phase_start]
    return " · ".join(f"{call['provider']} {call['model']} {'✓' if call.get('finishedAt') else '…'}" for call in current)


def running(state: dict[str, Any]) -> list[str]:
    return [f"{call['provider']} {call['model']}" for call in state.get("calls", []) if call.get("startedAt") and not call.get("finishedAt")]


def elapsed(state: dict[str, Any]) -> str:
    """'step mm:ss · total mm:ss'; a finished run's clock stops at its finish time."""
    end = state.get("finishedAt")
    stop = datetime.datetime.fromisoformat(end) if end else None
    if stop:
        return f"total {clock(seconds_since(state.get('startedAt'), stop))}"
    return f"step {clock(seconds_since(state.get('phaseStartedAt')))} · total {clock(seconds_since(state.get('startedAt')))}"


def line(state: dict[str, Any], frame: int = 0) -> str:
    stage = state.get("stage", "?")
    head = "✔" if stage in TERMINAL else SPINNER[frame % len(SPINNER)]
    parts = [f"{head} {state.get('council', '?')}", bar(state), state.get("phase", stage), agents(state), elapsed(state)]
    return "  ".join(part for part in parts if part)
