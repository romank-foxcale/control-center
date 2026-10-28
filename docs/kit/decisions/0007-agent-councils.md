---
id: kit.decision.0007-agent-councils
title: Agent roles and councils bound to models
status: accepted
summary: The CC assigns a provider CLI and model to each agent role, and runs coding, testing and debug councils in which Claude and GPT propose or diagnose, an anonymous judge decides, the user approves and a writer implements under ./cc verify.
verified_at: 2026-10-17
evidence:
  - scripts/council/run.py
  - scripts/council/providers.py
  - scripts/validate-control-center.py
  - templates/agents/writer.json
  - templates/councils/coding.json
  - templates/councils/debug.json
  - .agents/skills/cc-council/SKILL.md
relations:
  - docs/index.md
  - docs/kit/decisions/0005-multi-tool-agent-configs.md
  - docs/kit/decisions/0006-target-platform-gates.md
  - docs/kit/rules/task-planning.md
  - docs/kit/decisions/0010-live-run-events.md
  - docs/kit/decisions/0014-cursor-provider-and-security-council.md
  - docs/kit/decisions/0029-risk-council.md
---

# Agent roles and councils bound to models

## Context

One model's blind spots go unchallenged when it designs, writes and judges its own code. A team often holds subscriptions to several providers, such as Claude and ChatGPT, and wants them to work on the same task, with a chosen model per task type. Earlier the CC never chose a model; each person picked one in their own tool.

## Decision

- A role is `catalog/agents/<role>.json`: provider CLI (`claude`, `codex` or `cursor`), model, access (`read-only` or `write-worktree`) and role skills. A council is `catalog/councils/<name>.json`: the roles that fill a fixed pipeline, `approval` and `maxRetries`.
- `./cc council run` executes the pipeline as headless CLI calls under the user's own subscription logins: proposers in parallel, a judge that sees only shuffled labels, a pause for user approval, the writer, `./cc feature <feature> verify` with bounded retries, then a security review (several reviewers summarized by the judge, see 0014).
- Access is enforced by CLI flags or the provider's permission config, never by prompts: Claude gets a fixed tool set without Bash, Codex runs in its OS sandbox with temporary directories excluded, and Cursor runs read-only in a snapshot that carries its deny config ([0014](0014-cursor-provider-and-security-council.md)). Only the writer can edit, and only inside the feature worktree. Claude roles load user settings only, with hooks off: the folder a role reads is not its session, and a project hook's reply would replace the role's final message (a CC's Stop hook did this in the first risk council run, [0029](0029-risk-council.md)).
- Proposers, judge and writer share the `lean-code` skill, adapted from Ponytail (MIT): read fully, then stop at the first rung of the reuse ladder that holds. It also applies to interactive coding. Test writers and the judge share the `tdd` skill, and failures are investigated with the `debug` skill, both adapted from Superpowers (MIT).
- Every call is a new CLI process with an empty context, so quality does not decay over a run; what carries over is text the runner chooses. A call that fills its own context still degrades, so the runner stops any call past its role's `maxToolCalls` (default 100), counted from the tool lines in its log, the same way for every provider: Codex and Cursor have no turn limit of their own. A writer stopped at the cap counts as a failed attempt, not a failed run.
- Every call's tokens (new input, cached input, output) are measured from what its CLI reports and written as the last line of its log, then added up per writer attempt and per run. The CC cannot see a subscription's remaining quota, so this measurement is the basis for any token budget. A call stopped before its CLI reported tokens is shown as unreported, never as zero, and makes the run's total a lower bound.
- Each writer attempt is recorded as one plain-language section of `attempts.md` (outcome, files changed, failed gates, key error lines, what the writer said), and every retry receives all earlier sections, so a fresh writer does not repeat an approach that already failed.
- The debug council gives every role the CC's own verify output as evidence, and the runner enforces red before green: the writer's reproduction test must fail on the unfixed code, must stay unchanged during the fix, and three failed fixes stop the run.
- The CC still holds no credentials and never falls back to another provider or model when one is logged out.
- Run artifacts live in `worktrees/<feature>/.cc-runs/`, outside Git. Councils never commit or push.

## Options considered

- The interactive session conducts the council: quicker, but its behaviour depends on which tool and session runs it, and the process would live in Markdown.
- A Paperclip-style system of long-running, messaging agents: more general, but far more machinery than a fixed pipeline needs.

## Consequences

- A council costs several model calls per task and can hit subscription rate limits.
- Provider CLI flags change between versions; `providers.py` must follow them, and a broken flag fails the run instead of loosening a restriction.
- The writer cannot run commands, so it relies on `./cc verify` output for feedback.

## Revisit when

A provider offers a stable programmatic interface for subscription use, or councils for planning are needed. (Security review councils: see 0014.)
