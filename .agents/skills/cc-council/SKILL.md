---
name: cc-council
description: Run, approve, reject or inspect multi-model coding, testing and debug councils with ./cc council, and change which provider and model each council role uses. Use when the user wants a council to implement, test or debug something on a feature, decides on a council verdict, asks about runs, or wants a role on another model.
---

# CC council

First read `../_shared/cc-cli.md` and follow it.

## Commands

| Request | Command |
|---|---|
| Implement X on feature F | `./cc council run coding --feature <feature> [--repo <repo>] --task "<task>"` |
| Write tests for X on feature F | `./cc council run testing --feature <feature> [--repo <repo>] --task "<task>"` |
| Find and fix bug X on feature F | `./cc council run debug --feature <feature> [--repo <repo>] --task "<symptom>"` |
| Go ahead / approve | `./cc council approve <run-id>` |
| Use proposal B instead / change something | `./cc council approve <run-id> [--pick <label>] [--note "<instruction>"]` |
| Stop this run | `./cc council reject <run-id>` |
| What runs are there? / show run R | `./cc council status [<run-id>]` |
| Are my subscriptions logged in? | `./cc doctor` |

## Rules

- The feature worktree must exist (`cc-worktree`) and be clean; the council refuses uncommitted changes. A plan work package makes the best task: pass it with `--task-file`.
- Write the task as an outcome with constraints and acceptance, not as a design: the proposers design. The council never sees this conversation: put every fact it needs into the task (paths, error text, decisions already made), never "as discussed" or "the bug above".
- For a debug council, describe the symptom exactly (input, expected, actual, platform, error text), never a suspected cause. The runner first runs verify and gives its output to the investigators; after approval the writer must produce a reproduction test that fails, and only then the fix. Stage `failed` with "does not reproduce the bug" means the chosen diagnosis was wrong; "fixes failed: question the design" means three fixes failed and the design needs a conversation, not a fourth attempt.
- A run stops after the verdict. Show the user the verdict and the hidden proposal authors, and wait for an explicit decision; never approve on the user's behalf.
- Runs take minutes. Run them in the background when the tool allows it and report the final stage line and the artifacts folder. The user must never have to ask for progress: right after starting or approving a run in the background, run `./cc watch <run-id>` as a streaming watcher (the Monitor tool where available) and relay each event to the user as it arrives, in one short line: steps, handoffs between agents (`⇄`), what agents say (`💬`), stalls (`⚠`, an agent silent for 3 minutes), errors and the end (`■`). Say a stall plainly ("the GPT proposer has been silent 4 minutes, last reading X") and keep waiting; it is a warning, not a failure. Re-arm the watcher if it expires before the end event. `./cc watch` without an id follows the newest unfinished run. `state.json` in the run folder holds the plan, the current step and each call's start and finish time.
- Councils verify with `--quality`. A `done` summary that ends in `WARNING: mutation gate missing` means the tests were not checked against injected bugs; repeat that warning to the user. "tests pass but miss injected bugs" means the mutation gate failed.
- Report a run's cost with its result: the `cost:` line it prints at the end (also in `./cc council status <run>`, and per run in the TOKENS column of `./cc council status`). "at least" means some calls stopped before their CLI reported tokens. Each call's own count is the last line of its log.
- Every writer attempt is a fresh process; the runner records each one as a section of `attempts.md` in the run folder (outcome, files changed, failed gates, key error lines, what the writer said) and gives all earlier sections to the next retry. `./cc council status <run>` lists the attempt outcomes. When a run retried or failed, summarize `attempts.md` for the user attempt by attempt rather than pasting verify logs. "stopped at its cap of N tool calls" means that call grew too long: it counts as a failed attempt; if it keeps happening, the task is too big for one call, so suggest splitting it before raising the cap.
- Final stages: `done` (verify passed; the user reviews `changes.diff` and commits), `verify-failed` (report `attempts.md`; for testing it means suspected bugs), `blocked-security` (report `security.md` and, when a reviewer's high finding blocked it, that `review-<role>.md` and the review authors the run printed), `rejected`, `failed`. Never commit, push or fix things after the run on your own.

## Roles and models

Roles are `catalog/agents/<role>.json`: `provider` (`claude`, `codex` or `cursor`), `model`, `access` (`read-only` or `write-worktree`), `skills`, and optionally `maxToolCalls` (default 100): the runner stops one call of the role past that many tool calls, because a call that fills its context window degrades. Councils are `catalog/councils/<name>.json` and name the roles for `proposers`, `judge`, `writer` and `security` (a list of reviewers; with several, the judge summarizes them), plus `approval` and `maxRetries`.

- Change a role with `./cc council set-role <role> [--provider claude|codex|cursor] [--model <model>] [--max-tool-calls N]`, and a council with `./cc council set <council> [--approval on|off] [--max-retries N]`; both validate and refuse an invalid change. Never edit the JSON by hand. List models with `./cc council models` (Codex: `codex debug models`; Cursor: `cursor-agent models`); Prefer full model ids such as `claude-opus-5-5`: aliases such as `opus` follow the newest model, so `./cc check` and `set-role` warn about them.
- Settings (roles and models, councils) can also be changed with the `cc-settings` skill in chat or `./cc settings` in a terminal.
- "How is it going?": run `./cc council status <run>` and summarize it: the progress bar, the current step, which agents are done or still working, the timers, and what each working agent did last (its log lines). Without a run id, `./cc council status` lists all runs with their progress.
- Proposers, judge and security must stay `read-only`; only the writer is `write-worktree`. Cursor roles can only be `read-only`: Cursor's write limits are not enforced by the CC. Validation enforces both.
- After updating `cursor-agent`, prove its read-only confinement still holds with `python3 scripts/council/providers.py probe cursor <model>` (it must print `no writes`).
- The `threads` council (kind `threads`) is not run with `./cc council run`. It names the `sceptics` and the `judge` that `./cc prs judge` uses on review threads (the `cc-threads` skill). Change its models with `set-role` on `sceptic-claude`, `sceptic-grok` and `sceptic-judge`. It has no approval or retries. With two or more sceptics it needs a judge, and using two sceptics from the same provider defeats the point.
- A council needs at least two proposers. Using the same provider for every proposer defeats the point; warn the user if they ask for it.
