---
id: kit.decision.0034-council-load-balance
title: The winning proposal's provider writes it, and the CC measures who wins and who spends
status: accepted
summary: A council names one writer role per provider that can write; the provider of the winning proposal (or the user's pick) writes it, and when that is Cursor or there is no single winner the provider with fewer new input and output tokens in 7 days writes. The choice is made once per run. ./cc council stats shows win rates and tokens per provider, so roles move between models by results. A writer's diff is scanned for secrets and for weakened tests.
verified_at: 2026-11-18
evidence:
  - scripts/council/run.py
  - scripts/council/stats.py
  - scripts/council/council_test.py
  - scripts/council/stats_test.py
  - scripts/validate-control-center.py
  - templates/agents/writer-gpt.json
  - templates/councils/coding.json
  - templates/councils/debug.json
  - templates/councils/testing.json
  - .agents/roles/council-write/SKILL.md
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0007-agent-councils.md
  - docs/kit/decisions/0014-cursor-provider-and-security-council.md
  - docs/kit/decisions/0029-risk-council.md
  - docs/kit/decisions/0033-codex-rule-hooks.md
---

# The winning proposal's provider writes it, and the CC measures who wins and who spends

## Context

Measured quota use on 2026-11-18 was Claude 20 %, GPT 2 % and Cursor 1 %. Every council writer was Claude, and the writer is the most expensive call of a run: it reads, edits and may retry. A run that stops on one provider's rate limit while the others are idle wastes the subscriptions the team pays for. Nothing in the CC measured which model's proposals win, so any move of a role was a guess.

Probes on 2026-11-18: a Codex writer refused all work under the writer text "you cannot run commands", because Codex reads files only through its shell; with a Codex-specific instruction it finished a small task in 25 s and left the tests it could not run unchanged.

## Decision

- A council's `writer` maps each provider that can write (`claude`, `codex`) to a `write-worktree` role whose provider matches; one role (older catalogs) still works. The templates use `{"claude": "writer", "codex": "writer-gpt"}`.
- When writing starts, the runner chooses the writer once and saves it as `writer` and `writerReason` in `state.json`; every phase (the debug test and fix), every retry and a resumed run use it. The rule:
  - the provider of the winning proposal writes it, where the user's `--pick` decides over the judge's label;
  - when that provider cannot write (Cursor) or the verdict is `HYBRID`, the provider with the fewest new input plus output tokens over the last 7 days writes. Cached tokens are left out because they cost little quota, and an unreported call counts as nothing, so the measure is a lower bound; a tie goes to the first writer listed.
- `./cc council stats` reads every run folder (`.cc-local/runs`, `.cc-local/runs-archive`, `worktrees/*/.cc-runs`): proposal wins per council kind, with the model each run recorded (not today's catalog), the judge model next to the rates and user picks counted apart; and tokens per provider from every call log. Chat sessions and `./cc prs learn` (temporary logs) are not counted.
- The writer role skill tells a writer without a shell to use its read and edit tools, and a Codex writer to read through the shell, edit with apply_patch and leave a test that fails only for sandbox reasons unchanged.
- After the writer, the runner scans the lines its diff adds for credentials (AWS, private key, GitHub, Anthropic or OpenAI, Slack patterns) and stops the run `blocked-security` on a hit. It flags removed test definitions, new skip markers and fewer assertions in test files: the security reviewers get the flags above the diff, and the run summary repeats them as a warning. The flags never block: a test may be removed on purpose.
- A run checks before its first call that every role's model is offered by its provider, when the provider lists models.
- The risk judge's `VERDICT:` line is accepted on any line of its reply.

## Options considered

- Always the provider with the lower weekly use: balances tokens but separates the approach from the model that designed it, and ignores which model is better at the task.
- Alternate writers by run: simple, but blind to both results and quota.
- Read the remaining subscription quota: no provider CLI exposes it.
- Block a run on a weakened test: a test can be removed or skipped on purpose; the user decides with the flag in front of them.

## Consequences

- A coding, debug or testing run now needs both the Claude and the Codex login, because either writer may be chosen.
- A Codex writer can run commands in its sandbox (no network, writes only in the worktree, [0007](0007-agent-councils.md)); it may try local tests that cannot pass there, which the instruction above addresses.
- Each Codex writer call adds a `[projects."<worktree>"]` trust entry to the user's `~/.codex/config.toml` (probed 2026-11-18). The runner does not remove it yet.
- The secret patterns catch common token formats only; a password in plain text passes.
- Win rates mean little until about 10-20 runs per kind; a judge on the same model as a proposer may favour it, which is why the judge model is shown next to the rates.

## Revisit when

A provider exposes remaining quota, a Cursor role can write, or the win rates show one model should take a role for good.
