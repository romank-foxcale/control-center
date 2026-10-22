#!/usr/bin/env python3
"""Run a council: independent proposals, an anonymous judge, user approval, a writer and ./cc verify."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import progress
import providers

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import events  # noqa: E402


TERMINAL = {"done", "rejected", "verify-failed", "blocked-security", "failed"}
BLOCKING = {"high", "unparsed"}


def parse_severity(review: str) -> str:
    match = re.match(r"\s*SEVERITY:\s*(\w+)", review)
    return match.group(1).lower() if match else "unparsed"


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be an object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def say(message: str) -> None:
    if sys.stderr.isatty():
        sys.stderr.write("\r\033[K")  # clear the live status line first
    print(message, flush=True)


class Ticker(threading.Thread):
    """Shows that a run is alive: a live status line on a terminal, a heartbeat line every 30s otherwise."""

    def __init__(self, run: "Run"):
        super().__init__(daemon=True)
        self.watched = run
        self.stopped = threading.Event()

    def run(self) -> None:
        live = sys.stderr.isatty()
        frame = 0
        while not self.stopped.wait(1 if live else 30):
            frame += 1
            state = self.watched.state
            if live:
                sys.stderr.write("\r\033[K" + progress.line(state, frame)[:200])
                sys.stderr.flush()
            else:
                busy = ", ".join(progress.running(state)) or "the CC"
                waited = progress.clock(progress.seconds_since(state.get("phaseStartedAt")))
                print(f"... still {state.get('phase', state.get('stage'))}: {busy} ({waited})", flush=True)

    def __enter__(self) -> "Ticker":
        self.watched.events.emit("start", f"{self.watched.state['council']} on {self.watched.state['feature']}/{self.watched.state['repository']}", pid=os.getpid())
        self.watchdog = events.Watchdog(self.watched.events, self.watched.activity)
        self.watchdog.start()
        self.start()
        return self

    def __exit__(self, kind: type | None, error: BaseException | None, _: object) -> None:
        self.stopped.set()
        self.watchdog.stopped.set()
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
        # A crash must not leave the run looking alive: record it, then let main report the error.
        if error is not None and self.watched.state.get("stage") not in TERMINAL:
            self.watched.finish("failed", f"{kind.__name__}: {error}")


def git(worktree: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(worktree), *arguments], check=True, capture_output=True, text=True
    ).stdout


def worktrees_root(root: Path) -> Path:
    return (root / load_json(root / "control-center.json")["layout"]["worktrees"]).resolve()


def skill_path(root: Path, name: str) -> Path:
    """A role-only skill lives in .agents/roles, out of every session's skill list; roles also use ordinary skills."""
    role = root / ".agents" / "roles" / name / "SKILL.md"
    return role if role.is_file() else root / ".agents" / "skills" / name / "SKILL.md"


def skill_text(root: Path, name: str) -> str:
    text = skill_path(root, name).read_text(encoding="utf-8")
    # Drop the frontmatter: the CLI only needs the instructions.
    return re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL).strip()


def role_prompt(root: Path, role: dict[str, Any], state: dict[str, Any], body: str) -> str:
    skills = "\n\n".join(skill_text(root, skill) for skill in role.get("skills", []))
    context = (
        f"Council: {state['council']} ({state['kind']}). Feature: {state['feature']}. "
        f"Repository: {state['repository']}, targets: {', '.join(state['targets']) or 'unknown'}, "
        f"stack: {', '.join(state.get('stack', [])) or 'unknown'}. "
        "The current directory is the feature worktree."
    )
    return f"{skills}\n\n# Context\n\n{context}\n\n{review_lessons(root, state['repository'])}{body}"


def review_lessons(root: Path, repository: str) -> str:
    """The CC's review lessons for this repository (docs/lessons/), so the code follows them before review does."""
    import lessons  # late: lessons imports threads, which imports this module

    found = [f"- {path}: {lesson['summary']}" for path, lesson in lessons.known_lessons(root).items() if repository in lesson["repos"]]
    if not found:
        return ""
    return ("# Review lessons\n\nReviewers of this repository kept raising these shortcomings. Code you propose, write or "
            "judge must not repeat them; the full note is at each path in the Control Center.\n\n" + "\n".join(found) + "\n\n")


