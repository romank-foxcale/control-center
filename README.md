# Control Center Kit

A **Control Center (CC)** is a folder that teaches AI coding agents (Claude Code, Codex, Cursor, OpenCode) how your system fits together:

- which repos belong to it,
- how each one is built and tested,
- why things are the way they are.

The agents then plan, code, test and review across those repos without you explaining the setup every time.

You don't need to know how it works inside. You talk to the agent; it does the rest.

## Get started

**1. Install (once per computer, 10–30 minutes)**

- **Windows:** open PowerShell (a normal window, not as administrator) and paste:

  ```powershell
  irm https://raw.githubusercontent.com/romank-foxcale/command-center/main/install/windows/install.ps1 -OutFile $env:TEMP\cc-install.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\cc-install.ps1
  ```

  When Ubuntu asks, create a Linux username and password. If Windows asks to restart, restart it: the install continues by itself after you sign in.

- **macOS:** in Terminal:

  ```bash
  curl -fsSL https://raw.githubusercontent.com/romank-foxcale/command-center/main/install/install.sh | bash
  ```

**2. Name your first Control Center.** The installer asks for a name at the end. Use the product or system it covers, for example `shop`.

**3. Answer the agent's questions.** Claude opens and interviews you: which repos, how they relate, which platforms they ship on. It reads the code first, so it only asks what it can't find out. It then connects each repo and proves it builds and passes its tests.

That's it. You need a Claude subscription (Pro, Max, Team or Enterprise). Other tools work too: see [GUIDE.md](GUIDE.md).

## Everyday use

Open your CC with `cc-kit open shop`, or open the folder in your AI tool. Then just say what you want:

| Say | What happens |
|---|---|
| "add github.com/acme/api" | Connects the repo: clones it, sets up its build and tests, verifies them |
| "plan this: …" | Interviews you and writes an agreed plan before any code changes |
| "start a feature for …" | Creates a separate working copy of the affected repos for the task |
| "fix this bug: …" | Finds the root cause first, adds a failing test, then fixes it |
| "is it ready to push?" | Builds and tests everything on every platform the repo ships on |
| "any new PRs?" / "review PR 42" | Lists open pull requests, reviews one |
| "what can you do?" | Lists the CC's skills and explains the one you pick |

The `cc-kit` command, in PowerShell on Windows or in a terminal on macOS:

| Command | What it does |
|---|---|
| `cc-kit new <name>` | Create another CC; the setup interview starts by itself |
| `cc-kit open <name>` | Open a CC; an unfinished setup continues where it stopped |
| `cc-kit migrate <folder>` | Turn an existing control center, repo list or docs folder into a CC |
| `cc-kit list` | Your CCs, and which ones still need setup |
| `cc-kit doctor` | Check that everything is installed |
| `cc-kit update` | Update the kit |

Full instructions, every AI tool and troubleshooting: **[GUIDE.md](GUIDE.md)**.

---

# For maintainers

## How a CC works

A CC stores two kinds of knowledge:

- **how to carry out a process**: Nix `packages`, `checks`, `apps` and, where needed, NixOS modules. Build, test and deploy steps are never written down as Markdown: when a process changes, an executable declaration must break.
- **why the system is built this way**: atomic linked Markdown notes in `docs/`. `docs/kit/` is the kit's own layer and is never edited in a CC.

Everything runs through `./cc` in the CC folder (from PowerShell: `.\cc`, which forwards to WSL):

```bash
./cc check                                    # every pure check: builds, tests, notes, skill sync
./cc verify                                   # plus each repo's gate on every target platform
./cc repo add <git-url> --targets linux --stack java --clone
./cc worktree create feature1 repo1 repo2     # task folders in worktrees/feature1/
./cc open feature1                            # start the agent on the feature, with the CC's rules
./cc feature feature1 verify
./cc plan list
./cc settings                                 # agents, models, councils, repos
./cc eval run before --ref main && ./cc eval run after && ./cc eval compare before after
                                              # measure an AGENTS.md or skill change: pass rate, tokens, cost, time
```

Requires Nix with flakes. Docker or Podman is only needed for container workflows.

**Without `cc-kit`:** clone this repo as `cc_template/` inside an empty folder (or one holding a CC in another format), open that folder in your AI tool and say: "Create a Control Center here. Read `cc_template/BOOTSTRAP.md`, treat `cc_template` as read-only and start grilling." AI tools don't find skills inside a nested clone, so the prompt has to name `BOOTSTRAP.md`. `cc-kit new` and `cc-kit migrate` do this for you.

## Where to look

- [AGENTS.md](AGENTS.md): the mandatory protocol for agents.
- [BOOTSTRAP.md](BOOTSTRAP.md): creating and migrating a CC.
- [Knowledge map](docs/index.md): the entry point into `docs/`.
- [Executable processes](docs/kit/rules/executable-processes.md): what counts as the source of truth.
- [Connecting projects](docs/kit/rules/project-adapters.md): the contract for a project repository.
- `nix/projects/` (build adapters per repo), `nix/workflows/` (cross-repo pipelines).

