#!/usr/bin/env python3
"""Test the council's security step: reviewers run in parallel, the judge summarizes anonymous reviews,
and a high from the summary or from any one reviewer blocks; one reviewer and none behave as before."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import progress
import providers
import run as council_run

ROLES = ("security-claude", "security-gpt", "security-grok", "judge")


class FakeProviders:
    """Stands in for the provider CLIs: each role answers from a script and every prompt is recorded."""

    def __init__(self, answers: dict[str, str]):
        self.answers = answers
        self.prompts: dict[str, str] = {}

    def __call__(self, role, prompt, cwd, log, timeout=3600, on_say=None):
        self.prompts[role["id"]] = prompt
        return self.answers[role["id"]]


def make_run(base: Path, name: str) -> council_run.Run:
    root = base / name
    for identifier in ROLES:
        path = root / "catalog" / "agents" / f"{identifier}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"id": identifier, "provider": "claude", "model": "m", "access": "read-only", "skills": []}))
    directory = root / "run"
    directory.mkdir()
    (directory / "state.json").write_text(json.dumps({
        "id": "r1", "council": "coding", "kind": "coding", "feature": "f", "repository": "api", "worktree": str(root),
        "targets": ["linux"], "stack": [], "task": "t", "stage": "writing", "plan": [],
    }))
    return council_run.Run(root, directory)


def review(base: Path, name: str, answers: dict[str, str], reviewers: list[str]) -> tuple[council_run.Run, FakeProviders, str]:
    fake = FakeProviders(answers)
    providers.run = fake
    run = make_run(base, name)
    reason = run.review_security({"judge": "judge"}, reviewers, "# Diff\n\n```diff\n+eval(input())\n```")
    return run, fake, reason


VERIFY_FAILS = "running tests\nFAILED test_app.py::test_parse - AssertionError: 1 != 2\n\nREPO\tGATE\tRESULT\napi\ttests\tFAIL (exit 1)\n"


def check_writer_history(base: Path) -> None:
    """Every retry gets one readable section per earlier attempt; a capped attempt is recorded, not fatal."""
    worktree = base / "history-worktree"
    worktree.mkdir()
    git = ["git", "-C", str(worktree), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(worktree)], check=True)
    (worktree / "app.py").write_text("base\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], check=True)

    def fake_writer(attempts: list):
        prompts: list[str] = []

        def call(role, prompt, cwd, log, timeout=3600, on_say=None):
            prompts.append(prompt)
            content, reply = attempts[len(prompts) - 1]
            (worktree / "app.py").write_text(content)
            if reply is None:
                raise providers.ToolCapError("writer reached its cap of 100 tool calls")
            return reply
        return call, prompts

    def write(name: str, attempts: list) -> tuple[council_run.Run, list[str], str]:
        (worktree / "app.py").write_text("base\n")
        run = make_run(base, name)
        writer = run.root / "catalog" / "agents" / "writer.json"
        writer.write_text(json.dumps({"id": "writer", "provider": "claude", "model": "opus", "access": "write-worktree", "skills": []}))
        run.save(worktree=str(worktree), instructions="Make parse return 2.")
        providers.run, prompts = fake_writer(attempts)
        run.verify = lambda name: ((worktree / "app.py").read_text() == "fixed\n", VERIFY_FAILS, council_run.verify_rows(VERIFY_FAILS))
        run.write_and_verify({"writer": "writer", "maxRetries": 2, "judge": "judge", "security": None})
        return run, prompts, (run.directory / "attempts.md").read_text()

    run, prompts, history = write("history", [
        ("wrong\n", "Changed parse in app.py to return 1."), ("half\n", None), ("fixed\n", "Return 2 from parse."),
    ])
    assert run.state["stage"] == "done", run.state
    assert "Earlier attempts" not in prompts[0], "the first attempt has no history"
    assert "## Attempt 1 of 3: verify failed" in prompts[1] and "## Attempt 2" not in prompts[1]
    third = prompts[2]
    assert "## Attempt 1 of 3: verify failed" in third and "## Attempt 2 of 3: stopped at its cap of 100 tool calls, then verify failed" in third
    assert third.index("# Earlier attempts") < third.index("# Verification failed (retry 2 of 2)"), "history, then the latest output"
    assert "## Attempt 3 of 3: verify passed" in history, history
    for expected in ("- Writer: writer (claude opus), 0 tool calls, tokens not reported,","- Files it changed: app.py", "- Failed gates: api tests",
                     "  - FAILED test_app.py::test_parse - AssertionError: 1 != 2",
                     "- What the writer said: Changed parse in app.py to return 1.",
                     "- What the writer said: nothing; it was stopped before it replied",
                     "- Full output: verify-0.log, logs/write-0.log"):
        assert expected in history, (expected, history)
    assert "running tests" not in history, "only error lines are quoted; the rest stays in the log"
    assert "Failed gates" not in history.split("## Attempt 3")[1], "a passing attempt lists no failures"

    run, _, history = write("history-exhausted", [("wrong\n", "One."), ("wrong\n", "Two."), ("wrong\n", "Three.")])
    assert run.state["stage"] == "verify-failed" and run.state["summary"].endswith("see attempts.md"), run.state
    assert "- Files it changed: none" in history.split("## Attempt 2")[1], "an attempt that changed nothing says so"


def check_catalog_rules() -> None:
    """The shipped templates validate, a writing Cursor role does not, and security takes a list or one role."""
    template = Path(__file__).resolve().parent.parent.parent
    spec = importlib.util.spec_from_file_location("validator", template / "scripts" / "validate-control-center.py")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        shutil.copytree(template / "templates" / "agents", root / "catalog" / "agents")
        shutil.copytree(template / "templates" / "councils", root / "catalog" / "councils")
        shutil.copytree(template / ".agents", root / ".agents")
        for path in root.rglob("*"):  # the Nix store copy is read-only
            path.chmod(path.stat().st_mode | 0o200)

        def errors() -> list[str]:
            found: list[str] = []
            validator.validate_councils(root, validator.validate_agents(root, found), found)
            return found

        assert errors() == [], errors()
        coding = root / "catalog" / "councils" / "coding.json"
        assert json.loads(coding.read_text())["security"] == ["security-claude", "security-gpt", "security-grok"]
        assert json.loads((root / "catalog" / "agents" / "judge.json").read_text())["provider"] == "cursor"

        writer = root / "catalog" / "agents" / "writer.json"
        writer.write_text(json.dumps({**json.loads(writer.read_text()), "provider": "cursor"}))
        assert any("cursor roles must be read-only" in error for error in errors()), errors()
        writer.write_text(json.dumps({**json.loads(writer.read_text()), "provider": "claude"}))
        data = json.loads(writer.read_text())
        for cap, valid in ((50, True), (1000, True), (0, False), (1001, False), ("80", False), (True, False)):
            writer.write_text(json.dumps({**data, "maxToolCalls": cap}))
            assert (errors() == []) == valid, (cap, errors())
        writer.write_text(json.dumps(data))

        # A floating alias is allowed but warned about; the shipped roles pin full model ids.
        assert validator.floating_models(root) == [], validator.floating_models(root)
        sceptic = root / "catalog" / "agents" / "sceptic-claude.json"
        pinned = json.loads(sceptic.read_text())
        for provider, model in (("claude", "opus"), ("claude", "sonnet"), ("cursor", "auto")):
            sceptic.write_text(json.dumps({**pinned, "provider": provider, "model": model}))
            assert validator.floating_models(root) == [
                f"catalog/agents/sceptic-claude.json: model '{model}' follows the newest {provider} model; pin a full model id"
            ], (model, validator.floating_models(root))
            assert errors() == [], (model, errors())
        sceptic.write_text(json.dumps(pinned))

        data = json.loads(coding.read_text())
        for security, valid in (("security-claude", True), (None, True), ([], True), (["security-grok", "security-grok"], False),
                                (["writer"], False), (["nobody"], False), ({"a": 1}, False)):
            coding.write_text(json.dumps({**data, "security": security}))
            assert (errors() == []) == valid, (security, errors())
        coding.write_text(json.dumps(data))

        # The threads council: read-only sceptics, and a judge whenever there are several of them.
        threads = root / "catalog" / "councils" / "threads.json"
        data = json.loads(threads.read_text())
        assert data["sceptics"] == ["sceptic-claude", "sceptic-grok"] and data["judge"] == "sceptic-judge", data
        assert {json.loads((root / "catalog" / "agents" / f"{role}.json").read_text())["provider"]
                for role in (*data["sceptics"], data["judge"])} == {"claude", "cursor", "codex"}, "three model families"
        for changes, valid in (({}, True), ({"sceptics": ["sceptic-claude"], "judge": None}, True),
                               ({"judge": None}, False), ({"sceptics": ["sceptic-claude", "sceptic-claude"]}, False),
                               ({"sceptics": ["writer", "sceptic-grok"]}, False), ({"sceptics": []}, False),
                               ({"judge": "writer"}, False)):
            threads.write_text(json.dumps({**data, **changes}))
            assert (errors() == []) == valid, (changes, errors())
        threads.write_text(json.dumps(data))

        # The risk council: at least two distinct assessors and a judge, read-only with or without web, from two families.
        risk = root / "catalog" / "councils" / "risk.json"
        data = json.loads(risk.read_text())
        assert {json.loads((root / "catalog" / "agents" / f"{role}.json").read_text())["provider"]
                for role in data["assessors"]} == {"claude", "codex"}, "two model families"
        for changes, valid in (({}, True), ({"assessors": ["risk-claude", "risk-gpt", "security-grok"]}, True),
                               ({"assessors": ["risk-claude"]}, False), ({"assessors": ["risk-gpt", "risk-gpt"]}, False),
                               ({"assessors": ["writer", "risk-gpt"]}, False), ({"judge": "writer"}, False),
                               ({"judge": "judge"}, True), ({"judge": None}, False)):
            risk.write_text(json.dumps({**data, **changes}))
            assert (errors() == []) == valid, (changes, errors())
        risk.write_text(json.dumps(data))
        # Web search is for risk roles only: a coding proposer with it is refused.
        proposer = root / "catalog" / "agents" / "proposer-claude.json"
        proposer.write_text(json.dumps({**json.loads(proposer.read_text()), "access": "read-only-web"}))
        assert any("proposer 'proposer-claude' must have access 'read-only'" in error for error in errors()), errors()


def check_risk_council(base: Path) -> None:
    """Assessors work alone on the planned change, the judge sees them only as anonymous labels, and the run ends with
    the judge's verdict in risk.md; a reply without a verdict fails the run instead of passing as advice."""
    council = {"assessors": ["risk-a", "risk-b"], "judge": "risk-judge"}

    def assess(name: str, answers: dict[str, str]) -> tuple[council_run.Run, FakeProviders]:
        fake = FakeProviders(answers)
        providers.run = fake
        root = base / name
        for identifier in [*council["assessors"], council["judge"]]:
            path = root / "catalog" / "agents" / f"{identifier}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"id": identifier, "provider": "claude", "model": "m", "access": "read-only-web", "skills": []}))
        directory = root / ".cc-local" / "runs" / "r1-risk"
        directory.mkdir(parents=True)
        (root / "control-center.json").write_text(json.dumps({"layout": {"worktrees": "worktrees"}}))
        (directory / "state.json").write_text(json.dumps({
            "id": "r1-risk", "council": "risk", "kind": "risk", "feature": "", "repository": "ai",
            "repositories": {"ai": "repos/project/ai"}, "worktree": str(root), "targets": ["linux", "windows"], "stack": ["python"],
            "task": "Replace TEI with PyTorch for embeddings", "stage": "assessing", "plan": ["assess", "risk summary"],
        }))
        run = council_run.Run(root, directory)
        run.assess(council)
        return run, fake

    register = "VERDICT: GO-WITH-CONDITIONS\n\n## Summary\nParity of embeddings decides."
    run, fake = assess("risk", {"risk-a": "R1 cosine drift", "risk-b": "R2 cold start", "risk-judge": register})
    assessed = fake.prompts["risk-a"]
    assert "Replace TEI with PyTorch" in assessed and "- ai: repos/project/ai" in assessed and "Control Center root" in assessed
    assert "R2 cold start" not in assessed, "assessors work alone"
    judged = fake.prompts["risk-judge"]
    assert "# Assessment A" in judged and "# Assessment B" in judged and "R1 cosine drift" in judged and "R2 cold start" in judged
    assert "risk-a" not in judged and "risk-b" not in judged, "the judge must not know who wrote which assessment"
    assert sorted(run.state["labels"].values()) == ["risk-a", "risk-b"]
    assert run.state["stage"] == "done" and run.state["decision"] == "GO-WITH-CONDITIONS", run.state
    assert (run.directory / "risk.md").read_text().startswith("VERDICT: GO-WITH-CONDITIONS"), "the register is risk.md"
    assert (run.directory / "assessment-risk-b.md").read_text().strip() == "R2 cold start"
    assert council_run.find_run(run.root, "r1-risk").directory == run.directory, "risk runs are found without a feature"

    run, _ = assess("risk-unparsed", {"risk-a": "R1", "risk-b": "R2", "risk-judge": "Looks fine overall."})
    assert run.state["stage"] == "failed" and "VERDICT" in run.state["summary"], run.state
    run, _ = assess("risk-no-go", {"risk-a": "R1", "risk-b": "R2", "risk-judge": "VERDICT: NO-GO\n\nbreaks the contract"})
    assert run.state["decision"] == "NO-GO", "NO-GO is not read as GO"


