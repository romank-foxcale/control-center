---
id: kit.decision.0029-risk-council
title: A change to architecture, dependencies, runtime, data or deployment gets a risk council before it is planned
status: accepted
summary: ./cc council risk runs independent risk assessors on different providers - a pre-mortem over fixed categories, FMEA-style scores, every risk backed by a path, note or URL or marked as an assumption - and an anonymous judge that verifies them and writes one ranked register with spikes, checks and a GO, GO-WITH-CONDITIONS or NO-GO verdict to risk.md. It needs no feature, writes nothing else, and only its roles may use web search (access read-only-web).
verified_at: 2026-10-28
evidence:
  - scripts/council/run.py
  - scripts/council/providers.py
  - scripts/council/progress.py
  - scripts/council/council_test.py
  - scripts/council/providers_test.py
  - scripts/validate-control-center.py
  - templates/councils/risk.json
  - templates/agents/risk-claude.json
  - templates/agents/risk-gpt.json
  - templates/agents/risk-judge.json
  - .agents/roles/council-risk-assess/SKILL.md
  - .agents/roles/council-risk-judge/SKILL.md
  - .agents/skills/cc-risk/SKILL.md
  - .agents/skills/grill-task-planning/SKILL.md
  - AGENTS.md
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0007-agent-councils.md
  - docs/kit/decisions/0014-cursor-provider-and-security-council.md
  - docs/kit/decisions/0025-verifier-evidence-and-self-contained-briefs.md
  - docs/kit/decisions/0028-verify-before-push.md
---

# A change to architecture, dependencies, runtime, data or deployment gets a risk council before it is planned

## Context

Asked about a change such as replacing TEI with PyTorch for embeddings, an agent answered with a few generic risks from memory ("it might be slower"). Nothing checked them against the code, the notes or upstream issues, and nothing made the errors the change could cause, or the incompatibilities it could hit, visible before work began. One model also misses what it does not think of.

## Decision

- `./cc council risk --task <change> [--repo <repo>...] [--feature <feature>]` runs the `risk` council (`catalog/councils/risk.json`, kind `risk`): two or more **assessors** in parallel, then a **judge**. It needs no feature: it reads the CC and the base clones, or a feature's worktrees. The run lives in `.cc-local/runs/<run>/` and writes only its reports.
- The method comes from established practice, in the `council-risk-assess` role:
  - **Pre-mortem** (Klein): assume the change shipped and failed, and write concrete failure stories. Prospective hindsight finds more, and more plausible, risks than brainstorming. Assessors go through fixed categories (compatibility, correctness, performance, data, deploy and operations, dependencies, security, people) and say what they checked when they find nothing.
  - **FMEA**: severity, likelihood and detection for each risk, ranked by severity first (as the AIAG-VDA action priority does), so a rare data loss outranks a frequent nuisance.
  - **Readiness reviews**: blast radius, rollback and detection; an unknown is never scored as low risk.
  - **Evidence** ([0025](0025-verifier-evidence-and-self-contained-briefs.md)): every risk cites a `path:line`, a note or a URL, or is an `ASSUMPTION` with the cheapest way to settle it.
- **Independence and judging**: assessors do not see each other; the judge (`council-risk-judge`) sees them as shuffled labels, checks every claim, merges duplicates, keeps a risk only one assessor found, and cannot lower a severity 4 or 5 or drop a risk without refuting evidence. Its first line is the verdict; a reply without one fails the run.
- **Web access**: `read-only-web` is a new access level: Claude gets WebSearch and WebFetch (also allowed explicitly, since `dontAsk` denies them otherwise), Codex gets `web_search="live"` in its read-only sandbox. Only a risk council's assessors and judge may have it. Cursor roles stay `read-only`; Cursor's own web search is not switched by the CC.
- The default council is a Claude and a GPT assessor, both with web search, and a Claude judge with web, so it can check cited sources. A Grok assessor (Cursor) was left out after the first real run (2026-10-28): it took 15 minutes against 2 to 3 for the others. A CC can add it back as a `read-only` Cursor role.
- **When**: `cc-risk` and AGENTS.md make agents offer it before planning such a change, and never answer with risks from memory. `grill-task-planning` turns the register into failure modes, spike work packages and completion checks, which ties into running the change itself ([0028](0028-verify-before-push.md)).

## Options considered

- A single risk agent: cheaper, but one model's blind spots stay unchallenged ([0007](0007-agent-councils.md)).
- Debate rounds between assessors: research on critic-defender-judge setups shows a few rounds give most of the gain; here the judge's evidence check plays the defender, and independent assessors avoid converging on the first confident story.
- A risk number (severity × likelihood × detection): hides a rare catastrophic risk behind frequent small ones.
- Running it on every change: three model calls each; the trigger is the kind of change, matching review depth to risk.

## Consequences

- A risk audit takes a few minutes and three model calls (two assessors in parallel, then the judge).
- Web search widens what a read-only role can reach: it can send queries derived from the CC's code to the provider's search. Roles with it are limited to the risk council.
- An existing CC copies `templates/councils/risk.json` and the three `templates/agents/risk-*.json` into its catalog to use it.
- The verdict is advice: the user decides, and the plan carries the register.

## Revisit when

Assessors keep producing the same risks (one assessor would do), or teams want the register tracked over the life of a change rather than once before it.
