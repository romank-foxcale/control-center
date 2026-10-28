---
id: kit.decision.0028-verify-before-push
title: Push only the tree a passing run tested, and run the change itself before calling it done
status: accepted
summary: A passing ./cc verify stamps the Git tree of the CC and of each verified feature worktree (./cc check stamps the CC's tree); the Claude Code hook asks before git push or gh pr create when HEAD's tree has no stamp. AGENTS.md adds that a passing gate proves only what it runs, so a change no gate exercises is run for real or gets a gate, and every report says what was and was not verified.
verified_at: 2026-10-28
evidence:
  - scripts/verified.py
  - scripts/verify.py
  - scripts/verify_test.py
  - scripts/rule_hooks.py
  - scripts/rule_hooks_test.py
  - cc
  - AGENTS.md
  - .agents/skills/cc-verify/SKILL.md
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0013-rule-hooks.md
  - docs/kit/decisions/0006-target-platform-gates.md
  - docs/kit/decisions/0022-deterministic-checks-before-review.md
---

# Push only the tree a passing run tested, and run the change itself before calling it done

## Context

"Done means verified" was instruction text. In one CC an agent added a pgAdmin service to a project's compose file and pushed it to a team PR without starting it; the service crashed on a teammate's machine. It had also said Docker was missing without checking. Two gaps let this through: nothing tied a push to a passing run, and a passing run did not mean the changed thing had run, because no gate started that service.

## Decision

- **Stamps.** When `./cc verify` passes, it records the Git tree of the CC and of each selected feature worktree in `.cc-local/verified/<tree>`. A passing `./cc check` records the CC's tree. The tree is the one Nix evaluated: every tracked file as it is on disk. So a run on uncommitted changes covers the commit made from exactly those changes, and any later edit gives a tree without a stamp.
- **Hook.** On `git push` and `gh pr create`, the Claude Code hook ([0013](0013-rule-hooks.md)) compares HEAD's tree with the stamps of the repository the command runs in (`git -C`, else the last `cd`, else the shell's folder). Without a stamp it asks the user, naming the commit and the command to run. The CC accepts a `check` or `verify` stamp; a project worktree needs `verify`, because `./cc check` runs no target gate ([0006](0006-target-platform-gates.md)). Repositories outside the CC and `push --delete` are not checked.
- **Rule.** AGENTS.md "Done means verified": a gate passing proves only what it runs. A change that no gate exercises is run for real or gets a check or app that runs it. A missing tool is checked with a command before it is reported. Every report of finished work says what was and was not verified.

## Options considered

- Rule text only (in AGENTS.md or a skill): the state that let the pgAdmin push through.
- Deny instead of ask: some pushes are safe without a run (a docs-only branch, a revert the user wants now); the user decides, with the reason in front of them.
- Key stamps by commit: a run before the commit would never count, and every commit would need a second run.
- A Git pre-push hook: it works for every tool, but setting hooks in project repos overrides their own ([0013](0013-rule-hooks.md)).

## Consequences

- An agent runs verify before pushing, and again after any edit; Nix caching makes a rerun on an unchanged tree fast.
- The hook checks HEAD. `git push origin other-branch` from a different HEAD is compared with the wrong tree; the user still sees the ask or the push goes through on HEAD's stamp.
- Stamps are local to one machine and are not proof for reviewers; CI stays the shared gate ([0021](0021-cc-check-in-ci.md)).
- The hook cannot tell whether a gate exercised the change. That part stays a rule, and reviews check it.

## Revisit when

Councils or CI need a stamp they can trust across machines, or pushes from other tools than Claude Code bypass the rule in practice.