def check_review_lessons(base: Path) -> None:
    """Roles see the active lessons for the council's repository, and only those."""
    run = make_run(base, "lessons")
    state = {**run.state, "repository": "api"}
    role = {"id": "writer", "skills": []}
    assert "# Review lessons" not in council_run.role_prompt(run.root, role, state, "body"), "no lessons, no section"
    folder = run.root / "docs" / "lessons"
    folder.mkdir(parents=True)
    note = "---\nid: lessons.{0}\nstatus: {1}\nsummary: {0} rule\nrepos:\n  - {2}\n---\n"
    (folder / "index.md").write_text("---\nsummary: index\n---\n")
    (folder / "handles.md").write_text(note.format("handles", "active", "api"))
    (folder / "web-only.md").write_text(note.format("web-only", "active", "web"))
    (folder / "old.md").write_text(note.format("old", "superseded", "api"))
    prompt = council_run.role_prompt(run.root, role, state, "body")
    assert "- docs/lessons/handles.md: handles rule" in prompt, prompt
    assert "web-only" not in prompt and "old rule" not in prompt and "index" not in prompt.split("# Review lessons")[1], prompt
    assert prompt.index("# Review lessons") < prompt.index("body"), "lessons come before the task"


def main() -> int:
    three = ["security-claude", "security-gpt", "security-grok"]
    low = "SEVERITY: low\n\napp.py:3 minor"
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)

        run, fake, reason = review(base, "clean", {"security-claude": low, "security-gpt": "SEVERITY: none",
                                                   "security-grok": low, "judge": "SEVERITY: low\n\n## Confirmed findings"}, three)
        assert reason == "", reason
        assert run.state["security"] == "low" and run.state["securityFlagged"] == []
        assert sorted(run.state["securityLabels"]) == ["A", "B", "C"] and sorted(run.state["securityLabels"].values()) == three
        for identifier in three:
            assert (run.directory / f"review-{identifier}.md").is_file(), identifier
        assert (run.directory / "security.md").read_text().startswith("SEVERITY: low"), "the judge summary is security.md"
        judged = fake.prompts["judge"]
        assert "# Security summary" in judged and "+eval(input())" in judged
        assert all(f"# Review {label}" in judged for label in "ABC")
        assert not any(identifier in judged for identifier in three), "the judge must not know who wrote which review"

        run, _, reason = review(base, "reviewer-high", {"security-claude": low, "security-gpt": "SEVERITY: high\n\nRCE",
                                                        "security-grok": low, "judge": "SEVERITY: medium"}, three)
        assert "security-gpt" in reason and "cannot overrule" in reason, "one reviewer's high blocks even when the judge says less"
        assert run.state["securityFlagged"] == ["security-gpt"] and run.state["security"] == "medium"

        _, _, reason = review(base, "judge-high", {"security-claude": low, "security-gpt": low, "security-grok": low,
                                                   "judge": "SEVERITY: high\n\ncombined"}, three)
        assert "summary says high" in reason, reason

        _, _, reason = review(base, "unreadable", {"security-claude": low, "security-gpt": "I think it is fine",
                                                   "security-grok": low, "judge": "SEVERITY: none"}, three)
        assert "security-gpt" in reason, "an unreadable review blocks like a high one"

        run, fake, reason = review(base, "single", {"security-claude": "SEVERITY: medium"}, ["security-claude"])
        assert reason == "" and run.state["security"] == "medium"
        assert "judge" not in fake.prompts and (run.directory / "security.md").is_file(), "one reviewer: no summary step"
        _, _, reason = review(base, "single-high", {"security-claude": "SEVERITY: high"}, ["security-claude"])
        assert reason == "security review blocks the change; see security.md"

        check_writer_history(base)
        check_review_lessons(base)
        check_risk_council(base)

    check_catalog_rules()
    used = {"input": 10_000, "cached": 30_000, "output": 2_000}
    calls = [{"finishedAt": "t", "usage": used}, {"finishedAt": "t", "usage": used}, {"startedAt": "t"}]
    assert council_run.run_tokens({"calls": calls}) == "84.0k tokens (20.0k new input, 60.0k cached, 4.0k output) in 2 calls", \
        "a call still working is not counted yet"
    calls.append({"finishedAt": "t", "usage": None})
    assert council_run.run_tokens({"calls": calls}).startswith("at least 84.0k tokens") and \
        council_run.run_tokens({"calls": calls}).endswith("in 3 calls; 1 did not report tokens"), "a missing report makes a lower bound"
    assert council_run.run_tokens({}) == "0 tokens (0 new input, 0 cached, 0 output) in 0 calls"
    council = {"approval": True}
    assert progress.security_roles({**council, "security": None}) == []
    assert progress.security_roles({**council, "security": "security"}) == ["security"], "catalogs older than the list"
    assert progress.plan("coding", {**council, "security": three})[-2:] == ["security", "security summary"]
    assert progress.plan("coding", {**council, "security": "security"})[-1] == "security"
    assert "security" not in progress.plan("coding", {**council, "security": []})
    assert progress.plan("risk", {"assessors": ["a", "b"], "judge": "j"}) == ["assess", "risk summary"]
    print("council test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
