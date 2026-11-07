# native-core spike S2: rule hooks that block on native Windows

**Verdict: GO, with one condition.** On native Windows, Git for Windows (Git Bash) must be a prerequisite. With Git Bash, `"$CLAUDE_PROJECT_DIR/cc" hook <name>` plus `"onFailure": "block"` denied the call through the uv shim. It did so with no system Python and no uv on `PATH`. In Claude it took 1.7-4.2 s. Outside Claude the median was 2.9 s cold and 1.4 s warm. Without Git Bash, no single committed command works for both POSIX and Windows. The recommended form then fails closed: it blocks every Bash/PowerShell call with a PowerShell parse error.

Run on 2026-11-07. Host: Windows 11 Pro 10.0.26200, Claude Code 2.1.318 (native `claude.exe`), Git for Windows at `C:\Program Files\Git`, uv 0.13.1, model `claude-haiku-5-5`. Scratch project: `%TEMP%\claude\...\scratchpad\s2-hooks\proj`. It is not in the repository.

## Recommended command for `.claude/settings.json`

```json
{
  "type": "command",
  "command": "\"$CLAUDE_PROJECT_DIR/cc\" hook pre-bash",
  "timeout": 15,
  "onFailure": "block"
}
```

Use the same form for `pre-read`, `pre-edit` and the other events. It keeps today's quoting and only adds `onFailure`. Rules for the shim and the hook program:

- The POSIX shim `cc` runs under Git Bash on Windows. It must find uv without `PATH`: use `.cc-local/tools/uv/<version>/uv.exe` on Windows and `.../uv` on POSIX, and only then `command -v uv`. It must run `uv run --locked --offline ... python scripts/rule_hooks.py <name>`, with `exec` so that the exit code passes through unchanged.
- Return a decision only as exit 0 plus `hookSpecificOutput` JSON, or as exit 2 plus a reason on stderr. With `onFailure: "block"`, any other exit code, a crash, a spawn failure or a timeout blocks the call.
- Set `UV_PYTHON_PREFERENCE=only-managed` and a project-local `UV_PYTHON_INSTALL_DIR` in both shims. Then a system Python is never used. Without this, the venv is "incompatible": uv warns and does not repair it when `--no-sync` is set.
- uv exits 2 on its own errors, for example when the pinned Python is missing in offline mode. Claude Code reads exit 2 as a block and shows uv's stderr as the reason. So a broken uv setup blocks even without `onFailure`.
- `onFailure` needs Claude Code v2.1.295 or later. A doctor check should enforce a minimum version. This spike did not test what an older version does with the field.

On Windows without Git Bash, the honest option is to fail closed and have `cc doctor` / `cc.cmd` require Git for Windows. The CC needs git anyway, and Git for Windows installs bash. If a no-Git-Bash setup must be supported later, the Windows-only options that worked are `cmd.exe` exec form (F4) and PowerShell `& "...\cc.cmd"` (F6). They cannot share one committed `settings.json` with POSIX, so a per-machine generated `.claude/settings.local.json` would be needed. That is a separate decision.

## Results: form x setup

Every headless run used a `PATH` with only `C:\Windows\System32`, `C:\Windows`, WindowsPowerShell and `C:\Program Files\Git\cmd`. So there was no system Python (Python313) and no uv on `PATH`. uv was the copy in `proj/.cc-local/tools/uv/0.13.1/uv.exe`, and Python was the project-local uv-managed CPython 3.12.15 in `proj/.cc-local/python`. Each hook had `onFailure: "block"` and `timeout: 15`, with matcher `Bash|PowerShell`. The deny script logged its argv and env to a file, so a real deny can be told apart from a block caused by a failure. "Hook ms in Claude" is the `Hooks: PreToolUse:Bash [...] finished ... (N ms)` line from `--debug-file`. Cold and warm are medians of 10 direct runs outside Claude. Cold means a fresh empty `UV_CACHE_DIR` for each run. Warm means a shared cache after one discarded run.