## What the kit includes

| Feature | Where |
|---|---|
| Windows installer: asks for admin only for WSL, resumes after a reboot, adds `cc-kit` to PowerShell, creates the first CC | `install/windows/install.ps1` |
| macOS, Linux and WSL installer: Nix with flakes, systemd on WSL, the kit, Claude Code by default | `install/install.sh` |
| `cc-kit`: `new` and `migrate` open your AI tool with the setup interview started, `open` continues an unfinished setup | `install/cc-kit` |
| A CC that is not set up starts its own setup in every tool | `scripts/rule_hooks.py`, `AGENTS.md`, [decision 0018](docs/kit/decisions/0018-setup-starts-itself.md) |
| Interview skills: `grill-cc-bootstrap` (create, migrate, onboard a repo) and `grill-task-planning` (agent-ready plans) | `.agents/skills/` |
| Skills for every tool: canonical in `.agents/skills`, synced copy in `.claude/skills` for Claude Code | `scripts/agent-configs.py`, `./cc agents` |
| Claude Code hooks that enforce the hard rules when a tool is called | `scripts/rule_hooks.py`, `.claude/settings.json` |
| Per-repo target platforms with a gate each, including a real Windows host gate | `./cc verify`, `ccLib.mkWindowsHostGate` |
| Per-repo `stack` and optional mutation-testing gates | `./cc verify --quality`, `docs/kit/rules/quality-gates.md` |
| Coding, testing and debug councils: Claude and GPT propose or diagnose, an anonymous judge decides, a writer implements | `./cc council`, `catalog/agents`, `catalog/councils` |
| PR review and review-thread judging | `./cc prs`, `cc-review`, `cc-threads` |
| Settings panel (agents and models, councils, repos), also in chat via `cc-settings` | `./cc settings` |
| Feature worktrees and `./cc open <feature>`, so a coding session keeps the CC's skills and rules | `./cc worktree`, `scripts/worktrees.py` |
| Plans with a lifecycle: active, archived, completed | `./cc plan`, `plans/` |
| Trello on demand: link cards, preview then post comments or move cards | `./cc trello`, `cc-trello` |
| `lean-code`, `debug` and `tdd` skills | `.agents/skills/` |
| `cc.cmd`: run `.\cc check` from PowerShell or cmd | `cc.cmd` |
| `./cc eval`: A/B-measure a change to the agent context with headless graded runs (pass rate, tokens, cost, time) | `scripts/eval/`, [decision 0019](docs/kit/decisions/0019-lean-agent-context.md) |

The template contains no UI, no secrets and no production permissions.

## Tools and sources it builds on

**Adapted skills.** These skills are rewritten for the CC, not copied. Each keeps the upstream license next to it and an Origin section naming the exact upstream commit and what was changed.

| Source | License | Used for | What we took |
|---|---|---|---|
| [Ponytail](https://github.com/dietrichgebert/ponytail) by DietrichGebert | MIT | `lean-code` | The "laziest senior dev" ladder: read the problem fully, then stop at the first rung that holds (need it at all, already in the codebase, standard library, native platform feature, installed dependency, one line, minimum new code); root-cause bug fixes; no unrequested abstractions. Dropped: modes, intensity levels, commands. |
| [Superpowers](https://github.com/obra/superpowers) by Jesse Vincent | MIT | `debug`, `tdd` | Systematic debugging (no fix without a root cause, four phases, stop after three failed fixes, root-cause tracing, defense in depth, condition-based waiting) and test-driven development with its rules for honest tests (name the break, hand-derived expectations, no assertions on mocks, the mutation check). Changed: reproduction and verification through `./cc` on every target platform, tests-after allowed when each test is shown to fail. |

**Tools the kit runs.** The CC orchestrates these; it does not vendor them.

| Tool | Role in the CC |
|---|---|
| [Nix](https://nixos.org) with flakes, [nixpkgs](https://github.com/NixOS/nixpkgs) | Every build, test, check and app; pinned inputs; `./cc check`, `./cc verify` |
| Git | Repository catalog, feature worktrees, input overrides |
| Python 3 | The `./cc` scripts: catalog, worktrees, plans, validation, councils |
| WSL2 and PowerShell | Windows host gates (`ccLib.mkWindowsHostGate`) run the real Windows toolchain from WSL |
| [Claude Code](https://github.com/anthropics/claude-code) CLI (`claude`) | Interactive work, and the Claude roles in councils, under the user's own subscription |
| [Codex](https://github.com/openai/codex) CLI (`codex`) | Interactive work, and the GPT roles in councils, under the user's own ChatGPT login |
| Cursor, OpenCode | Supported editors and agents; they read the same `.agents/skills` |
| [Zed](https://zed.dev) | Supported editor; runs Claude Code or Codex through the Agent Client Protocol, with the same skills and hooks |
| Docker or Podman (optional) | Container end-to-end workflows |

Each project repository brings its own test tools through its Nix adapter; the CC prescribes none of them.
