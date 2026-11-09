---
id: kit.decision.0013-rule-hooks
title: Claude Code hooks enforce the hard rules early; the gates still decide
status: accepted
summary: The AGENTS.md hard rules that a tool call reveals are enforced by Claude Code hooks at the moment of the call, and every rule that leaves an artifact is also gated by ./cc check or ./cc verify, so other tools and missed hooks are still caught.
verified_at: 2026-11-09
evidence:
  - scripts/rule_hooks.py
  - scripts/rule_hooks_test.py
  - scripts/hook_guard.py
  - scripts/hook_guard_test.py
  - cc
  - cc.cmd
  - scripts/cc.py
  - scripts/agent-configs.py
  - scripts/validate-knowledge.py
  - scripts/verify.py
  - .claude/settings.json
relations:
  - docs/index.md
  - docs/kit/decisions/0005-multi-tool-agent-configs.md
  - docs/kit/rules/worktrees.md
  - docs/kit/decisions/0017-kit-and-project-layers.md
  - docs/kit/decisions/0018-setup-starts-itself.md
  - docs/kit/decisions/0021-cc-check-in-ci.md
  - docs/kit/decisions/0028-verify-before-push.md
  - docs/kit/decisions/0030-disk-management.md
---

# Claude Code hooks enforce the hard rules early; the gates still decide

## Context

The hard rules in `AGENTS.md` were instruction text only. Agents break them when a tool default disagrees: Claude Code appends an AI `Co-Authored-By` trailer to commits, agents reach for `cmake` or `pytest` instead of Nix, and they edit the generated `.claude/skills` copy. Gates found some of this late; some of it nothing found.

## Decision

- `scripts/rule_hooks.py` holds the hook logic; `cc hook <event>` runs it with the Python pinned in `uv.lock` ([0033](0033-native-core-nix-optional.md)), through `scripts/hook_guard.py`, which the shims call directly, not through `scripts/cc.py`. Every hook entry sets `onFailure: block` (Claude Code 2.1.295 or later): a gate hook (`pre-*`) that fails or times out blocks the call, and a non-gate hook reports its failure without blocking. A gate hook whose input has no `tool_input` object, an empty stdin included, denies.
- In hook mode the shims never download. When the pinned uv or the venv is missing, a gate hook denies at once with "run ./cc doctor once in a terminal" and a reporting hook says the same: a first download takes longer than the 15 s hook timeout and would start again on every tool call. After a pull that moves the uv pin, a hook uses a uv already in `.cc-local/tools/uv/`, so open sessions keep working until the next command outside a hook downloads the pinned one.
- `./cc agents sync` owns the `hooks` key of `.claude/settings.json`; `./cc agents check` fails when it was edited by hand. In `permissions.allow` it only adds: after each `Bash(./cc ...)` rule it adds the same rule for the PowerShell tool (`PowerShell(.\cc.cmd ...)`), so a rule the user allowed for one shell also holds in the other on Windows. Every other key and rule stays the user's.
- `session-start` and `./cc doctor` report an error when `claude --version` is below 2.1.295: older versions ignore `onFailure` and let a failing hook allow the call.
- What each hook does and the gate behind it:

  | Rule | Hook | Gate |
  | --- | --- | --- |
  | Push only what passed ([0028](0028-verify-before-push.md)) | `ask` on `git push` and `gh pr create` when no passing `./cc verify` (for the CC, `./cc check`) stamped HEAD's tree, or when the pushed commits change a `*.nix` file, a `flake.lock` or `catalog/` and the stamp has no Nix part; the question names "no Nix backend on this host" when that is the reason | none: the stamp is local |
  | The core every hook runs through stays repairable | `ask` before an edit of `pyproject.toml`, `uv.lock`, `.uv-pin`, `cc`, `cc.cmd`, `cc.ps1` or `scripts/hook_guard.py` | the `core` check runs the shims |
  | No AI credit in commits or PRs | deny `git commit`, `gh pr create/edit` with an AI `Co-Authored-By` or "Generated with" line | `./cc verify` scans branch commits in the CC and each feature worktree |
  | No direct build or test tools | `ask`: the user approves a diagnosis run | none: a command leaves no artifact |
  | Skills only in `.agents/skills` | deny edits under `.claude/skills` | `agent-configs` check |
  | No stray Markdown | deny creating `.md` outside `docs/`, `plans/`, the skills and roles, `templates/`, the root files and the folders the CC declares in `control-center.json` `markdownDirs` | `knowledge` check |
  | Notes follow the knowledge rules | after an edit in `docs/`, return that note's validation errors | `knowledge` check |
  | Skill copies in sync | at stop, report drift once | `agent-configs` check |
  | Stay inside the CC ([0016](0016-per-cc-repos-and-worktrees.md)) | `ask` when a Read, Grep, Glob, edit or shell path resolves into a folder next to the CC | `./cc verify` fails on clones in `repos/` outside the catalog |
  | Base clones are never edited | deny edits under `repos/`, naming the feature worktree instead | reference clones are read-only on disk |
  | Kit knowledge is not edited in a CC ([0017](0017-kit-and-project-layers.md)) | `ask` before an edit under `docs/kit/` when the folder has `control-center.json` | none: the next kit update replaces the edit |
  | Irreplaceable data is deleted only by the user ([0030](0030-disk-management.md)) | runs first in `pre-bash` (Bash and PowerShell), so no earlier `ask` in the same line hides it: deny a delete, move-away or overwrite that reaches an irreplaceable `localData` entry, its mirror or record (from `.cc-local/hooks/backup-locations.json`), or a folder above them; also `git clean -x` in the CC, `./cc data remove` and its wrappers, and the entry's `purge` apps. Deny an Edit or Write into those paths; `ask` before an edit of `control-center.json` drops the tag | none: `./cc data remove` reads the typed name from the user's terminal and refuses inside Claude Code |
  | A reference repo is guidance, not truth | after the first read of `repos/reference/<id>` in a session, add a note to check it against the project repos | none: it is judgement |

