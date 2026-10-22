---
name: grill-task-planning
description: Evidence-first interview that turns an ambiguous engineering request into an agreed, agent-ready plan: scope, repos and worktrees, work packages, failure modes and verifiable acceptance criteria. Use before complex, architectural, multi-repo or multi-agent work.
---

# Grill task planning

First read `../_shared/grilling-core.md` in full. Do planning only: do not create worktrees or start implementation until the plan is explicitly agreed.

## Frame

Build a provable model: outcome; non-goals; current behavior/evidence; affected repos, workflows, interfaces and users; domain entities/invariants; compatibility, performance, security, data and rollout constraints; failure modes/rollback; acceptance criteria and the CC output that verifies them.

Do not confuse the requested implementation with the outcome. Test the proposed solution against counterexamples and compare it with at least one real alternative.

## Plan lifecycle

If the plan is needed across sessions or agents, create it with `./cc plan create <task-id> --title <outcome>` in `plans/active/`. Otherwise keep the plan in the conversation.

- `active`: current plans;
- `archived`: plans that were decided against;
- `completed`: finished plans.

After deciding not to carry out a plan, run `./cc plan archive <task-id> --reason <reason>`. After a verified implementation, run `./cc plan complete <task-id> --evidence <evidence>`. Never copy a plan between states.

Once the plan is explicitly agreed, run `./cc plan accept <task-id>`. An unagreed plan cannot move to `completed`.

## Worktree scope

Choose a stable `task-id`; it maps to `worktrees/<task-id>/<repo>_wt`. Include only the repos that will change; a read-only dependency needs no worktree.

When moving to implementation, use `./cc worktree create <task-id> <repo>...` and `./cc feature <task-id> ...`.

Run plan transitions through `cc-plan`, worktrees through `cc-worktree` and checks through `cc-verify`.

## Agent-ready plan

Every work package has: one outcome; an exact write scope; inputs/outputs; dependencies; a completion check. Write each package for an agent that never saw the grilling: state the files, facts and decisions in it, never "per the findings" or "as discussed". Parallel packages never change the same files or interfaces. Derive parallelism from the graph, not from the desired number of agents. Keep integration/final verification separate.

Keep task-local assumptions, risks, alternatives and unknowns in the plan. Create an ADR only for a decision that matters beyond the task; a glossary term only if agents could otherwise understand the domain differently.

## Acceptance

The plan is ready when an agent without access to the grilling can unambiguously determine: what to change and not change; the repo/worktree; the interfaces/invariants to preserve; how to verify completion; the stop condition. Get explicit agreement before implementation.