| Form | Exact `command` (+ `args`) | Git Bash present | Without Git Bash (simulated) | Hook ms in Claude | Cold / warm median ms (max) | Cross-platform |
|---|---|---|---|---|---|---|
| F1 sh shim, shell form | `"$CLAUDE_PROJECT_DIR/cc" hook F1` | **denied** (script ran, JSON deny) | **blocked by failure** (`shell: powershell`, run F1ps): PowerShell parse error, `onFailure` blocks | 4173 (first), 1734 | 2887 (3637) / 1357 (1531) | yes (POSIX `sh -c`) |
| F2 cc.cmd, shell form | `"$CLAUDE_PROJECT_DIR\cc.cmd" hook F2` | **denied** (Git Bash runs `.cmd` through cmd) | not run; same parse error as F1ps expected | 1763 | 2529 (3173) / 797 (1168) | no |
| F3 uv.exe exec form | `${CLAUDE_PROJECT_DIR}/.cc-local/tools/uv/0.13.1/uv.exe` + `["run","--locked","--offline","--no-sync","--project","${CLAUDE_PROJECT_DIR}","python","${CLAUDE_PROJECT_DIR}/scripts/deny.py","F3"]` | **denied** | same (exec form uses no shell) | 1499 | 1609 (1894) / 643 (708); without `--no-sync`: 1663 / 699 | no (`.exe` path, uv version in settings) |
| F4 cmd.exe exec form | `cmd.exe` + `["/d","/c","${CLAUDE_PROJECT_DIR}\\cc.cmd","hook","F4"]` | **denied** | same (exec form uses no shell) | 1199 | 1899 (2303) / 819 (879) | no (Windows only) |
| F5 exec form of extensionless shim | `${CLAUDE_PROJECT_DIR}/cc` + `["hook","F5"]` | **blocked by failure** (ENOENT, the script never ran) | same | failed to start (27) | n/a | no |
| F6 PowerShell, `.cmd` | `& "${CLAUDE_PROJECT_DIR}\cc.cmd" hook F6`, `"shell": "powershell"` | **denied** | **denied** (this is the shell used without Git Bash) | 2140 | 2509 (14816) / 2205 (2671) | no |
| F7 PowerShell, extensionless | `& "${CLAUDE_PROJECT_DIR}/cc" hook F7`, `"shell": "powershell"` | **ALLOWED: fails open** | **ALLOWED** | 1195, exit 0, no output | n/a | no |
| F8 pinned interpreter exec form | `${CLAUDE_PROJECT_DIR}/.venv/Scripts/python.exe` + `["${CLAUDE_PROJECT_DIR}/scripts/deny.py","F8"]` | **denied** | same (exec form uses no shell) | 990 | 920 (1085) / 851 (1174) | no (`Scripts/` vs `bin/`; no lock check) |
| F1 + `CLAUDE_CODE_GIT_BASH_PATH=C:\nonexistent\bash.exe` | as F1 | **denied** (the bad value is ignored and Git Bash is auto-detected) | n/a | 1734 | n/a | yes |

Notes on the table:

- **F7 is the dangerous one.** In PowerShell, `& "<dir>/cc"` with files `cc` and `cc.cmd` side by side reached `cc.cmd`. But the hook got **no stdin** (the logged `tool_input.command` was empty). No JSON came back, the exit code was 0, and `echo FORBIDDEN-S2` **ran**. `onFailure` cannot catch this because nothing failed. Never call the shim by an extensionless path from PowerShell.
- A "blocked by failure" result blocks every call, whatever the command. It is fail-closed, but it does not enforce the rules.
- "Without Git Bash" was simulated with `"shell": "powershell"`. This is the documented default shell when Git Bash is absent. Git Bash could not be really removed: `CLAUDE_CODE_GIT_BASH_PATH` set to a bad path is ignored, and `C:\Program Files\Git` is auto-detected. Hiding it would need a change to the system install, which was out of scope. Not verified: the real no-Git-Bash rule that "only exit code 0 or 2 counts". The docs state it (see below), and the recommended shim already obeys it.
- All in-Claude times include contention with a user plugin hook (headroom) that ran in parallel. Claude logged `Slow PreToolUse hooks: 3.9-6.3 s for Bash (2 hooks)`. Those totals include the plugin, not only our hook.
- A fresh clone with no `.venv` but the Python present: `uv run --locked --offline` created `.venv` and denied in 1806 ms (single run). With the pinned Python missing, uv exited 2 in 437 ms, which blocks.
- Windows Defender was on. Plain `python.exe` start-up was about 0.9 s, which dominates all forms.