def worktree_diff(worktree: Path) -> str:
    diff = git(worktree, "diff", "HEAD")
    for path in git(worktree, "ls-files", "--others", "--exclude-standard").splitlines():
        result = subprocess.run(
            ["git", "-C", str(worktree), "diff", "--no-index", "--", "/dev/null", path],
            capture_output=True,
            text=True,
        )
        diff += result.stdout
    return diff


def tail(output: str, lines: int = 200) -> str:
    return "\n".join(output.splitlines()[-lines:])


def changed_files(worktree: Path) -> list[str]:
    tracked = git(worktree, "diff", "--name-only", "HEAD").splitlines()
    return tracked + git(worktree, "ls-files", "--others", "--exclude-standard").splitlines()


def file_states(worktree: Path) -> dict[str, bytes | str]:
    """The content of every file that differs from HEAD, to tell which files one writer attempt touched."""
    return {path: (worktree / path).read_bytes() if (worktree / path).is_file() else "deleted" for path in changed_files(worktree)}


def touched_files(before: dict[str, bytes | str], after: dict[str, bytes | str]) -> list[str]:
    return sorted(path for path in before.keys() | after.keys() if before.get(path, "head") != after.get(path, "head"))


ERROR_LINE = re.compile(r"error|fail|assert|traceback|exception|panic", re.IGNORECASE)


def key_errors(output: str, limit: int = 5) -> list[str]:
    """The last distinct error-looking lines before the result table: where test runners and builds put
    their summary. A pointer for a person, not a diagnosis; the full output stays in verify-N.log."""
    lines = output.splitlines()
    if "REPO\tGATE\tRESULT" in lines:
        lines = lines[: lines.index("REPO\tGATE\tRESULT")]
    found: list[str] = []
    for line in reversed(lines):
        line = line.strip()
        if ERROR_LINE.search(line) and not line.startswith("==") and line not in found:
            found.insert(0, line[:200])
            if len(found) == limit:
                break
    return found


def verify_rows(output: str) -> list[tuple[str, str, str]]:
    """Parse the REPO/GATE/RESULT table that ./cc verify prints last."""
    lines = output.splitlines()
    if "REPO\tGATE\tRESULT" not in lines:
        return []
    table = lines[lines.index("REPO\tGATE\tRESULT") + 1 :]
    return [tuple(line.split("\t", 2)) for line in table if line.count("\t") >= 2]


