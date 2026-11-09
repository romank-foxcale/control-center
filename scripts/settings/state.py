"""Read-only snapshot of a Control Center for ./cc settings: plans and runs for the header, roles, councils, repos."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


TERMINAL = {"done", "rejected", "verify-failed", "blocked-security", "failed"}


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_status(state: dict[str, Any]) -> str:
    stage = state.get("stage", "?")
    if stage in TERMINAL or stage == "awaiting-approval":
        return stage
    return stage if alive(state.get("pid")) else f"stalled ({stage})"


@dataclass
class Snapshot:
    root: Path
    name: str = "?"
    status: str = "not bootstrapped"
    plans: list[dict[str, str]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)
    roles: list[dict[str, Any]] = field(default_factory=list)
    councils: list[dict[str, Any]] = field(default_factory=list)
    repos: list[dict[str, Any]] = field(default_factory=list)
    tools: dict[str, bool] = field(default_factory=dict)
    trello: dict[str, str] = field(default_factory=dict)

    @property
    def projects(self) -> list[dict[str, Any]]:
        """Repos that are built and verified; reference repos are read-only guidance."""
        return [repo for repo in self.repos if repo["kind"] == "project"]

    @property
    def waiting(self) -> list[dict[str, Any]]:
        return [run for run in self.runs if run["status"] == "awaiting-approval"]

    @property
    def active_runs(self) -> list[dict[str, Any]]:
        return [run for run in self.runs if run["status"] not in TERMINAL and run["status"] != "awaiting-approval"]

    @property
    def problems(self) -> list[str]:
        found = [f"run {run['id']} {run['status']}" for run in self.runs if run["status"].startswith("stalled")]
        found += [f"run {run['id']} {run['status']}" for run in self.runs[:5] if run["status"] in TERMINAL - {"done", "rejected"}]
        found += [f"{repo['id']} {repo['status']}" for repo in self.repos if repo["status"] == "blocked"]
        found += [f"{tool} missing" for tool, ok in self.tools.items() if not ok]
        return found


def snapshot(root: Path) -> Snapshot:
    root = root.resolve()
    result = Snapshot(root=root, name=root.name)
    manifest = load_json(root / "control-center.json")
    if manifest:
        result.name = manifest.get("name", root.name)
        result.status = manifest.get("status", "?")
    layout = manifest.get("layout", {})
    worktrees_root = (root / layout.get("worktrees", "worktrees")).resolve()

    for lifecycle in ("active", "archived", "completed"):
        for path in sorted((root / "plans" / lifecycle).glob("*.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            title = text.splitlines()[0].lstrip("# ").split(": ", 1)[-1] if text else path.stem
            planning = re.search(r"^Planning status:\s+(\S+)", text, re.MULTILINE)
            result.plans.append(
                {"id": path.stem, "lifecycle": lifecycle, "planning": planning.group(1) if planning else "?", "title": title}
            )

    for path in worktrees_root.glob("*/.cc-runs/*/state.json"):
        state = load_json(path)
        if not state:
            continue
        result.runs.append(
            {
                "id": state.get("id", path.parent.name),
                "status": run_status(state),
                "started": state.get("startedAt", ""),
            }
        )
    result.runs.sort(key=lambda run: run["started"], reverse=True)

    for path in sorted((root / "catalog" / "agents").glob("*.json")):
        role = load_json(path)
        result.roles.append({"id": path.stem, **{key: role.get(key, "?") for key in ("provider", "model", "access")}})
    for path in sorted((root / "catalog" / "councils").glob("*.json")):
        council = load_json(path)
        result.councils.append(
            {
                "id": path.stem,
                "kind": council.get("kind", "?"),
                # A threads council has sceptics where the others have proposers.
                "proposers": ", ".join(council.get("proposers") or council.get("sceptics") or []),
                "judge": council.get("judge", "?"),
                "writer": council.get("writer", "?"),
                "approval": council.get("approval"),
                "maxRetries": council.get("maxRetries", "?"),
            }
        )
    for path in sorted((root / "catalog" / "repositories").glob("*.json")):
        repo = load_json(path)
        result.repos.append(
            {
                "id": path.stem,
                "kind": repo.get("kind", "project"),
                "branch": repo.get("defaultBranch", "?"),
                "remote": "" if repo.get("remote", "?") is None else repo.get("remote", "?"),  # none yet: ./cc repo new
                "status": repo.get("status", "?"),
                "targets": ",".join(repo.get("targets") or []) or "?",
                "stack": ",".join(repo.get("stack") or []) or "?",
                "role": repo.get("role", "?"),
            }
        )
    result.tools = {tool: shutil.which(tool) is not None for tool in ("nix", "git", "claude", "codex", "cursor-agent")}
    result.trello = trello(root)
    return result


def trello(root: Path) -> dict[str, str]:
    """The board settings, and whether credentials exist; the token itself is never read here."""
    settings = load_json(root / "catalog" / "integrations" / "trello.json")
    env = Path(os.environ.get("CC_TRELLO_ENV", Path.home() / ".config" / "cc" / "trello.env"))
    found = env.is_file() or bool(os.environ.get("TRELLO_TOKEN"))
    return {
        "board": settings.get("name", settings.get("board", "not connected")) if settings else "not connected",
        "active list": settings.get("lists", {}).get("active", "-") if settings else "-",
        "completed list": settings.get("lists", {}).get("completed", "-") if settings else "-",
        "credentials": f"found ({env})" if found else f"missing: put TRELLO_API_KEY and TRELLO_TOKEN in {env}",
    }
