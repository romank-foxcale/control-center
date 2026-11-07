---
id: kit.decision.0019-lean-agent-context
title: Agent context states what the repo cannot show, once; role skills stay out of sessions
status: accepted
summary: AGENTS.md keeps only what an agent cannot see in the repo and the high-stakes rules, skill descriptions say what and when, generic behaviour steps are dropped, and runner-only role skills (council, thread sceptic, review learner) live in .agents/roles; an A/B eval of 30 headless runs per variant kept every check passing at 9% lower cost.
verified_at: 2026-10-17
evidence:
  - AGENTS.md
  - .agents/skills/_shared/cc-cli.md
  - .agents/roles
  - scripts/council/run.py
  - scripts/validate-control-center.py
  - scripts/rule_hooks.py
  - scripts/eval/run.py
  - scripts/eval/scenarios.py
  - scripts/eval/fixture.py
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0005-multi-tool-agent-configs.md
  - docs/kit/decisions/0013-rule-hooks.md
---

# Agent context states what the repo cannot show, once; role skills stay out of sessions

## Context

Every session loaded about 4.2k tokens of CC context before the user's first word: `AGENTS.md` (about 1.7k) and 24 skill descriptions (about 2.5k). Seven of those descriptions belonged to council and thread role skills that only the runners load. The same rules appeared in `AGENTS.md`, the shared CLI note, several skills and the hook messages. Guidance from the Claude Code team for Claude 5 models (Thariq, "The new rules of context engineering for Claude 5 models", July 2026) says that over-constraining and repetition cost more than they protect: use judgement over rules, progressive disclosure over upfront detail, and short descriptions.

## Decision

- `AGENTS.md` keeps what an agent cannot see in the repo (layout, where knowledge lives, setup status, the kit layer) and the rules that conflict with tool defaults or are hard to undo (Nix only, no AI attribution, generated skill copies, production, English). Details stay in the skills and `docs/kit/rules/`, loaded when needed.
- A skill description says what the skill is for and when to use it. How it works stays in its body.
- The shared CLI note keeps only real gotchas: never retry with other arguments, confirm destructive commands, use the catalog instead of scanning folders.
- Council and thread role skills move to `.agents/roles/`, as does any later role skill that only a runner loads, such as `review-learner`. `scripts/council/run.py` and `./cc` validation look there first, then in `.agents/skills/` for shared skills such as `tdd`. `./cc agents sync` does not copy them, so no session lists them.

## Measurement

An A/B eval ran 10 scenarios × 3 runs per variant with headless `claude -p` (Opus 5.5) in copies of a ready fixture CC that had one Nix-verified repo. The scenarios: commit, run tests, list repos, remember a fact, edit a skill, fix a bug in a feature worktree, plan, add a repo, edit the kit layer, and ask for help. Deterministic checks graded each run on the final files and tool calls.

| | Before | After |
|---|---|---|
| Always-loaded CC context | about 4.2k tokens | about 2.5k tokens |
| Checks passed | 30/30 | 30/30 |
| Mean cost per run | $0.233 | $0.213 (−9%; lower in 9 of 10 scenarios) |
| Mean prompt tokens per run | 137k | 130k (−6%) |
| Mean time per run | 23.8 s | 23.3 s (−2%) |

The one slower scenario, commit (+13 s), was a gain: the shorter `AGENTS.md` made the agent run `./cc check` before committing in every run, as the rule always required. The longer version never did. With three runs per scenario, single-scenario differences below about 10% are noise.

## Consequences

- A rule that leaves an artifact is still gated by `./cc check` or `./cc verify`, and the hooks still enforce the rest at the tool call (0013). A shorter text does not weaken those gates.
- The baseline passed every check too: the gain is cost and attention, not fixed failures.
- The eval is part of the kit as `./cc eval`: `./cc eval run <label> --ref <git-ref>` for each variant, then `./cc eval compare <a> <b>`. Measure a change to `AGENTS.md`, a skill or a hook this way before keeping it, and add a scenario when a change targets behaviour the current ones do not cover. It needs Nix and a logged-in `claude`, uses the user's subscription, and skips permission prompts only inside throwaway copies under `$CC_EVAL_WORK`.

## Revisit when

A model generation changes how well instructions are followed, or a scenario that the gates do not cover starts failing.
