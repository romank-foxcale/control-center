#!/usr/bin/env python3
"""Headless smoke test for ./cc settings: render a fixture CC and check the panel, show and run progress."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import Ask, ChoicePicker, ModelPicker, Settings, show  # noqa: E402
from state import snapshot  # noqa: E402
import progress  # noqa: E402  (app.py puts scripts/council on the path)
from textual.widgets import DataTable, Input, OptionList, Static  # noqa: E402


def write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def fixture(base: Path) -> Path:
    root = base / "x_CC"
    write(root / "control-center.json", {"name": "x_CC", "status": "ready", "layout": {"projectsRoot": ".", "worktrees": "worktrees"}})
    write(root / "catalog/agents/judge.json", {"provider": "claude", "model": "opus", "access": "read-only"})
    write(root / "catalog/councils/coding.json", {"kind": "coding", "proposers": ["a", "b"], "judge": "judge", "writer": "w", "approval": True, "maxRetries": 2})
    write(root / "catalog/repositories/app.json", {"status": "verified", "targets": ["windows"], "stack": ["csharp"], "role": "client",
                                                    "kind": "project", "defaultBranch": "develop", "remote": "https://github.com/o/app"})
    # A reference repo is never verified, so it must not count against "repos verified".
    write(root / "catalog/repositories/demo.json", {"status": "discovered", "targets": [], "stack": [], "kind": "reference",
                                                     "defaultBranch": "main", "remote": "https://github.com/o/demo"})
    write(root / "plans/active/ship-it.md", "# ship-it: Ship [the] thing\n\nLifecycle: active\nPlanning status: accepted\n")
    runs = root / "worktrees/feat/.cc-runs"
    write(runs / "r1/state.json", {"id": "r1", "council": "coding", "feature": "feat", "repository": "app", "stage": "awaiting-approval",
                                   "labels": {"A": "proposer-gpt", "B": "proposer-claude"}, "startedAt": "2026-01-01T10:00:00"})
    # A process id that cannot exist: the run looks alive in its file but nobody is advancing it.
    write(runs / "r2/state.json", {"id": "r2", "council": "debug", "feature": "feat", "repository": "app", "stage": "writing",
                                   "pid": 2_000_000_000, "startedAt": "2026-01-01T11:00:00",
                                   "plan": ["reproduce", "propose", "judge", "approval", "write test", "red check", "fix", "verify", "security"],
                                   "phase": "fix", "phaseStartedAt": "2026-01-01T11:05:00",
                                   "calls": [{"step": "proposal-a", "provider": "codex", "model": "gpt", "startedAt": "2026-01-01T11:01:00",
                                              "finishedAt": "2026-01-01T11:02:00"},
                                             {"step": "write-0", "provider": "claude", "model": "opus", "startedAt": "2026-01-01T11:05:01"}]})
    return root


async def check(root: Path) -> None:
    app = Settings(root, models=lambda root: {"claude": ["opus", "sonnet"], "codex": []})
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        face = str(app.query_one("#mascot", Static).render())
        summary = str(app.query_one("#summary", Static).render())
        assert "×   ×" in face, f"a stalled run must make the mascot grim:\n{face}"
        assert "1 waiting for you" in summary and "1/1 repos verified" in summary, summary
        assert "stalled" in summary, summary
        assert [pane.id for pane in app.query("TabPane")] == ["agents", "councils", "repos", "trello"], "settings only"
        for table, rows in (("#t-agents", 1), ("#t-councils", 1), ("#t-repos", 2)):
            assert app.query_one(table, DataTable).row_count == rows, table

        # Navigation: the rows have focus from the start, and the arrow keys switch tabs.
        agents = app.query_one("#t-agents", DataTable)
        assert app.focused is agents, f"the Agents table must have focus, not {app.focused}"
        await pilot.press("right")
        await pilot.pause()
        assert app.query_one("#tabs").active == "councils" and app.focused is app.query_one("#t-councils"), app.focused
        await pilot.press("left")
        await pilot.pause()

        # Model picker: only logged-in providers, grouped under a header, current model preselected.
        await pilot.press("e")
        for _ in range(20):
            await pilot.pause(0.05)
            if isinstance(app.screen, ModelPicker):
                break
        assert isinstance(app.screen, ModelPicker), f"e on Agents must open the model picker, got {app.screen}"
        options = app.screen.query_one("#models", OptionList)
        prompts = [str(options.get_option_at_index(i).prompt).strip() for i in range(options.option_count)]
        assert prompts == ["claude", "opus", "sonnet"], prompts
        assert options.get_option_at_index(0).disabled, "provider headers are not selectable"
        assert options.highlighted == 1, "the role's current model is preselected"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ModelPicker)

        # Every change runs the CC's CLI through the interpreter: Windows cannot start the extensionless shim.
        write(root / "scripts" / "cc.py", "import sys\nsys.stdout.reconfigure(encoding='utf-8')\nprint('ran', *sys.argv[1:], '✓')\n")
        ran = app.run_cc(("repo", "list"))
        assert ran.returncode == 0 and ran.stdout.strip() == "ran repo list ✓", ran

        # Trello: the board and list mapping are picked, then connected through ./cc trello connect.
        trello = dict((row[0].plain, row[1].plain) for row in (app.query_one("#t-trello", DataTable).get_row_at(i) for i in range(4)))
        assert trello["board"] == "not connected" and trello["credentials"].startswith("missing"), trello
        sent: list[tuple[str, ...]] = []
        app.output = lambda *arguments: ["Bd1\tPV board"] if arguments[1] == "boards" else ["To do", "In progress", "Done"]
        app.cc = lambda *arguments: sent.append(arguments)
        for _ in range(3):
            await pilot.press("right")
        await pilot.pause()
        assert app.query_one("#tabs").active == "trello"
        await pilot.press("e")
        for choice in (None, "In progress", "Done"):  # board (first option), active list, completed list
            for _ in range(20):
                await pilot.pause(0.05)
                if isinstance(app.screen, ChoicePicker):
                    break
            assert isinstance(app.screen, ChoicePicker), f"expected a picker for {choice or 'the board'}"
            picker = app.screen.query_one("#models", OptionList)
            if choice:
                picker.highlighted = [str(picker.get_option_at_index(i).prompt) for i in range(picker.option_count)].index(choice)
            await pilot.press("enter")
            await pilot.pause()
        assert sent == [("trello", "connect", "--board", "Bd1", "--active-list", "In progress", "--completed-list", "Done")], sent

        # Repos: add by pasting a link, edit the main branch, remove after confirmation; all through ./cc repo.
        sent.clear()
        app.cc_later = lambda *arguments: sent.append(arguments)
        await pilot.press("left")
        await pilot.pause()
        assert app.query_one("#tabs").active == "repos"
        repos = app.query_one("#t-repos", DataTable)
        assert [cell.plain for cell in repos.get_row_at(0)][:3] == ["app", "project", "develop"], "kind and main branch are shown"

        async def screen(kind: type) -> None:
            for _ in range(20):
                await pilot.pause(0.05)
                if isinstance(app.screen, kind):
                    return
            raise AssertionError(f"expected {kind.__name__}, got {app.screen}")

        await pilot.press("a")
        await screen(Ask)
        app.screen.query(Input).first().value = "https://github.com/o/new-thing"
        await pilot.click("#ok")
        await screen(ChoicePicker)
        app.screen.query_one("#models", OptionList).highlighted = 1  # reference
        await pilot.press("enter")
        await pilot.pause()
        assert sent == [("repo", "add", "https://github.com/o/new-thing", "--kind", "reference")], sent

        sent.clear()
        await pilot.press("e")
        await screen(ChoicePicker)
        await pilot.press("enter")  # main branch: the first choice
        await screen(Ask)
        app.screen.query(Input).first().value = "main"
        await pilot.click("#ok")
        await pilot.pause()
        assert sent == [("repo", "set-branch", "app", "main")], sent

        sent.clear()
        await pilot.press("d")
        await screen(Ask)
        await pilot.click("#ok")
        await pilot.pause()
        assert sent == [("repo", "remove", "app")], sent

        # Shutdown removes the panel's widgets while the 1 s refresh timer can still fire; that refresh must do nothing.
        await app.query_one("#top").remove()
        app.action_refresh()

    text = show(snapshot(root))
    for expected in ("x_CC · ready", "1 waiting for you", "AGENTS", "judge", "COUNCILS", "approval on", "REPOS", "csharp", "develop", "reference", "TRELLO", "not connected"):
        assert expected in text, f"show must contain {expected!r}:\n{text}"

    # Run progress, as ./cc council status prints it.
    r2 = json.loads((root / "worktrees/feat/.cc-runs/r2/state.json").read_text())
    assert progress.bar(r2) == "▰▰▰▰▰▰▱▱▱ 6/9", progress.bar(r2)
    assert progress.agents(r2) == "claude opus …", f"only the current step's agents, still working: {progress.agents(r2)}"
    assert progress.elapsed(r2).startswith("step ") and "total" in progress.elapsed(r2)
    assert progress.elapsed({**r2, "finishedAt": "2026-01-01T11:10:00"}) == "total 10:00", "a finished run's clock stops"


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        # Independent of the machine: no Trello credentials unless the test creates them.
        os.environ["CC_TRELLO_ENV"] = str(Path(directory) / "no-trello.env")
        os.environ.pop("TRELLO_TOKEN", None)
        root = fixture(Path(directory))
        asyncio.run(check(root))
        # A repo made with ./cc repo new has remote null: the panel gets "", never None or "None", and the
        # set-remote prompt then starts empty (docs/kit/decisions/0034-new-project-cc.md).
        write(root / "catalog/repositories/fresh.json", {"status": "discovered", "targets": ["linux"], "stack": ["python"],
                                                          "kind": "project", "defaultBranch": "main", "remote": None})
        fresh = next(repo for repo in snapshot(root).repos if repo["id"] == "fresh")
        assert fresh["remote"] == "", fresh
        text = show(snapshot(root))
        assert "None" not in text and "none (local)" in text, text
    print("settings smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
