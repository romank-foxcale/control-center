---
id: kit.index
title: Kit knowledge map
status: active
summary: What makes a CC a CC - its rules, architecture and decisions - owned by the kit, identical in every CC and replaced as a whole when the kit is updated.
verified_at: 2026-10-15
evidence:
  - AGENTS.md
relations:
  - docs/index.md
  - docs/kit/architecture/control-center.md
  - docs/kit/rules/bootstrap.md
  - docs/kit/rules/executable-processes.md
  - docs/kit/rules/knowledge-layout.md
  - docs/kit/rules/project-adapters.md
  - docs/kit/rules/quality-gates.md
  - docs/kit/rules/task-planning.md
  - docs/kit/rules/worktrees.md
  - docs/kit/decisions/0017-kit-and-project-layers.md
  - docs/kit/decisions/0018-setup-starts-itself.md
  - docs/kit/decisions/0019-lean-agent-context.md
  - docs/kit/decisions/0020-review-lessons.md
  - docs/kit/decisions/0021-cc-check-in-ci.md
  - docs/kit/decisions/0022-deterministic-checks-before-review.md
  - docs/kit/decisions/0023-stale-evidence-warning.md
  - docs/kit/decisions/0024-pinned-model-ids.md
  - docs/kit/decisions/0025-verifier-evidence-and-self-contained-briefs.md
  - docs/kit/decisions/0026-compact-verify-output.md
  - docs/kit/decisions/0027-python-gate-venv-cache.md
  - docs/kit/decisions/0028-verify-before-push.md
---

# Kit knowledge map

This folder belongs to the kit. A CC never edits it: to change kit behaviour for one CC, record a decision in [`docs/decisions/`](../decisions/index.md) that names the kit decision it overrides ([0017](decisions/0017-kit-and-project-layers.md)).

## Mandatory rules

- [Bootstrap and migration](rules/bootstrap.md)
- [Executable processes](rules/executable-processes.md)
- [Markdown knowledge layout](rules/knowledge-layout.md)
- [Connecting sibling projects](rules/project-adapters.md)
- [Planning agent tasks](rules/task-planning.md)
- [Quality gates per stack](rules/quality-gates.md)
- [Worktree topology](rules/worktrees.md)

## Architecture

- [Control Center boundaries](architecture/control-center.md)

## Decisions

- [Nix as the format for executable knowledge](decisions/0001-nix-executable-knowledge.md)
- [Atomic linked notes](decisions/0002-atomic-linked-notes.md)
- [Template as a temporary seed](decisions/0003-template-as-seed.md)
- [Plan lifecycle](decisions/0004-plan-lifecycle.md)
- [One set of skills for every AI tool](decisions/0005-multi-tool-agent-configs.md)
- [Every target platform has its own gate](decisions/0006-target-platform-gates.md)
- [Agent roles and councils bound to models](decisions/0007-agent-councils.md)
- [Language-specific quality gates behind language-neutral skills](decisions/0008-quality-gates.md)
- [Trello cards are updated on demand](decisions/0009-trello-on-demand.md)
- [Long runs report progress as a live event stream](decisions/0010-live-run-events.md)
- [The catalog is the CC's repo scope](decisions/0011-catalog-is-the-repo-scope.md)
- [Reviews report only BREAK, SCOPE and KNOWLEDGE findings](decisions/0012-review-bar.md)
- [Claude Code hooks enforce the hard rules early; the gates still decide](decisions/0013-rule-hooks.md)
- [Cursor is a read-only provider, and security review is a three-model council](decisions/0014-cursor-provider-and-security-council.md)
- [Review conversations are judged by two anonymous sceptics, and a judge settles only their splits](decisions/0015-thread-sceptics.md)
- [Every CC keeps its own repos and worktrees inside its folder](decisions/0016-per-cc-repos-and-worktrees.md)
- [Kit knowledge and a CC's own knowledge are separate layers](decisions/0017-kit-and-project-layers.md)
- [A CC that is not set up starts its own setup; nobody needs a magic prompt](decisions/0018-setup-starts-itself.md)
- [Agent context states what the repo cannot show, once; role skills stay out of sessions](decisions/0019-lean-agent-context.md)
- [Accepted review comments become user-confirmed, team-level review lessons](decisions/0020-review-lessons.md)
- [CI runs ./cc check on every pull request to the CC](decisions/0021-cc-check-in-ci.md)
- [What a tool can decide is a check, so review judges only the rest](decisions/0022-deterministic-checks-before-review.md)
- [A note whose evidence changed after verified_at gets a warning, not a failure](decisions/0023-stale-evidence-warning.md)
- [Agent roles pin full model ids; a floating alias gets a warning](decisions/0024-pinned-model-ids.md)
- [Verifier roles name their failure modes and count only evidence read in the run; briefs to other agents are self-contained](decisions/0025-verifier-evidence-and-self-contained-briefs.md)
- [./cc verify prints a gate's pass line or failure tail and keeps the full output in a log](decisions/0026-compact-verify-output.md)
- [Python host gates reuse a venv per dependency set, rebuilt at least weekly](decisions/0027-python-gate-venv-cache.md)
- [Push only the tree a passing run tested, and run the change itself before calling it done](decisions/0028-verify-before-push.md)
