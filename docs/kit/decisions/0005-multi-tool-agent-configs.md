---
id: kit.decision.0005-multi-tool-agent-configs
title: One set of skills for every AI tool
status: accepted
summary: Skills live in .agents/skills, Claude Code gets a checked copy in .claude/skills, and instructions stay in AGENTS.md.
verified_at: 2026-10-26
evidence:
  - scripts/agent-configs.py
  - CLAUDE.md
  - .agents/skills/grill-cc-bootstrap/SKILL.md
  - .claude/skills/grill-cc-bootstrap/SKILL.md
  - flake.nix
relations:
  - docs/index.md
  - docs/kit/decisions/0003-template-as-seed.md
  - docs/kit/rules/bootstrap.md
  - docs/kit/decisions/0013-rule-hooks.md
  - docs/kit/decisions/0019-lean-agent-context.md
---

# One set of skills for every AI tool

## Context

People open the CC in Claude Code, Cursor, Codex, OpenCode and Zed. Codex reads project skills only from `.agents/skills`, Claude Code only from `.claude/skills`; Cursor and OpenCode read both folders. Every tool reads `AGENTS.md`, except Claude Code when a `CLAUDE.md` exists. Zed runs Claude Code or Codex through the Agent Client Protocol; the Claude adapter (`@agentclientprotocol/claude-agent-acp`) starts Claude Code with the user, project and local setting sources and keeps the native tool names, so the same `CLAUDE.md`, skills and hook matchers apply. On 2026-10-26 a Claude Agent thread in Zed on WSL had an edit of `.claude/skills` denied by the `pre-edit` hook.

## Decision

- The canonical skills are in `.agents/skills/`.
- `.claude/skills/` is a copy created by `./cc agents sync`; `./cc check` fails if they differ.
- `CLAUDE.md` contains only `@AGENTS.md`.
- A copy rather than a symlink: a symlink breaks when the repo is cloned on Windows without WSL.

## Consequences

- Cursor and OpenCode see the same skills twice; the contents are identical.
- Editing the copy in `.claude/skills/` without editing the source breaks the check.
- `.opencode/skills` is not used: OpenCode reads `.agents/skills`.
- Council and thread role skills live in `.agents/roles/`, which no tool lists and `./cc agents sync` does not copy; only the runners load them ([0019](0019-lean-agent-context.md)).
- `./cc agents sync` also writes the `hooks` key of `.claude/settings.json`; hooks are Claude Code only ([0013](0013-rule-hooks.md)).
- Zed's own agent runs no hooks, so in Zed the CC is used through Claude Code or Codex. If the ACP adapter stops loading project settings or renames tools, the hooks stop firing there without an error.
