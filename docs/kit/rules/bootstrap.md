---
id: kit.rules.bootstrap
title: Control Center bootstrap and migration
status: active
summary: The template is cloned as a nested read-only seed (or installed as the kit), and the target CC is created in the parent folder through evidence-first grilling.
verified_at: 2026-11-09
evidence:
  - BOOTSTRAP.md
  - scripts/materialize-template.py
  - .agents/skills/grill-cc-bootstrap/SKILL.md
  - install/cc-kit
relations:
  - docs/index.md
  - docs/kit/architecture/control-center.md
  - docs/kit/decisions/0003-template-as-seed.md
  - docs/kit/decisions/0018-setup-starts-itself.md
  - docs/kit/decisions/0034-new-project-cc.md
  - docs/kit/rules/project-adapters.md
  - docs/kit/rules/worktrees.md
---

# Control Center bootstrap and migration

## Boundary

`cc_template/` is a temporary source of the protocol and files. It never becomes a submodule or a folder of the final CC. Materialization copies only files tracked by Git and never overwrites a non-empty target.

## Modes

- `new`: safe materialization, then grilling and one-by-one repository onboarding. The interview first asks whether the CC covers existing repos, a new project, or both: an existing repo is cloned with `./cc repo add`, a new one is created empty with `./cc repo new` ([0034](../decisions/0034-new-project-cc.md)).
- `migration`: read-only inventory, mapping onto the target model, and a vertical-slice migration compared against the legacy behavior.

Nobody has to start a mode by hand: `cc-kit new` and `cc-kit migrate` open the AI tool with the opening prompt, and while `control-center.json` is `discovery` or `adapting` every session opens with the setup interview ([0018](../decisions/0018-setup-starts-itself.md)). Adding a project repo to a ready CC runs the same repository onboarding.

Migration never justifies losing meaning. An entity is either preserved, or its replacement or removal is recorded as a decision.

## State

`control-center.json` holds the schema version, name, bootstrap status and topology. `catalog/` holds one descriptor per repository, workflow and benchmark. The conversation and internal reasoning are not stored.

`ready` means the descriptors agree with the Nix contracts, at least one project repo exists, and every project repo is verified or blocked with a written `blockedReason` (for example no gate can run on this host yet) ([0034](../decisions/0034-new-project-cc.md)). A new repo with no code yet stays `discovered`, so the CC stays `adapting` until its first feature adds the code and the gates. Only when `ready` is `cc_template/` no longer needed.
