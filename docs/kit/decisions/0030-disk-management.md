---
id: kit.decision.0030-disk-management
title: A CC shows where its disk space goes and frees Nix space without touching CC state
status: accepted
summary: ./cc doctor --disk is a read-only report (Nix store, this CC's GC roots, .cc-local state against declared and undeclared data, caches, three nix.conf keys, the nixpkgs pin, the WSL disk file); ./cc gc removes this CC's result links and collects old generations, dry run by default; ./cc backup copies Git history, uncommitted files, kit state, every project clone's branches and changed worktree files to a folder outside the CC (a Windows drive on WSL), and with --push sends the same files as one replaced commit to the cc-backup branch of the CC's origin, only when GitHub says it is private; ./cc relock moves only the locked nixpkgs to the kit's recommended rev in nix/kit-nixpkgs.json. No command deletes anything in .cc-local, edits nix.conf or compacts a disk.
verified_at: 2026-11-01
evidence:
  - cc
  - scripts/disk.py
  - scripts/disk_test.py
  - scripts/store_gc.py
  - scripts/store_gc_test.py
  - scripts/backup.py
  - scripts/backup_test.py
  - scripts/relock.py
  - scripts/relock_test.py
  - scripts/validate-control-center.py
  - scripts/validate_control_center_test.py
  - templates/control-center.json
  - nix/kit-nixpkgs.json
  - flake.nix
relations:
  - docs/kit/index.md
  - docs/kit/rules/local-data.md
  - docs/kit/decisions/0003-template-as-seed.md
  - docs/kit/decisions/0017-kit-and-project-layers.md
  - docs/kit/decisions/0027-python-gate-venv-cache.md
  - docs/kit/decisions/0028-verify-before-push.md
  - docs/kit/decisions/0029-risk-council.md
---

# A CC shows where its disk space goes and frees Nix space without touching CC state

## Context

On one Windows machine (2026-10), WSL used about 120 GB: a 62 GB Nix store and a 24 GB `.cc-local` in one CC, where under 15 MB was CC state and the rest was rebuildable data mixed in with it. Three CCs locked three different nixpkgs revs, so the store held a copy of every tool per CC. Nothing collected garbage: `min-free` was 0 and nobody ran `nix-collect-garbage`. Three days after a 54.7 GiB collection, the store had grown back to 27 GiB. WSL's disk file does not shrink when files inside it are deleted, and WSL disables sparse mode for corruption risk, so making it smaller is a manual Windows step that should come after a backup.

A risk council ([0029](0029-risk-council.md)) audited the plan before it was built. Its findings set most of the limits below.

## Decision

- **`./cc doctor --disk`** prints, section by section: store size and free space, this CC's GC roots, `.cc-local` kit state, each `localData` entry against its budget with what a rebuild needs, undeclared folders over 100 MiB, scripts that Git does not track, caches (the kit's gate venvs of [0027](0027-python-gate-venv-cache.md) plus `localCaches`), `min-free`/`max-free`/`auto-optimise-store`, the nixpkgs pin against `nix/kit-nixpkgs.json`, the age of the newest backup and, on WSL, free space inside the distro, the disk file's size from the Lxss registry `BasePath` of this distro only, and free space on the Windows drive.
  - It is read-only and never locks the flake. Plain `./cc doctor` does not walk anything.
  - It prints only the three nix.conf keys, as a number or boolean: a user nix.conf can hold a GitHub token.
  - Its guidance names `/etc/nix/nix.conf`: a user that is not trusted cannot change daemon settings.
  - It never calls data safe to delete. A walk stops after 20 s and says so; Windows folders are measured by `powershell.exe`, because a walk through `/mnt/c` reads about 1 MiB of metadata per second.
- **`./cc gc`** is a dry run unless `--apply` is given.
  - It removes a link only if all of these are true: the link is a symlink named `result` or `result-N`, it points into `/nix/store`, it is inside this CC but not in `.cc-local`, and Nix lists it as a root.
  - It then runs `nix-collect-garbage --delete-older-than <N>d` (default 14) as the user.
  - It refuses `--apply` while `nix build`, `nix flake`, `nix run` or `nix-build` of the user runs; an open `nix develop` shell does not block it.
  - It says that collection is store-wide.
