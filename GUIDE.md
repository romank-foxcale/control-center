# Control Center: setup and usage guide

A **Control Center (CC)** is a git repo that sits next to your project repos and teaches AI coding agents how the whole system fits together:

- **how to build, test and run things**: as Nix code, which fails loudly when something drifts;
- **why things are the way they are**: short, linked Markdown notes in `docs/`;
- **how to plan work**: two built-in agent skills that interview you before anything gets changed;
- **how to code and test with several models**: councils in which Claude and GPT each propose, an anonymous judge decides, you approve, and a writer implements until `./cc verify` passes.

You don't need to understand any of this to use it. Install it ([section 1](#1-install)), name your CC, and answer the agent's questions. The agent sets everything else up.

Everyone creates **their own CC** and uses **their own AI tool and account**. The CC holds no credentials. You sign in to your tool as usual and choose any model your subscription includes. Councils call the `claude` and `codex` CLIs with your own logins; which model each council role uses is set in `catalog/agents/*.json` (see [Councils](#councils)).

| You use | Your subscription covers |
|---|---|
| Claude Code (desktop, CLI, VS Code extension) | Claude Pro / Max / Team / Enterprise |
| Cursor | Cursor plan (includes Claude, GPT and other models in Cursor's model picker) |
| Codex (app, CLI, IDE extension) | ChatGPT plan (Plus, Pro, Business, …) or an OpenAI API key |
| OpenCode | Whichever provider you connect. A Claude subscription can't be used here |

---

## 1. Install

You do this once per computer. It takes 10–30 minutes, mostly downloading.

### Windows

Open **PowerShell** (no need to run it as administrator) and paste:

```powershell
irm https://raw.githubusercontent.com/romank-foxcale/command-center/main/install/windows/install.ps1 -OutFile $env:TEMP\cc-install.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\cc-install.ps1
```

What happens:

1. If WSL (Linux on Windows, needed because Nix doesn't run on Windows) is missing, Windows asks for administrator rights in a second window and installs it.
2. Ubuntu asks you to create a **Linux username** (lowercase, no spaces) and **password**. The password stays invisible while you type. Then type `exit`.
3. If Windows needs a restart, restart it. **The setup continues by itself** after you sign in.
4. It installs the rest inside Ubuntu: Nix, the kit and Claude Code. When `sudo` asks for a password, it's your **Linux** password.
5. It adds the `cc-kit` command to PowerShell and asks for a name for your first Control Center. Type one, for example `shop`, and the next step starts by itself.

Want Codex or OpenCode too? Add `-Tools claude,codex,opencode` to the end of the second command.

> Your Control Centers live inside Linux, in `~/Projects`, never under `C:\`. Nix and git are very slow on the Windows drive.

### macOS

In Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/romank-foxcale/command-center/main/install/install.sh | bash
```

It installs the Apple command line tools if they're missing (run it again after that dialog finishes), then Nix, the kit and Claude Code. It ends by asking for your first Control Center's name. Add `-s -- --tools claude,codex,opencode` after `bash` for more tools.

### Check it worked

```bash
cc-kit doctor
```

Everything under *Required* must say `ok`. If `cc-kit` isn't found, open a new terminal.

---

## 2. Create a Control Center

```bash
cc-kit new my-project
```

(On Windows this works in PowerShell too.) This creates `~/Projects/my-project_CC` and **opens Claude with the setup interview already started**. You don't need to type a special prompt; just answer the questions. Section 4 explains what happens next.

- Another tool: `cc-kit new my-project codex` (or `opencode`, `code`, `cursor`, `none`). Set `CC_KIT_TOOL` to change the default.
- Stop any time. `cc-kit open my-project` continues where you left off.
- `cc-kit list` shows your Control Centers and which ones still need setup.

The folder layout, which you never need to touch by hand:

```
~/Projects/
├── my-project_CC/        ← your Control Center (its own git repo)
│   ├── repos/project/    ← clones of the repos this CC changes (git-ignored)
│   ├── repos/reference/  ← read-only reference repos, such as a PoC
│   └── worktrees/<feature>/ ← one folder per task, with a git worktree per changed repo
```

---

## 3. Open it in your AI tool

`cc-kit open my-project` opens it in your default tool. Opening it any other way works too: while setup isn't finished, the agent starts or continues it by itself. Every tool reads the same instructions (`AGENTS.md`) and skills.

### Claude Code desktop

- **Windows:** in the Code tab, open the environment picker and choose **WSL → Ubuntu**. Then open the folder `\\wsl.localhost\Ubuntu\home\<your-linux-user>\Projects\my-project_CC`. The whole session (Claude, its tools, git) runs inside Ubuntu.
  In WSL sessions the integrated terminal, file browser pane, connectors and `@` file suggestions aren't available yet. Use an Ubuntu terminal next to it for `./cc` commands.
- **macOS:** open `~/Projects/my-project_CC`.

### VS Code + Claude Code extension, or VS Code + Codex extension

- **Windows:** run `cc-kit open my-project code` (in PowerShell or Ubuntu). VS Code opens *connected to WSL* (bottom-left corner says `WSL: Ubuntu`).
  Install the Claude Code and/or Codex extension **in that WSL window**; VS Code offers "Install in WSL: Ubuntu". Sign in with your own account.
  If Codex still runs on the Windows side, enable the setting `chatgpt.runCodexInWindowsSubsystemForLinux`.
- **macOS:** `cc-kit open my-project code`, or open the folder normally.

### Cursor

- **Windows:** run `cc-kit open my-project cursor` (in PowerShell or Ubuntu). The Cursor window must show `WSL: Ubuntu` in the bottom-left corner. If the new Agent window doesn't connect to WSL, use the classic editor window (`cursor --classic`).
- **macOS:** open the folder normally.
- Pick any model from Cursor's model picker; it's billed to your Cursor plan.

### Zed

Zed runs Claude Code or Codex in its Agent Panel through the Agent Client Protocol (ACP). They read the same instructions, skills and (Claude Code only) hooks as in a terminal, under your own Claude or ChatGPT login; Zed's own AI plan isn't needed. Start a thread with **Claude Agent** (Claude Code) or **Codex** from the panel's `+` menu, not with **Zed Agent**: it runs none of the CC's hooks.

- **Windows:** in the command palette run `projects: open wsl`, choose **Ubuntu**, then open `~/Projects/my-project_CC`. To check the agent runs inside Ubuntu, run `uname` in Zed's terminal (`` Ctrl+` ``): it must answer `Linux`. A `PS ...\\wsl.localhost\...` prompt means the folder was opened as a Windows path (for example with *File → Open*): close it and open it again with `projects: open wsl`, later from the WSL entry in Recent Projects.
- The agent starts in the project's first top-level folder, so the CC folder must be the only one in Zed's Project panel; opening `~/Projects` gives an agent without the CC's instructions, skills or hooks. A thread keeps the folder it started in: after reopening, start a new thread.
- **macOS:** open the folder normally.

### Codex app or CLI

- **App on Windows:** Settings → switch the agent to **WSL**, restart the app, then open `~/Projects/my-project_CC`.
- **CLI (Windows in Ubuntu, or macOS):** `cd ~/Projects/my-project_CC && codex`. The first time, run `codex login` and sign in with your ChatGPT account.

### OpenCode

`cd ~/Projects/my-project_CC && opencode` (on Windows, in Ubuntu). Connect a provider with `/connect`.

---

## 4. First session: the setup interview

The agent asks about your repos: where they are, how they depend on each other, and **which platforms each one ships on** (Windows, Linux, macOS). It reads the code and CI first and asks only what it can't work out itself. You never need to write Nix or know how the CC works inside. Then it connects the repos one at a time:

1. registers and clones the repo (`./cc repo add`),
2. writes the build and test definition for it, including a check for each platform it ships on,
3. proves it builds and passes (`./cc verify`).

Expect several rounds of questions. When it's done, `cc-kit list` shows the CC as `ready`. Commit it and push it to your own remote. The agent can do that too if you ask.

**Adding a repo later:** say "add `<link to the repo>`". The agent connects it the same way, not just as a list entry.

If the interview doesn't start (for example in a tool that ignores `AGENTS.md`), say "Set up this Control Center", or call the skill directly: `/grill-cc-bootstrap` in Claude Code and Cursor, `$grill-cc-bootstrap` in Codex.

---

## 5. Everyday work

Not sure which skill fits? Ask "what can the CC do?" or type `/cc-help`: it lists the skills by purpose and explains the one you pick.

To plan a bigger task:

> Plan this with the grill-task-planning skill: <what you want to achieve>

The agent turns it into an agreed plan with work packages and checks, saved in `plans/active/`. It won't write code until you accept the plan. Then:

```bash
./cc worktree create my-feature repo1 repo2   # task folders under ~/Projects/my-project_CC/worktrees/my-feature/
./cc open my-feature                          # start Claude Code (or --tool codex) on the feature
./cc feature my-feature verify                # build + test your changed code on every target platform
./cc worktree status my-feature
./cc worktree remove my-feature               # refuses if anything is uncommitted or unpushed
```

**Always start coding with `./cc open my-feature`**, not by opening Claude Code or Codex inside `~/Projects/my-project_CC/worktrees/my-feature/<repo>_wt`. A session started inside a worktree doesn't get the CC's skills (`tdd`, `debug`, `lean-code`, …) or its rules, so it codes without them. `./cc open` starts the tool in the CC with the feature's worktrees added. If you do start a Claude session inside a worktree, it will tell you to restart.

**From Windows without opening Ubuntu:** the `cc.cmd` launcher in the CC folder runs the same commands inside WSL for you, with the same output and exit code. In PowerShell type `.\cc check`, in cmd `cc check`, in Git Bash `./cc.cmd check`. It works whether the CC lives on `C:` or inside Ubuntu (`\\wsl.localhost\Ubuntu\home\...`). With several Linux distributions installed, set `CC_WSL_DISTRO` to the one with Nix.

Other commands, run from the CC folder:

| Command | What it does |
|---|---|
| `./cc verify` | Run every check plus each repo's target platform gates (e.g. the real Windows build) |
| `./cc verify --quality` | Also run each repo's mutation gate: do the tests catch injected bugs? Warns for repos without one |
| `./cc check` | Run every pure check: builds, tests, knowledge notes, skill sync |
| `./cc settings` | Settings panel: agents and their models, councils, repos, with a status line (logins, plans, runs). ←/→ switch tabs, ↑/↓ pick a row, `e` edits it (a role gets a model list of your logged-in subscriptions; a repo its main branch, link, kind, platforms, stack), `a` adds a repo by pasting its link, `d` removes one. `/cc-settings` opens this panel in the desktop app's Terminal panel; `./cc settings show` prints the same as text |
| `./cc council status [run]` | Progress of council runs; for one run also what each working agent is doing right now |
| `./cc doctor` | Check Nix, Docker and the `claude`/`codex` logins |
| `./cc show` | List everything that can be built, checked or run |
| `./cc build <name>` / `./cc run <name>` | Build or run one thing |
| `./cc repo list` | Connected repos: kind, main branch, status |
| `./cc repo update` | Fetch the base clones; fast-forward the read-only reference clones |
| `./cc prs` | Open PRs of the connected repos, new ones marked (or ask "any new PRs?") |
| `./cc plan list` | Plans in progress, archived, completed |
| `./cc validate` | Check the `docs/` notes only (works without Nix) |
| `./cc agents sync` | After editing a skill in `.agents/skills/`, copy it to `.claude/skills/` |

### Trello (optional, on demand)

Link plans and features to Trello cards and update them when **you** ask; nothing is posted automatically.

1. Get a Trello API key and token (Trello's Power-Up admin page, then its token link) and put them in `~/.config/cc/trello.env` (never in a repo):
   ```
   TRELLO_API_KEY=...
   TRELLO_TOKEN=...
   ```
2. Connect the CC to a board: in `./cc settings` open the Trello tab and press `e`, or ask `/cc-settings`, or run `./cc trello connect --board <board url>` (default lists: "In progress" for active plans, "Done" for completed).
3. Link cards: `./cc trello link --plan <id> <card url>`, `./cc trello link --feature <name> <card url>`, or `./cc worktree create <feature> <repo> --card <card url>`.
4. Ask in your session: "post the verdict to the card", "move the card to Done". You see the exact comment or move first; it is sent only after your yes.

### Councils

A council makes Claude and GPT work on the same task. It needs both CLIs installed and logged in inside Ubuntu (`claude auth login`, `codex login`); `./cc doctor` shows their state.

> Run a coding council on my-feature: <what you want>

1. Both models study the worktree and propose an approach, without editing.
2. A judge compares them as "Proposal A" and "Proposal B", without knowing who wrote which, and picks one or a hybrid.
While it works, the terminal shows a live line with a progress bar, the current step, which models are still working and timers, for example `⠹ debug ▰▰▰▱▱▱▱▱▱ 3/9 propose claude claude-opus-5-5 ✓ · codex gpt-6.1-sol … step 02:31 · total 04:10`; `./cc council status <run>` shows the same plus each working agent's latest actions, and in a session you can just ask "how is it going?".
3. The run stops and shows you the verdict and who wrote what. Approve it, pick the other proposal, add a note, or reject it.
4. The writer implements it in the worktree, reusing existing code first; `./cc feature my-feature verify` must pass, with up to 2 retries.
5. A security reviewer reads the diff. You review `changes.diff` and commit it yourself.

A testing council works the same way: both models write tests, the judge merges them, and a failing new test is reported as a suspected bug.

A debug council finds and fixes a bug: `./cc council run debug --feature my-feature --task "<exact symptom>"`. The CC first runs verify and hands its output to both models; each diagnoses the root cause with evidence; the judge picks the best-evidenced one; after your approval the writer adds a reproduction test that must fail, and only then fixes the cause. After three failed fixes it stops and asks you to rethink the design.

A risk council audits a change before anyone plans or builds it:

> What could go wrong if we switch ai from TEI to PyTorch for embeddings?

The agent runs `./cc council risk --repo ai --task "<the change and its reason>"`. It needs no feature. Claude and GPT each imagine the change shipped and failed, and write how: the errors it would show, incompatibilities, performance, data, rollback. Every risk cites the code, a note or a web source, or is marked as an assumption. A judge checks the claims, without knowing who made them, and writes one ranked register to `.cc-local/runs/<run>/risk.md`, with experiments to run first and a verdict: GO, GO-WITH-CONDITIONS or NO-GO. Planning then turns the register into the plan's checks. The agent offers this on its own before a change to architecture, dependencies, runtime, data or deployment.

Which model plays which role is one field per file in `catalog/agents/` (for example `"model": "claude-opus-5-5"` in `writer.json`; an alias such as `opus` works but follows the newest model, so `./cc check` warns about it); `catalog/councils/` sets the roles, the approval pause and the retry limit. Change them with `/cc-settings` in your session, in `./cc settings` (on Agents, select a role, press `e` and pick a model from the list), or with `./cc council set-role` and `./cc council set`. Run files are kept in `worktrees/<feature>/.cc-runs/` inside the CC.

---

## 6. Migrating an existing CC

If you already have a control center, repo catalog or docs in another format:

```bash
cc-kit migrate ~/Projects/old-thing
```

This puts a temporary copy of the kit in `old-thing/cc_template` and opens your AI tool with the migration interview started. The agent inventories what exists without changing anything first, then moves it over one piece at a time. When it's done it tells you to delete `cc_template/`. Move the folder out of `/mnt/c/...` first if it lives on the Windows drive.

---

## 7. Docker (only for container workflows)

- **Windows:** install Docker Desktop, then Settings → Resources → **WSL Integration** → enable **Ubuntu**. Don't also install Docker inside Ubuntu.
- **macOS:** Docker Desktop or OrbStack works for *running* containers. *Building* Linux images with Nix on a Mac needs a Linux builder (for example nix-darwin's `nix.linux-builder.enable = true;`) or CI. Container checks are skipped on macOS until you set one up.

---

## 8. Updating the kit

```bash
cc-kit update
```

This updates `~/.cc-kit` only. Existing CCs are separate repos and never change behind your back. To pull a kit improvement into a CC, ask your agent to compare the CC with `~/.cc-kit` and bring over the change. The kit's knowledge lives in `docs/kit/`, which a CC never edits, so that part is a plain replacement; your own decisions, notes and plans in the rest of `docs/` and `plans/` are left alone.

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `Python was not found` / Microsoft Store opens | You're in PowerShell or Git Bash, not Ubuntu. Run commands in Ubuntu. |
| `cc-kit: command not found` | Open a new terminal (a new PowerShell window on Windows), or run `source ~/.bashrc` in Ubuntu. |
| The installer stopped after a restart | Sign in again and wait a moment, or run the one-line installer again: it continues where it stopped. |
| `$'\r': command not found` | The repo was cloned on the Windows side with Windows line endings. Clone it again from inside Ubuntu. |
| `Nix is not installed` right after installing | Open a new terminal. |
| `Nix flakes are disabled` | Re-run `~/.cc-kit/install/install.sh`. |
| Everything is very slow | Your CC is under `/mnt/c`. Move it to `~/Projects`. |
| Nix can't see a new file | Nix only sees files git knows about: `git add <file>`. |
| Installer stops after "enabling systemd" | Run `wsl --shutdown` in PowerShell, then run the installer again. |
| The tool doesn't list the skills | Run `./cc agents check` in the CC and make sure you opened the CC folder itself, not `~/Projects`. |
