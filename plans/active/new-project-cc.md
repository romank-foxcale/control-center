# new-project-cc: A CC can start a new project with no repos

Lifecycle: active
Planning status: accepted

## Outcome

A Control Center serves "one new project or many existing repos". The setup interview asks first whether the user has existing repos, starts a new project, or both. For a new project the CC creates an empty local Git repo under `repos/project/<id>` with no remote, records it in the catalog, and lets features run on it. A remote is added later with `./cc repo set-remote`; until then a verified feature lands through a local merge command. Existing CCs and the existing-repo flow keep working unchanged.

## Non-goals

- No code skeleton or project generator: the CC creates an empty repo only; the first feature adds the code, the adapter and the gates.
- No native gate backend: that is milestone 2 (`docs/kit/decisions/0033-native-core-nix-optional.md`). On a machine without Nix a new repo's gates stay unrun.
- No creation of a remote repository on GitHub or another host.
- No change to the per-CC topology (`docs/kit/decisions/0016-per-cc-repos-and-worktrees.md`) or to the migration mode.
- No new descriptor `schemaVersion`: existing descriptors stay valid as they are.

## Evidence and current behavior

- Positioning assumes existing repos: `AGENTS.md` line 3 ("the map and executable control plane for the repositories in its catalog"), `README.md` intro and "Get started" step 3, `GUIDE.md` line 3 and section 4 "First session: the setup interview".
- `scripts/repositories.py` `command_add` (lines 167-222) requires a remote URL, reads the default branch with `git ls-remote`, and writes a non-empty `remote`. `set-remote` (line 368) accepts only a URL.
- `scripts/validate-control-center.py` lines 116-121 require `remote` to be a non-empty string; lines 482-488 make a `ready` CC need at least one repository and every project repo `verified`. Repository statuses are `discovered`, `adapted`, `verified`, `blocked`; there is no field for a blocked reason.
- `scripts/verify.py` lines 180-182 fail a project repo with no targets; `override_args` (line 44) overrides flake inputs only for features, so a base `./cc verify` needs a flake input pinned to a remote (`docs/kit/rules/project-adapters.md`: "The remote source is pinned in flake.lock").
- `scripts/worktrees.py` `select_base_ref` (line 108) falls back to the local branch when there is no `origin` (works without a remote). `unpublished_commits` (line 265) counts commits since `baseHead` when the branch has no upstream, so `./cc worktree remove` refuses forever on a repo with no remote.
- `.agents/skills/grill-cc-bootstrap/SKILL.md` "New CC" step 5 builds a repository inventory and step 6 starts with `./cc repo add`; "Repository grilling" reads the stack from existing files and CI. `BOOTSTRAP.md` acceptance criterion 2 requires "a pinned source" for every repo. `docs/kit/rules/bootstrap.md` "State" defines `ready`.
- `scripts/rule_hooks.py` line 643 holds the session-start message for `discovery`; `install/cc-kit` lines 30-31 hold neutral opening prompts.
- Every project gate is a Nix flake output in milestone 1 (decision 0033).

## Affected scope

| Repository | Worktree | Read/write | Interfaces |
| --- | --- | --- | --- |
| command-center kit (this repo) | git branch `new-project-cc` cut from `redefinition`; PR targets `redefinition` | write | `./cc repo new`, `./cc repo set-remote`, `./cc worktree land`, repository descriptor (`remote` may be `null`, new `blockedReason`), `ready` rule, setup skill |

## Invariants and decisions