Headless runs: 10 (F1, F3, F4, F5, F6, F7, F1ps, F2, F8, F1+bad bash path). The total cost was about $0.04.

## How `$CLAUDE_PROJECT_DIR` arrives on Windows

Seen in the env and argv that the deny script logged:

- **Shell form under Git Bash:** `C:/Users/KozyrevRoman/.../proj`: drive letter, forward slashes, long names. It is not `/c/...`. The hook was started from an 8.3 path (`KOZYRE~1`), and Claude Code still expanded the long form. Git Bash itself passes `C:\...` through unchanged (checked directly), so Claude Code converts the value before it spawns bash. `"$CLAUDE_PROJECT_DIR/cc"` and `"$CLAUDE_PROJECT_DIR\cc.cmd"` both resolve.
- **Exec form and PowerShell:** `C:\Users\KozyrevRoman\...\proj` with backslashes. In exec form, `${CLAUDE_PROJECT_DIR}` is substituted as a plain string, so `${CLAUDE_PROJECT_DIR}/x` gives mixed separators `C:\...\proj/x`. Windows accepts that.
- **PowerShell shell form:** Claude Code rewrites `${CLAUDE_PROJECT_DIR}` to `${env:CLAUDE_PROJECT_DIR}`, but not bare `$CLAUDE_PROJECT_DIR`. Bare `$CLAUDE_PROJECT_DIR` is `$null` in PowerShell. In F1ps the line did not parse at all.

## Answers to the spike questions

1. **Shell.** Shell form (no `args`) runs in Git Bash on Windows, and in PowerShell when Git Bash is not found. A per-hook `"shell": "bash" | "powershell"` field chooses the shell explicitly. cmd.exe is not a choice. Exec form (`args` set) uses no shell and needs a real `.exe` on Windows: `.cmd`/`.bat` cannot be spawned. Git Bash is not required to run Claude Code (it then has the PowerShell tool and no Bash tool). But no committed shell-form string runs correctly under both bash and PowerShell. A bad `CLAUDE_CODE_GIT_BASH_PATH` is ignored with a warning and auto-detection continues. Since v2.1.219 it no longer stops start-up.
2. **`$CLAUDE_PROJECT_DIR`** is `C:/...` under Git Bash and `C:\...` in exec form and PowerShell. It is never `/c/...`. See the section above.
3. **Forms that deny.** (a) With Git Bash: F1, F2, F3, F4, F6, F8 all denied. F1 is the only one that also works on POSIX, so it is the recommendation. (b) Without Git Bash: only the Windows-only forms F3, F4, F6 and F8 deny. F1 fails closed and blocks everything. Do not use F5 (spawn fails) or F7 (fails open).

Today's form (`"$CLAUDE_PROJECT_DIR"/scripts/hook`, which probes `python3`/`python`/`py -3` on `PATH` and exits 1 when none works) fails **open** with no system Python. Exit 1 without `onFailure` is a non-blocking error. This comes from reading `scripts/hook` and the docs. It was not run in Claude.

## Docs citations

- Hooks reference, command hook fields (`args`, `shell`, `onFailure`), "Exec form and shell form", "Block the action when a hook fails", "Failed hooks on Windows without Git Bash", "PowerShell started no program", "Reference scripts by path in PowerShell hooks": https://code.claude.com/docs/en/hooks
  - Shell form: the command goes to "`sh -c` on macOS and Linux, Git Bash on Windows, or PowerShell when Git Bash isn't installed".
  - `shell`: "Defaults to `"bash"`, or to `"powershell"` on Windows when Git Bash isn't installed ... Ignored when `args` is set".
  - Exec form on Windows: "requires `command` to resolve to a real executable such as a `.exe`. The `.cmd` and `.bat` shims ... can't be spawned without a shell."
  - `onFailure`: "Requires Claude Code v2.1.295 or later". Failures are can't start, exit code other than 0 or 2, timeout, and invalid output.
  - Without Git Bash, a shell-form PreToolUse hook that fails with `onFailure` unset blocks, and "only exit code 0 or 2 counts as this hook's verdict".
  - PowerShell form: Claude Code rewrites `${CLAUDE_PROJECT_DIR}` to `${env:...}`, and bare `$CLAUDE_PROJECT_DIR` resolves to `$null`.
  - Deny protocol: exit 0 with `hookSpecificOutput.permissionDecision: "deny"` and `permissionDecisionReason`, or exit 2 with the reason on stderr. A timed-out PreToolUse hook does not block unless `onFailure: "block"` is set.
