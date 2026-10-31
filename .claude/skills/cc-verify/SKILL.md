---
name: cc-verify
description: Verify, check, build, run or inspect the CC and feature worktrees through Nix with ./cc verify, check, build, run, show, validate, doctor and ./cc feature, and manage disk use with doctor --disk, gc, backup and relock. Use when the user asks whether code works, passes or is ready to push, wants to build or run something, check the setup, or free disk space.
---

# CC verify

First read `../_shared/cc-cli.md` and follow it.

## Commands

| Request | Command |
|---|---|
| Does it work? Is it ready to push? | `./cc verify [<repo>...]` |
| Same for feature F | `./cc feature <feature> verify` |
| Do the tests actually catch bugs? | `./cc [feature <feature>] verify [<repo>...] --quality` |
| Quick pure checks only | `./cc check` / `./cc feature <feature> check` |
| Build / run target X | `./cc build <name>` / `./cc run <name> [args]` |
| What can I build, check or run? | `./cc show` |
| Validate the knowledge notes | `./cc validate` |
| Is my setup OK? | `./cc doctor` |
| Where does the disk space go? | `./cc doctor --disk` |
| Free Nix store space | `./cc gc` (dry run), then `./cc gc --apply` **destructive** |
| Back up the CC before risky disk work | `./cc backup [--to DIR]`; add `--push` to also send it to the private `cc-backup` branch on GitHub |
| Share one nixpkgs with other CCs | `./cc relock`, then `./cc check` and `./cc verify` before the commit |

## Rules

- "Works", "tested", "done" and "ready to push" mean `./cc verify` passed: every flake check plus the gate of every target platform the catalog declares (for example the windows gate on the real Windows host). `./cc check` alone does not cover host gates; never report a repo as working on a platform whose gate did not run and pass.
- A gate passing proves only what the gate runs. Before calling a change done, find what actually exercises it: a test, a gate, an app. If nothing does (a new compose service, a config file, a script), run it for real (`./cc run`, start the service, call the feature) or add a check or app that does, then report "verified: ... / not verified: ...". Check that a tool is missing with a command (`command -v docker`, `./cc doctor`) before saying so.
- A passing `./cc verify` (and, for the CC itself, `./cc check`) stamps the tree it tested. `git push` and `gh pr create` ask when HEAD's tree has no stamp. Committing exactly what passed keeps the stamp; any later edit needs a new run. When the hook asks, run verify; never ask the user to approve a push that was not verified without saying what was not.
- When a feature worktree is in play, use `./cc feature <feature> ...`; plain commands use the pinned sources, not the worktree. `./cc feature <feature> verify` covers exactly the repos in that feature.
- `--quality` adds each repository's `quality.mutation` gate (slow). `WARN: no gate` means the tests were not checked against injected bugs: say so explicitly, never present the result as tested quality. A failing mutation gate lists surviving mutants; the fix is tests that kill them (`tdd`), or marking a truly equivalent mutant with a reason.
- A `FAIL: no targets.<platform> gate` result means the adapter lacks the gate for a declared platform: report it; the fix is a gate in the adapter, never dropping the target.
- Take target names from `./cc show`; never guess them.
- `run` executes apps, which may use the network or containers. Confirm before running an app that deploys, publishes or touches production; production actions need a dedicated adapter.
- Never bypass Nix with `cmake`, `make`, `msbuild`, `dotnet`, `docker build`, `pytest` or similar to make something pass. A direct command is only for diagnosing a failure, and the fix goes into the Nix contract or the project.
- Verify prints one line per passing gate and the last 80 lines of a failing one, with the path of its full log under `.cc-local/runs/`. Report the final REPO/GATE/RESULT table and quote the first real failure; when the tail does not show the cause, search that log instead of running verify again. `--full` streams every line, for a person at a terminal.
- Verify takes minutes. When it runs in the background, also run `./cc watch` as a streaming watcher (the Monitor tool where available) and relay each gate start, result and stall (`⚠`, a gate silent for 3 minutes) to the user as it happens; the user must never have to ask how it is going.
- If the Nix part cannot run, run `./cc validate` and say explicitly that the Nix part was not verified.
- `./cc gc --apply` deletes store paths for every CC on the machine: show the dry run and get a yes first. Nothing deletes `.cc-local` state; never remove data there yourself because `doctor --disk` shows it over budget, and never run disk compaction, `nix.conf` edits or sudo for the user (docs/kit/rules/local-data.md).