- D1 Interview branch: "New CC" asks one question before the inventory: existing repos, a new project, or both. Both paths may be used in one CC; a repo can always be added later with `repo add` or `repo new`.
- D2 Creation: `./cc repo new <id> --targets <platform>... --stack <language>... [--branch main] [--role <role>]` runs `git init -b <branch>` in `repos/project/<id>`, makes one empty initial commit (so worktrees have a base), and writes a descriptor with `"remote": null` and status `discovered`. It refuses an id that exists in the catalog or a non-empty folder.
- D3 Remote later: `./cc repo set-remote <id> <url>` on a repo whose `remote` is `null` sets `origin` in the base clone and records the URL. From then on the repo follows the normal push and PR flow, and the local merge command refuses it.
- D4 No skeleton: the repo stays `discovered` until the first feature adds code, the adapter and a gate per target. While any project repo is `discovered` or `adapted`, the CC stays `adapting`.
- D5 `ready`: every project repo is `verified`, or `blocked` with a non-empty `blockedReason` (for example "no native gate yet: needs Nix or milestone 2"). An empty catalog is never `ready`. `./cc repo set-status <id> blocked` requires `--reason`.
- D6 Local merge: `./cc worktree land <feature> <repo>` works only for a repo with `remote: null`. It is fast-forward only (`git merge --ff-only` in the base clone): when the base moved, it refuses and tells the user to rebase or merge in the worktree and verify again. It refuses a dirty worktree or a dirty base clone, never squashes or rebases, and leaves the base clean when it fails. Stamp rule: when the repo has an adapter with a gate, `land` requires a passing `./cc feature <feature> verify` stamp for the feature tree; when there is no gate or no Nix, it requires an explicit `--unverified "<reason>"` from the user and prints it (S2 confirms or changes this; decision 0034 records the result). "Published" for `worktree remove` means "an ancestor of the local default branch" when `remote` is `null`.
- D9 Safety of the only copy (R1): `repo new`, `repo remove` and `./cc doctor` warn for every `remote: null` repo: "only copy on this disk; last backup: <date or never>". The setup skill offers `backup.path`.
- D10 `ready` needs at least one `project` repo (R12), and `./cc verify` and `./cc doctor` list `blocked` repos whose gates can run on this host (R5), so `ready` never reads as "tested".
- D11 `repo new` writes `.gitattributes` with `* text=auto eol=lf` in the initial commit (R11, pending S4), checks the Git identity before `git init`, and validates the id before any write.
- D12 `set-remote` on a null-remote repo adds `origin` and checks the remote first: it refuses unless the remote is empty or its default branch is an ancestor of the local default branch; it writes the descriptor last, so a retry is safe (R6, R8).
- D13 Every reader of `remote` handles `null`: `repo update` skips with a note, `set-branch` checks local refs, `prs`, `lessons`, `repo list` and the settings app show "none (local)" (R9, R10).
- D7 Positioning: "A CC is for one new project or many existing repos" in `AGENTS.md`, `README.md`, `GUIDE.md`.
- D8 A kit decision `docs/kit/decisions/0034-new-project-cc.md` records D1-D7 and amends 0018 (onboarding) and the bootstrap rule.
- Invariant: no absolute local path is ever committed; a repo with `remote: null` gets its flake input only through `--override-input` at run time.
- Invariant: existing descriptors (non-null `remote`, no `blockedReason`) validate exactly as before, except that a `blocked` project repo in a `ready` CC now needs a reason.

## Open questions

| Question | Impact | Owner/next evidence |
| --- | --- | --- |
| How does a repo with `remote: null` declare its flake input, so that every Nix call evaluates without a committed absolute path? | Decides WP4; a wrong choice breaks every Nix command of the CC, also for repos with a remote | Spike S1 |
| Which minimum Nix version does the kit support (relative `path:` inputs changed in Nix 2.26; 2.28 had a regression, per the audit, not fetched)? | S1 must run on it; `doctor` checks it | S1 |
| Does the stamp rule in D6 hold on a host without Nix and through the push hook after `set-remote`? | Safety of `land` and of the first push | Spike S2 |
| Does a Windows Git with `core.autocrlf=true` break a WSL Nix gate on a new repo? | D11 | Spike S4 (assumption R11) |

Risk audit: run `20261108-223254-risk` (council `risk`, verdict GO-WITH-CONDITIONS). The run used a temporary `catalog/` and `control-center.json`, removed after the run, because the kit repo has none. Full register: `.cc-local/runs/20261108-223254-risk/risk.md` (local, not committed).

## Spike results (2026-11-09)

