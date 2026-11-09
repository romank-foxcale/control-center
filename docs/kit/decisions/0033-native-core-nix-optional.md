---
id: kit.decision.0033-native-core-nix-optional
title: The CC core runs natively through uv on Windows, macOS and Linux; Nix is an optional extra
status: accepted
summary: ./cc and every CC check run natively on Windows, macOS and Linux with a pinned uv and Python (the cc and cc.cmd shims), with no WSL and no Nix; Nix becomes an optional extra for build, run, relock, gc, eval and the Nix-defined project gates, which stay Nix until project adapters get a native backend (milestone 2). This supersedes 0032 and amends 0001.
verified_at: 2026-11-09
evidence:
  - cc
  - cc.cmd
  - .uv-pin
  - pyproject.toml
  - uv.lock
  - scripts/cc.py
  - scripts/hostos.py
  - scripts/checks.py
  - scripts/verify.py
  - scripts/hook_guard.py
  - scripts/uv-bootstrap.ps1
  - scripts/materialize-template.py
  - scripts/validate-control-center.py
  - install/windows/install.ps1
  - .github/workflows/cc-check.yml
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0001-nix-executable-knowledge.md
  - docs/kit/decisions/0032-code-center-keeps-nix-behind-the-center-contract.md
  - docs/kit/decisions/0006-target-platform-gates.md
  - docs/kit/decisions/0013-rule-hooks.md
  - docs/kit/decisions/0021-cc-check-in-ci.md
  - docs/kit/decisions/0027-python-gate-venv-cache.md
  - docs/kit/decisions/0028-verify-before-push.md
  - docs/kit/rules/executable-processes.md
---

# The CC core runs natively through uv on Windows, macOS and Linux; Nix is an optional extra

## Context

[0032](0032-code-center-keeps-nix-behind-the-center-contract.md) kept Nix, and so WSL, for everything inside the kit. On Windows that meant a Linux VM with its own memory and disk, a second file system that is slow from the Windows side (a rule hook on `/mnt/c` takes 13-24 s, longer than its timeout), and a setup that most users of the kit could not do alone. The user asked for a CC that runs on Windows, macOS and Linux "without allocating memory to WSL".

A spike (2026-11-07) showed that the CC's own code is Python and Git, which uv runs natively with a pinned interpreter, and that a risk council found no blocker that a work package could not remove.

## Decision

- **The core runs natively.** `cc` (POSIX shells and Git Bash) and `cc.cmd` (Windows) download the uv pinned in `.uv-pin`, check its SHA-256 and run `scripts/cc.py` with the Python pinned in `uv.lock`. Nothing else is installed first: no WSL, no Nix, no system Python. The shims set `UV_NO_CONFIG=1` and `UV_PYTHON_PREFERENCE=only-managed`, so a user's `uv.toml` or a system Python never changes how the CC runs. The pin moves only with all six SHA-256 values of `.uv-pin` together.
- **Hooks never download.** In hook mode a shim that finds no uv or venv denies at once and points to `./cc doctor`; after a pull that moves the pin it uses a uv already in `.cc-local` ([0013](0013-rule-hooks.md)).
- **Text with line breaks goes through Git Bash or a file.** cmd.exe cuts an argument at its first line break, so `cc.cmd` refuses one with a clear error; PowerShell 5.1 drops double quotes inside an argument to a `.cmd` file and leaves `& | < >` unquoted. Long text goes through `./cc` in Git Bash, or through a file option such as `./cc council ... --task-file`.
- **`cc` stays executable in a CC made on Windows.** A Windows checkout cannot set the file mode, so `materialize-template.py` marks `cc` as `100755` in the index and `validate-control-center.py` fails on any other mode.
- **The commands keep their names, arguments and exit codes.** `check`, `verify` and `show` run natively and add their Nix part when Nix is installed. `build`, `run`, `relock`, `gc` and `eval` belong to the optional Nix extra and fail with "needs the optional Nix extra" without it. The command × host table is in [executable processes](../rules/executable-processes.md).
- **A missing gate is never a pass.** Without Nix a declared target or quality gate has no runnable backend on the host, so `./cc verify` fails and writes no stamp ([0028](0028-verify-before-push.md)). A stamp needs the same tree at the start and the end of the run.
- **OS-specific code lives in one module**, `scripts/hostos.py`: process liveness and tree stop, `.cmd` resolution, the canonical spelling of a path (8.3 names, letter case, links), write protection, and the retries a Windows file needs.
- **The proof is CI on three OSes** ([0021](0021-cc-check-in-ci.md)): `./cc check` on Windows, macOS and Ubuntu from a fresh clone, a comparison that every OS ran the same tests, and `nix flake check` for the extra.
- **The boundary to the hub stays** as 0032 set it: the hub reaches the kit only through the center-kit contract. Without WSL that contract can run natively; how the hub calls it is the hub's own decision.

## Options considered

- **Keep Nix in WSL (0032):** reproducible, but the WSL cost above stays for every Windows user.
- **Docker for the core:** on Windows and macOS Docker also runs a Linux VM, so it moves the cost instead of removing it.
- **pixi for the core too:** it covers the project toolchains well (S3 spike), but the core needs only Python, and uv is smaller and faster for that. pixi is the candidate for project adapters in milestone 2.

## Consequences

- In milestone 1 the project gates are still Nix-defined. A CC with catalog repos needs the Nix extra where the CC runs (Linux, macOS, or a CC inside WSL on Windows; a CC on the Windows drive does not use the Nix in WSL) for `./cc verify` to pass; without it verify fails honestly. Milestone 2 gives adapters a native backend.
- The Windows installer (`install/windows/install.ps1`) sets up the native core by default: Git for Windows, the AI tools, the kit, a `cc-kit` command and a first CC, with no WSL and no Nix. `-WithNix` installs the WSL and Nix extra instead. A clone plus `cc.cmd` or `./cc` is also enough for the core.
- A stamp records whether the Nix part ran (`nix` in `.cc-local/verified/<tree>`); a push of commits that change Nix inputs asks when it did not ([0013](0013-rule-hooks.md)).
- `./cc doctor` exits 1 when it reports an error, such as a Claude Code below 2.1.295 or a missing Git Bash, and warns when the CC is inside OneDrive, whose sync locks and replaces files under git and the venv.
- The shims download the glibc builds of uv and Python. On NixOS they start only with `nix-ld` enabled (`programs.nix-ld.enable = true`); musl Linux such as Alpine is not supported.
- Existing CCs keep the bash `./cc` until they migrate; this branch changes the template only. `redefinition` is not merged into `main` before the milestone 2 plan and the migration plan exist.
- Claude Code hooks need Git Bash on Windows. A fresh clone has no uv or Python until the first command outside a hook: run `./cc doctor` once before the first session, or every gate hook denies.

## Revisit when

A project stack cannot get a native gate in milestone 2; or uv or the pinned Python stops supporting one of the three OSes.
