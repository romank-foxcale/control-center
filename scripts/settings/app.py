#!/usr/bin/env python3
"""./cc settings: the Control Center's settings panel (agents, councils, repos) with a status header.
Reads files; every change goes through ./cc. `./cc settings show` prints the same as text."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, OptionList, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option

sys.path.insert(0, str(Path(__file__).resolve().parent))
from state import Snapshot, snapshot  # noqa: E402
import hostos  # noqa: E402  (state.py puts scripts/ on the path)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "council"))
from progress import SPINNER  # noqa: E402  shared with the council runner

TABS = ("agents", "councils", "repos", "trello")


def available_models(root: Path) -> dict[str, list[str]]:
    """Models per logged-in provider, from the CC's own provider adapter."""
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "council" / "providers.py"), "models"], capture_output=True,
        encoding="utf-8", errors="replace",
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        # An older CC whose provider adapter cannot list models yet, or a broken one: say so.
        raise RuntimeError((result.stderr or result.stdout).strip()[-300:] or "providers.py models printed nothing") from None

FACES = {
    "calm": ("◉   ◉", " ─── "),
    "working": ("◔   ◔", " ─── "),
    "alert": ("◉   ◉", " ─o─ "),
    "grim": ("×   ×", " ─── "),
}
LINES = {
    "calm": [
        "{verified} repos green. Did you check, or did you hope?",
        "Nothing is broken. Yet.",
        "A clean worktree is a quiet conscience.",
        "No council is waiting. Neither should you.",
    ],
    "working": [
        "{active} council run(s) at work. Watching.",
        "Somewhere a model is thinking. Let it.",
        "The writer writes. The tests will judge.",
    ],
    "alert": [
        "A council awaits your word. It will not decide for you.",
        "{waiting} verdict(s) on your desk. Read them.",
        "The judge has spoken. Now you.",
    ],
    "grim": [
        "Something stopped: {problem}.",
        "Red is information. Ignore it at your price.",
        "A stalled run does not finish itself.",
    ],
}


def mood(state: Snapshot) -> str:
    if state.problems:
        return "grim"
    if state.waiting:
        return "alert"
    if state.active_runs:
        return "working"
    return "calm"


def mascot(face: str) -> str:
    eyes, mouth = FACES[face]
    return "\n".join(
        [
            "   ▗▄▄▄▄▄▄▄▖",
            "  ▐██▀▀▀▀▀██▌",
            f"  ▐█  {eyes} █▌",
            f"  ▐█  {mouth} █▌",
            "   ▝▀█▄▄▄▄█▀▘",
            "     ▝▘   ▝▘",
        ]
    )


class Ask(ModalScreen[list[str] | None]):
    """A small form: one input per field, or just a confirmation when there are none."""

    def __init__(self, title: str, fields: list[tuple[str, str]]):
        super().__init__()
        self.title_text = title
        self.fields = fields

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.title_text)
            for placeholder, value in self.fields:
                yield Input(value=value, placeholder=placeholder)
            with Horizontal():
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss([field.value.strip() for field in self.query(Input)] if event.button.id == "ok" else None)

    def on_input_submitted(self) -> None:
        self.dismiss([field.value.strip() for field in self.query(Input)])


