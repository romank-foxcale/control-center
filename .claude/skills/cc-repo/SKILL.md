---
name: cc-repo
description: List, show, add (by pasting a link), create (a new repo with no remote yet), remove or change repositories in the CC catalog with ./cc repo: main branch, link, kind (project or reference), platforms, stack, status. Use when the user asks which repos the CC has, or adds, creates, removes or changes one.
---

# CC repo

First read `../_shared/cc-cli.md` and follow it.

## Commands

| Request | Command |
|---|---|
| Which repos are there? | `./cc repo list` |
| Show repo X | `./cc repo show <id>` |
| Add a repo (pasted link) | `./cc repo add <url> --targets <platform>... --stack <language>... [--branch <name>] [--role "<role>"] [--id <id>] [--clone]` |
| Start a new repo that does not exist yet | `./cc repo new <id> --targets <platform>... --stack <language>... [--branch main] [--role "<role>"]` |
| Add a PoC/demo used only as guidance | `./cc repo add <url> --kind reference [--branch <name>]` |
| Remove repo X | `./cc repo remove <id>` |
| X's main branch is develop | `./cc repo set-branch <id> develop` |
| X moved | `./cc repo set-remote <id> <url>` |
| X is only a reference / now a real project | `./cc repo set-kind <id> reference\|project` |
| X now ships on windows and linux | `./cc repo set-targets <id> windows linux` |
| X is written in Java and Kotlin | `./cc repo set-stack <id> java kotlin` |
| Change its status | `./cc repo set-status <id> <discovered\|adapted\|verified>` |
| X cannot be gated here | `./cc repo set-status <id> blocked --reason "<why>"` |
| What needs a look (repos with no remote, blocked repos) | `./cc repo attention` |

## Rules

- `add` derives the id from the link (`Acme-Shop-Backend` → `acme-shop-backend`) and reads the main branch from the remote; pass `--branch` only when the user names another one, or when the remote cannot be reached.
- Kinds: `project` (default) is built, verified and changed through feature worktrees; `reference` is read-only guidance such as a PoC or demo. It needs no targets, stack or adapter, is skipped by `./cc verify`, and `./cc worktree` refuses it. Ask which kind when the user's words leave it open.
- Targets are the platforms a project repo ships on: `windows`, `linux`, `macos`. Always ask the user; never infer them from CI or the build setup. `set-targets` replaces the whole set.
- After `set-targets`, every target needs a gate in the repo's Nix adapter (`targets.<platform>`); if one is missing, say so and add it through the adapter, then run `./cc verify <id>`. Changing the targets of a verified repo resets it to `adapted`.
- The stack is the repo's languages in lowercase (`java`, `python`, `typescript`, `csharp`, `cpp`, `rust`, ...). Read it from the code when it is unambiguous and confirm it with the user.
- `remove` is **destructive** for this CC's catalog: confirm first. It refuses while a feature worktree uses the repo, and never deletes the base clone in `repos/`; `./cc repo strays` then reports it until it is deleted.
- `set-branch` checks that the remote has the branch (for a repo with no remote, the local clone); new feature worktrees start from it.
- `new` creates an empty project repo with `"remote": null` (docs/kit/decisions/0034-new-project-cc.md). Its clone is the only copy of its history: say so, and offer `./cc backup` or a remote. `set-remote` on it adds `origin` and accepts only an empty remote or one that already has the local history; tell the user to create the remote repository empty, without a README. Creating a repo is onboarding too: hand over to "New project repos" of `grill-cc-bootstrap`.
- `--clone` also clones into `repos/<kind>/<id>`; a reference clone is made read-only on disk. Ask before cloning.
- `set-kind` moves the clone between `repos/project/` and `repos/reference/` and switches its read-only protection; it refuses to make a repo a reference while a feature worktree uses it.
- `update` fetches the base clones and fast-forwards reference clones, lifting their protection only for the pull. Never `chmod` a reference clone yourself.
- Status flow: `discovered` → `adapted` once it has a Nix adapter → `verified` once `./cc verify <id>` passed on every target in this conversation. Otherwise set `blocked` with `--reason` and report the reason.
- Adding a `project` repo is onboarding: after `add`, hand over to the "Repository grilling" of `grill-cc-bootstrap` for that repo (clone, adapter, a gate per target, `./cc verify <id>`) unless the user wants only the catalog entry. Writing the Nix adapter belongs to that skill; this one only runs catalog commands.
- In an unbootstrapped template clone, `repo` commands fail on a missing `control-center.json`; point the user to `grill-cc-bootstrap`.
