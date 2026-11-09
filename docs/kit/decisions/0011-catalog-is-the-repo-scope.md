---
id: kit.decision.0011-catalog-is-the-repo-scope
title: The catalog is the CC's repo scope, managed by link
status: accepted
summary: A CC's repositories are exactly its catalog; its clones live in its own repos/ (0016) and are never scanned. Repos are added by pasting a link (main branch read from the remote), removed, re-branched and marked project or reference through ./cc repo and the settings panel; ./cc prs lists open PRs of catalog repos only.
verified_at: 2026-10-15
evidence:
  - scripts/repositories.py
  - scripts/repositories_test.py
  - scripts/prs.py
  - scripts/prs_test.py
  - scripts/settings/app.py
  - scripts/verify.py
  - scripts/worktrees.py
  - .agents/skills/cc-prs/SKILL.md
  - .agents/skills/_shared/cc-cli.md
relations:
  - docs/index.md
  - docs/kit/decisions/0016-per-cc-repos-and-worktrees.md
  - docs/kit/rules/worktrees.md
  - docs/kit/rules/project-adapters.md
---

# The catalog is the CC's repo scope, managed by link

## Context

Several CCs sat in one parent folder and shared `../repos/`. Asked for "new PRs", agents in both CCs looped `gh pr list` over every clone in `../repos/` and mixed the other CC's repos in. Nothing executable stated the scope, and the topology rule pointed at the shared folder. Separately, a repo's main branch is not always `main`, and some repos (a PoC) are guidance only.

## Decision

- The catalog (`catalog/repositories/*.json`, `./cc repo list`) is the only list of a CC's repos. Agents never enumerate clone folders; the shared skill note says so. Since [0016](0016-per-cc-repos-and-worktrees.md) each CC also keeps its clones in its own `repos/`, so the shared folder this note started from is gone.
- `./cc prs` lists open PRs of catalog repos only, newest first, marking PRs not seen or changed since the last listing (`.cc-local/prs-seen.json`, per machine). The `cc-prs` skill shows them and lets the user pick one from a choice list.
- `./cc repo add <url>` derives the id from the link and reads the main branch from the remote's HEAD; `remove`, `set-branch` (checked against the remote), `set-remote` and `set-kind` complete the set. The settings panel's Repos tab runs the same commands. `update` fetches the base clones.
- `kind: reference` marks read-only guidance: no targets, stack or adapter, skipped by `./cc verify`, refused by `./cc worktree`; its clone sits read-only in `repos/reference/` (0016).
- `/cc-settings` opens the `./cc settings` panel in a terminal (the desktop app's Terminal panel) rather than imitating it in the chat; the chat version remains for sessions that cannot open a terminal.

## Options considered

- Giving each CC its own `repos/` folder: isolates clones, but duplicates them and changes the agreed topology. Adopted later in [0016](0016-per-cc-repos-and-worktrees.md), once sharing proved to save little.
- A memory or prose note per CC: per machine and per agent, and does not survive the next agent.

## Consequences

- `./cc prs` needs an authenticated `gh` in the environment `./cc` runs in: Windows for a CC on the Windows drive, Ubuntu for a CC inside WSL.
- `remove` keeps the base clone and any Nix adapter: `./cc repo strays` then reports the clone until it is deleted, and adapter removal is a deliberate Nix change.

## Revisit when

A CC needs repos from a forge other than GitHub in `./cc prs`.