- **`./cc backup [--to DIR] [--push]`** writes `<DIR>/<cc name>/<stamp>/` with a verified bundle of every branch and tag (not `--all`, which would put the previous `cc-backup` inside the next backup), the files that differ from HEAD or are untracked, `.cc-local` without `data/` and without other folders over 100 MiB (listed instead), a verified bundle of every branch of each project clone in `repos/project/`, and the files of each feature worktree that differ from its HEAD or are untracked. On one machine (2026-11-01), 36 worktree commits were on no remote; the clone bundles keep them.
  - On WSL the destination must be on a Windows drive; a destination inside the CC is refused.
  - It keeps the newest `backup.keep` (default 5).
  - With `--push` (or `backup.push: true`; `--no-push` wins), after the local backup is complete, it pushes the same files to the branch `cc-backup` of the CC's `origin`:
    - only when `gh repo view` says the repository is `PRIVATE`; otherwise, with no `origin`, or with a file over 95 MB (GitHub refuses 100 MB), it refuses and exits 1, and the local backup stays;
    - as one commit with no parent, built with a temporary index and force-pushed, so the CC's HEAD, index, working tree and branches do not change, and the repository does not grow with each backup;
    - `runs/` (agent transcripts with private code) goes too: a private repository of the CC's own owner is the boundary. A public repository is never a backup target;
    - `cc-backup` holds no code tree that a check tested, so this push needs no verify stamp ([0028](0028-verify-before-push.md) is about code branches).
- **`./cc relock`** runs `nix flake lock --override-input nixpkgs github:<owner>/<repo>/<kit rev>`, then a plain lock. If any other lock node changed or the rev moved again, it puts `flake.lock` back byte for byte. It never commits.
- **`control-center.json`** may declare `localData` (name, path under `.cc-local/data/`, `budgetGB`, `rebuild` app or null, `rebuildNeeds`), `localCaches` (name, path) and `backup` (`path`, `keep`, `push`). All are optional. Machine paths start at `~/`, `$HOME/`, `$XDG_CACHE_HOME/`, `%LOCALAPPDATA%` or `%USERPROFILE%`, never at an absolute path. The rule for what goes where is [local data](../rules/local-data.md).
- **Not in the kit**: compacting a disk, editing nix.conf, root collection, moving any CC's existing data, and a binary cache (a later decision, after a spike).

## Options considered

- A `--delete-older-than` default only: on a machine with one profile generation it frees the same as `-d`, and keeps rollbacks where they exist.
- Printing `nix config show`: one command, but it can print an access token.
- Sparse VHD mode: WSL disables it for corruption risk.
- A flake input on the kit for nixpkgs: exact sharing, but every CC would depend on the kit repo at evaluation time; a recorded rev and a warning keep CCs independent ([0003](0003-template-as-seed.md)).
- Backups as commits on `main` or as a growing branch: the repository would grow by megabytes with every backup; one replaced commit on `cc-backup` keeps only the newest on GitHub, and the local folder keeps the older ones.
- One repository with a folder per CC for backups: a CC's Git root is the CC root, so each CC pushes to its own private repository.
- Moving data in the kit: data paths are fixed in each CC's project apps, so a move belongs to that CC's change, together with its apps.

## Consequences

- On the machine that prompted this, `./cc gc --apply` freed 21.3 GiB (27.4 GiB to 5.9 GiB). The `.cc-local` of three CCs did not change, and `./cc check` passed afterwards. It took the deleted tools from the cache again.
- Collection is store-wide: other CCs on the machine lose their unrooted paths too. Their next check downloads or builds them again, and private inputs need a working token.
- A relock can change tool versions and fixed-output hashes (for example plugins pinned to a package version), so the CC runs `./cc check` and `./cc verify` before it commits the new lock.
- Existing CCs get the commands only when they take this kit change ([0017](0017-kit-and-project-layers.md)). Until then their data stays undeclared.
- Not run on macOS, native Linux or a machine with several WSL distros; fixture tests cover those paths.

## Revisit when

WSL's sparse mode is safe again, Nix gains per-flake garbage collection, or a binary cache decision changes what a rebuild costs.
