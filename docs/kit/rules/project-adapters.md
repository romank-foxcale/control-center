---
id: kit.rules.project-adapters
title: Connecting sibling projects
status: active
summary: Every external repository is connected through a catalog descriptor, a pinned source and a Nix adapter with packages, checks and apps.
verified_at: 2026-10-15
evidence:
  - scripts/repositories.py
  - nix/projects/default.nix
  - templates/project.nix
  - nix/lib/default.nix
  - templates/workflow.nix
relations:
  - docs/kit/architecture/control-center.md
  - docs/kit/rules/executable-processes.md
  - docs/projects/index.md
  - docs/kit/decisions/0006-target-platform-gates.md
  - docs/kit/rules/quality-gates.md
  - docs/kit/decisions/0022-deterministic-checks-before-review.md
  - docs/kit/decisions/0027-python-gate-venv-cache.md
---

# Connecting sibling projects

## Project contract

`catalog/repositories/<id>.json` records the remote, default branch, kind (`project`, or `reference` for read-only guidance that needs no adapter), role, target platforms, stack (languages), flake input, adapter and onboarding status. The descriptor is created by `./cc repo add`; build commands never go into it.

The adapter is created with `ccLib.mkProject` and declares:

- `src`: a pinned flake input or a local source override;
- `packages`: the project's artifacts;
- `checks`: builds, tests and the static checks of its stack (formatter, linter, architecture rules; see [quality gates](quality-gates.md#static-checks));
- `apps`: running, generation, external integrations;
- `targets`: one gate per catalog target platform, a check or a host app (see [target gates](../decisions/0006-target-platform-gates.md)); a Python repo that installs its requirements on the host uses `ccLib.mkPythonHostGate`, which reuses its venv ([0027](../decisions/0027-python-gate-venv-cache.md));
- `quality`: optional `quality.mutation` gate with the mutation tool for the repository's stack (see [quality gates](quality-gates.md));
- `metadata`: role and owner.

The adapter's attribute name in `nix/projects` equals the catalog id, so its targets can be matched against the catalog.

The remote source is pinned in `flake.lock`. Local development does not change the contract: the same input is temporarily overridden to `path:../repo`.

## Division of responsibility

A project adapter knows how to produce the verifiable outputs of one repository. A workflow knows how to connect the outputs of several projects. Project build logic is never duplicated in a workflow.

When one repo consumes what another defines (an API spec and the client or types generated from it, a shared schema, a message format), the contract between them is a workflow check: `ccLib.mkContractCheck` regenerates the consumer's committed artifact from the producer's spec and fails on any difference. `templates/workflow.nix` is the starting point. Drift is then a failing check in `./cc verify`, not something a review has to notice.

## Drift

If upstream changes its structure, dependencies or test interface, the adapter/check breaks. The fix goes there; it is never masked by updating Markdown.