- S1 go: a scratch flake in WSL with `inputs.app = { url = "path:./nix/placeholders/app"; flake = false; }` locks the relative path (`"path": "./nix/placeholders/app"` in `flake.lock`), fails on purpose without an override (a guard throws when `CC_PLACEHOLDER` exists in the source), and passes with `--override-input app path:<base clone>`, which does not rewrite the lock. Exit codes checked on Nix 2.28.8, 2.30.6, 2.34.9 and 2.35.2. Minimum supported Nix: 2.28.
- S2 go, with a simpler stamp rule: stamps are keyed by Git tree (`scripts/verified.py`), and the push hook (`scripts/rule_hooks.py` `unverified_push`) looks the tree up for any repository in the CC. After a fast-forward `land`, the base HEAD tree equals the feature tree, so a stamp from `./cc feature <feature> verify` covers the push from the base clone after `set-remote`; without a stamp the hook asks the user. Rule: `land` needs a passing verify stamp for the feature tree, or `--unverified "<reason>"` from the user, which it prints and keeps in the feature manifest.
- S3: done as the `set-remote` test matrix in WP2 (empty, matching, unrelated and other-default-branch bare remotes).
- S4 confirmed: this Windows host has global `core.autocrlf=true`; a worktree of a repo without `.gitattributes` checks out `run.sh` as `w/crlf`, with `* text=auto eol=lf` as `w/lf`. D11 stays.

## Work packages

