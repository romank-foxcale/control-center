---
id: kit.decision.0031-new-project-cc
title: A CC can start a new project whose repos do not exist yet
status: accepted
summary: A CC is for one new project or many existing repos. ./cc repo new makes an empty local repo with remote null and a tracked Nix placeholder that ./cc overrides with the base clone on every Nix call; set-remote connects it later to an empty remote; ./cc worktree land fast-forwards a verified (or user-excused) feature into it while it has no remote; ready means every project repo is verified or blocked with a written reason. This amends 0018.
verified_at: 2026-11-09
evidence:
  - scripts/repositories.py
  - scripts/repositories_test.py
  - scripts/worktrees.py
  - scripts/worktrees_test.py
  - scripts/validate-control-center.py
  - scripts/validate_control_center_test.py
  - scripts/verify.py
  - cc
  - nix/lib/default.nix
  - .agents/skills/grill-cc-bootstrap/SKILL.md
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0018-setup-starts-itself.md
  - docs/kit/decisions/0016-per-cc-repos-and-worktrees.md
  - docs/kit/decisions/0028-verify-before-push.md
  - docs/kit/decisions/0030-disk-management.md
  - docs/kit/rules/bootstrap.md
  - docs/kit/rules/project-adapters.md
---

# A CC can start a new project whose repos do not exist yet

## Context

The kit presented a CC only as a map over existing repositories: `./cc repo add` needed a remote URL, every descriptor needed a non-empty `remote`, adapters took their source from a flake input pinned to a remote, and `./cc worktree remove` refused a branch that was never pushed. A user who wants to start a new project with the CC had no path. A risk council (plan new-project-cc, run 2026-11-08, verdict go with conditions) and four spikes shaped the rules below.

## Decision

- **Positioning.** A CC is for one new project or many existing repos. The setup interview asks first which one it is; both may mix in one CC.
- **`./cc repo new <id> --targets ... --stack ...`** runs `git init` in `repos/project/<id>` with one first commit. That commit holds `.gitattributes` with `* text=auto eol=lf`: a Windows checkout with `core.autocrlf=true` otherwise gives WSL gates CRLF scripts (spike S4). The descriptor has `"remote": null`, only a project repo may have it, and it starts `discovered`. The CC adds no code skeleton: the first feature adds the code, the adapter and a gate per target, and the CC stays `adapting` until then.
- **Nix source of a repo with no remote.** `repo new` writes a tracked placeholder `nix/placeholders/<id>/CC_PLACEHOLDER`; the adapter's flake input is `{ url = "path:./nix/placeholders/<id>"; flake = false; }`, which locks as a relative path, so no absolute path is committed. `scripts/worktrees.py` `source_overrides` is the one place that builds the `--override-input` arguments of every Nix call `./cc` makes (`check`, `verify`, `build`, `run`, `show`): the base clone replaces the placeholder, and a feature's worktree replaces the base clone. `ccLib.mkProject` refuses a source that is the placeholder, so a Nix call outside `./cc` fails loudly instead of testing nothing. Tested on Nix 2.28.8, 2.30.6, 2.34.9 and 2.35.2 (spike S1); `./cc doctor` warns below 2.28.
- **Remote later.** `./cc repo set-remote <id> <url>` on a repo with no remote adds `origin` and accepts only an empty remote or one whose default branch is already in the local history; a remote created with a README is refused, because new features would start from that unrelated commit. It writes the catalog last, so a retry after a failure is safe.
- **Landing without a remote.** `./cc worktree land <feature> <repo>` works only while the repo has no remote. It fast-forwards the base clone's default branch to the feature branch and never merges, squashes or rebases: the base then holds exactly the tree the feature's verify tested (stamps are keyed by tree, [0028](0028-verify-before-push.md)), and a conflict can never leave the base clone mid-merge. When the base moved, the user merges or rebases in the worktree and verifies again. It needs a passing `./cc feature <feature> verify` stamp for the feature tree, or `--unverified "<reason>"` after the user agrees; the reason is kept in the feature manifest. `./cc worktree remove` counts commits that are already in the branch the feature started from as published.
- **`ready`.** Every project repo is `verified`, or `blocked` with a `blockedReason` (`./cc repo set-status <id> blocked --reason "<why>"`), and at least one project repo exists; reference repos alone are not a project. A new repo with no code yet has no gate to run, so it may be blocked with that reason.
- **Nothing hides.** `./cc doctor` and `./cc verify` print `./cc repo attention`: every repo with no remote is the only copy of its history on this disk (with the date of the last `./cc backup`, [0030](0030-disk-management.md)), and every blocked repo is named until it is verified.

This amends [0018](0018-setup-starts-itself.md): onboarding covers a repo the CC creates as well as one it clones.

## Options considered

- **Require a remote before the first feature:** simpler, but a user starting alone often has no hosting decided yet, and the CC would push them to create an empty GitHub repo first.
- **A code skeleton per stack in `repo new`:** gives the first gate something to run at once, but it chooses frameworks and layouts the user has not decided; the user chose the empty repo.
- **Merge commits in `land`:** lands a moved base without a rebase, but the merged tree was never tested and a conflict stops inside the base clone, where agents may not edit.
- **An absolute `git+file:` flake input:** works without overrides, but commits a machine path.

## Consequences

- A repo with no remote has its whole history in one git-ignored folder until it gets a remote: the warnings and `./cc backup` are the only protection.
- A CC whose catalog has a repo with no remote needs `./cc` for every Nix call; a bare `nix flake check` fails on the placeholder by design.
- `ready` no longer means "every gate passed": the setup is finished, and a blocked repo says why it is not gated.

## Revisit when

A user needs a skeleton per stack; or the minimum Nix version moves.
