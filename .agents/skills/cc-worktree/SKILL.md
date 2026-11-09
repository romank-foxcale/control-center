---
name: cc-worktree
description: Create, extend, inspect, land or remove feature worktrees under worktrees/<feature>/<repo>_wt by running ./cc worktree. Use when the user wants to start work on a feature or task in one or more repos, add a repo to a running feature, see the state of a feature's worktrees, finish a feature on a repo with no remote yet, or clean a feature up.
---

# CC worktree

First read `../_shared/cc-cli.md` and follow it.

## Commands

| Request | Command |
|---|---|
| Start feature X in repos A, B | `./cc worktree create <feature> <repo>... [--branch <name>]` |
| Start coding on feature X | `./cc open <feature> [--tool claude\|codex]` (the user runs it in their terminal; it starts a new session) |
| Also change repo C in feature X | `./cc worktree add <feature> <repo>... [--branch <name>]` |
| Finish feature X on a repo with no remote yet | `./cc worktree land <feature> <repo> [--unverified "<reason>"]` |
| State of feature X | `./cc worktree status <feature>` |
| Clean up feature X (**destructive**) | `./cc worktree remove <feature> [<repo>...]` |

## Rules

- The feature name is the plan's task id when a plan exists; check `./cc plan list` before inventing one.
- Repo arguments are catalog ids; check `./cc repo list` when unsure. Include only repos the task will change, never read-only dependencies.
- The branch defaults to the feature name. Pass `--branch` only when the user names one.
- Never edit files in the base clones under `repos/`; all feature changes happen in the `_wt` worktree.
- Before `remove`, run `status` and show it. `remove` refuses dirty worktrees and unpublished commits; report that, and never force it, clean, stash or reset to get past it. Branches are preserved after removal. Removing a feature's last worktree also removes the feature folder: its council runs (`.cc-runs/`) move to `.cc-local/runs-archive/<feature>/`, which the output names, and any other file left in the folder makes `remove` refuse before it changes anything. Report that file to the user; never delete it yourself.
- `land` is only for a repo with no remote (`./cc repo new`; docs/kit/decisions/0034-new-project-cc.md). It fast-forwards the base clone to the feature branch, so run `./cc feature <feature> verify` first. When it refuses because the base moved, merge or rebase the default branch in the worktree, verify again, then land. Pass `--unverified "<reason>"` only after the user agrees that no gate can run; never use it to skip a failing gate. A repo with a remote goes through push and a pull request instead.
- After creating a feature, tell the user to start coding with `./cc open <feature>`, never by opening a session inside the worktree: only a session started in the CC has the CC's skills and rules. A session can edit the worktree only if it was started that way, or in the CC root with the worktree added.
- To build or check the feature's code, hand off to `cc-verify`.
