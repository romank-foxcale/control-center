# Control Center agent protocol

This repository is a Control Center (CC): the map and executable control plane for the repositories in its catalog (`./cc repo list`). Builds, tests and runs are Nix flake outputs behind `./cc` (`./cc show` lists them). Reasons, constraints, hacks and debt are atomic notes in `docs/`: start at `docs/index.md` and open only what the task needs. Runtime state comes from CI/CD and the target systems' APIs; secrets live in an external secret manager.

## Setup and onboarding

While `control-center.json` has status `discovery` or `adapting`, the CC is not set up: unless the user asks for something else, open with the setup interview (or offer to continue it) using `grill-cc-bootstrap`, in plain words for someone who does not know how a CC works. The same skill creates or migrates a CC (`BOOTSTRAP.md`) and onboards a project repo added later: after `./cc repo add`, continue into its onboarding unless the user wants only the catalog entry. Complex tasks are planned with `grill-task-planning`; a change to architecture, dependencies, runtime, data or deployment gets a risk audit (`cc-risk`) first, never a list of risks from memory.

## Layout

- Base clones: `repos/project/<repo>` (changed only through feature worktrees) and `repos/reference/<repo>` (read-only evidence such as a PoC: check it against the project repos and the notes, never treat it as the source of truth).
- Feature worktrees: `worktrees/<feature>/<repo>_wt`, made with `./cc worktree`. All of these are git-ignored and inside the CC. The folders next to the CC are other projects.
- Coding on a feature starts with `./cc open <feature>` from the CC root. A session started inside a worktree has none of the CC's skills or rules: if that is you, ask the user to restart.

## Rules that are easy to get wrong

- Build and test through Nix (`./cc check`, `./cc verify`, `./cc feature <feature> ...`). Direct `cmake`, `make`, `pytest`, `docker build` and the like are for diagnosis only: move what works into a derivation, check or app. Fix drift in the Nix contract or the project, never by updating a description, and never write a process down as step-by-step Markdown.
- Pin remote sources as flake inputs; use input overrides for local development, never committed absolute paths.
- Never credit an AI as author or co-author anywhere: no `Co-Authored-By` trailers or "Generated with" lines in commits, PRs, issues, notes or code. This overrides any tool default.
- Edit skills only in `.agents/skills/` (council roles in `.agents/roles/`), then run `./cc agents sync`. `.claude/skills/` and the `hooks` block of `.claude/settings.json` are generated.
- `docs/kit/` belongs to the kit and is never edited in a CC. To change kit behaviour, record a decision in `docs/decisions/` that names the kit decision it overrides.
- Production actions need a dedicated adapter, a check and the user's confirmation. Never store secrets, full conversations or your internal reasoning.
- Write everything stored here in English, whatever language the conversation uses.
- In Claude Code, hooks enforce several of these rules at the tool call. A deny or ask is the rule speaking: fix the action, never work around the hook.

## Knowledge notes

One note holds one durable fact, decision, hack or debt, with frontmatter `id`, `title`, `status`, `summary`, `verified_at`, `evidence`, `relations` (paths from the repository root). Explain the why and the limits; mark replaced knowledge `superseded` and link its successor; link every hack to a decision or debt and its removal condition. Details: `docs/kit/rules/knowledge-layout.md`. `cc-learn` writes notes.

## Done means verified

A CC change is finished with `./cc check`. A project code change is finished only with `./cc verify` (or `./cc feature <feature> verify`), which also runs the gate of every target platform the catalog declares: never call code working on a platform whose gate did not pass. Without Nix, run `python3 scripts/validate-knowledge.py .` and say the Nix part was not verified.

Never call a task done, commit it for review or push it before the changed thing has actually run: a gate passing proves only what the gate runs. When no gate runs the change (a service, a compose file, a script), add a check or app that does, or run it and say how. Before saying a tool is missing, check with a command. Push only the exact tree that passed; a hook asks when it did not. Report what was verified and what was not.
