# Running the CC CLI

The `cc-*` skills are thin front ends over `./cc`. `./cc` stays the single source of truth: never reimplement its logic, edit its state files by hand, or call the underlying `scripts/*.py` or `nix` directly.

## Invocation

Run every command from the CC root. The CC runs natively on Windows, macOS and Linux: the shims fetch the pinned uv and Python on first use (docs/kit/decisions/0033-native-core-nix-optional.md).

- Linux, macOS, WSL and Git Bash on Windows: `./cc <command> ...`
- PowerShell: `.\cc.cmd <command> ...`, always with `.cmd`. cmd: `cc <command> ...`

Use one environment per CC clone: a clone used from WSL is used from WSL, a clone on the Windows drive from Windows. Worktree manifests hold paths of the environment that wrote them.

Nix is optional. Without it `check`, `verify`, `show`, `doctor` and every other command run; `build`, `run`, `relock`, `gc`, `eval` and the Nix-defined project gates stop with "needs the optional Nix extra". Report that message as it is: never install Nix, WSL or a toolchain yourself, and never call a gate passed that did not run.

## Scope

This CC's repositories are exactly those in `./cc repo list`. Never enumerate or loop over clone folders to answer a question about its repos, PRs or branches; use `./cc repo` and `./cc prs`.

## Behaviour

- Infer arguments from the conversation, active plans and `./cc ... list|status` output; ask only for a required value you cannot infer.
- Report the real output. On failure, quote the error and stop: never retry with other arguments to make it pass.
- Confirm anything the skill marks **destructive** first, naming exactly what will change.
