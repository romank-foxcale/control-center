---
id: kit.decision.0014-cursor-provider-and-security-council
title: Cursor is a read-only provider confined by its permission config, and security review is a three-model council
status: accepted
summary: Council roles can use the Cursor subscription through cursor-agent, only read-only and only inside a throwaway snapshot that carries Cursor's deny config; the security step runs Claude, GPT and Grok reviewers in parallel and the judge summarizes them anonymously, while a high from any one reviewer still blocks.
verified_at: 2026-10-17
evidence:
  - scripts/council/providers.py
  - scripts/council/providers_test.py
  - scripts/council/run.py
  - scripts/council/council_test.py
  - scripts/validate-control-center.py
  - templates/agents/judge.json
  - templates/agents/security-grok.json
  - templates/councils/coding.json
  - .agents/roles/council-judge/SKILL.md
relations:
  - docs/index.md
  - docs/kit/decisions/0007-agent-councils.md
  - docs/kit/decisions/0015-thread-sceptics.md
  - docs/kit/decisions/0029-risk-council.md
---

# Cursor is a read-only provider confined by its permission config, and security review is a three-model council

## Context

The user wants Grok models, available through a Cursor subscription, as the judge and later for research, and wants security reviewed by Claude, GPT and Grok instead of Claude alone. ADR 0007 requires access to be enforced by the provider, never by prompt text. `cursor-agent --print` can write files, and its `plan` and `ask` modes only instruct the model: in probes the model made no tool call, so they proved nothing.

## Decision

- Provider `cursor` runs `cursor-agent --print --trust --workspace <snapshot> --output-format stream-json --model <model>` without `--force`, prompt on stdin.
- `<snapshot>` is a fresh temporary copy of what Git would commit in the worktree, plus a `.cursor/cli.json` that allows `Read(**)` and denies `Write(**)` and `Shell(*)`, replacing any project copy. Probed on 2026-10-08 with `grok-4.7-high`: the model called edit and shell tools, and the CLI refused each with "Blocked by permissions configuration". The snapshot is fingerprinted before the call; any change fails the call, and the snapshot is deleted. A thread judge run instead shares one snapshot across its calls and checks it after each phase ([0015](0015-thread-sceptics.md)). The copy leaves out `.claude/settings*.json`: Cursor runs a folder's Claude Code hooks too, and in the first risk council run a CC's hooks ran Python in the snapshot, which then failed its fingerprint ([0029](0029-risk-council.md)).
- Cursor roles can only be `read-only`; validation refuses `write-worktree`. The reply is what the agent said after its last tool call, because Cursor's result joins every message.
- A council's `security` is a list of read-only reviewers (one role id still works). Several reviewers run in parallel; the judge receives their reviews as anonymous `Review A`, `Review B`, ..., verifies each finding against the code and writes `security.md`. Each review is kept as `review-<role>.md`.
- The run is `blocked-security` when the summary is `high` or unreadable, or when any reviewer is. The judge may explain why it does not confirm a reviewer's high finding, but only the user can overrule it.
- Templates: the judge and `security-grok` use Cursor `grok-4.7-high`; `security-claude` and `security-gpt` use the Claude and GPT models of the other roles.

## Options considered

- Cursor `--mode plan` or `ask` as the restriction: unproven; the model, not the CLI, declined.
- Writing `.cursor/cli.json` into the project worktree: changes a repo the CC does not own, and a project's own config would conflict.
- The judge's severity alone decides: fewer false blocks, but one model could overrule another's real finding.

## Consequences

- ADR 0007's rule widens from "CLI flags" to "CLI flags or the provider's permission config, verified by `providers.py probe`, never prompt text".
- A Cursor role reads a copy: ignored files (build output, dependencies) are absent, and copying a large worktree adds time to each Cursor call.
- If a `cursor-agent` update stops honouring the workspace config, the fingerprint check fails the call; re-run `providers.py probe cursor <model>` after updating it.
- The security step costs three reviews and a summary per council run.

## Revisit when

Cursor gains a CLI flag for permissions or a sandbox that confines writes, or a Cursor role needs to write.
