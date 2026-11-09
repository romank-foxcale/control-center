---
name: grill-cc-bootstrap
description: Evidence-first interview that creates or migrates a Control Center for one new project or many existing repos, and onboards its repositories (catalog entry, Nix adapter, a gate per platform, verify). Use to set up, continue setting up or migrate a CC, when its status is discovery or adapting, and when a project repo is added to or created in a ready CC.
---

# Grill CC bootstrap

First read `../_shared/grilling-core.md` in full, then `BOOTSTRAP.md`. Apply both contracts together.

## New CC

1. Determine `template_root` and `target_root` as described in `BOOTSTRAP.md`. If the CC was created with `cc-kit new`, steps 1–2 are already done.
2. Run the materialization; continue from `target_root`.
3. Record the name, status and topology in `control-center.json`.
4. Ask first: "Does this CC cover repos that already exist, a new project that has no repos yet, or both?" Then collect the use cases, boundaries and permissions.
5. Build the repository inventory: the existing repos, and the new repos to create, each with a name, a role and its relationships. Do not write adapters until they are agreed.
6. Onboard one existing repo at a time: `./cc repo add --targets ... --stack ...` → inspect → flake input/adapter → package/check, static checks, one gate per target and, where feasible, `quality.mutation` → `./cc verify <id> --quality` → `./cc repo set-status <id> verified`. Create each new repo with "New project repos" below.
7. Only then assemble workflows and benchmarks. Every contract between two repos (an API spec and the client or types generated from it, a shared schema) gets a contract check from `templates/workflow.nix`.
8. Replace the template README and project map with real data; remove the placeholders. Keep `GUIDE.md`, `install/`, `CLAUDE.md` and `.claude/skills`: they are the entry point for people and AI tools.

## New project repos

A new repo has no code and no remote yet (docs/kit/decisions/0034-new-project-cc.md). Agree its id, role, stack and target platforms, then:

1. `./cc repo new <id> --targets <platform>... --stack <language>... [--branch main] [--role "<role>"]`. It runs `git init` in `repos/project/<id>`, makes one first commit with LF line ends, writes the descriptor with `"remote": null`, and writes the placeholder `nix/placeholders/<id>/`. Commit the descriptor and the placeholder in the CC.
2. Tell the user in plain words: the repo is only on this disk until it has a remote, so offer `backup.path` in `control-center.json` (or `./cc backup --to <folder>`); a remote can come any time with `./cc repo set-remote <id> <url>`, and it must be an empty repository (no README).
3. Do not write a code skeleton. The repo stays `discovered`, and the CC stays `adapting`, until the first feature (`./cc worktree create <feature> <id>`) adds the code, the adapter (flake input `<id> = { url = "path:./nix/placeholders/<id>"; flake = false; };`) and a gate per target. While the repo has no remote, finish a feature with `./cc worktree land <feature> <id>`: it needs a passing `./cc feature <feature> verify`, or `--unverified "<reason>"` only after the user agrees.
4. Where no gate can run on this host (no Nix before milestone 2), set `./cc repo set-status <id> blocked --reason "<why>"`. The CC can then be `ready`; `./cc doctor` and `./cc verify` keep naming the blocked repo on a host that has Nix.
5. Without Nix, Claude Code asks before a direct build or test tool call that bypasses the gates. Explain that this prompt is expected, and that the gate belongs in the adapter.

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

For an existing repo, first study the repo and its CI, then close the gaps: remote/branch/role/owner; inputs/outputs/consumers; build/test interface; system/network dependencies; platforms/hardware; secrets/datasets/models/external state; the minimal adapter check.

Put build commands straight into the Nix adapter, not into the grilling transcript.

### Target platforms

Always ask, never infer: "Which platforms does `<repo>` ship on: windows, linux, macos?" CI or a Dockerfile shows where something is built, not where it runs. Record the answer with `--targets` on `./cc repo add`. For each target, agree on its gate:

- a pure Nix check (`targets.<platform> = <derivation>`) when Nix can build and test natively for that platform;
- `ccLib.mkWindowsHostGate` for windows: a PowerShell script that builds and tests with the real toolchain on the Windows host from WSL. Ask for the toolchain and the exact build and test commands;
- `ccLib.mkPythonHostGate` for a Python repo that installs its requirements from PyPI on the host (windows, linux or macos): it reuses a venv per requirements and interpreter and rebuilds it weekly (`docs/kit/decisions/0027-python-gate-venv-cache.md`). Ask for the requirements files, any special install step (another index, constraints) and the test command;
- a cross-compiled or emulated check (MinGW, Wine) only as an extra early signal, never as the only windows gate.

A target the user names but nobody can gate yet stays in `targets`; mark the repo `blocked` with that reason instead of dropping the target.

Every gate above is Nix-defined until project adapters get a native backend (docs/kit/decisions/0033-native-core-nix-optional.md). The CC itself runs without Nix, but `./cc verify` runs a project gate only where the optional Nix extra is installed: Linux, macOS, or WSL on Windows. Tell the user this in plain words when their machine has no Nix, and never install it for them.

### Stack, static checks and quality gate

Record the repo's languages with `--stack`, read from the code and confirmed with the user. Wire its existing test framework into the check.

Static checks: wire the formatter, linter and architecture tests the repo already configures (look in its build files, CI and pre-commit hooks) into the adapter's `checks` in check mode. Where it has none, propose the stack's defaults from `docs/kit/rules/quality-gates.md` ("Static checks") as ordinary fast checks; if the code does not pass them yet, start from a baseline or a smaller rule set that passes and say what was left out. Explain why in plain words: what a tool can check is caught on every run, so reviews and councils need not look for it.

Then propose a `quality.mutation` gate with the stack's default tool from `docs/kit/rules/quality-gates.md`; the gate must fail on surviving mutants. A repo without the gate is allowed, but `./cc verify --quality` and councils will warn that its tests are not checked against injected bugs.

## Acceptance

- `control-center.json` and the catalog are valid.
- At least one project repo exists. Every repo declares its targets and is verified on all of them by `./cc verify`, or is blocked with a `blockedReason`. A new repo with no code yet keeps the CC `adapting`.
- Every repo runs its stack's static checks, or the reason it does not is recorded.
- Cross-repo relationships are represented by a workflow/check; a generated contract artifact by a contract check.
- The critical path passes end to end.
- The target CC does not depend on `cc_template`.
