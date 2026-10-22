---
id: kit.decision.0025-verifier-evidence-and-self-contained-briefs
title: Verifier roles name their failure modes and count only evidence read in the run; briefs to other agents are self-contained
status: accepted
summary: The judge, thread sceptic, security reviewer and cc-review name the two ways a verifier fails (stopping when the first part checks out, and trusting confident wording or code not read in this run) and count a claim only with a path:line or gate result read in the run; council tasks, plan work packages and the judge's writer instructions state every fact in full instead of pointing to a conversation or proposal the reader never sees.
verified_at: 2026-10-22
evidence:
  - .agents/roles/council-judge/SKILL.md
  - .agents/roles/council-security/SKILL.md
  - .agents/roles/thread-sceptic/SKILL.md
  - .agents/skills/cc-review/SKILL.md
  - .agents/skills/cc-council/SKILL.md
  - .agents/skills/grill-task-planning/SKILL.md
  - scripts/council/run.py
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0007-agent-councils.md
  - docs/kit/decisions/0012-review-bar.md
  - docs/kit/decisions/0015-thread-sceptics.md
  - docs/kit/decisions/0019-lean-agent-context.md
---

# Verifier roles name their failure modes and count only evidence read in the run; briefs to other agents are self-contained

## Context

Third-party analyses of the Claude Code source that leaked in March 2026 describe two prompt patterns that fit the CC. Its verification agent is told the specific ways verifiers fail (avoiding the check, and accepting work once the first 80% looks right), and a check without the command and its output does not count as a pass. Its coordinator may not hand understanding to a worker with phrases like "based on your findings": it must read the results and write exact instructions. These are secondhand readings of unofficial code, so the CC adopts the patterns, not any wording.

The CC's verifiers already demand evidence, but none named its own failure modes, and a `path:line` could come from memory instead of from the run. Three CC handoffs go to an agent that does not see the source conversation: a council task (the proposers see only the task, the worktree and the lessons), a plan work package, and the judge's writer instructions (the writer sees `verdict.md`, never the proposals; `scripts/council/run.py`, `command_approve`).

## Decision

- `council-judge`, `thread-sceptic`, `council-security` and `cc-review` each name two failures to guard against: stopping when the first part checks out, and relying on confident wording or on code not read in this run. A claim counts only with a `path:line` or gate result read in the run. A `cc-review` ✓ without one becomes `?`.
- The judge's writer instructions, a council task (`cc-council`) and a plan work package (`grill-task-planning`) state every fact the reader needs and never point to "proposal B", "as discussed" or "the findings".
- Each rule is one or two sentences in the role or skill that uses it, in line with [0019](0019-lean-agent-context.md).

## Options considered

- A shared verifier note loaded by every role: one copy, but each role loads more text than it needs and the rules sit away from the output format they govern.
- An A/B eval before adoption, as in 0019: skipped because the change adds a few sentences to runner-only roles and two skills, not session context.

## Consequences

- Verdicts carry more citations; a verifier that cannot cite returns `NEEDS-INFO`, `?` or no finding instead of a guess.
- Briefs get longer, because they repeat facts the writer of the brief already knows.

## Revisit when

Councils or sceptics start returning more `NEEDS-INFO` or `?` results than the user finds useful, or a provider's model follows evidence rules without being told.
