#!/usr/bin/env bash
set -euo pipefail

cc_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  printf '%s\n' \
    'Usage: ./cc <command> [arguments]' \
    '' \
    'Commands:' \
    '  doctor         Check local prerequisites' \
    '  show           Show flake packages, checks and apps' \
    '  check          Evaluate and build every flake check' \
    '  build <name>   Build .#<name>' \
    '  run <name>     Run .#<name>' \
    '  verify [repo...] [--quality] [--full]  Run every check plus each catalog target gate (and quality gates)' \
    '  council <run|approve|reject|status|models|set-role|set> ...  Multi-model councils and their settings' \
    '  watch [run]    Follow a council or verify run live: steps, agent talk, stalls, result' \
    '  eval <run|compare|trace|regrade> ...  Measure an agent-context change: headless claude runs, graded, with tokens, cost and time' \
    '  settings [show]  Settings panel: agents and models, councils, repos; show prints them as text' \
    '  validate       Validate the Markdown knowledge graph' \
    '  agents <sync|check>  Mirror .agents/skills into .claude/skills' \
    '  bootstrap install [target]  Materialize a new CC from this clone' \
    '  bootstrap validate          Validate the configured CC contract' \
    '  repo <add|list|show|remove|set-branch|set-remote|set-kind|set-status|set-targets|set-stack|strays> ...' \
    '  prs [repo...] [--json]  Open pull requests of the catalog repos, newest first, new ones and waiting threads marked' \
    '  prs show <repo> <n> [--diff] | prs checkout <repo> <n>  One PR for review; its head as feature pr-<repo>-<n>' \
    '  prs threads|judge <repo> <n> [--all] [--json]  Review threads waiting on you; judge them with anonymous sceptics' \
    '  prs learn [repo...] [--since YYYY-MM-DD] [--harvest-only] [--json]  Accepted review comments of merged PRs since the last run; the review-learner agent turns them into candidate lessons' \
    '  worktree <create|add|status|remove> ...' \
    '  migrate-layout [--dry-run]  Move this CC'"'"'s clones and worktrees from ../repos and ../worktrees into repos/ and worktrees/' \
    '  trello <status|boards|lists|connect|link|show|comment|move> ...  Trello cards, on demand only' \
    '  open <feature> [--tool claude|codex]  Start a coding session in the CC with the feature'"'"'s worktrees' \
    '  plan <create|list|show|accept|archive|complete> ...' \
    '  feature <name> <show|check|build|run|verify> ...  Use feature input overrides'
}

require_nix() {
  if ! command -v nix >/dev/null 2>&1; then
    printf '%s\n' 'error: Nix is not installed; see https://nixos.org/download/' >&2
    exit 1
  fi
}

# Write or refresh flake.lock before evaluating: when an evaluation creates the lock
# itself, Nix sees the source tree change mid-evaluation and aborts.
lock_flake() {
  nix flake lock "$cc_root" --quiet
}

nix_override_args=()
load_feature_overrides() {
  if [[ -z "${CC_FEATURE:-}" ]]; then
    return
  fi
  override_output="$(python3 "$cc_root/scripts/worktrees.py" "$cc_root" nix-args "$CC_FEATURE")"
  if [[ -n "$override_output" ]]; then
    while IFS= read -r argument; do
      nix_override_args+=("$argument")
    done <<< "$override_output"
  fi
}

