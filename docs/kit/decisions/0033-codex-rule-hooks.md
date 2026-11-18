---
id: kit.decision.0033-codex-rule-hooks
title: The rule hooks also guard a Codex chat, where every ask becomes a deny
status: accepted
summary: Codex sends shell commands as Bash and every edit as one apply_patch text, and it runs a command whose hook answers ask without a word. The rule hooks check each file of a patch as its own edit and turn every ask into a deny that tells the agent to ask the user in chat; stop and session-start are shared with Claude Code.
verified_at: 2026-11-18
evidence:
  - scripts/rule_hooks.py
  - scripts/rule_hooks_test.py
relations:
  - docs/kit/index.md
  - docs/kit/decisions/0005-multi-tool-agent-configs.md
  - docs/kit/decisions/0013-rule-hooks.md
  - docs/kit/decisions/0030-disk-management.md
---

# The rule hooks also guard a Codex chat, where every ask becomes a deny

## Context

Codex reads `AGENTS.md` and `.agents/skills` (0005), so a Codex chat in a CC gets the hard rules as text, but the hooks of 0013 were Claude Code only. Some rules have no gate behind them: the deny on deleting irreplaceable data (0030), the ask before an unverified push, the ask before a direct build tool. Codex CLI 0.160.1 has hooks with Claude-like events. Probed on 2026-11-18 with `gpt-6.1-sol`:

- `tool_name` is `Bash` for a shell command and `apply_patch` for an edit; the whole patch is one text in `tool_input.command`, with `*** Add File:`, `*** Update File:`, `*** Delete File:` and `*** Move to:` lines. Codex has no read or search tool events: it reads through `Bash`.
- `permissionDecision: "deny"` and exit 2 block both a command and a patch. `permissionDecision: "ask"` and exit 1 let the call run, and the model sees nothing.
- PostToolUse `{"decision": "block", "reason", "hookSpecificOutput": {"additionalContext"}}`, a Stop `block` and SessionStart `additionalContext` all reach the model, as in Claude Code.

## Decision

- `rule_hooks.py codex-pre-tool` checks a `Bash` event with the same `pre_bash` rules, and an `apply_patch` event file by file with the `pre_edit` rules: an added file is a Write of its added lines, an updated file an Edit of its removed lines to its added lines, a deleted file (and the source of a move) an empty overwrite, and a move target a Write. One denied file denies the whole patch.
- Every ask becomes a deny whose reason keeps the Claude Code reason and adds that the agent must ask the user in chat and, if they agree, give them the exact command or change to run themselves. A deny wins over an ask anywhere in the patch.
- `rule_hooks.py codex-post-tool` returns the knowledge-rule feedback for every note a patch wrote, in Codex's block form.
- `stop` and `session-start` are the same handlers for both tools; their replies already have the form Codex reads.
- A hook that fails still exits 1 and blocks nothing; in Codex a failed hook also lets the call run.

## Options considered

- Pass `ask` through: Codex runs the call silently, so every ask rule would be off.
- Turn an ask into an allow with a warning in `additionalContext`: the push or edit happens before anyone reads it.
- Let the agent repeat a denied call with an approval marker in the command: the agent could add the marker itself, which removes the point of asking.

## Consequences

- In Codex an approved exception costs the user a step: they run the command or make the edit themselves, where Claude Code shows an approve button.
- The reference-repo reminder (post-read) has no Codex form, because Codex reads through `Bash`.
- A path the shell builds at run time (a variable, a script) is not seen, the same limit as 0013.
- Codex must load the hooks and trust each one before they run; how the CC writes and checks them is a separate step of the same plan.

## Revisit when

Codex supports `ask` in PreToolUse, adds read or search tool events, or changes the `apply_patch` format.
