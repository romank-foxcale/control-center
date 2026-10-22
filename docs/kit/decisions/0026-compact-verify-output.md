---
id: kit.decision.0026-compact-verify-output
title: ./cc verify prints a gate's pass line or failure tail and keeps the full output in a log
status: accepted
summary: Each gate's output goes to gates/<gate>.log in the verify run folder; verify prints one line for a pass and the last 80 lines of a failure with the log path, so an agent reading verify keeps its context for the error. --full streams everything; council runs pass it because they keep their own verify log and tail.
verified_at: 2026-10-22
evidence:
  - scripts/verify.py
  - scripts/verify_test.py
  - scripts/council/run.py
  - .agents/skills/cc-verify/SKILL.md
  - cc
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0010-live-run-events.md
  - docs/kit/decisions/0019-lean-agent-context.md
---

# ./cc verify prints a gate's pass line or failure tail and keeps the full output in a log

## Context

`./cc verify` streamed every gate's full output: `nix flake check`, and `nix build -L` with the complete build log of each target gate. A chat agent reads that output as a tool result, so a passing build fills its context with lines it never needs, and an agent tool that truncates long output can cut off the failure, which comes last. Analyses of the leaked Claude Code source (March 2026) describe the same answer there: an oversized tool result goes to disk and the model gets a short preview.

## Decision

- Each gate's output goes to `gates/<gate>.log` next to the run's `events.jsonl` (`.cc-local/runs/<run>/` for a standalone verify, see [0010](0010-live-run-events.md)).
- A pass prints `pass in <time>; log: <path>`. A failure prints the last 80 lines and the log path. The section headers and the REPO/GATE/RESULT table are unchanged.
- `--full` streams every line as before, for a person at a terminal.
- A council runs verify with `--full`: it already writes the whole output to `verify-<n>.log` and gives the writer the last 200 lines, so its behaviour does not change.

## Options considered

- Compact output only when stdout is not a terminal: an agent's shell is not a terminal either way, and the same command would print differently for a person and an agent.
- Print only the error-looking lines: test runners and Nix already put their summary at the end, and a filter can drop the line that matters.

## Consequences

- A cause above the last 80 lines is in the log, not the output: `cc-verify` tells the agent to search the log instead of running verify again.
- Live progress at a terminal comes from `./cc watch` or `--full`, not from the default output.

## Revisit when

Failures regularly need more than the last 80 lines, or a gate's useful output is at its start.
