---
name: cc-risk
description: Audit the risks of a planned change before planning or building it, with a council of models and a judge (./cc council risk) - failure stories, likely errors, incompatibilities, a ranked risk register, spikes and a go/no-go verdict. Use when the user asks for risks, a risk audit or what could go wrong, and offer it unasked before a change to architecture, dependencies, runtime, data or deployment.
---

# CC risk

First read `../_shared/cc-cli.md` and follow it.

## When

- The user asks what could go wrong, for the risks, or for an audit of a change ("I want to change TEI to PyTorch").
- Offer it, without being asked and before planning, when a change replaces a component or library, changes a runtime, model, data format or schema, migrates data, or changes how something is deployed. Say in one line why and that it costs three model calls. Not for a local code change that the gates cover.
- Never answer such a question with a few generic risks from memory ("it might be slower"): either run the council or say plainly that you have not audited it.

## Commands

| Request | Command |
|---|---|
| Audit change X in repo R | `./cc council risk --repo <repo> [--repo <repo>...] --task "<change>"` |
| Audit it on a feature's worktrees | `./cc council risk --feature <feature> [--repo <repo>] --task "<change>"` |
| A change to the CC itself | `./cc council risk --task "<change>"` |
| Progress, or a past run | `./cc watch <run-id>`, `./cc council status [<run-id>]` |

## Rules

- The council never sees this conversation. Write the task as the change and its reason with every fact it needs: what is replaced by what, versions, where it runs, data volumes, constraints the user named, decisions already made. Do not list risks you expect: the assessors must find them independently.
- Assessors read the CC's notes and the repositories in scope; both default assessors (Claude and GPT) can search the web for upstream issues. Name every repository the change touches with `--repo`.
- Runs take minutes: run in the background and follow `./cc watch <run-id>`, relaying steps and stalls as `cc-council` describes.
- The result is `risk.md` in the run folder `.cc-local/runs/<run-id>/`, with the judge's verdict on its first line. Give the user the verdict, the summary, the risks of severity 4 and 5 with their predicted errors, and the spikes, then the path to the full register. Say which assessment found what only if asked (`./cc council status <run-id>` shows the hidden authors).
- The verdict is advice; the user decides. Never start planning or implementing on `GO` without the user.
- Then hand off to `grill-task-planning`: the register becomes the plan's failure modes, each spike a first work package, each check a completion check (a check that exercises the change, not only the existing gates).
- Change the roles' models with `./cc council set-role risk-claude|risk-gpt|risk-judge ...`. An assessor or the judge may be `read-only` or `read-only-web`; nothing else. A catalog without `catalog/councils/risk.json` gets it, and its three roles, from `templates/`. A third assessor on another provider (a Cursor role, `read-only`) is a new role file plus its id in `assessors`.