- The `session-start` hook enforces no rule: it makes a CC that is not set up open with the setup interview ([0018](0018-setup-starts-itself.md)).
- When the rule hooks cannot load (a syntax error in `rule_hooks.py` or a module it imports), `hook_guard.py` decides alone, with the standard library only: it allows reads inside the CC and edits under `scripts/`, asks before a read outside the CC, denies every other edit and every shell command, and turns the reporting hooks into a message. Without it one broken file blocked every tool call, the Read and Edit that would repair it included, and only the user could fix it (2026-11-08, a null byte in `hostos.py`).

## Options considered

- Rules as text only: the status quo that let attribution trailers through.
- Git `commit-msg` hooks: they work for every tool, but setting `core.hooksPath` in project repos overrides the projects' own hooks.
- A SessionStart hook against sessions started in a worktree: such a session never loads the CC's settings, so it cannot fire. The session guard file written by `./cc worktree` covers this instead.

## Consequences

- A CC that keeps other committed Markdown, such as a `research/` folder of source material, lists it in `markdownDirs` (validated by the `bootstrap` check). The exception is per CC and visible, so the template's default stays strict.
- Hooks exist only in Claude Code. Codex, Cursor and OpenCode rely on the gates.
- The guard cannot help when `hook_guard.py` itself, the shim, `pyproject.toml` or `uv.lock` is broken: then every hook still blocks. These files are small and rarely edited, and an edit of one asks first; the user repairs them with `git checkout` in a terminal.
- Hooks match shell text, so a command hidden in a script or another shell (`wsl.exe bash -lc ...`) passes the hook; only the gates are complete where a gate exists. For irreplaceable data the hook also cannot see a variable set by an earlier command, a delete inside a script file, or an alias; it reads `bash -c` strings and inline Python that deletes or writes, and variables set in the same line. A `/mnt/c/...`, `C:\...` and `\\wsl.localhost\...` spelling of one path count as the same path. CI runs `./cc check` on every pull request so the gates do not depend on someone running them ([0021](0021-cc-check-in-ci.md)).
- New CCs get the hooks through `./cc bootstrap install`; existing CCs only through a migration.

## Revisit when

Codex, Cursor or OpenCode gain a hook format worth generating, or a hook blocks legitimate work often enough that its matcher needs a different design.
