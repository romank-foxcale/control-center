#!/usr/bin/env python3
"""The native check runner behind ./cc check, on Windows, macOS and Linux without Nix (plan native-core, W2).

The registry holds the same check groups and scripts as the checks.* outputs of flake.nix, plus `core` for the CLI
itself; checks_test.py fails when the two drift apart. Every group runs its scripts in order with this interpreter,
in a fresh temporary HOME, the groups in parallel. When Nix is installed, `nix flake check` runs as an optional last
row ("nix extra"); it also covers the Nix-only checks (contract-check, gate-venv) and the adapters' own checks.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hostos import resolve_exe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ROOT_ARGUMENT = "<root>"  # replaced by the CC root in a step's arguments
TAIL = 30  # lines of output shown for a failed row
NO_NIX = "not run (optional, no Nix)"


@dataclass
class Group:
    """One check group: its steps run in order and the first failing step fails the group.

    A step is a script path relative to the CC root followed by its arguments. cwd is relative to the CC root.
    """

    name: str
    steps: list[tuple[str, ...]]
    cwd: str = "."


@dataclass
class Result:
    name: str
    status: str  # "pass", "FAIL" or a "not run ..." note
    seconds: float
    output: str = field(default="", repr=False)

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"


def registry(root: Path = ROOT) -> list[Group]:
    """The check groups of flake.nix (minus the Nix-only contract-check and gate-venv) and `core`."""
    groups = [
        Group("knowledge", [("scripts/validate-knowledge.py", ROOT_ARGUMENT), ("scripts/validate_knowledge_test.py",)]),
        Group("agent-configs", [("scripts/agent-configs.py", ROOT_ARGUMENT, "check"), ("scripts/agent_configs_test.py",)]),
        Group("council", [("scripts/council/providers_test.py",), ("scripts/council/council_test.py",)],
              cwd="scripts/council"),
        Group("rule-hooks", [("scripts/rule_hooks_test.py",)]),
        Group("trello", [("scripts/trello_test.py",)]),
        Group("events", [("scripts/events_test.py",), ("scripts/verify_test.py",)]),
        Group("repositories", [(f"scripts/{name}_test.py",) for name in (
            "repositories", "migrate_layout", "validate_control_center", "disk", "store_gc", "backup", "backup_where",
            "data", "relock", "plans")]),
        Group("prs", [("scripts/prs_test.py",), ("scripts/threads_test.py",), ("scripts/lessons_test.py",)]),
        Group("eval", [("scripts/eval/eval_test.py",)]),
        Group("settings-smoke", [("scripts/settings/smoke.py",)]),
        Group("core", [("scripts/hostos_test.py",), ("scripts/cc_test.py",), ("scripts/checks_test.py",)]),
    ]
    # Like flake.nix, only a materialized CC (one with control-center.json) is validated. Without Nix the adapters
    # cannot be evaluated, so no adapter targets file is passed: the catalog is checked as by ./cc bootstrap validate,
    # and the nix extra cross-checks each adapted repo's targets against its adapter. An empty targets file would
    # instead fail every adapted repo for gates it does declare.
    if (root / "control-center.json").is_file():
        groups.append(Group("bootstrap", [("scripts/validate-control-center.py", ROOT_ARGUMENT)]))
    return groups


def run_group(group: Group, root: Path = ROOT) -> Result:
    started = time.monotonic()
    output: list[str] = []
    status = "pass"
    with tempfile.TemporaryDirectory(prefix=f"cc-check-{group.name}-") as home:
        # A fresh HOME, as the Nix sandbox gives: no user git config, credentials or CC state leak into a test.
        environment = {**os.environ, "HOME": home, "USERPROFILE": home, "PYTHONUTF8": "1"}
        environment.pop("CC_FEATURE", None)
        for script, *arguments in group.steps:
            argv = [sys.executable, "-B", str(root / script),
                    *(str(root) if argument == ROOT_ARGUMENT else argument for argument in arguments)]
            try:
                completed = subprocess.run(argv, cwd=root / group.cwd, env=environment, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                           encoding="utf-8", errors="replace")
            except OSError as error:
                output.append(f"$ {script}: {error}\n")
                status = "FAIL"
                break
            output.append(f"$ {script} {' '.join(arguments)}\n{completed.stdout}")
            if completed.returncode != 0:
                output.append(f"{script} exited with {completed.returncode}\n")
                status = "FAIL"
                break
    return Result(group.name, status, time.monotonic() - started, "".join(output))


def nix_extra(arguments: list[str], root: Path = ROOT) -> Result:
    """nix flake check, as ./cc check ran it before the native runner; a note, never a failure, without Nix."""
    started = time.monotonic()
    nix = resolve_exe("nix")
    if nix is None:
        return Result("nix extra", NO_NIX, 0.0)
    import cc  # the CLI's own lock and feature-override helpers

    try:
        cc.lock_flake(nix)
        overrides = cc.feature_overrides()
    except subprocess.CalledProcessError as error:
        return Result("nix extra", "FAIL", time.monotonic() - started, f"{error}\n")
    completed = subprocess.run([nix, "flake", "check", *overrides, str(root), "--keep-going", *arguments],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace")
    return Result("nix extra", "pass" if completed.returncode == 0 else "FAIL", time.monotonic() - started,
                  completed.stdout)


def run(groups: list[Group], root: Path = ROOT,
        extra: Callable[[], Result] | None = None) -> tuple[int, list[Result]]:
    """Run every group (and the extra, if any) in parallel; print one row each, in registry order, extra last."""
    with ThreadPoolExecutor(max_workers=len(groups) + 1) as pool:
        futures = [pool.submit(run_group, group, root) for group in groups]
        extra_future = pool.submit(extra) if extra else None
        results = [future.result() for future in futures]
        if extra_future:
            results.append(extra_future.result())
    width = max(len(result.name) for result in results)
    for result in results:
        print(f"{result.name:<{width}}  {result.status:<4}  {result.seconds:6.1f}s", flush=True)
    for result in results:
        if result.failed:
            print(f"\n--- {result.name}: last {TAIL} lines ---")
            print("\n".join(result.output.rstrip("\n").splitlines()[-TAIL:]))
    return (1 if any(result.failed for result in results) else 0), results


def main(arguments: list[str], root: Path = ROOT) -> int:
    """./cc check: every native group, then the nix extra with the arguments the native runner does not take."""
    if arguments and resolve_exe("nix") is None:
        print(f"note: arguments for nix flake check ignored, Nix is absent: {' '.join(arguments)}", file=sys.stderr)
    status, _ = run(registry(root), root, lambda: nix_extra(arguments, root))
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