command_name="${1:-help}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "$command_name" in
  doctor)
    require_nix
    nix --version
    if command -v docker >/dev/null 2>&1; then
      docker --version
    elif command -v podman >/dev/null 2>&1; then
      podman --version
    else
      printf '%s\n' 'note: Docker/Podman is absent; container E2E apps will be unavailable'
    fi
    if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
      printf '%s\n' 'gh: logged in'
    else
      printf '%s\n' 'note: gh is missing or not logged in; ./cc prs will be unavailable'
    fi
    python3 "$cc_root/scripts/council/providers.py" status
    python3 "$cc_root/scripts/repositories.py" "$cc_root" strays || true
    ;;
  show)
    require_nix
    lock_flake
    load_feature_overrides
    exec nix flake show "${nix_override_args[@]}" "$cc_root" "$@"
    ;;
  check)
    require_nix
    lock_flake
    load_feature_overrides
    status=0
    nix flake check "${nix_override_args[@]}" "$cc_root" --keep-going "$@" || status=$?
    # A pass stamps the CC's tree, which the push hook looks for. Why: docs/kit/decisions/0028-verify-before-push.md
    if [[ "$status" -eq 0 ]] && command -v python3 >/dev/null; then
      python3 "$cc_root/scripts/verified.py" "$cc_root" record "$cc_root" check
    fi
    # Notes whose evidence changed after verified_at: a warning, never a failure. It needs the Git history, which
    # the Nix sandbox lacks. Why: docs/kit/decisions/0023-stale-evidence-warning.md
    if command -v python3 >/dev/null; then
      python3 "$cc_root/scripts/validate-knowledge.py" --stale "$cc_root" || true
    fi
    exit "$status"
    ;;
  build)
    require_nix
    lock_flake
    target="${1:?usage: ./cc build <name>}"
    shift
    load_feature_overrides
    exec nix build "${nix_override_args[@]}" "${cc_root}#$target" "$@"
    ;;
  run)
    require_nix
    lock_flake
    target="${1:?usage: ./cc run <name>}"
    shift
    load_feature_overrides
    exec nix run "${nix_override_args[@]}" "${cc_root}#$target" -- "$@"
    ;;
  verify)
    require_nix
    lock_flake
    exec python3 "$cc_root/scripts/verify.py" "$cc_root" "$@"
    ;;
  validate)
    exec python3 "$cc_root/scripts/validate-knowledge.py" "$cc_root"
    ;;
  agents)
    exec python3 "$cc_root/scripts/agent-configs.py" "$cc_root" "${1:-check}"
    ;;
  bootstrap)
    bootstrap_command="${1:-help}"
    if [[ $# -gt 0 ]]; then
      shift
    fi
    case "$bootstrap_command" in
      install)
        exec python3 "$cc_root/scripts/materialize-template.py" "${1:-.}"
        ;;
      validate)
        exec python3 "$cc_root/scripts/validate-control-center.py" "$cc_root"
        ;;
      *)
        printf '%s\n' 'usage: ./cc bootstrap <install [target]|validate>' >&2
        exit 2
        ;;
    esac
    ;;
  repo)
    exec python3 "$cc_root/scripts/repositories.py" "$cc_root" "$@"
    ;;
  prs)
    exec python3 "$cc_root/scripts/prs.py" "$cc_root" "$@"
    ;;
  worktree)
    exec python3 "$cc_root/scripts/worktrees.py" "$cc_root" "$@"
    ;;
  migrate-layout)
    exec python3 "$cc_root/scripts/migrate_layout.py" "$cc_root" "$@"
    ;;
  trello)
    exec python3 "$cc_root/scripts/trello.py" "$cc_root" "$@"
    ;;
  open)
    exec python3 "$cc_root/scripts/worktrees.py" "$cc_root" open "$@"
    ;;
  watch)
    exec python3 "$cc_root/scripts/events.py" "$cc_root" "$@"
    ;;
  council)
    exec python3 "$cc_root/scripts/council/run.py" "$cc_root" "$@"
    ;;
  eval)
    require_nix
    exec python3 "$cc_root/scripts/eval/run.py" "$cc_root" "$@"
    ;;
  settings)
    require_nix
    lock_flake
    exec nix run "$cc_root#settings" -- "$cc_root" "$@"
    ;;
  plan)
    exec python3 "$cc_root/scripts/plans.py" "$cc_root" "$@"
    ;;
  feature)
    feature_name="${1:?usage: ./cc feature <name> <show|check|build|run|verify> [arguments]}"
    shift
    if [[ $# -eq 0 ]]; then
      printf '%s\n' 'error: feature requires a command' >&2
      exit 2
    fi
    export CC_FEATURE="$feature_name"
    exec "$cc_root/cc" "$@"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    printf 'error: unknown command: %s\n' "$command_name" >&2
    usage >&2
    exit 2
    ;;
esac
