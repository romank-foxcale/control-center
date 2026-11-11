---
name: cc-prs
description: List the open pull requests of the CC's catalog repos with ./cc prs, new and updated ones marked, and let the user pick one to review or summarize. Use when the user asks about new, open or pending PRs or what to review next.
---

# CC pull requests

First read `../_shared/cc-cli.md` and follow it.

## 1. List

Run `./cc prs --json`. It queries only the repos in this CC's catalog; never list PRs any other way (no `gh` over the clones in `repos/`).

Show one compact table, newest first: a `new` or `updated` mark (since the last listing), repo, `#number`, title, author, age, review state, and the `threads` field when it is set. `N to judge` means comments by others on the user's PR; `author replied` means replies to the user's comments on someone else's PR. Add a short English gloss in parentheses after a title in another language. When `base` differs from the repo's main branch (`./cc repo list`), note "stacked on `<base>`". Repeat any `warning:` lines from stderr. If the list is empty, say so and stop.

## 2. Choose

Ask which PR to work on as a choice question: in Claude Code with the question tool (one clickable option per PR); in Codex and other tools as a numbered list answered with a number.

- Option label: `<repo> #<number>`; description: title · author · age · review state.
- The question tool takes at most 4 options. With more PRs, offer the 4 most pressing, in this order: new or updated, then `review required`, then newest. Say that any other PR can be typed as `<repo> #<number>`.
- Never pick for the user.

## 3. Act on the chosen PR

Ask what to do, again as a choice question:

| Choice | What happens |
|---|---|
| Review it | Follow `cc-review`: only `[BREAK]`, `[SCOPE]` and `[KNOWLEDGE]` findings, or a CLEAN verdict with what was checked. Findings stay in the chat. |
| Judge the comments | Follow `cc-threads`: two anonymous sceptics judge each comment waiting on the user, or each reply to the user's comments, against the code. Verdicts and drafted replies stay in the chat. Offer this first when the PR's `threads` field is set. |
| Summarize it | Small PR: run `./cc prs show <repo> <number> --diff` and explain what changes and why, in a few lines. Big PR, or the user wants a page: follow `cc-changes`. |
| Check it out | `./cc prs checkout <repo> <number>`: the PR head as feature `pr-<repo>-<number>`, to run its gates or try it. Running it again follows the PR's latest push. |
| Nothing now | Stop. |

## Rules

- Read-only by default. Approving, commenting, requesting changes, merging or closing a PR is outward-facing: only when the user asks, after showing the exact text or action and getting a yes.
- Only repos in `./cc repo list`. A PR from a repo outside the catalog does not belong to this CC; say so instead of opening it.
- `reference` repos are listed like the others but are never committed to.
