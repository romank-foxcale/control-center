---
id: kit.rules.executable-processes
title: Executable processes
status: active
summary: Project builds, tests, packages and runs are Nix outputs; the CC's own checks run natively through ./cc on every OS, and a command x host table shows what needs the optional Nix extra.
verified_at: 2026-11-08
evidence:
  - flake.nix
  - nix/lib/default.nix
  - nix/workflows/default.nix
  - templates/project.nix
  - scripts/cc.py
  - scripts/checks.py
  - scripts/verify.py
relations:
  - docs/kit/architecture/control-center.md
  - docs/kit/rules/project-adapters.md
  - docs/kit/decisions/0001-nix-executable-knowledge.md
  - docs/kit/decisions/0033-native-core-nix-optional.md
---

# Executable processes

## Contract

| Knowledge | Flake output | Property |
|---|---|---|
| Buildable artifact | `packages` | Pure, cacheable, reproducible |
| Automatic verification | `checks` | Runs through `nix flake check` |
| Run/deploy/E2E | `apps` | Explicit runtime side effect |
| Developer environment | `devShells` | Tool versions are defined in code |
| Host/service | `nixosModules` | Declarative state and rollback |

## Invariants

- A cross-project workflow consumes derivation outputs; it never looks for artifacts at arbitrary paths.
- Changing a source input invalidates the dependent builds and checks.
- A command found during diagnosis does not count as knowledge until it is expressed in Nix.
- Networked, interactive and privileged actions are never disguised as pure checks.
- Markdown may explain the goal and the trade-off, but it is not a build runbook.

## Commands on each host

The CC core runs natively through uv on every OS; Nix is an optional extra (docs/kit/decisions/0033-native-core-nix-optional.md). `scripts/cc_test.py` and
`scripts/verify_test.py` assert the rows that differ.

| Command | Windows, macOS or Linux without Nix | Linux or macOS with Nix |
|---|---|---|
| `check` | The native check groups; the `nix extra` row says `not run` | The native groups, then `nix flake check` |
| `verify` | The native checks; every declared target or quality gate is `FAIL` (no runnable backend), so no stamp | The checks with `nix flake check`, then each Nix gate |
| `show` | The check groups | The check groups, then `nix flake show` |
| `build`, `run`, `relock`, `gc`, `eval` | Exit 1: "needs the optional Nix extra" | Run through Nix |
| `doctor` | uv, Python, git, Git Bash (Windows), agent CLIs; Nix is a note | The same, with the Nix version |
| Every other command | Native | Native |

- A `check` or `verify` stamp names the tree from the start of the run, and only when the tree is the same at the end.
- `doctor` warns about a CC on a Windows drive seen from WSL (`/mnt/c`): a rule hook there is slower than its timeout.
- The one test skipped on Windows outside the Nix extra is `store_gc_test.py`, because `gc` needs Nix.

## Example

A cross-project end-to-end test is a chain of outputs, not a script that finds its inputs on disk:

```text
project A package ─┐
                   ├→ dockerTools image → HTTP service → E2E app
project B package ─┘
```

Each project's adapter (`nix/projects/`) builds its package as a check. The workflow (`nix/workflows/`, `ccLib.mkWorkflow`) builds the image from those packages, which is also a check. Running Docker stays an app, because it needs an external daemon.
