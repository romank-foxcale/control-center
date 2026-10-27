---
id: kit.decision.0027-python-gate-venv-cache
title: Python host gates reuse a venv per dependency set, rebuilt at least weekly
status: accepted
summary: ccLib.mkPythonHostGate installs a repo's requirements into one venv per requirements files, interpreter and install step, kept in the host's cache folder and rebuilt when any of them changes, when it is older than 7 days, when a build did not finish, or on CC_GATE_FRESH=1. The sources are still copied fresh and pip freeze is still printed on every run.
verified_at: 2026-10-27
evidence:
  - nix/lib/default.nix
  - nix/lib/gate-venv.sh
  - flake.nix
  - templates/project.nix
  - .agents/skills/grill-cc-bootstrap/SKILL.md
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0006-target-platform-gates.md
  - docs/kit/rules/project-adapters.md
---

# Python host gates reuse a venv per dependency set, rebuilt at least weekly

## Context

A Python repo whose gate runs on a real host ([0006](0006-target-platform-gates.md)) installs its `requirements.txt` from PyPI, because Nix's Python packages are not what the repo ships with. A fresh venv on every run repeats that install. In one CC a gate for a repo with torch and transformers took more than 10 minutes for a one-file change, almost all of it installing; the tests on a cached venv ran in seconds. Each CC that solved this wrote its own cache in shell.

## Decision

- `ccLib.mkPythonHostGate { name, src, platform, requirements, install, test, ... }` is the kit's gate for Python repos that install their dependencies on the host: windows (native Python through WSL), linux and macos (a Nix interpreter).
- It keeps one venv keyed by the hash of the requirements files, the interpreter (Windows: its path and version; otherwise its store path) and the install step, outside the work directory: Windows `%LOCALAPPDATA%\control-center\<name>\gate-<hash>`, otherwise `${XDG_CACHE_HOME:-~/.cache}/control-center/<name>/gate-<hash>`.
- The venv is rebuilt when the key changes, when it is older than `maxAgeDays` (default 7), when an earlier build did not finish (no `.complete` marker), or when `CC_GATE_FRESH=1` is set. Building a new one removes the gate's other venvs.
- Unchanged from a fresh-install gate: the sources are copied to a new work directory on every run, and `pip freeze` is printed, so the log shows what the run tested.

## Options considered

- A fresh venv on every run: always current, but the install is paid on every change, including CC-only ones.
- Reuse until the requirements change, with no age limit: most requirements are lower bounds or unpinned, so a fresh install picks up new releases; a venv kept forever hides them.
- A lock file with exact versions: makes reuse exact, but the lock file belongs to the project repo, and many repos have none.
- Only a rule in the bootstrap skill: every CC would write its own copy of the cache logic.

## Consequences

- After a code-only change a gate starts testing in seconds.
- A new release of an unpinned dependency reaches the gate up to `maxAgeDays` late. Before a PR that changes dependencies, or after a long pause, run with `CC_GATE_FRESH=1`.
- A venv broken in place (a half-removed package) is not detected; `CC_GATE_FRESH=1` repairs it.
- Two CCs on one host with a repo of the same name share the cache folder; with different requirements they replace each other's venv.
- Disk: one venv per gate, several GB for a heavy stack.

## Revisit when

Project repos commonly ship lock files, or a dependency release breaks a repo between rebuilds and nobody notices for days.
