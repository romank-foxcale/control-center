---
id: kit.rules.local-data
title: .cc-local holds CC state; bulky local data is declared and lives in .cc-local/data
status: active
summary: .cc-local keeps the kit's state (runs, runs-archive, threads, verified, lessons, hooks, eval, prs-seen.json), which nothing may delete and ./cc backup copies. Bulky data that a CC's apps produce goes to .cc-local/data/<name>/ and is declared in control-center.json localData with a budget, its rebuild app and what a rebuild needs. Code never lives in .cc-local.
verified_at: 2026-11-01
evidence:
  - scripts/disk.py
  - scripts/backup.py
  - scripts/backup_where.py
  - scripts/data.py
  - scripts/store_gc.py
  - scripts/validate-control-center.py
  - templates/control-center.json
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0030-disk-management.md
  - docs/kit/rules/executable-processes.md
---

# .cc-local holds CC state; bulky local data is declared and lives in .cc-local/data

## Invariant

`.cc-local/` is git-ignored, so what it holds exists nowhere else. It has two kinds of content, and they are never mixed:

- **Kit state**: `runs/`, `runs-archive/`, `threads/`, `verified/`, `lessons/`, `hooks/`, `eval/` and `prs-seen.json`. It is small, and much of it cannot be made again: run history, judged threads, the verify stamps that push protection reads. No command deletes it, and `./cc backup` always copies it.
- **Data**: anything a CC's apps produce or download, such as indexes, embeddings, features or replay sets. It lives in `.cc-local/data/<name>/` and has a `localData` entry in `control-center.json`:

```json
{ "name": "embeddings", "path": ".cc-local/data/embeddings", "budgetGB": 6,
  "rebuild": "embed-pool", "rebuildNeeds": "EC2 GPU host and the pool export, about 5 h" }
```

`rebuild` names the flake app that makes the data again, or is `null`. `rebuildNeeds` says what that takes: remote hosts, credentials, GPU hours, `nothing`. `./cc doctor --disk` reports an entry over its budget together with this text, so a warning never reads as "safe to delete". `./cc backup` does not put `data/` in its stamp backups.

Optional keys of an entry:

- `tags`: `irreplaceable` when a rebuild costs too much to risk (GPU hours, data that exists nowhere else): `./cc backup` mirrors it and exits 1 when it cannot, and only `./cc data remove`, confirmed by the user in their own terminal, deletes it or its mirror. `sensitive`: it never leaves this machine's drives; no upload or push may take it. No tag: rebuildable, not copied.
- `quiesce`: `pgrep -f` patterns of processes that write the entry (for example `opensearch`); while one runs, the entry is not mirrored.
- `purge`: the apps that delete the entry's data; for an irreplaceable entry such an app must refuse, and the hook denies it for agents.

```json
{ "name": "embeddings", "path": ".cc-local/data/embeddings", "budgetGB": 6, "rebuild": "embed-pool",
  "rebuildNeeds": "EC2 GPU host, about 9 h", "tags": ["irreplaceable", "sensitive"],
  "quiesce": ["ai-vectors-load"], "purge": ["ai-embeddings-purge"] }
```

## Rules

- A new app that writes bulky output writes it under `.cc-local/data/<name>/` and adds its `localData` entry in the same change.
- Code, notes and scripts never live in `.cc-local`: they belong in the CC's tracked `scripts/`, `nix/` or `docs/`, or in a project repo through a worktree. `./cc doctor --disk` lists scripts it finds there.
- A folder in `.cc-local` that is neither kit state nor declared data shows as undeclared once it passes 100 MiB, and `./cc backup` leaves it out with a line that says so. Declare it, move it, or copy it yourself.
- Moving existing data into `.cc-local/data/` is one change together with every app, purge app and plan that names the old path, made while no process uses the data (for example with OpenSearch stopped).
- `./cc backup --push` sends kit state, including `runs/`, to the private `cc-backup` branch of the CC's own repository; it refuses a repository that is not private. Data that should never leave the machine does not belong in kit state; it belongs in a `sensitive` data entry, which is never pushed.
- Agents never delete, move away or overwrite irreplaceable data or its mirror. When the user wants it gone, the agent gives them `./cc data remove <name> [--mirror]` to run in their own terminal.
- Before compacting a WSL disk or any other risky disk step, `./cc backup where` must exit 0: every irreplaceable entry has a complete mirror.
- `./cc backup where` is the one place that says where each backup and mirror is; nothing else records it by hand.
- Caches outside the CC (venvs, model downloads) are declared in `localCaches` with a path that starts at a per-user root, so `doctor --disk` measures them.