class ModelPicker(ModalScreen[tuple[str, str] | None]):
    """Every model of every logged-in provider, grouped under a provider header."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, role: str, current: tuple[str, str], models: dict[str, list[str]]):
        super().__init__()
        self.role = role
        self.current = current
        self.models = models

    def compose(self) -> ComposeResult:
        options: list[Option] = []
        highlight = None
        for provider, names in self.models.items():
            if not names:
                continue
            options.append(Option(Text(provider, style="bold reverse"), disabled=True))
            for name in names:
                if (provider, name) == self.current:
                    highlight = len(options)
                options.append(Option(f"  {name}", id=f"{provider}\t{name}"))
        with Vertical(id="dialog"):
            yield Label(f"[b]{self.role}[/b] uses {self.current[0]} {self.current[1]} · Enter choose · Esc cancel")
            if options:
                picker = OptionList(*options, id="models")
                picker.highlighted = highlight
                yield picker
            else:
                yield Label("No provider is logged in: run claude auth login, codex login or cursor-agent login.")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        provider, name = event.option.id.split("\t", 1)
        self.dismiss((provider, name))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ChoicePicker(ModalScreen[str | None]):
    """Pick one of a few values (boards, lists) instead of typing it."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, choices: list[tuple[str, str]]):
        super().__init__()
        self.title_text = title
        self.choices = choices

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"{self.title_text} · Enter choose · Esc cancel")
            yield OptionList(*(Option(label, id=value) for value, label in self.choices), id="models")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class Settings(App):
    TITLE = "Control Center settings"
    CSS = """
    #top { height: 8; }
    #mascot { width: 18; color: $text; }
    #summary { padding: 1 2; }
    #dialog { width: 70; height: auto; border: heavy $accent; padding: 1 2; background: $surface; }
    Ask, ModelPicker, ChoicePicker { align: center middle; }
    #models { height: auto; max-height: 20; }
    DataTable { height: 1fr; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("e", "edit", "Edit selected"),
        Binding("a", "add", "Add repo"),
        Binding("d", "remove", "Remove repo"),
        Binding("left", "tab(-1)", "Prev tab", priority=True),
        Binding("right", "tab(1)", "Next tab", priority=True),
    ]

    def __init__(self, root: Path, models=available_models):
        super().__init__()
        self.root = root
        self.models = models
        self.state = snapshot(root)
        self.line = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static(id="mascot")
            yield Static(id="summary")
        with TabbedContent(id="tabs"):
            for tab, columns in (
                ("Agents", ("role", "provider", "model", "access")),
                ("Councils", ("council", "kind", "proposers", "judge", "writer", "approval", "retries")),
                ("Repos", ("repo", "kind", "main branch", "status", "targets", "stack", "remote")),
                ("Trello", ("setting", "value")),
            ):
                with TabPane(tab, id=tab.lower()):
                    table = DataTable(id=f"t-{tab.lower()}", cursor_type="row", zebra_stripes=True)
                    table.add_columns(*columns)
                    yield table
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = str(self.root)
        self.render_state()
        # Every second, so the header's run spinner moves.
        self.set_interval(1, self.action_refresh)

    def action_tab(self, step: int) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.active = TABS[(TABS.index(tabs.active) + step) % len(TABS)]

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        # Keep the arrow keys on the rows of whichever tab is showing.
        self.query_one(f"#t-{event.pane.id}", DataTable).focus()

    # Rendering -----------------------------------------------------------------------

    def action_refresh(self) -> None:
        self.state = snapshot(self.root)
        self.line = int(time.time() // 20)
        try:
            self.render_state()
        except NoMatches:
            # The 1 s timer can fire while the app shuts down, after the panel's widgets are removed (CI, 2026-11-08).
            pass

    def render_state(self) -> None:
        state = self.state
        face = mood(state)
        options = LINES[face]
        text = options[self.line % len(options)].format(
            verified=sum(repo["status"] == "verified" for repo in state.projects),
            active=len(state.active_runs),
            waiting=len(state.waiting),
            problem=(state.problems or ["?"])[0],
        )
        self.query_one("#mascot", Static).update(mascot(face))
        active_plans = [plan for plan in state.plans if plan["lifecycle"] == "active"]
        tools = "  ".join(f"{tool} {'✓' if ok else '✗'}" for tool, ok in state.tools.items())
        verified = sum(repo["status"] == "verified" for repo in state.projects)
        spinner = f"{SPINNER[int(time.time()) % len(SPINNER)]} " if state.active_runs else ""
        summary = (
            f"[b]{escape(state.name)}[/b] · {escape(state.status)}        {tools}\n"
            f"{len(active_plans)} plan(s) active · {spinner}{len(state.active_runs)} run(s) working · "
            f"[b]{len(state.waiting)} waiting for you[/b] · {verified}/{len(state.projects)} repos verified\n\n"
            f"[i]\"{escape(text)}\"[/i]"
        )
        if state.problems:
            summary += "\n[b red]" + escape(" · ".join(state.problems[:3])) + "[/b red]"
        self.query_one("#summary", Static).update(summary)

        self.fill("agents", [(r["id"], r["provider"], r["model"], r["access"]) for r in state.roles])
        self.fill(
            "councils",
            [(c["id"], c["kind"], c["proposers"], c["judge"], c["writer"], "on" if c["approval"] else "off", str(c["maxRetries"])) for c in state.councils],
        )
        self.fill("repos", [(r["id"], r["kind"], r["branch"], self.paint(r["status"]), r["targets"], r["stack"], r["remote"]) for r in state.repos])
        self.fill("trello", list(state.trello.items()))

    @staticmethod
    def paint(status: str) -> Text:
        if status == "awaiting-approval":
            return Text(status, style="bold yellow")
        if status.startswith("stalled") or status in {"failed", "verify-failed", "blocked-security", "blocked"}:
            return Text(status, style="bold red")
        if status in {"done", "verified"}:
            return Text(status, style="green")
        return Text(status)

    def fill(self, tab: str, rows: list[tuple]) -> None:
        table = self.query_one(f"#t-{tab}", DataTable)
        cursor = table.cursor_row
        table.clear()
        for row in rows:
            table.add_row(*(cell if isinstance(cell, Text) else Text(str(cell)) for cell in row), key=str(row[0]))
        if rows:
            table.move_cursor(row=min(cursor, len(rows) - 1))

    def selected(self, tab: str) -> str | None:
        table = self.query_one(f"#t-{tab}", DataTable)
        if not table.row_count:
            return None
        return str(table.get_row_at(table.cursor_row)[0].plain)

    # Actions: every change goes through ./cc ------------------------------------------

    def run_cc(self, arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        """./cc with these arguments, started through the interpreter: the extensionless shim cannot run on Windows."""
        return subprocess.run([*hostos.cc_command(self.root), *arguments], cwd=self.root, capture_output=True,
                              encoding="utf-8", errors="replace")

    def cc(self, *arguments: str) -> None:
        result = self.run_cc(arguments)
        output = (result.stdout + result.stderr).strip() or "done"
        self.notify(output[-400:], severity="information" if result.returncode == 0 else "error", timeout=8)
        self.action_refresh()

    def cc_later(self, *arguments: str) -> None:
        """./cc in a worker thread, for commands that ask a remote (git ls-remote) and may take a moment."""
        self.notify("asking the remote...", timeout=2)
        self.run_worker(lambda: self._cc_thread(arguments), thread=True)

    def _cc_thread(self, arguments: tuple[str, ...]) -> None:
        result = self.run_cc(arguments)
        output = (result.stdout + result.stderr).strip() or "done"
        self.call_from_thread(self.notify, output[-400:], severity="information" if result.returncode == 0 else "error", timeout=8)
        self.call_from_thread(self.action_refresh)

    # Repos: paste a link to add, pick what to change, confirm to remove ---------------

    def on_repos_tab(self) -> bool:
        if self.query_one("#tabs", TabbedContent).active == "repos":
            return True
        self.notify("switch to the Repos tab to add or remove repos", severity="warning")
        return False

    def action_add(self) -> None:
        if not self.on_repos_tab():
            return

        def details(answer: list[str] | None) -> None:
            if not answer or not answer[0]:
                return
            url, branch = answer
            extra = ("--branch", branch) if branch else ()

            def kind_chosen(kind: str | None) -> None:
                if kind == "reference":
                    self.cc_later("repo", "add", url, "--kind", "reference", *extra)
                elif kind == "project":
                    self.push_screen(Ask("Project repo: target platforms and stack (comma-separated)",
                        [("windows, linux, macos", ""), ("languages, e.g. java, typescript", "")]),
                        lambda more: more and self.cc_later("repo", "add", url, *extra,
                            "--targets", *more[0].replace(",", " ").split(), "--stack", *more[1].replace(",", " ").split()))

            self.push_screen(ChoicePicker("What is this repo?", [
                ("project", "project · we build, verify and change it"),
                ("reference", "reference · read-only guidance (PoC, demo), never committed to"),
            ]), kind_chosen)

        self.push_screen(Ask("Add a repo: paste its link; leave the branch empty to read it from the remote",
            [("https://github.com/org/repo", ""), ("main branch (empty = detect)", "")]), details)

    def action_remove(self) -> None:
        if not self.on_repos_tab():
            return
        item = self.selected("repos")
        if item:
            self.push_screen(Ask(f"Remove {item} from this CC's catalog? Its clone in repos/ is kept.", []),
                lambda answer: answer is not None and self.cc("repo", "remove", item))

    def edit_repo(self, item: str) -> None:
        repo = next(repo for repo in self.state.repos if repo["id"] == item)

        def chosen(setting: str | None) -> None:
            if setting == "branch":
                self.push_screen(Ask(f"{item}: main branch new feature worktrees start from", [("branch", repo["branch"])]),
                    lambda answer: answer and answer[0] != repo["branch"] and self.cc_later("repo", "set-branch", item, answer[0]))
            elif setting == "remote":
                self.push_screen(Ask(f"{item}: Git URL", [("https://github.com/org/repo", repo["remote"])]),
                    lambda answer: answer and answer[0] != repo["remote"] and self.cc("repo", "set-remote", item, answer[0]))
            elif setting == "targets":
                self.push_screen(Ask(f"{item}: target platforms (comma-separated)", [("windows, linux, macos", repo["targets"])]),
                    lambda answer: answer and self.cc("repo", "set-targets", item, *answer[0].replace(",", " ").split()))
            elif setting == "stack":
                self.push_screen(Ask(f"{item}: languages (comma-separated)", [("languages", repo["stack"])]),
                    lambda answer: answer and self.cc("repo", "set-stack", item, *answer[0].replace(",", " ").split()))
            elif setting == "kind":
                self.push_screen(ChoicePicker(f"{item} is a", [("project", "project"), ("reference", "reference (read-only)")]),
                    lambda kind: kind and kind != repo["kind"] and self.cc("repo", "set-kind", item, kind))

        self.push_screen(ChoicePicker(f"Change {item}", [
            ("branch", f"main branch · {repo['branch']}"),
            ("remote", f"link · {repo['remote']}"),
            ("targets", f"target platforms · {repo['targets']}"),
            ("stack", f"stack · {repo['stack']}"),
            ("kind", f"kind · {repo['kind']}"),
        ]), chosen)

    def pick_model(self, role: str, current: tuple[str, str]) -> None:
        """Runs in a worker thread: listing Codex models takes a moment."""
        try:
            models = self.models(self.root)
        except RuntimeError as error:
            self.call_from_thread(self.notify, f"cannot list models: {error}", severity="error", timeout=10)
            return
        self.call_from_thread(
            self.push_screen,
            ModelPicker(role, current, models),
            lambda choice: choice and self.cc("council", "set-role", role, "--provider", choice[0], "--model", choice[1]),
        )

    def output(self, *arguments: str) -> list[str] | None:
        """Run ./cc in a worker thread; on failure tell the user and return None."""
        result = self.run_cc(arguments)
        if result.returncode != 0:
            self.call_from_thread(self.notify, (result.stderr or result.stdout).strip()[-300:], severity="error", timeout=10)
            return None
        return [line for line in result.stdout.splitlines() if line.strip()]

    def connect_trello(self) -> None:
        """Board, then the list for active plans, then the list for completed ones; connect runs ./cc trello."""
        boards = self.output("trello", "boards")
        if not boards:
            return
        choices = [tuple(line.split("\t", 1)) for line in boards]

        def board_chosen(board: str | None) -> None:
            if board:
                self.run_worker(lambda: self.pick_lists(board), thread=True)

        self.call_from_thread(self.push_screen, ChoicePicker("Trello board for this CC", choices), board_chosen)

    def pick_lists(self, board: str) -> None:
        lists = self.output("trello", "lists", "--board", board)
        if not lists:
            return
        choices = [(name, name) for name in lists]

        def active_chosen(active: str | None) -> None:
            if active:
                self.push_screen(ChoicePicker("List for completed plans", choices), lambda done: done and self.cc(
                    "trello", "connect", "--board", board, "--active-list", active, "--completed-list", done))

        self.call_from_thread(self.push_screen, ChoicePicker("List for active plans", choices), active_chosen)

    def action_edit(self) -> None:
        tab = self.query_one("#tabs", TabbedContent).active
        item = self.selected(tab)
        if not item:
            return
        if tab == "agents":
            role = next(role for role in self.state.roles if role["id"] == item)
            self.notify("loading models of the logged-in providers...", timeout=2)
            self.run_worker(lambda: self.pick_model(item, (role["provider"], role["model"])), thread=True)
        elif tab == "councils":
            council = next(council for council in self.state.councils if council["id"] == item)
            if council["kind"] == "threads":
                self.notify("the threads council has no approval or retries; change its roles' models in the agents tab")
                return
            self.push_screen(Ask(f"Council {item}: approval pause (on/off) and max retries (0-5)",
                [("on or off", "on" if council["approval"] else "off"), ("max retries", str(council["maxRetries"]))]),
                lambda answer: answer and self.cc("council", "set", item, "--approval", answer[0], "--max-retries", answer[1]))
        elif tab == "trello":
            self.notify("loading your Trello boards...", timeout=2)
            self.run_worker(self.connect_trello, thread=True)
        elif tab == "repos":
            self.edit_repo(item)
        else:
            self.notify("nothing to edit here", severity="warning")


def show(state: Snapshot) -> str:
    """The settings as text, for sessions (the cc-settings skill) and terminals without the panel."""
    tools = "  ".join(f"{tool} {'ok' if ok else 'MISSING'}" for tool, ok in state.tools.items())
    active_plans = sum(plan["lifecycle"] == "active" for plan in state.plans)
    verified = sum(repo["status"] == "verified" for repo in state.projects)
    lines = [
        f"{state.name} · {state.status} · {tools}",
        f"{active_plans} plan(s) active · {len(state.active_runs)} run(s) working · {len(state.waiting)} waiting for you · "
        f"{verified}/{len(state.projects)} repos verified",
        "",
        "AGENTS (role · provider · model · access)",
        *(f"  {r['id']:<22} {r['provider']:<7} {r['model']:<26} {r['access']}" for r in state.roles),
        "",
        "COUNCILS (council · approval · max retries · proposers → judge → writer)",
        *(f"  {c['id']:<9} approval {'on ' if c['approval'] else 'off'}  retries {c['maxRetries']}  {c['proposers']} → {c['judge']} → {c['writer']}"
          for c in state.councils),
        "",
        "TRELLO",
        *(f"  {name:<15} {value}" for name, value in state.trello.items()),
        "",
        "REPOS (repo · kind · main branch · status · targets · stack · remote)",
        *(f"  {r['id']:<20} {r['kind']:<9} {r['branch']:<10} {r['status']:<11} {r['targets']:<15} {r['stack']:<12} {r['remote']}"
          for r in state.repos),
    ]
    return "\n".join(lines)


def main() -> int:
    arguments = sys.argv[1:]
    root = Path(arguments[0] if arguments else ".").resolve()
    if arguments[1:2] == ["show"]:
        print(show(snapshot(root)))
        return 0
    Settings(root).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