| ID | Outcome | Write scope | Depends on | Completion check |
| --- | --- | --- | --- | --- |
| S1 | Spike (R2, R3): one tracked placeholder per null-remote repo in `nix/placeholders/<id>` that fails on purpose (`throw "no remote: run through ./cc"`), plus run-time `--override-input <sourceInput> path:<base clone>` on every Nix call. Scratch CC with a trivial adapter; run `nix flake lock`, `./cc check`, base `./cc verify`, `./cc build`, a feature verify on the repo and a feature verify on a second repo, on the oldest supported Nix and the current Nix | scratch CC outside the repo; result and minimum Nix version written into this plan | - | Go: all commands pass, the placeholder never builds, `git grep -nE '[A-Za-z]:\\|/home/|/Users/'` finds nothing. Stop: re-plan with the user (for example "remote required before the first adapter") |
| S2 | Spike (R4, R7, R15): `land` and push. Scratch repo with an adapter; land a fast-forward feature, a conflicting feature and a non-fast-forward feature; `set-remote` to a `file://` bare repo; `git push` from the base clone through the hook, on a host with Nix and one without | scratch CC; result into this plan | - | Go: failed `land` leaves the base clean; the D6 stamp rule works or is corrected; the push gets a stamp or a recorded user decision. Stop: `land` refuses for ever, the base stays in a merge, or an untested tree can be pushed with no prompt |
| S3 | Spike (R6, R8): `set-remote` onto bare remotes that are empty, matching, unrelated (README commit) and on another default branch; then `repo update` and `worktree create` | scratch CC; result into this plan | - | Go: unrelated and other-branch cases refused; catalog and Git agree after each run. Stop: a worktree starts from the README commit |
| S4 | Spike (R11): on the Windows host, `git config --show-origin core.autocrlf`; commit a `.sh` file in a `repo new` repo and run it through a WSL Nix gate, with and without the D11 `.gitattributes` | scratch CC; result into this plan | - | Go: the gate gets LF with `.gitattributes`. If no problem appears without it either, drop D11's `.gitattributes` part |
| WP1 | Decision 0034 written (D1-D13 and the spike results, including the stamp rule and the minimum Nix version), linked from `docs/index.md`; 0018 and `docs/kit/rules/bootstrap.md` relations updated; `docs/kit/rules/project-adapters.md` says "a pinned remote source, or a tracked placeholder overridden with the base clone while the repo has no remote" | `docs/kit/decisions/0034-new-project-cc.md`, `docs/index.md`, `docs/kit/rules/bootstrap.md`, `docs/kit/rules/project-adapters.md` | S1-S4 | `./cc check` passes (knowledge validator) |
| WP2 | `./cc repo new` (D2, D9, D11), `set-remote` on a null remote (D3, D12), `set-status blocked --reason` storing `blockedReason` (D5), null-remote handling in `repo update`, `set-branch`, `repo remove` and `repo list` (D13) | `scripts/repositories.py`, `scripts/repositories_test.py` | S1, S3, S4 | New tests, each failing before the change: `repo new` makes a Git repo with one commit, `.gitattributes` and a descriptor with `remote: null`; refuses an existing id, a non-empty folder and `../x` (no folder made); fails clearly with no Git identity; prints the "only copy" warning; `set-remote` adds `origin`, refuses an unrelated remote, and a retry after an injected failure ends consistent; `set-status blocked` without `--reason` fails; `repo update` with one null-remote and one normal repo fetches the second and exits 0. Tests set `GIT_AUTHOR_*` and `GIT_COMMITTER_*` and run inside `./cc check` |
| WP3 | Validator accepts `remote: null` and applies D5 and D10 | `scripts/validate-control-center.py`, `scripts/validate_control_center_test.py` | - | New tests: `null` valid; absent key and `""` invalid; URL valid; `ready` with a blocked project repo without reason fails, with reason passes; `ready` with zero repos fails; `ready` with only reference repos fails; existing fixtures unchanged |
| WP4 | One override helper used by every Nix call (`verify.py`, `checks.py` Nix extra, `cc.py` Nix commands): every null-remote repo is overridden with `path:<base clone>`, a feature worktree wins over its base clone; the placeholder from S1 for `repo new` | `scripts/verify.py`, `scripts/checks.py`, `scripts/cc.py`, the helper's module and their tests, `nix/placeholders/` scaffolding as S1 decides | S1 | Tests: the helper gives the override for a null-remote repo and none for a repo with a remote; a feature on repo Y still overrides null-remote repo X; without Nix gates are "not run", never passed. With Nix (fixture): base `verify`, `./cc check`, `./cc build` and a feature verify on another repo pass and the placeholder never builds |
| WP5 | `./cc worktree land` per D6, and `remove` treats commits in the local default branch as published | `scripts/worktrees.py` and its test file | S2 | Tests: `land` fast-forwards the base; refuses a non-fast-forward feature, a dirty worktree, a dirty base and a repo with a remote; the base is clean after a failed `land`; the base HEAD tree equals the stamped tree; `--unverified` reason required without a gate; `remove` after `land` succeeds, without `land` still refuses |
| WP5b | Readers of `remote` outside the repo command handle `null` (D13), and `./cc doctor` and `./cc verify` report null-remote repos with no recent backup (D9) and blocked repos whose gates can run here (D10) | `scripts/prs.py`, `scripts/lessons.py`, `scripts/settings/` (state and app), `scripts/disk.py` or the doctor's module as found, and their tests | WP2 (descriptor shape only) | Fixture with `remote: null` passes through `prs`, `lessons`, `repo list` and `settings/smoke.py` with no crash and no "None"; doctor test flags a null-remote repo with no backup; verify test warns for a blocked repo with a gate on a Nix host |
| WP6 | Setup skill and onboarding text cover the new-project path: the first question (D1), `repo new`, no skeleton, the CC stays `adapting` (D4), `blocked` with reason on a no-Nix machine (D5), the offer of `backup.path` (D9), the bypass prompt explained (R14); `cc-repo` and `cc-worktree` skills list `repo new`, `set-status --reason` and `worktree land`; `BOOTSTRAP.md` criterion 2 allows "a pinned source, or a local repo with no remote yet"; the `discovery` and `adapting` hook messages do not assume repos, and `adapting` names the next step (R14) | `.agents/skills/grill-cc-bootstrap/SKILL.md`, `.agents/skills/cc-repo/SKILL.md`, `.agents/skills/cc-worktree/SKILL.md`, `BOOTSTRAP.md`, `scripts/rule_hooks.py` messages and `scripts/rule_hooks_test.py`; then `./cc agents sync` | WP1, WP2, WP5 | Hook test: a CC with a `discovered` repo and an open feature gets the "next step" text; `./cc agents sync` then `./cc check` pass |
| WP7 | Positioning per D7 | `AGENTS.md`, `README.md`, `GUIDE.md` | WP1 | `./cc check` passes; a read of each file finds no sentence that requires existing repos |
| WP8 | Integration: end-to-end run on a scratch CC | none in the repo (scratch folder) | WP2-WP7 | See "Integration and verification" |

