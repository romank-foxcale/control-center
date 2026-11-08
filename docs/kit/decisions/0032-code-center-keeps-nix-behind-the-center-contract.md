---
id: kit.decision.0032-code-center-keeps-nix-behind-the-center-contract
title: code-center keeps Nix inside, in WSL, and joins the control-center hub only through the center contract
status: superseded
summary: The kit, to be named code-center, becomes one center of the control-center hub (today the control-center-jarvis repository). It keeps Nix and therefore WSL for its builds, gates and pinning; the hub and the other centers stay Windows-native with uv and never depend on Nix. The hub reaches code-center only through the center-kit JSON contract.
verified_at: 2026-11-08
evidence:
  - flake.nix
  - cc
  - nix/lib/default.nix
  - docs/kit/rules/executable-processes.md
  - https://github.com/romank-foxcale/control-center-jarvis/blob/cf090a66782807740826ea938e301fe5fc6baa85/centers.json
relations:
  - docs/kit/decisions/0033-native-core-nix-optional.md
  - docs/kit/index.md
  - docs/kit/decisions/0006-target-platform-gates.md
  - docs/kit/decisions/0027-python-gate-venv-cache.md
  - docs/kit/rules/executable-processes.md
---

# code-center keeps Nix inside, in WSL, and joins the control-center hub only through the center contract

> Superseded by [0033](0033-native-core-nix-optional.md): the core now runs natively and Nix is an optional extra. The boundary to the hub through the center contract stays.

## Context

The user's "centers" system has a personal hub, control-center (today the `control-center-jarvis` repository), and several centers that share the center-kit contract: a `center.json` manifest, a JSON CLI envelope, and fail-closed hooks installed with `ck sync`. The hub and center-kit are Windows-native by decision: `uv`, no Nix, no WSL. Their pinning method is a git tag in `uv.lock`, not a flake input.

This kit, the Control Center, is to be named code-center and become one of those centers. Nix is its core, not a detail: builds, tests and every gate are flake outputs behind `./cc` ([executable processes](../rules/executable-processes.md)), sources are pinned as flake inputs, and the council verifies through the same gates. Nix does not run on native Windows, so a Nix-based CC runs in WSL. The user chose WSL for the CC on 2026-10-06. Until now the hub lists the existing CCs as `legacy`: listed, never called.

## Decision

- code-center keeps Nix, and so WSL, for everything inside it: flake outputs, gates, `./cc check` and `./cc verify`, pinning, councils.
- Nix stays inside code-center. The hub, center-kit and the other centers never need Nix, and the project repositories a code-center instance manages do not either: a Windows-native repository gets a Windows host gate, the pattern of [0006](0006-target-platform-gates.md) and [0027](0027-python-gate-venv-cache.md) (a CC in WSL runs native Windows Python with a venv under `%LOCALAPPDATA%`).
- The hub reaches code-center only through the center-kit contract (its manifest and JSON CLI), run through `wsl.exe`, with `host: wsl:Ubuntu`. The hub never calls `./cc` directly or reads code-center's files beyond what the contract exposes.

## Options considered

- **Drop Nix and make code-center Windows-native like the other centers:** one runtime everywhere, but gates, verify, checks and pinning would need a rewrite, and each stack (Java with PostgreSQL, C#, Python) would need its own per-machine setup. The reproducible builds that are code-center's purpose would be lost.
- **The hub calls `./cc` directly:** no contract work in code-center, but the hub would depend on code-center's internals and on Nix being present, which breaks the hub's Windows-native boundary.

## Consequences

- code-center is the only center that needs WSL: `jarvis doctor` keeps checking WSL, and center-kit's hooks keep matching WSL path spellings (both do so today).
- code-center needs a `center.json` and the contract's read-only commands before the hub can call it; until then the hub keeps listing CCs as `legacy`.
- The rename to code-center is not done yet; this decision does not rename files, commands or the template repository.

## Revisit when

Nix runs reliably on native Windows; or the hub needs something from code-center that the center contract cannot express.