class Run:
    def __init__(self, root: Path, directory: Path):
        self.root = root
        self.directory = directory
        self.state_path = directory / "state.json"
        self.state = load_json(self.state_path)
        # Proposers run in parallel threads and all record their calls in state.json.
        self.lock = threading.Lock()
        self.events = events.Events(directory / "events.jsonl")

    def activity(self) -> dict[str, tuple[float, str]]:
        """Working agents and their last log activity, for the stall watchdog."""
        return {
            f"{call['role']} ({call['provider']} {call['model']})": events.file_activity(self.directory / "logs" / f"{call['step']}.log")
            for call in self.state.get("calls", [])
            if call.get("startedAt") and not call.get("finishedAt")
        }

    @property
    def worktree(self) -> Path:
        return Path(self.state["worktree"])

    def role(self, identifier: str) -> dict[str, Any]:
        return load_json(self.root / "catalog" / "agents" / f"{identifier}.json")

    def save(self, **changes: Any) -> None:
        with self.lock:
            # The process that last advanced the run; ./cc settings marks a live stage with a dead pid as stalled.
            self.state.update(changes, pid=os.getpid())
            write_json(self.state_path, self.state)

    def step(self, name: str) -> None:
        """Enter a step of the plan: ./cc council status and the ticker show it with its own timer."""
        say(f"-> {name}")
        self.save(phase=name, phaseStartedAt=now())
        self.events.emit("step", f"{name}  {progress.bar(self.state)}")

    def handoff(self, text: str) -> None:
        """Record what one agent's output gives to the next: the conversation between agents."""
        self.events.emit("handoff", text)

    def call(self, identifier: str, body: str, name: str) -> str:
        role = self.role(identifier)
        say(f"   {name}: {identifier} ({role['provider']} {role['model']}, {role['access']})")
        self.events.emit("agent", f"{identifier} ({role['provider']} {role['model']}) started {name}", who=identifier)
        started = datetime.datetime.now()
        call = {"step": name, "role": identifier, "provider": role["provider"], "model": role["model"], "startedAt": now()}
        with self.lock:
            calls = self.state.setdefault("calls", [])
            index = len(calls)
            calls.append(call)
        self.save()
        try:
            reply = providers.run(
                role,
                role_prompt(self.root, role, self.state, body),
                self.worktree,
                self.directory / "logs" / f"{name}.log",
                on_say=lambda text: self.events.emit("say", f"{identifier}: {text}", who=identifier),
            )
        except providers.ProviderError as error:
            self.events.emit("error", str(error), who=identifier)
            raise
        finally:
            with self.lock:
                self.state["calls"][index]["finishedAt"] = now()
                self.state["calls"][index]["toolCalls"] = providers.tool_calls(self.directory / "logs" / f"{name}.log")
                self.state["calls"][index]["usage"] = providers.usage(self.directory / "logs" / f"{name}.log")
            self.save()
        took = events.clock(int((datetime.datetime.now() - started).total_seconds()))
        self.events.emit("agent", f"{identifier} finished {name} in {took}", who=identifier)
        (self.directory / f"{name}.md").write_text(reply + "\n", encoding="utf-8")
        return reply

    def verify(self, name: str) -> tuple[bool, str, list[tuple[str, str, str]]]:
        say(f"   ./cc feature {self.state['feature']} verify ({name})")
        result = subprocess.run(
            [str(self.root / "cc"), "feature", self.state["feature"], "verify", self.state["repository"], "--quality", "--full"],
            cwd=self.root,
            capture_output=True,
            text=True,
            # verify reports its gates and stalls into this run's stream.
            env={**os.environ, "CC_EVENTS": str(self.events.path)},
        )
        output = result.stdout + result.stderr
        (self.directory / f"verify-{name}.log").write_text(output, encoding="utf-8")
        return result.returncode == 0, output, verify_rows(output)

    # Stages -------------------------------------------------------------------------

    def propose_and_judge(self, council: dict[str, Any]) -> None:
        task = f"# Task\n\n{self.state['task']}"
        if self.state["kind"] == "debug":
            # Evidence before opinions: every role sees what the CC itself reports for the failure.
            self.step("reproduce")
            passed, output, _ = self.verify("reproduce")
            command = f"./cc feature {self.state['feature']} verify {self.state['repository']} --quality"
            task += (
                f"\n\n# Reproduction\n\n`{command}` passes: no existing check reproduces the failure, "
                "so the reproduction test has to."
                if passed
                else f"\n\n# Reproduction\n\n`{command}` fails:\n\n```\n{tail(output)}\n```"
            )
        self.save(brief=task)
        self.step("propose")
        with concurrent.futures.ThreadPoolExecutor(len(council["proposers"])) as pool:
            futures = {
                identifier: pool.submit(self.call, identifier, task, f"proposal-{identifier}")
                for identifier in council["proposers"]
            }
            proposals = {identifier: future.result() for identifier, future in futures.items()}

        # The judge sees shuffled labels only; the mapping stays in state.json for the user.
        order = list(proposals)
        random.SystemRandom().shuffle(order)
        labels = {chr(ord("A") + index): identifier for index, identifier in enumerate(order)}
        self.save(labels=labels)
        anonymous = "\n\n".join(
            f"# Proposal {label}\n\n{proposals[identifier]}" for label, identifier in labels.items()
        )
        self.step("judge")
        self.handoff(f"{', '.join(proposals)} → {council['judge']}: proposals as anonymous {', '.join(labels)}")
        verdict = self.call(council["judge"], f"{task}\n\n{anonymous}", "verdict")
        decision = re.match(r"\s*DECISION:\s*([A-Z]+)", verdict)
        self.save(decision=decision.group(1) if decision else "UNPARSED")
        if self.state["decision"] == "REJECT":
            self.finish("rejected", "the judge rejected every proposal; see verdict.md")
            return
        if self.state["decision"] == "UNPARSED":
            self.finish("failed", "the judge reply has no DECISION line; see verdict.md")
            return
        if council["approval"]:
            self.save(stage="awaiting-approval", phase="approval", phaseStartedAt=now())
            # The process stops here, so the stream ends too; approve starts a new segment of it.
            self.events.emit(events.END, f"verdict {self.state['decision']}, awaiting approval: ./cc council approve|reject {self.state['id']}", ok=True)
            self.show_verdict()
            return
        self.save(instructions=verdict)
        self.write_and_verify(council)

    def write_and_verify(self, council: dict[str, Any]) -> None:
        self.save(stage="writing")
        brief = self.state.get("brief") or f"# Task\n\n{self.state['task']}"
        instructions = f"{brief}\n\n# Approved instructions\n\n{self.state['instructions']}"
        source = f"proposal {self.state['pick'].upper()}" if self.state.get("pick") else f"{council['judge']} verdict"
        self.handoff(f"{source}{' + user note' if '# User note' in self.state['instructions'] else ''} → {council['writer']}")
        phase = ""
        guarded: dict[str, bytes] = {}
        if self.state["kind"] == "debug":
            # Red first, enforced: the reproduction test must fail on the unfixed code.
            self.step("write test")
            self.call(
                council["writer"],
                f"{instructions}\n\n# Phase 1 of 2: reproduction test\n\n"
                "Write only the failing reproduction test from the instructions. Do not change production code yet.",
                "write-test",
            )
            self.step("red check")
            passed, output, _ = self.verify("red")
            if passed:
                self.finish("failed", "the reproduction test passes on the unfixed code, so it does not reproduce the bug; see verify-red.log")
                return
            guarded = {path: (self.worktree / path).read_bytes() for path in changed_files(self.worktree)}
            phase = (
                "\n\n# Phase 2 of 2: fix\n\nThe reproduction test is written and fails as expected. Fix the root cause "
                f"so it passes. Do not modify the reproduction test.\n\n```\n{tail(output)}\n```"
            )
        attempt = 0
        failure = ""
        # Every attempt is a fresh writer with an empty context; this history is what carries over.
        history = self.directory / "attempts.md"
        title = (
            f"# Writer attempts: run {self.state['id']}\n\n"
            "One section per writer attempt, oldest first. Each attempt is a new writer process that starts with "
            "an empty context; every retry is given the sections above it.\n"
        )
        entries: list[str] = []
        history.write_text(title, encoding="utf-8")
        writer = self.role(council["writer"])
        while True:
            body = instructions + phase
            if failure:
                body += (
                    "\n\n# Earlier attempts\n\n"
                    "Each attempt below was a fresh writer like you, and the worktree keeps its edits. Do not repeat an "
                    "approach that already failed; if the same error comes back, question its cause before editing.\n\n"
                    + "\n".join(entries)
                    + f"\n# Verification failed (retry {attempt} of {council['maxRetries']})\n\n"
                    f"Fix the cause. Never weaken tests or checks.\n\n```\n{failure}\n```"
                )
            self.step("fix" if self.state["kind"] == "debug" else "write")
            before, started, stopped = file_states(self.worktree), datetime.datetime.now(), False
            try:
                reply = self.call(council["writer"], body, f"write-{attempt}")
            except providers.ToolCapError:
                # Half-done edits stay in the worktree: verify shows their state, and the next writer starts fresh.
                reply, stopped = "", True
            seconds = int((datetime.datetime.now() - started).total_seconds())
            changed = touched_files(before, file_states(self.worktree))
            touched = [path for path, content in guarded.items() if not (self.worktree / path).is_file() or (self.worktree / path).read_bytes() != content]
            if touched:
                self.finish("failed", f"the writer changed the reproduction test during the fix: {', '.join(touched)}")
                return
            self.step("verify")
            passed, output, rows = self.verify(str(attempt))
            outcome = "verify passed" if passed else "verify failed"
            if stopped:
                outcome = f"stopped at its cap of {providers.max_tool_calls(writer)} tool calls, then {outcome}"
            entry = [
                f"## Attempt {attempt + 1} of {council['maxRetries'] + 1}: {outcome}",
                "",
                f"- Writer: {council['writer']} ({writer['provider']} {writer['model']}), "
                f"{providers.tool_calls(self.directory / 'logs' / f'write-{attempt}.log')} tool calls, "
                f"{providers.describe(providers.usage(self.directory / 'logs' / f'write-{attempt}.log'))}, {events.clock(seconds)}",
                f"- Files it changed: {', '.join(changed) or 'none'}",
            ]
            if not passed:
                failed = [f"{repo} {gate}" for repo, gate, result in rows if result.startswith("FAIL")]
                entry.append(f"- Failed gates: {', '.join(failed) or 'none listed; see the full output'}")
                entry += ["- Key errors:", *(f"  - {line}" for line in key_errors(output) or ["none recognized; see the full output"])]
            entry.append(f"- What the writer said: {' '.join(reply.split())[:400] or 'nothing; it was stopped before it replied'}")
            entry.append(f"- Full output: verify-{attempt}.log, logs/write-{attempt}.log\n")
            entries.append("\n".join(entry))
            history.write_text(title + "\n" + "\n".join(entries), encoding="utf-8")
            (self.directory / "changes.diff").write_text(worktree_diff(self.worktree), encoding="utf-8")
            self.save(qualityWarnings=[f"{gate} gate missing for {repo}" for repo, gate, result in rows if result.startswith("WARN")])
            if passed:
                break
            if attempt >= council["maxRetries"]:
                failed = {gate for _, gate, result in rows if result.startswith("FAIL")}
                if failed == {"mutation"}:
                    reason = "tests pass but miss injected bugs (surviving mutants)"
                elif self.state["kind"] == "testing":
                    reason = "new tests fail: suspected bugs in the code"
                elif self.state["kind"] == "debug":
                    reason = f"{attempt + 1} fixes failed: question the design with the user before another attempt"
                else:
                    reason = f"verify still fails after {attempt} retries"
                self.finish("verify-failed", f"{reason}; see attempts.md")
                return
            attempt += 1
            failure = tail(output)
            failed = ", ".join(f"{repo} {gate}" for repo, gate, result in rows if result.startswith("FAIL")) or "see log"
            self.events.emit("warn", f"verify failed ({failed}); retry {attempt} of {council['maxRetries']}")
            self.handoff(f"verify failure + {len(entries)} earlier attempt(s) → {council['writer']} (retry {attempt})")

        reviewers = progress.security_roles(council)
        if reviewers:
            diff = (self.directory / "changes.diff").read_text(encoding="utf-8")
            reason = self.review_security(council, reviewers, f"# Diff\n\n```diff\n{diff}\n```")
            if reason:
                self.finish("blocked-security", reason)
                return
        summary = "verify passed; review changes.diff and commit it yourself"
        if self.state.get("qualityWarnings"):
            summary += f"; WARNING: {'; '.join(self.state['qualityWarnings'])}"
        self.finish("done", summary)

    def review_security(self, council: dict[str, Any], reviewers: list[str], diff: str) -> str:
        """Run the reviewers in parallel and, with several, the judge's summary; return why it blocks, or ''.

        A high (or unreadable) severity from the summary or from any single reviewer blocks: the judge
        verifies and explains findings but never overrules a reviewer's high one; the user decides.
        """
        self.step("security")
        self.handoff(f"changes.diff → {', '.join(reviewers)}")
        if len(reviewers) == 1:
            severity = parse_severity(self.call(reviewers[0], diff, "security"))
            self.save(security=severity)
            return "security review blocks the change; see security.md" if severity in BLOCKING else ""
        with concurrent.futures.ThreadPoolExecutor(len(reviewers)) as pool:
            futures = {identifier: pool.submit(self.call, identifier, diff, f"review-{identifier}") for identifier in reviewers}
            reviews = {identifier: future.result() for identifier, future in futures.items()}
        severities = {identifier: parse_severity(review) for identifier, review in reviews.items()}
        order = list(reviews)
        random.SystemRandom().shuffle(order)
        labels = {chr(ord("A") + index): identifier for index, identifier in enumerate(order)}
        self.save(securityLabels=labels, securityReviews=severities)
        anonymous = "\n\n".join(f"# Review {label}\n\n{reviews[identifier]}" for label, identifier in labels.items())
        self.step("security summary")
        self.handoff(f"{', '.join(reviewers)} → {council['judge']}: reviews as anonymous {', '.join(labels)}")
        summary = self.call(council["judge"], f"# Security summary\n\n{diff}\n\n{anonymous}", "security")
        severity = parse_severity(summary)
        flagged = [identifier for identifier, value in severities.items() if value in BLOCKING]
        self.save(security=severity, securityFlagged=flagged)
        say("Review authors (hidden from the judge): " + ", ".join(f"{label}: {identifier}" for label, identifier in labels.items()))
        if severity in BLOCKING:
            return f"the security summary says {severity}; see security.md"
        if flagged:
            return (f"{', '.join(flagged)} reported high or an unreadable review, which the judge rated {severity}; "
                    "the judge cannot overrule it: read security.md and " + ", ".join(f"review-{role}.md" for role in flagged))
        return ""

    def finish(self, stage: str, summary: str) -> None:
        self.save(stage=stage, summary=summary, finishedAt=now())
        self.events.emit(events.END, f"{stage}: {summary}", ok=stage == "done", stage=stage)
        say(f"\nrun {self.state['id']}: {stage} - {summary}")
        say(f"cost: {run_tokens(self.state)}")
        say(f"artifacts: {self.directory}")

    def show_verdict(self) -> None:
        say("\n" + (self.directory / "verdict.md").read_text(encoding="utf-8"))
        say("Proposal authors (hidden from the judge):")
        for label, identifier in self.state["labels"].items():
            role = self.role(identifier)
            say(f"  {label}: {identifier} ({role['provider']} {role['model']})")
        say(f"\nAwaiting approval. Approve: ./cc council approve {self.state['id']} [--pick <label>] [--note <text>]")
        say(f"Reject: ./cc council reject {self.state['id']}")


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def run_tokens(state: dict[str, Any]) -> str:
    """Every finished call's tokens added up, e.g. '312.4k tokens (...) in 7 calls'. A call whose CLI
    never reported tokens (stopped or failed early) makes the total a lower bound, and says so."""
    finished = [call for call in state.get("calls", []) if call.get("finishedAt")]
    reported = [call["usage"] for call in finished if call.get("usage")]
    total = {key: sum(used[key] for used in reported) for key in ("input", "cached", "output")}
    text = f"{providers.describe(total)} in {len(finished)} calls"
    missing = len(finished) - len(reported)
    return f"at least {text}; {missing} did not report tokens" if missing else text