S1-S4 run first and may run in parallel. WP3 needs no spike and may start at once. After the spikes, WP2, WP4 and WP5 change different files and may run in parallel; WP5b starts when WP2 fixes the descriptor shape. WP6 and WP7 change only text and may run in parallel after their dependencies. If two packages must both touch `scripts/cc.py` (command wiring and the WP4 helper), WP4 owns it and the other package hands its change to WP4.

## Integration and verification

1. `./cc check` on the branch passes (unit tests, notes, skill sync).
2. On a scratch CC made with `cc-kit new` (or `./cc bootstrap install`) from this branch, without Nix: `./cc repo new app --targets windows --stack python`; `./cc worktree create f1 app`; commit a file in the worktree; `./cc worktree land f1 app --unverified "no gate yet"`; `./cc worktree remove f1`; `./cc backup --to <scratch>` and a restore of the `app` clone give back all branch tips; `./cc repo set-status app blocked --reason "no native gate yet"`; set the CC to `ready`; `./cc bootstrap validate` passes. Then `./cc repo set-remote app <file:// URL of an empty bare repo>` and `git push` from the base clone works; `./cc worktree land` now refuses.
3. The same scratch CC with Nix (WSL or Linux): an adapter with one Linux gate for `app`; base `./cc verify`, `./cc check` and `./cc build` run against the base clone, and the placeholder never builds.
4. An existing CC fixture (descriptor with a remote) validates and verifies as before.

Report which of steps 2-4 ran and which did not (step 3 needs Nix).

## Failure, rollback and stop conditions

Failure modes from the risk audit (S = severity 1-5): R1 only copy of the history lost (S5; D9); R2 a Nix call builds the placeholder or passes without code (S4; WP4); R3 old Nix breaks relative inputs (S4; S1); R4 `land` lands an untested tree or refuses for ever (S4; D6, S2); R5 `ready` read as "tested" (S4; D10); R6 `set-remote` onto unrelated history (S4; D12, S3); R7 conflict leaves the base mid-merge (S3; D6); R8 half-written `set-remote` (S3; D12); R9 `repo update` stops at a null remote (S3; D13); R10 readers crash on `None` (S3; D13, WP5b); R11 CRLF in WSL gates (S3, assumption; S4); R12 `ready` with only reference repos (S2; D10); R13 no Git identity (S2; D11); R14 setup prompt during the first feature (S2; WP6); R15 squash breaks `remove` (S2; D6); R16 id escapes `repos/` (S2; D11).

- Stop if S1 finds no way to give a null-remote repo a flake input without a committed absolute path on the supported Nix versions: then re-plan (for example require a remote before the first adapter) with the user.
- Stop if S2 shows that `land` can leave the base in a merge or let an untested tree be pushed with no prompt.
- Stop if a change makes an existing CC fixture fail validation or verify.
- Rollback: the whole change is one PR into `redefinition`; revert the PR.

## Acceptance criteria

- A new user can answer "new project" in the setup interview and get an empty repo in `repos/project/<id>` with a catalog entry, without a remote URL.
- A feature on that repo can be created, landed locally and removed.
- `./cc repo set-remote` moves the repo to the normal push flow.
- A CC whose only project repo is blocked with a reason validates as `ready`; one with a `discovered` repo, zero repos or only reference repos does not.
- Every null-remote repo is reported as "only copy on this disk" until it has a remote, and every Nix command overrides its placeholder.
- `AGENTS.md`, `README.md` and `GUIDE.md` present the CC as for one new project or many existing repos.
- `./cc check` passes; integration steps 2 and 4 ran; step 3 ran or is reported as not run.

## Lifecycle closure

Not closed.
