# Control Center bootstrap

This file is the first instruction for a model started from the target CC folder.

## Determine the roots

- `template_root` is the folder containing this file; usually `<target_root>/cc_template`.
- `target_root` is the parent of `cc_template`; it becomes the CC.
- Do not change, move or commit `template_root` into the target CC. It is a read-only seed.

Immediately read `<template_root>/.agents/skills/grill-cc-bootstrap/SKILL.md` in full, along with the shared core it references. AI tools do not discover skills inside a child Git repository automatically.

## Determine the mode

If the current folder already has a `control-center.json` with status `discovery` and nothing besides the template files, the CC was already materialized by `cc-kit new`. In that case `template_root` equals `target_root`: do not run the installer, and continue grilling from step 3 of the "New CC" section of the `grill-cc-bootstrap` skill.

- `new`: `target_root` has no CC files apart from `cc_template`. Run from `target_root`:

  ```bash
  ./cc_template/cc bootstrap install .
  ```

- `migration`: `cc-kit migrate <folder>` clones the kit into `<folder>/cc_template` and starts this mode. `target_root` already has a CC, repository catalog, automation or knowledge in another format. Do not run the installer. First do a read-only inventory, a mapping and a staged migration.

If the mode is unclear, do not write to the target folder until it is clarified.

## Fixed topology

```text
Projects/
├── project1_CC/
│   ├── repos/                (git-ignored base clones of this CC's catalog)
│   │   ├── project/          (repos this CC changes)
│   │   │   ├── repo1/
│   │   │   └── repo2/
│   │   └── reference/        (read-only guidance, read-only on disk)
│   │       └── poc/
│   └── worktrees/            (git-ignored feature worktrees)
│       ├── feature1/
│       │   ├── repo1_wt/
│       │   └── repo2_wt/
│       └── feature2/
└── project2_CC/              (its own repos/ and worktrees/, never shared)
```

Relative to any `<project>_CC` (docs/kit/decisions/0016-per-cc-repos-and-worktrees.md):

- base checkouts: `repos/project/<repo>` and, read-only, `repos/reference/<repo>`;
- worktrees: `worktrees/<feature>/<repo>_wt`;
- nothing outside the CC folder belongs to it; a CC on the old shared `../repos` and `../worktrees` moves with `./cc migrate-layout`;
- the CC is never copied into a feature folder;
- local absolute paths never end up in Git;
- Nix receives feature sources through `--override-input` from the worktree manifest.

This topology can only be changed by a separate, explicit decision of the user.

In normal work use the executable interface, not manual `git worktree`:

```bash
./cc repo add <git-url> --role <role> --targets <windows|linux|macos>... --stack <language>... --clone
./cc repo new <id> --role <role> --targets <windows|linux|macos>... --stack <language>...   # a new project repo, no remote yet
./cc worktree create <feature> <repo>...
./cc feature <feature> verify
./cc worktree land <feature> <repo>     # only while the repo has no remote
./cc worktree status <feature>
./cc worktree remove <feature>
```

`worktree remove` never forces and refuses to work on dirty or unpublished state.

## Acceptance criteria

Do not declare the bootstrap finished until:

1. `control-center.json` and the catalog reflect the agreed model.
2. Every project repository has a pinned source (or, while it has no remote yet, its base clone through `./cc`), a Nix adapter, a check, and a gate for each target platform it ships on; or it is blocked with a `blockedReason`. A new repo with no code yet keeps the CC `adapting` (docs/kit/decisions/0031-new-project-cc.md).
3. Workflows consume declared outputs instead of duplicating build commands.
4. Unknown facts are explicitly marked and contradictions are resolved.
5. `./cc bootstrap validate` and `./cc verify` pass, or the unverified environment is explicitly recorded.
6. The target CC does not depend on the contents of `cc_template/`.

After that, tell the user that `cc_template/` can be deleted. Do not delete it yourself.
