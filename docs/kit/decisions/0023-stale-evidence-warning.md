---
id: kit.decision.0023-stale-evidence-warning
title: A note whose evidence changed after verified_at gets a warning, not a failure
status: accepted
summary: ./cc check and ./cc validate list every active, accepted or draft note whose in-repo evidence was committed after the note's verified_at, from one git log over all evidence paths, where the commit that first adds a path is not a change; it is a warning because most such changes leave the note true, and it is skipped where there is no full Git history (the Nix sandbox, a shallow clone), and a CC skips docs/kit, which only the template can fix.
verified_at: 2026-11-15
evidence:
  - scripts/validate-knowledge.py
  - scripts/validate_knowledge_test.py
  - cc
  - .github/workflows/cc-check.yml
relations:
  - docs/kit/index.md
  - docs/kit/rules/knowledge-layout.md
  - docs/kit/decisions/0002-atomic-linked-notes.md
  - docs/kit/decisions/0017-kit-and-project-layers.md
  - docs/kit/decisions/0021-cc-check-in-ci.md
  - docs/kit/decisions/0022-deterministic-checks-before-review.md
---

# A note whose evidence changed after verified_at gets a warning, not a failure

## Context

Every note names its `evidence` and the day it was last checked (`verified_at`), but the validator only checked that the evidence paths exist. A note could describe code that has since changed and nothing would say so. Asking a model to compare every note with the code is costly and varies between runs; the history already records when each evidence file last changed, so the drift signal is free and deterministic ([0022](0022-deterministic-checks-before-review.md)).

## Decision

- `scripts/validate-knowledge.py --stale` lists `note: evidence 'path' changed on DATE, after verified_at DATE` for each active, accepted or draft note whose evidence path was committed on a later day than `verified_at`. A directory counts as changed when any file under it changed. Superseded and archived notes are not maintained, so they are skipped.
- One `git log --name-only` over all distinct evidence paths gives every date, so the cost does not grow with the number of notes.
- The oldest commit that touches an evidence path only adds it, so it does not count as a change. `cc-kit new` copies the seed and the first commit dates every seed file to that day; without this rule every seed note in a new CC would be flagged although nothing in it changed. A note cannot be verified against a file that did not exist yet, so nothing real is lost.
- Only paths Git tracks have a date: URLs, ignored paths (`repos/`, `worktrees/`) and untracked files are skipped.
- A CC (it has `control-center.json`) skips notes under `docs/kit/`: it never edits the kit ([0017](0017-kit-and-project-layers.md)), so it could not act on them, and a kit update would leave warnings nobody there can clear. The template, which owns the kit, checks kit notes.
- It never fails. Many changes (a refactor, a new option) leave a note true; the warning asks for a look, and the fix is to re-check the note and refresh `verified_at`, or correct it.
- `./cc check` prints it after `nix flake check`, on the host, because the Nix source copy has no `.git`. `./cc validate` prints it too. A shallow clone is skipped, since every file there dates to its one commit, so CI checks out the full history.

## Options considered

- Fail the check: every unrelated edit to an evidence file would block a merge until someone bumped a date, which teaches people to bump dates without looking.
- One `git log -1` per path: same answer, one process per path; slow on large catalogs and on `/mnt/c`.
- A model reading notes against the code: catches meaning, not just timing, but costs a call per note and is not repeatable; it can still be run on the notes this warning lists.

## Consequences

- A CC whose notes were never refreshed sees a long list at first; it shrinks as notes are re-verified.
- Evidence in project repos is a permalink URL, so it is never flagged; drift there is caught by the project's checks.

## Revisit when

The warning is routinely ignored, or notes start citing evidence at a granularity (functions, line ranges) that a per-file date cannot track.
