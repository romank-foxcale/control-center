#!/usr/bin/env python3
"""Live event stream of a long ./cc process (a council run, a verify): one JSON line per event in events.jsonl.

Writers append steps, handoffs between agents, what agents say, stalls and the end; `./cc watch` follows
the file so a terminal or a chat agent sees progress without asking. Tool calls stay in the per-agent logs.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostos  # noqa: E402

# Silence longer than this is reported as a stall, and again after each further period of silence.
STALL_SECONDS = 180
# Ends the stream; a nested process (verify inside a council) writing to its parent's stream never emits it.
END = "end"
MARKS = {"start": "▶", "step": "→", "handoff": "⇄", "say": "💬", "agent": "·", "stall": "⚠", "error": "✖", "warn": "!", "gate": "✓", END: "■"}


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class Events:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, kind: str, text: str, **fields: Any) -> None:
        line = json.dumps({"at": now(), "kind": kind, "text": text, **fields}, ensure_ascii=False) + "\n"
        with self.lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(line)


def own_stream(root: Path, name: str) -> tuple[Events, bool]:
    """The stream a process reports to: its parent's (CC_EVENTS) or a new one under .cc-local/runs; True if new."""
    inherited = os.environ.get("CC_EVENTS")
    if inherited:
        return Events(Path(inherited)), False
    identifier = f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{name}"
    return Events(root / ".cc-local" / "runs" / identifier / "events.jsonl"), True


class Watchdog(threading.Thread):
    """Emit a stall event when a watched source stays silent for STALL_SECONDS, and again for each further period.

    `sources` returns {who: (last activity as epoch seconds, last thing it did)} for what should be active now.
    """

    def __init__(
        self, events: Events, sources: Callable[[], dict[str, tuple[float, str]]], limit: float = STALL_SECONDS, every: float = 5
    ):
        super().__init__(daemon=True)
        self.events, self.sources, self.limit, self.every = events, sources, limit, every
        self.stopped = threading.Event()
        self.warned: dict[str, float] = {}

    def run(self) -> None:
        while not self.stopped.wait(self.every):
            current = time.time()
            for who, (last, doing) in self.sources().items():
                silent = current - max(last, self.warned.get(who, 0))
                if silent >= self.limit:
                    quiet = clock(int(current - last))
                    self.events.emit("stall", f"{who} silent for {quiet}" + (f"; last: {doing}" if doing else ""), who=who)
                    self.warned[who] = current


def file_activity(path: Path) -> tuple[float, str]:
    """Last write time of a log and its last non-empty line."""
    try:
        lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        return path.stat().st_mtime, (lines[-1][:120] if lines else "")
    except OSError:
        return time.time(), ""


def clock(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes // 60}:{minutes % 60:02d}:{seconds:02d}" if minutes >= 60 else f"{minutes:02d}:{seconds:02d}"


def render(event: dict[str, Any]) -> str:
    stamp = event.get("at", "")[11:19]
    return f"{stamp} {MARKS.get(event.get('kind'), '·')} {event.get('text', '')}"


def streams(root: Path) -> list[Path]:
    """Every event stream the CC knows: council runs in feature worktrees and standalone runs in .cc-local."""
    found = []
    config = root / "control-center.json"
    if config.is_file():  # an unconfigured template has no worktrees yet
        layout = json.loads(config.read_text(encoding="utf-8"))["layout"]
        found += list((root / layout["worktrees"]).resolve().glob("*/.cc-runs/*/events.jsonl"))
    found += list((root / ".cc-local" / "runs").glob("*/events.jsonl"))
    return sorted(found, key=lambda path: path.parent.name)


def read(path: Path) -> list[dict[str, Any]]:
    events = []
    if not path.is_file():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a writer may be mid-line
    return events


def ended(path: Path) -> bool:
    events = read(path)
    return bool(events) and events[-1].get("kind") == END


def alive(pid: int | None) -> bool:
    """True while the writer runs; a stream that names no writer pid counts as alive."""
    if not pid:
        return True
    return hostos.alive(pid)


def follow(path: Path, out=sys.stdout, poll: float = 1.0) -> int:
    """Print the stream's events as they arrive; return when it ends. 0 on success, 1 on failure or a dead writer.

    A run that pauses (a council awaiting approval) ends its segment, and resuming it appends a new segment
    that opens with another start event. So the watch follows the latest segment only: an earlier segment's
    end is history, not the end of the run.
    """
    events = read(path)
    starts = [index for index, event in enumerate(events) if event.get("kind") == "start"]
    seen = starts[-1] if starts else 0
    owner = None
    while True:
        events = read(path)
        for event in events[seen:]:
            print(render(event), file=out, flush=True)
            if event.get("kind") == "start":
                owner = event.get("pid")
            if event.get("kind") == END:
                return 0 if event.get("ok") else 1
        seen = len(events)
        # A crash or kill leaves no end event: notice the dead writer instead of waiting forever.
        if not alive(owner):
            print(render({"at": now(), "kind": "error", "text": f"process {owner} exited without finishing the run"}), file=out, flush=True)
            return 1
        time.sleep(poll)


def main() -> int:
    """`./cc watch [<run-id>]`: follow a run; without an id the newest one still running, else the newest one."""
    root = Path(sys.argv[1]).resolve()
    wanted = sys.argv[2] if len(sys.argv) > 2 else None
    known = streams(root)
    if wanted:
        known = [path for path in known if path.parent.name == wanted]
        if not known:
            print(f"error: no run with events: {wanted}", file=sys.stderr)
            return 2
    else:
        # Started right next to a new run, the watch must wait for it, not replay the last finished one.
        before = set(known)
        for _ in range(60):
            running = [path for path in streams(root) if path not in before or not ended(path)]
            if running:
                known = running
                break
            time.sleep(1)
    if not known:
        print("no runs with events yet", file=sys.stderr)
        return 2
    path = known[-1]
    print(f"watching {path.parent.name}", flush=True)
    try:
        return follow(path)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
