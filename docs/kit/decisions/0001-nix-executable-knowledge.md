---
id: kit.decision.0001-nix-executable-knowledge
title: Nix as the format for executable knowledge
status: accepted
summary: Processes are described as Nix flakes; NixOS is used for machines and services, not as a universal format for everything.
verified_at: 2026-11-08
evidence:
  - flake.nix
  - nix/lib/default.nix
relations:
  - docs/kit/decisions/0033-native-core-nix-optional.md
  - docs/kit/rules/executable-processes.md
  - docs/kit/architecture/control-center.md
---

# Nix as the format for executable knowledge

## Context

Markdown commands go stale without anyone noticing. A cross-project build must break as soon as an incompatible change lands.

## Decision

Use flake outputs as the public interface of processes:

- derivations/packages: builds;
- checks: automatic verification;
- apps: side effects and runtime;
- NixOS modules/tests: hosts and long-running services.

## Consequences

- The dependency graph becomes computable and cacheable.
- Sources must be pinned.
- Nix was a prerequisite for every CC command. Since [0033](0033-native-core-nix-optional.md) the CC core runs natively and Nix is an optional extra; the project gates stay flake outputs until adapters get a native backend.
- Docker E2E on macOS needs a Linux builder/CI or a separate runtime adapter.

## Revisit when

If Nix cannot express a significant part of the real processes without a large impure layer, compare Bazel, Earthly and a CI-native workflow on a single pipeline.
