---
id: kit.decision.0035-review-council
title: Every pull request review runs a council of reviewers and a judge that verifies each finding
status: accepted
summary: cc-review reviews every catalog PR with ./cc council review - three read-only reviewers on Claude, GPT and Grok review the PR head alone, and a judge that sees them only as shuffled labels verifies each finding against the code, drops the ones the code refutes into a Refuted list, and writes review.md with a VERDICT line. The chat session coordinates and runs the gate but adds no findings. A failed reviewer is named and the run goes on with two. Reviewers trace each acceptance criterion through code the PR does not change, and never count a comment or spec text as evidence.
verified_at: 2026-11-19
evidence:
  - scripts/council/run.py
  - scripts/council/progress.py
  - scripts/council/council_test.py
  - scripts/validate-control-center.py
  - scripts/settings/state.py
  - templates/councils/review.json
  - templates/agents/review-claude.json
  - templates/agents/review-gpt.json
  - templates/agents/review-grok.json
  - templates/agents/review-judge.json
  - .agents/roles/council-review/SKILL.md
  - .agents/roles/council-review-judge/SKILL.md
  - .agents/skills/cc-review/SKILL.md
  - .agents/skills/cc-council/SKILL.md
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0007-agent-councils.md
  - docs/kit/decisions/0012-review-bar.md
  - docs/kit/decisions/0015-thread-sceptics.md
  - docs/kit/decisions/0025-verifier-evidence-and-self-contained-briefs.md
  - docs/kit/decisions/0029-risk-council.md
  - docs/kit/decisions/0034-council-load-balance.md
---

# Every pull request review runs a council of reviewers and a judge that verifies each finding

## Context

`cc-review` was one pass by the chat session's model. On one PR (2026-11-19) it reported CLEAN; a Codex review of the same commit found a real `[SCOPE]` finding: responses the batch contract does not define went to the generic offline retry instead of the hourly batch retry. The cause sat in a classifier the PR did not change, behind a code comment that said the case was handled. The same Codex review also drafted a finding that a later check refuted from the implementation: it had read the spec sentence, not the code behind it. One model in one pass misses findings, and a finding nobody verified can be wrong and still get posted.

## Decision

- `./cc council review <repo> <number> [--context-file <file>]` runs the `review` council (`catalog/councils/review.json`, kind `review`): two or more **reviewers** in parallel, then a **judge**. It checks out the PR head as feature `pr-<repo>-<n>` (as `./cc prs checkout`), gives every role the PR text and diff between unforgeable markers, and writes `review.md` in `.cc-local/runs/<run>/`. Nothing else is written.
- The shipped reviewers run on three providers (`review-claude`, `review-gpt`, `review-grok`), all `read-only`; the judge (`review-judge`) is Claude, because GPT already judges the risk and threads councils ([0034](0034-council-load-balance.md)). The runner prints that the judge shares a model with one reviewer, so the user weighs that.
- **Reviewer rules** (`council-review`): the review bar of [0012](0012-review-bar.md); trace every acceptance criterion to the line that meets it, through unchanged code, and through every case the criterion names; a comment, docstring or PR text is never evidence that code works; a contract claim is checked against its implementation where it is readable.
- **Judge rules** (`council-review-judge`): verify every finding by reading the cited code and what it depends on; keep a finding only one reviewer found when the evidence holds; drop one only with refuting evidence, into `## Refuted`; merge duplicates into the widest correct statement; mark each kept finding `Found by`. The first line is `VERDICT: CLEAN` or `VERDICT: <n> finding(s)`; a reply without it fails the run.
- **Failures**: a reviewer that is not logged in, errors or replies empty is named in `state.json` and in `## Missing reviewers`, and the run goes on; with fewer than two reviews the run fails. The judge must run.
- **cc-review** runs the council on every PR and the Nix gate beside it, adds a failing gate as `[BREAK]`, and reports the judge's review with the Refuted list. It adds no findings of its own. It points to `./cc prs judge` ([0015](0015-thread-sceptics.md)) when comment threads wait. A feature diff is still reviewed in the session.

## Options considered

- A stricter single-session review: cheaper, but the missed finding above came from a pass that already had the rules to find it; one model's blind spots stay ([0007](0007-agent-councils.md)).
- The chat session as a fourth reviewer: the same model as `review-claude`, for more tokens and no new view.
- A council only on request: the user wanted every PR covered, and a review is the step where a missed finding reaches production.
- Stop when one reviewer fails: one logged-out provider would block every review.

## Consequences

- A PR review costs about four model calls and a few minutes, instead of one session pass.
- A Grok assessor was once far slower than the others in a risk run ([0029](0029-risk-council.md)); a slow reviewer delays the whole review. Measure it on real PRs and move or drop the role if it keeps happening.
- An existing CC copies `templates/councils/review.json` and the four `templates/agents/review-*.json` into its catalog to use it.

## Revisit when

The judge keeps refuting most of one reviewer's findings, one reviewer never adds a finding the others missed, or review time becomes the bottleneck for merging.
