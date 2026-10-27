---
name: grill-cc-bootstrap
description: Evidence-first interview that creates or migrates a Control Center and onboards its repositories (catalog entry, Nix adapter, a gate per platform, verify). Use to set up, continue setting up or migrate a CC, when its status is discovery or adapting, and when a project repo is added to a ready CC.
---

# Grill CC bootstrap

First read `../_shared/grilling-core.md` in full, then `BOOTSTRAP.md`. Apply both contracts together.

## New CC

1. Determine `template_root` and `target_root` as described in `BOOTSTRAP.md`. If the CC was created with `cc-kit new`, steps 1–2 are already done.
2. Run the materialization; continue from `target_root`.
3. Record the name, status and topology in `control-center.json`.
4. Collect the use cases, boundaries and permissions.
5. Build the repository inventory. Do not write adapters until names, roles and relationships are agreed.
6. Onboard one repo at a time: `./cc repo add --targets ... --stack ...` → inspect → flake input/adapter → package/check, static checks, one gate per target and, where feasible, `quality.mutation` → `./cc verify <id> --quality` → `./cc repo set-status <id> verified`.
7. Only then assemble workflows and benchmarks. Every contract between two repos (an API spec and the client or types generated from it, a shared schema) gets a contract check from `templates/workflow.nix`.
8. Replace the template README and project map with real data; remove the placeholders. Keep `GUIDE.md`, `install/`, `CLAUDE.md` and `.claude/skills`: they are the entry point for people and AI tools.

## Migration

1. Do not change any files before a read-only inventory.
2. Find projects, workflows, build/test/deploy, knowledge, secrets boundaries and runtime integrations.
3. Map each entity to `catalog`, `nix/projects`, `nix/workflows`, `benchmarks`, `docs` or `external`.
4. Mark each one `preserve`, `transform`, `replace` or `drop`; a `drop` requires a reason and agreement.
5. Migrate one vertical slice at a time. Treat the legacy setup as the oracle until equivalence is proven.
6. Do not copy Markdown commands: rebuild the executable contract from them and from the actual CI.
7. Do not delete the legacy setup before the slice is verified.

## Repository grilling

The user may know nothing about Nix or how a CC works: never ask them to write or choose Nix, and explain each step in plain words. Read the stack and the build and test commands from the repo's files (`package.json`, `pyproject.toml`, `*.csproj`, `go.mod`, `Cargo.toml`, `CMakeLists.txt`, ...) and its CI, propose them, and ask only for confirmation. To add one repo to a ready CC, run only this section and step 6 of "New CC" for it.

First study the repo and its CI, then close the gaps: remote/branch/role/owner; inputs/outputs/consumers; build/test interface; system/network dependencies; platforms/hardware; secrets/datasets/models/external state; the minimal adapter check.

Put build commands straight into the Nix adapter, not into the grilling transcript.

### Target platforms

Always ask, never infer: "Which platforms does `<repo>` ship on: windows, linux, macos?" CI or a Dockerfile shows where something is built, not where it runs. Record the answer with `--targets` on `./cc repo add`. For each target, agree on its gate:

- a pure Nix check (`targets.<platform> = <derivation>`) when Nix can build and test natively for that platform;
- `ccLib.mkWindowsHostGate` for windows: a PowerShell script that builds and tests with the real toolchain on the Windows host from WSL. Ask for the toolchain and the exact build and test commands;
- `ccLib.mkPythonHostGate` for a Python repo that installs its requirements from PyPI on the host (windows, linux or macos): it reuses a venv per requirements and interpreter and rebuilds it weekly (`docs/kit/decisions/0027-python-gate-venv-cache.md`). Ask for the requirements files, any special install step (another index, constraints) and the test command;
- a cross-compiled or emulated check (MinGW, Wine) only as an extra early signal, never as the only windows gate.

A target the user names but nobody can gate yet stays in `targets`; mark the repo `blocked` with that reason instead of dropping the target.

### Stack, static checks and quality gate

Record the repo's languages with `--stack`, read from the code and confirmed with the user. Wire its existing test framework into the check.

Static checks: wire the formatter, linter and architecture tests the repo already configures (look in its build files, CI and pre-commit hooks) into the adapter's `checks` in check mode. Where it has none, propose the stack's defaults from `docs/kit/rules/quality-gates.md` ("Static checks") as ordinary fast checks; if the code does not pass them yet, start from a baseline or a smaller rule set that passes and say what was left out. Explain why in plain words: what a tool can check is caught on every run, so reviews and councils need not look for it.

Then propose a `quality.mutation` gate with the stack's default tool from `docs/kit/rules/quality-gates.md`; the gate must fail on surviving mutants. A repo without the gate is allowed, but `./cc verify --quality` and councils will warn that its tests are not checked against injected bugs.

## Acceptance

- `control-center.json` and the catalog are valid.
- Every repo declares its targets and is verified on all of them by `./cc verify`, or has an explicit blocker.
- Every repo runs its stack's static checks, or the reason it does not is recorded.
- Cross-repo relationships are represented by a workflow/check; a generated contract artifact by a contract check.
- The critical path passes end to end.
- The target CC does not depend on `cc_template`.