def find_run(root: Path, identifier: str) -> Run:
    matches = list(worktrees_root(root).glob(f"*/.cc-runs/{identifier}/state.json"))
    if len(matches) != 1:
        raise ValueError(f"run not found: {identifier}")
    return Run(root, matches[0].parent)


def council_config(root: Path, identifier: str) -> dict[str, Any]:
    path = root / "catalog" / "councils" / f"{identifier}.json"
    if not path.is_file():
        raise ValueError(f"council not found: catalog/councils/{identifier}.json")
    return load_json(path)


def pipeline_council(root: Path, identifier: str) -> dict[str, Any]:
    """A council that runs on a feature; the threads council judges PR threads instead, through ./cc prs judge."""
    council = council_config(root, identifier)
    if council.get("kind") == "threads":
        raise ValueError(f"council {identifier} judges review threads: run ./cc prs judge <repo> <n>; "
                         "change its roles with ./cc council set-role")
    return council


def command_run(root: Path, args: argparse.Namespace) -> None:
    council = pipeline_council(root, args.council)
    manifest = load_json(worktrees_root(root) / args.feature / ".cc-worktree.json")
    repositories = manifest.get("repositories", {})
    repository = args.repo or (next(iter(repositories)) if len(repositories) == 1 else None)
    if repository not in repositories:
        raise ValueError(f"choose one of the feature's repositories with --repo: {', '.join(repositories)}")
    worktree = (root / load_json(root / "control-center.json")["layout"]["projectsRoot"]).resolve() / repositories[
        repository
    ]["worktree"]
    if git(worktree, "status", "--porcelain"):
        raise ValueError(f"worktree has uncommitted changes; commit or stash them first: {worktree}")
    task = Path(args.task_file).read_text(encoding="utf-8") if args.task_file else args.task
    if not task or not task.strip():
        raise ValueError("give the task with --task or --task-file")
    for slot in [*council["proposers"], council["judge"], council["writer"], *progress.security_roles(council)]:
        if slot:
            role = load_json(root / "catalog" / "agents" / f"{slot}.json")
            available, detail = providers.login_status(role["provider"])
            if not available:
                raise ValueError(f"role {slot} needs {role['provider']}: {detail}")

    descriptor = root / "catalog" / "repositories" / f"{repository}.json"
    entry = load_json(descriptor) if descriptor.is_file() else {}
    identifier = f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{council['id']}"
    directory = worktrees_root(root) / args.feature / ".cc-runs" / identifier
    directory.mkdir(parents=True)
    write_json(
        directory / "state.json",
        {
            "id": identifier,
            "council": council["id"],
            "kind": council["kind"],
            "feature": args.feature,
            "repository": repository,
            "worktree": str(worktree),
            "targets": entry.get("targets", []),
            "stack": entry.get("stack", []),
            "task": task.strip(),
            "stage": "proposing",
            "plan": progress.plan(council["kind"], council),
            "startedAt": now(),
        },
    )
    say(f"run {identifier}: {council['kind']} council on {args.feature}/{repository}")
    run = Run(root, directory)
    with Ticker(run):
        run.propose_and_judge(council)


