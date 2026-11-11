---
id: kit.decision.0032-change-maps
title: A PR is explained as a change map that an agent fills as data into a fixed template
status: accepted
summary: ./cc changes collect writes deterministic facts (file classes, line ranges, symbols, test names) and a source-only patch for one catalog PR; the agent reads those, fills map.json (intent, groups by concern, before/after, reading order, risks, optional Mermaid) and ./cc changes render checks it against the facts and fills a fixed offline HTML template. The agent never writes HTML.
verified_at: 2026-11-11
evidence:
  - scripts/changes.py
  - scripts/changes_test.py
  - scripts/change_map/index.html
  - .agents/skills/cc-changes/SKILL.md
  - flake.nix
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0012-review-bar.md
  - docs/kit/decisions/0019-lean-agent-context.md
  - docs/kit/decisions/0022-deterministic-checks-before-review.md
---

# A PR is explained as a change map that an agent fills as data into a fixed template

## Context

A raw diff lists changes in file order. On a big PR (for example 38 files, +3.8k lines, more than half of them tests) a reader cannot see the intent, which changes belong together, or where to start. `cc-review` answers "should this merge", not "what does this change". An agent that writes a full HTML page for each PR spends about 8-12k output tokens on markup alone, and every page looks different.

## Decision

- `./cc changes collect <repo> <n>` writes `.cc-local/changes/<repo>-<n>/`: `facts.json` (deterministic: stats, commits, a class per file from path rules, changed line ranges, symbols, test names), `source.patch` (only `source` and `config` hunks) and a `map.json` skeleton. A filled `map.json` is never overwritten.
- The agent (skill `cc-changes`) reads the facts and the source patch, not the whole diff, and fills only `map.json`: intent, groups by concern, before/after rows, reading order, risk spots, an optional Mermaid diagram, and `reclass` for a wrong file class.
- `./cc changes render <repo> <n>` checks `map.json` against the facts (each source file in exactly one group, references inside changed lines, length limits), reports every problem at once, and fills the fixed template `scripts/change_map/index.html`. The page loads nothing from the network; Mermaid 11.4.1 is kept in `scripts/change_map/vendor/` and its sha256 is pinned in the test.
- Version 1 covers catalog PRs only, and the page is a local file.

## Options considered

- The agent writes the HTML: more output tokens, a different page each time, and a fix means rewriting the page.
- Terminal summary only: cheapest, but no diagram and no foldable groups. The renderer prints it as well.
- Mermaid from a CDN: no 2.5 MB file in the kit, but the page then needs the network.
- Full Python `ast` symbols: needs each file at the PR head; a regex on changed lines gives enough for orientation.

## Consequences

- Reading cost for a big PR drops to about half (source hunks and test names instead of the full diff); the output is a few kilobytes of JSON.
- The page layout changes in one place for every map.
- Path rules can misclass a file; `reclass` corrects it and the page shows the move.
- The kit carries a 2.5 MB vendored script.

## Revisit when

Feature-worktree diffs are needed, the maps should be shared as hosted pages, or the vendored Mermaid needs an update.
