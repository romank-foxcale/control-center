#!/usr/bin/env bash
# Control Center kit installer for macOS, Linux and WSL2 (Ubuntu/Debian).
# Safe to re-run: every step checks what is already installed.
#
#   curl -fsSL https://raw.githubusercontent.com/romank-foxcale/command-center/main/install/install.sh | bash
#   ~/.cc-kit/install/install.sh --tools claude,codex,opencode   (default: claude; none installs no tool)
set -euo pipefail

kit_repo="${CC_KIT_REPO:-https://github.com/romank-foxcale/command-center.git}"
kit_branch="${CC_KIT_BRANCH:-main}"
kit_dir="${CC_KIT_DIR:-$HOME/.cc-kit}"
# Claude Code by default, so a newcomer has an agent to talk to when the install ends.
tools="claude"

# Exit code the Windows installer recognises as "restart WSL, then run me again".
EXIT_RESTART_WSL=10

step() { printf '\n==> %s\n' "$1"; }
note() { printf '    %s\n' "$1"; }
die() { printf 'error: %s\n' "$1" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tools) tools="${2:?--tools needs a comma-separated list}"; shift 2 ;;
    --tools=*) tools="${1#--tools=}"; shift ;;
    -h | --help)
      printf 'Usage: install.sh [--tools claude,codex,opencode|none]   (default: claude)\n'
      exit 0
      ;;
    *) die "unknown option: $1" ;;
  esac
done

os="$(uname -s)"
is_wsl=false
grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null && is_wsl=true

load_nix() {
  local profile
  for profile in \
    /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh \
    "$HOME/.nix-profile/etc/profile.d/nix.sh"; do
    # shellcheck disable=SC1090
    [[ -r "$profile" ]] && . "$profile" && return 0
  done
  return 0
}

# 1. Base packages.
step "Checking base tools"
case "$os" in
  Darwin)
    if ! xcode-select -p >/dev/null 2>&1; then
      note "Installing Apple command line tools (git, python3). A dialog will open."
      xcode-select --install || true
      die "finish the Apple command line tools install, then run this script again"
    fi
    ;;
  Linux)
    missing=()
    for command in git curl python3 xz; do
      command -v "$command" >/dev/null || missing+=("$command")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
      command -v apt-get >/dev/null || die "install these with your package manager, then re-run: ${missing[*]}"
      note "Installing: ${missing[*]} (sudo will ask for your Linux password)"
      sudo apt-get update -qq
      sudo apt-get install -y -qq git curl python3 xz-utils ca-certificates
    fi
    ;;
  MINGW* | MSYS* | CYGWIN*) die "this is Git Bash on Windows; run install/windows/install.ps1 in PowerShell instead" ;;
  *) die "unsupported system: $os (use macOS, Linux or WSL2)" ;;
esac
note "git, curl and python3 are present"