def command_approve(root: Path, args: argparse.Namespace) -> None:
    run = find_run(root, args.run)
    if run.state["stage"] != "awaiting-approval":
        raise ValueError(f"run {args.run} is {run.state['stage']}, not awaiting approval")
    if args.pick:
        identifier = run.state["labels"].get(args.pick.upper())
        if not identifier:
            raise ValueError(f"unknown proposal label: {args.pick}")
        instructions = (run.directory / f"proposal-{identifier}.md").read_text(encoding="utf-8")
    else:
        instructions = (run.directory / "verdict.md").read_text(encoding="utf-8")
    if args.note:
        instructions += f"\n\n# User note (overrides the above where they conflict)\n\n{args.note}"
    run.save(instructions=instructions, approvedAt=now(), pick=args.pick)
    with Ticker(run):
        run.write_and_verify(council_config(root, run.state["council"]))


def command_reject(root: Path, args: argparse.Namespace) -> None:
    run = find_run(root, args.run)
    if run.state["stage"] != "awaiting-approval":
        raise ValueError(f"run {args.run} is {run.state['stage']}, not awaiting approval")
    run.finish("rejected", "rejected by the user")


def command_status(root: Path, args: argparse.Namespace) -> None:
    if args.run:
        run = find_run(root, args.run)
        if run.state["stage"] == "awaiting-approval":
            run.show_verdict()
            return
        say(progress.line(run.state))
        if run.state.get("summary"):
            say(f"  {run.state['summary']}")
        say(f"  cost so far: {run_tokens(run.state)}")
        history = run.directory / "attempts.md"
        for line in history.read_text(encoding="utf-8").splitlines() if history.is_file() else []:
            if line.startswith("## Attempt "):
                say(f"  {line[3:]}")
        # What each working agent is doing: the last lines of its live log.
        for call in run.state.get("calls", []):
            if call.get("startedAt") and not call.get("finishedAt"):
                waited = progress.clock(progress.seconds_since(call["startedAt"]))
                say(f"  {call['role']} ({call['provider']} {call['model']}), working {waited}:")
                log = run.directory / "logs" / f"{call['step']}.log"
                lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[2:] if log.is_file() else []
                for line in [line for line in lines if line.strip()][-args.lines:] or ["(no activity logged yet)"]:
                    say(f"    {line[:160]}")
        return
    rows = []
    for path in sorted(worktrees_root(root).glob("*/.cc-runs/*/state.json")):
        state = load_json(path)
        rows.append(f"{state['id']}\t{state['feature']}/{state['repository']}\t{state['stage']}\t{progress.bar(state)}\t"
                    f"{run_tokens(state).split(' (')[0]}")
    say("RUN\tFEATURE/REPO\tSTAGE\tPROGRESS\tTOKENS\n" + "\n".join(rows) if rows else "no council runs")


