---
id: kit.decision.0021-cc-check-in-ci
title: CI runs ./cc check on every pull request to the CC
status: accepted
summary: A GitHub Actions workflow shipped with the template runs the same ./cc check a developer runs, from a fresh clone on Windows, macOS and Linux, compares the tests each OS ran, and runs nix flake check for the optional Nix extra, with pinned actions, a read-only token and no secrets; every CC materialized from the template gets it as a tracked file.
verified_at: 2026-11-08
evidence:
  - .github/workflows/cc-check.yml
  - cc
  - cc.cmd
  - scripts/checks.py
  - scripts/ci_compare.py
  - flake.nix
  - scripts/materialize-template.py
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0001-nix-executable-knowledge.md
  - docs/kit/decisions/0003-template-as-seed.md
  - docs/kit/decisions/0013-rule-hooks.md
---

# CI runs ./cc check on every pull request to the CC

## Context

The hooks catch rule breaks only in Claude Code, and only in the shell text they see; [0013](0013-rule-hooks.md) relies on `./cc check` and `./cc verify` to catch the rest. Nothing ran those gates unless a person or an agent remembered to, so a change made with another tool, or with a hook bypassed, could merge unchecked. A deterministic gate does this job; it needs no agent, only a runner.

## Decision

- `.github/workflows/cc-check.yml` runs `./cc check` on every pull request and on pushes to `main`: the same entry point a developer runs, so CI and local runs cannot drift apart.
- It runs on `ubuntu-latest`, `macos-latest` and `windows-latest` (through `cc.cmd`) from a fresh clone with nothing preinstalled: the shims fetch the pinned uv and Python, as on a new machine.
- Each OS writes a summary of the tests every step ran and skipped (`CC_CHECK_SUMMARY`); the `same-tests` job fails when the OSes differ, unless `scripts/ci_compare.py` lists the skip. A test turned off on one OS cannot hide in a passing row.
- The optional Nix extra gets its own Ubuntu job with Nix from `cachix/install-nix-action`; it calls `nix flake check` directly, because the committed `flake.lock` needs no refresh and the native rows already ran in the matrix.
- Every action is pinned by commit SHA with its version in a comment.
- The job gets `contents: read` and no secrets.
- The template tracks the workflow, so `materialize-template.py` (and `cc-kit new`) copies it into every new CC without special handling.
- CI runs `./cc check`, not `./cc verify`: verify also needs clones of the catalog repos, their target platform gates and the branch history of feature worktrees, which a CC's own runner does not have.

## Options considered

- No CI, gates by discipline: the gap this note closes.
- Only `nix flake check`, as before the native core: it never ran the CC on Windows or macOS, where most users work.
- `./cc check` with Nix on one Ubuntu job instead of the matrix plus a Nix job: one job fewer, but Linux without Nix, the default now, would go untested.
- `DeterminateSystems/nix-installer-action`: equivalent here; the choice is not load-bearing.

## Consequences

- A CC whose flake inputs are private repositories fails in CI until it grants the job read access to them; that token is that CC's own decision, never the template default.
- A CC migrated from another format gets the workflow only through the migration, not by materialization.
- A CC hosted outside GitHub ports the jobs to its CI; the gate itself stays `./cc check`.
- The macOS and Windows runners are free for a public repository; a private CC pays for their minutes, and may drop OSes its team does not use.
- Making the check required before merge is a branch protection setting of each CC's repository, outside the kit.

## Revisit when

`./cc verify` can run in CI without the catalog clones, or a CC needs a runner other than GitHub Actions often enough to ship a second workflow.