- Windows setup: Git for Windows is optional, and without it Claude Code uses the PowerShell tool: https://code.claude.com/docs/en/setup (section "Set up on Windows").
- `CLAUDE_CODE_GIT_BASH_PATH`, "If the path doesn't exist ... Claude Code ignores the variable and auto-detects Git Bash ... Before v2.1.219, Claude Code exited at startup", and `CLAUDE_CODE_USE_POWERSHELL_TOOL`: https://code.claude.com/docs/en/env-vars
- Community report (not official): with a per-user Git install, `"shell": "bash"` hooks failed until `CLAUDE_CODE_GIT_BASH_PATH` was set: https://claudeissues.com/issue/84822-windows-hooks-with-shell-bash-fail-with-git-bash-was-not-found-when-git-is-insta

## Exact error and log texts met

- F5 (exec form of the extensionless `cc`):
  - `Hook command failed to spawn (PreToolUse:Bash): ENOENT: no such file or directory, uv_spawn 'C:\Users\KozyrevRoman\...\proj/cc'`
  - `Hooks: PreToolUse:Bash [${CLAUDE_PROJECT_DIR}/cc hook F5] failed to start (27ms)`
  - `Hooks: PreToolUse command hook failed; blocking (onFailure: "block")`
- F1ps (bash form under PowerShell):
  - `At line:1 char:26` / `+ "$CLAUDE_PROJECT_DIR/cc" hook F1ps` / `Unexpected token 'hook' in expression or statement.` / `+ CategoryInfo : ParserError: (:) [], ParentContainsErrorRecordException` / `+ FullyQualifiedErrorId : UnexpectedToken`
  - The log then shows `finished with status 1`, followed by `PreToolUse command hook failed; blocking (onFailure: "block")`.
- Bad Git Bash path:
  - `[WARN] CLAUDE_CODE_GIT_BASH_PATH "C:\nonexistent\bash.exe" not found; falling back to auto-detection`
  - `Using bash path: "C:\Program Files\Git\bin\bash.exe"`
- uv, pinned Python missing, offline (exit 2):
  - `error: No interpreter found for Python 3.12 in managed installations`
  - `hint: A managed Python download is available for Python 3.12, but uv is set to offline mode`
- uv, venv interpreter not uv-managed under `only-managed` with `--no-sync`: `warning: Using incompatible environment (...\.venv) due to `--no-sync` (The project environment's Python interpreter does not meet the Python preference: `only managed`)`
- uv, user's global managed Python 3.12 broken (host state, not caused here): `error: Missing expected target directory for Python minor version link at C:\Users\KozyrevRoman\AppData\Roaming\uv\python\cpython-3.12.15-windows-x86_64-none`. The workaround was a project-local `UV_PYTHON_INSTALL_DIR`.
- `uv python install` into a project dir also tries to place `python3.12.exe` in `~/.local/bin`. On this host it refused: `Executable already exists at C:\Users\KozyrevRoman\.local\bin\python3.12.exe but is not managed by uv; use --force to replace it`. Shims should pass `--no-bin` so that setup never touches the user's bin folder.
- Claude Code timing: `[INFO] Slow PreToolUse hooks: 6116ms for Bash (2 hooks)`.

## Not verified

- A real machine with no Git for Windows (simulated with `"shell": "powershell"`).
- macOS/Linux behaviour of the recommended form (unchanged from today's form, but not run in this spike).
- Claude Code versions older than 2.1.295 with `onFailure` present.
- The real `rule_hooks.py` through the shim. A stand-in deny script was used, and the real module's start-up time is not included.