def edit_validated(root: Path, path: Path, changes: dict[str, Any]) -> list[str]:
    """Apply changes to a catalog file and keep them only if the CC still validates; return its warnings."""
    spec = importlib.util.spec_from_file_location("validator", root / "scripts" / "validate-control-center.py")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    original = path.read_text(encoding="utf-8")
    write_json(path, {**json.loads(original), **changes})
    errors = [error for error in validator.validate(root) if "catalog/agents" in error or "catalog/councils" in error]
    if errors:
        path.write_text(original, encoding="utf-8")
        raise ValueError("change rejected:\n  " + "\n  ".join(errors))
    return [warning for warning in validator.floating_models(root) if warning.startswith(path.relative_to(root).as_posix())]


def command_set_role(root: Path, args: argparse.Namespace) -> None:
    path = root / "catalog" / "agents" / f"{args.role}.json"
    if not path.is_file():
        raise ValueError(f"role not found: catalog/agents/{args.role}.json")
    changes = {key: value for key, value in (("provider", args.provider), ("model", args.model)) if value}
    if args.max_tool_calls is not None:
        changes["maxToolCalls"] = args.max_tool_calls
    if not changes:
        raise ValueError("give --provider, --model and/or --max-tool-calls")
    warnings = edit_validated(root, path, changes)
    role = load_json(path)
    say(f"{args.role}: {role['provider']} {role['model']}, at most {providers.max_tool_calls(role)} tool calls per call")
    for warning in warnings:
        say(f"warning: {warning}")