# 2. WSL specifics: Linux filesystem and systemd (the Nix daemon needs it).
if $is_wsl; then
  step "Checking WSL"
  if [[ "$PWD" == /mnt/* ]]; then
    note "You are on the Windows drive ($PWD). Control Centers will live in ~/Projects instead."
  fi
  if [[ "$(ps -p 1 -o comm= 2>/dev/null)" != "systemd" ]]; then
    note "Enabling systemd in /etc/wsl.conf"
    if [[ -f /etc/wsl.conf ]] && grep -q '^\[boot\]' /etc/wsl.conf; then
      sudo sed -i '/^\[boot\]/a systemd=true' /etc/wsl.conf
    else
      printf '\n[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf >/dev/null
    fi
    note "WSL must restart. In PowerShell run 'wsl --shutdown', then run this script again."
    exit "$EXIT_RESTART_WSL"
  fi
  note "systemd is running"
fi

# 3. Nix, upstream installer, multi-user.
step "Checking Nix"
load_nix
if ! command -v nix >/dev/null; then
  note "Installing Nix from nixos.org (sudo will ask for your password)"
  curl --proto '=https' --tlsv1.2 -sSfL https://nixos.org/nix/install -o /tmp/nix-install.sh
  sh /tmp/nix-install.sh --daemon --yes
  rm -f /tmp/nix-install.sh
  load_nix
  command -v nix >/dev/null || die "Nix installed but not on PATH; open a new terminal and re-run"
fi
note "$(nix --version)"

# Flakes are off by default upstream; enable them for this user only.
if ! nix config show experimental-features 2>/dev/null | grep -qw flakes; then
  mkdir -p "$HOME/.config/nix"
  printf 'experimental-features = nix-command flakes\n' >> "$HOME/.config/nix/nix.conf"
  note "Enabled Nix flakes in ~/.config/nix/nix.conf"
fi
note "Nix flakes enabled"

# 4. The kit itself. When run from a clone, use that clone.
step "Installing the kit"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [[ -n "$script_dir" && -x "$script_dir/cc-kit" && -d "$script_dir/../.git" ]]; then
  kit_dir="$(cd -- "$script_dir/.." && pwd)"
elif [[ -d "$kit_dir/.git" ]]; then
  git -C "$kit_dir" pull --ff-only --quiet
else
  git clone --quiet --branch "$kit_branch" "$kit_repo" "$kit_dir"
fi
mkdir -p "$HOME/.local/bin" "${CC_PROJECTS_ROOT:-$HOME/Projects}"
ln -sf "$kit_dir/install/cc-kit" "$HOME/.local/bin/cc-kit"
note "kit: $kit_dir"
note "command: ~/.local/bin/cc-kit"

path_line='export PATH="$HOME/.local/bin:$PATH"'
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  [[ "$rc" == "$HOME/.zshrc" && "$os" != Darwin && ! -f "$rc" ]] && continue
  touch "$rc"
  grep -qF "$path_line" "$rc" || printf '\n%s\n' "$path_line" >> "$rc"
done
export PATH="$HOME/.local/bin:$PATH"

# 5. Optional command-line AI tools. Desktop apps are installed by each person.
install_tool() {
  case "$1" in
    claude)
      command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash
      ;;
    opencode)
      command -v opencode >/dev/null || curl -fsSL https://opencode.ai/install | bash
      ;;
    codex)
      if command -v codex >/dev/null; then return; fi
      if command -v npm >/dev/null; then
        npm install -g @openai/codex
      elif [[ "$os" == Darwin ]] && command -v brew >/dev/null; then
        brew install --cask codex
      else
        nix profile add nixpkgs#codex 2>/dev/null || nix profile install nixpkgs#codex
      fi
      ;;
    "" | none) ;;
    *) die "unknown tool '$1' (choose from claude, codex, opencode)" ;;
  esac
}
if [[ -n "$tools" ]]; then
  step "Installing AI command-line tools: $tools"
  IFS=',' read -ra requested <<< "$tools"
  for tool in "${requested[@]}"; do
    install_tool "$(printf '%s' "$tool" | tr -d ' ')"
    note "$tool ready"
  done
fi

# 6. Final check.
step "Checking everything"
"$kit_dir/install/cc-kit" doctor || true

# 7. The first Control Center right away, unless one exists or nobody is at the keyboard
# (curl | bash has no stdin, so ask on the terminal itself).
shopt -s nullglob
existing=("${CC_PROJECTS_ROOT:-$HOME/Projects}"/*_CC/control-center.json)
shopt -u nullglob
if [[ ${#existing[@]} -eq 0 && -z "${CC_KIT_NO_FIRST:-}" ]] && { : </dev/tty; } 2>/dev/null; then
  printf '\nDone. Now create your first Control Center.\n'
  printf 'Name it after the product or system it covers, in lowercase with dashes (for example: shop).\n'
  printf 'Name (Enter to skip): '
  first_name=""
  read -r first_name </dev/tty || true
  if [[ -n "$first_name" ]]; then
    # macOS kqueue rejects /dev/tty (EINVAL), which crashes Claude Code (Bun) at start. Under curl | bash stdout is
    # still the terminal, so give the tool that device (/dev/ttys003) instead; fd 3 carries it past $( ).
    terminal=/dev/tty
    if [[ "$os" == Darwin ]] && ! { terminal="$(tty <&3 2>/dev/null)"; } 3>&1; then
      printf '\nDone. Open a new terminal and run:\n    cc-kit new %s\n' "$first_name"
      exit 0
    fi
    exec "$kit_dir/install/cc-kit" new "$first_name" <"$terminal"
  fi
fi

printf '\nDone. Create a Control Center any time with:\n'
printf '    cc-kit new my-project\n'
printf 'It opens your AI tool and the agent walks you through the setup. More: %s/GUIDE.md\n' "$kit_dir"
