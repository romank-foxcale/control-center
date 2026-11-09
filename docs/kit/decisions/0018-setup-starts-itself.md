---
id: kit.decision.0018-setup-starts-itself
title: A CC that is not set up starts its own setup; nobody needs a magic prompt
status: accepted
summary: cc-kit new and migrate open the AI tool with the opening prompt, a Claude Code session-start hook and an AGENTS.md rule make the agent open with the setup interview while control-center.json is discovery or adapting, adding a project repo always continues into onboarding, and both installers end by creating the first CC.
verified_at: 2026-10-15
evidence:
  - install/cc-kit
  - install/install.sh
  - install/windows/install.ps1
  - scripts/rule_hooks.py
  - scripts/rule_hooks_test.py
  - scripts/agent-configs.py
  - AGENTS.md
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0003-template-as-seed.md
  - docs/kit/decisions/0013-rule-hooks.md
  - docs/kit/decisions/0031-new-project-cc.md
---

# A CC that is not set up starts its own setup; nobody needs a magic prompt

## Context

The kit is meant for people who know what a CC is for but not how it works. Getting from install to a working CC needed knowledge they do not have: the Windows installer had to be started as administrator and restarted by hand after a reboot, `cc-kit` only worked inside Ubuntu, a new CC sat idle until the user typed a specific prompt naming `grill-cc-bootstrap`, migration needed a hand clone of the kit and a longer prompt, and adding a repo to a ready CC stopped at a catalog entry with no adapter.

## Decision

- **Install.** The Windows installer starts as a normal user, elevates only for `wsl --install`, and registers a one-time `RunOnce` entry to resume after a reboot. It installs Claude Code by default, adds a `cc-kit.cmd` launcher to the user's PATH, and offers to create the first CC. `install.sh` does the same last step on macOS and Linux.
- **Create and migrate.** `cc-kit new` and `cc-kit migrate <folder>` open the AI tool with the opening prompt (`claude` and `codex` take it as an argument). `cc-kit open` continues an unfinished setup. The default tool is `$CC_KIT_TOOL`, else the first installed of claude, codex, opencode.
- **Every way in.** While `control-center.json` is `discovery` or `adapting`, the Claude Code `session-start` hook adds context that makes the agent open with the setup interview. An `AGENTS.md` rule says the same for every other tool. So a CC opened from the desktop app or an editor also starts setup.
- **Onboarding a repo.** Adding a project repo continues into the repository grilling of `grill-cc-bootstrap` (adapter, a gate per target, `./cc verify`). The skill reads the stack and commands from the repo and asks only for confirmation and the target platforms.

## Consequences

- The `session-start` handler lives with the rule hooks, because `scripts/hook` and `./cc agents sync` already deliver hooks to Claude Code, but it enforces no rule and never blocks.
- The template has no `control-center.json`, so the hook stays quiet while the kit itself is edited.
- The opening prompts live in `install/cc-kit`. Renaming `grill-cc-bootstrap` means changing them, the hook text and the `AGENTS.md` rule.
- The `RunOnce` entry and the PATH entry are the only changes the installer makes to Windows user settings outside WSL.

## Revisit when

A supported tool can start a session with an agent-initiated message, or the CC gains a non-terminal entry point such as a desktop launcher.