def command_models(root: Path, args: argparse.Namespace) -> None:
    for provider in providers.PROVIDERS:
        names = providers.models(provider)
        say(f"{provider}: {', '.join(names) if names else '(not logged in)'}")


def command_set_council(root: Path, args: argparse.Namespace) -> None:
    path = root / "catalog" / "councils" / f"{args.council}.json"
    pipeline_council(root, args.council)
    changes: dict[str, Any] = {}
    if args.approval:
        changes["approval"] = args.approval == "on"
    if args.max_retries is not None:
        changes["maxRetries"] = args.max_retries
    if not changes:
        raise ValueError("give --approval and/or --max-retries")
    edit_validated(root, path, changes)
    council = load_json(path)
    say(f"{args.council}: approval {'on' if council['approval'] else 'off'}, max retries {council['maxRetries']}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="./cc council")
    result.add_argument("root", type=Path)
    subparsers = result.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="start a council on a feature worktree")
    run.add_argument("council", help="council id in catalog/councils, e.g. coding or testing")
    run.add_argument("--feature", required=True)
    run.add_argument("--repo", help="repository in the feature; required when it has several")
    task = run.add_mutually_exclusive_group(required=True)
    task.add_argument("--task")
    task.add_argument("--task-file")
    run.set_defaults(handler=command_run)

    approve = subparsers.add_parser("approve", help="approve the verdict and let the writer implement it")
    approve.add_argument("run")
    approve.add_argument("--pick", help="use this proposal label instead of the judge's instructions")
    approve.add_argument("--note", help="extra instruction for the writer")
    approve.set_defaults(handler=command_approve)

    reject = subparsers.add_parser("reject", help="close a run awaiting approval")
    reject.add_argument("run")
    reject.set_defaults(handler=command_reject)

    status = subparsers.add_parser("status", help="list runs, or show one")
    status.add_argument("run", nargs="?")
    status.add_argument("--lines", type=int, default=5, help="activity lines per working agent")
    status.set_defaults(handler=command_status)

    set_role = subparsers.add_parser("set-role", help="change which provider and model a role uses")
    set_role.add_argument("role")
    set_role.add_argument("--provider", help="claude, codex or cursor (cursor: read-only roles only)")
    set_role.add_argument("--model")
    set_role.add_argument("--max-tool-calls", type=int, help="stop one call of this role past this many tool calls")
    set_role.set_defaults(handler=command_set_role)

    models = subparsers.add_parser("models", help="list the models of each logged-in provider")
    models.set_defaults(handler=command_models)

    set_council = subparsers.add_parser("set", help="change a council's approval pause or retry limit")
    set_council.add_argument("council")
    set_council.add_argument("--approval", choices=("on", "off"))
    set_council.add_argument("--max-retries", type=int)
    set_council.set_defaults(handler=command_set_council)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        args.handler(args.root.resolve(), args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError, providers.ProviderError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
