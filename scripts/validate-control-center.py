#!/usr/bin/env python3
"""Validate the machine-readable Control Center bootstrap contract."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
CC_STATUSES = {"discovery", "adapting", "ready"}
REPOSITORY_STATUSES = {"discovered", "adapted", "verified", "blocked"}
REPOSITORY_KINDS = {"project", "reference"}
TARGETS = {"windows", "linux", "macos"}
# Must match scripts/council/providers.py.
PROVIDERS = {"claude", "codex", "cursor"}
# Model names that silently move to a newer model, so a role's behaviour changes with no change here
# (docs/kit/decisions/0024-pinned-model-ids.md).
FLOATING_MODELS = {"claude": {"opus", "sonnet", "haiku"}, "cursor": {"auto"}}
ACCESS_LEVELS = {"read-only", "read-only-web", "write-worktree"}
COUNCIL_KINDS = {"coding", "testing", "debug", "threads", "risk"}
EXPECTED_LAYOUT = {
    "projectsRoot": ".",
    "repositories": "repos",
    "worktrees": "worktrees",
    "worktreePattern": "{feature}/{repository}_wt",
}
PLAN_LIFECYCLES = ("active", "archived", "completed")
# Machine paths in the committed manifest start at a per-user root, never at an absolute path
# (docs/kit/decisions/0030-disk-management.md).
USER_ROOTS = ("~/", "$HOME/", "$XDG_CACHE_HOME/", "%LOCALAPPDATA%\\", "%LOCALAPPDATA%/", "%USERPROFILE%\\", "%USERPROFILE%/")


def load_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"{path}: required file is missing")
        return None
    except json.JSONDecodeError as error:
        errors.append(f"{path}: invalid JSON: {error}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{path}: root must be an object")
        return None
    return value


def placeholder_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, str) and ("__" in value or value == "replace-me"):
        paths.append(prefix)
    elif isinstance(value, dict):
        for key, child in value.items():
            paths.extend(placeholder_paths(child, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(placeholder_paths(child, f"{prefix}[{index}]"))
    return paths


def validate_targets(
    relative: Path,
    data: dict[str, Any],
    adapter_targets: dict[str, Any] | None,
    errors: list[str],
) -> None:
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets or not all(isinstance(target, str) for target in targets):
        errors.append(
            f"{relative}: 'targets' must list the platforms it ships on ({', '.join(sorted(TARGETS))}); "
            f"set it with ./cc repo set-targets {data.get('id')} <target>..."
        )
        return
    for target in sorted(set(targets) - TARGETS):
        errors.append(f"{relative}: unsupported target '{target}'")
    if len(set(targets)) != len(targets):
        errors.append(f"{relative}: duplicate targets")
    # Without the evaluated adapters (plain ./cc bootstrap validate) only the catalog is checked.
    if adapter_targets is None or data.get("status") not in {"adapted", "verified"}:
        return
    declared = adapter_targets.get(str(data.get("id")), {})
    for target in sorted(set(targets) & TARGETS):
        if target not in declared:
            errors.append(
                f"{relative}: target '{target}' has no gate; declare targets.{target} "
                f"(a check or an app) in {data.get('adapter')}"
            )


def validate_repository(
    root: Path,
    path: Path,
    errors: list[str],
    adapter_targets: dict[str, Any] | None = None,
) -> str | None:
    data = load_json(path, errors)
    if data is None:
        return None
    relative = path.relative_to(root)
    identifier = data.get("id")
    if not isinstance(identifier, str) or not ID.fullmatch(identifier):
        errors.append(f"{relative}: id must use lowercase hyphen-case")
        return None
    if path.stem != identifier:
        errors.append(f"{relative}: filename must equal id")
    if data.get("schemaVersion") != 1:
        errors.append(f"{relative}: schemaVersion must be 1")
    if data.get("status") not in REPOSITORY_STATUSES:
        errors.append(f"{relative}: unsupported status '{data.get('status')}'")
    for field in ("remote", "defaultBranch", "role", "sourceInput", "adapter"):
        if not isinstance(data.get(field), str) or not data[field]:
            errors.append(f"{relative}: '{field}' must be a non-empty string")
    remote = data.get("remote")
    if isinstance(remote, str) and Path(remote).is_absolute():
        errors.append(f"{relative}: remote must not be an absolute local path")
    adapter = data.get("adapter")
    if isinstance(adapter, str) and (Path(adapter).is_absolute() or ".." in Path(adapter).parts):
        errors.append(f"{relative}: adapter must stay inside the Control Center")
    source_input = data.get("sourceInput")
    if isinstance(source_input, str) and not ID.fullmatch(source_input):
        errors.append(f"{relative}: sourceInput must use lowercase hyphen-case")
    checkout = data.get("checkout")
    if not isinstance(checkout, str) or checkout != identifier:
        errors.append(f"{relative}: checkout must equal id to preserve worktree naming")
    kind = data.get("kind", "project")
    if kind not in REPOSITORY_KINDS:
        errors.append(f"{relative}: unsupported kind '{kind}'; use project or reference")
    if kind == "reference":
        # Read-only guidance: never built, verified or committed to, so it needs no adapter, targets or stack.
        for field in placeholder_paths(data):
            errors.append(f"{relative}: unresolved placeholder at {field}")
        return identifier
    if data.get("status") in {"adapted", "verified"}:
        adapter = data.get("adapter")
        if isinstance(adapter, str) and not (root / adapter).is_file():
            errors.append(f"{relative}: adapter does not exist: {adapter}")
    validate_targets(relative, data, adapter_targets, errors)
    stack = data.get("stack")
    if not isinstance(stack, list) or not stack or not all(isinstance(entry, str) and ID.fullmatch(entry) for entry in stack):
        errors.append(
            f"{relative}: 'stack' must list the repository's languages in lowercase hyphen-case; "
            f"set it with ./cc repo set-stack {identifier} <language>..."
        )
    for field in placeholder_paths(data):
        errors.append(f"{relative}: unresolved placeholder at {field}")
    return identifier


def validate_descriptor_group(root: Path, group: str, errors: list[str]) -> None:
    directory = root / "catalog" / group
    if not directory.is_dir():
        errors.append(f"catalog/{group}: directory is missing")
        return
    for path in sorted(directory.glob("*.json")):
        data = load_json(path, errors)
        if data is None:
            continue
        relative = path.relative_to(root)
        identifier = data.get("id")
        if not isinstance(identifier, str) or not ID.fullmatch(identifier):
            errors.append(f"{relative}: id must use lowercase hyphen-case")
        elif path.stem != identifier:
            errors.append(f"{relative}: filename must equal id")
        if data.get("schemaVersion") != 1:
            errors.append(f"{relative}: schemaVersion must be 1")
        adapter = data.get("adapter")
        if isinstance(adapter, str) and (Path(adapter).is_absolute() or ".." in Path(adapter).parts):
            errors.append(f"{relative}: adapter must stay inside the Control Center")
        if not isinstance(adapter, str) or not (root / adapter).is_file():
            errors.append(f"{relative}: adapter does not exist: {adapter}")
        for field in placeholder_paths(data):
            errors.append(f"{relative}: unresolved placeholder at {field}")


def validate_agents(root: Path, errors: list[str]) -> dict[str, dict[str, Any]]:
    """Validate catalog/agents (optional): one role per file, bound to a provider CLI and model."""
    roles: dict[str, dict[str, Any]] = {}
    directory = root / "catalog" / "agents"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        data = load_json(path, errors)
        if data is None:
            continue
        relative = path.relative_to(root)
        identifier = data.get("id")
        if not isinstance(identifier, str) or not ID.fullmatch(identifier) or path.stem != identifier:
            errors.append(f"{relative}: id must use lowercase hyphen-case and equal the filename")
            continue
        if data.get("schemaVersion") != 1:
            errors.append(f"{relative}: schemaVersion must be 1")
        if data.get("provider") not in PROVIDERS:
            errors.append(f"{relative}: provider must be one of {', '.join(sorted(PROVIDERS))}")
        if not isinstance(data.get("model"), str) or not data["model"].strip():
            errors.append(f"{relative}: 'model' must be a non-empty string")
        if data.get("access") not in ACCESS_LEVELS:
            errors.append(f"{relative}: access must be one of {', '.join(sorted(ACCESS_LEVELS))}")
        elif data.get("provider") == "cursor" and data["access"] != "read-only":
            # Cursor's write limits are not enforced by the CC (scripts/council/providers.py).
            errors.append(f"{relative}: cursor roles must be read-only")
        cap = data.get("maxToolCalls", 1)  # optional; scripts/council/providers.py has the default
        if not isinstance(cap, int) or isinstance(cap, bool) or not 1 <= cap <= 1000:
            errors.append(f"{relative}: 'maxToolCalls' must be an integer from 1 to 1000")
        skills = data.get("skills")
        if not isinstance(skills, list) or not all(isinstance(skill, str) for skill in skills):
            errors.append(f"{relative}: 'skills' must be a list of skill names")
        else:
            for skill in skills:
                if not any((root / ".agents" / folder / skill / "SKILL.md").is_file() for folder in ("roles", "skills")):
                    errors.append(f"{relative}: skill does not exist in .agents/roles or .agents/skills: {skill}")
        roles[identifier] = data
    return roles


def floating_models(root: Path) -> list[str]:
    """Warnings, never errors, for roles in catalog/agents that use a floating model alias."""
    warnings = []
    for path in sorted((root / "catalog" / "agents").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # validate_agents reports it
        provider, model = data.get("provider"), data.get("model")
        if model in FLOATING_MODELS.get(provider, ()):
            warnings.append(f"{path.relative_to(root).as_posix()}: model '{model}' follows the newest {provider} model;"
                            " pin a full model id")
    return warnings


def validate_trello(root: Path, errors: list[str]) -> None:
    """catalog/integrations/trello.json (optional): the board and list mapping, never credentials."""
    path = root / "catalog" / "integrations" / "trello.json"
    if not path.is_file():
        return
    data = load_json(path, errors)
    if data is None:
        return
    relative = path.relative_to(root)
    if not isinstance(data.get("board"), str) or not data["board"]:
        errors.append(f"{relative}: 'board' must be the board's short id")
    lists = data.get("lists")
    if not isinstance(lists, dict) or not all(isinstance(lists.get(key), str) and lists[key] for key in ("active", "completed")):
        errors.append(f"{relative}: 'lists' must map active and completed to list names")
    for key in data:
        if re.search(r"key|token|secret", key, re.IGNORECASE):
            errors.append(f"{relative}: '{key}' looks like a credential; credentials belong in ~/.config/cc/trello.env")


def validate_councils(root: Path, roles: dict[str, dict[str, Any]], errors: list[str]) -> None:
    """Validate catalog/councils (optional): which roles fill each slot of a fixed pipeline."""
    directory = root / "catalog" / "councils"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        data = load_json(path, errors)
        if data is None:
            continue
        relative = path.relative_to(root)
        if not isinstance(data.get("id"), str) or path.stem != data["id"]:
            errors.append(f"{relative}: id must equal the filename")
        if data.get("schemaVersion") != 1:
            errors.append(f"{relative}: schemaVersion must be 1")
        if data.get("kind") not in COUNCIL_KINDS:
            errors.append(f"{relative}: kind must be one of {', '.join(sorted(COUNCIL_KINDS))}")

        def require_role(slot: str, value: Any, access: str, *others: str) -> None:
            if value not in roles:
                errors.append(f"{relative}: {slot} '{value}' is not a role in catalog/agents")
            elif roles[value].get("access") not in (access, *others):
                errors.append(f"{relative}: {slot} '{value}' must have access '{access}'"
                              + "".join(f" or '{other}'" for other in others))

        if data.get("kind") == "risk":
            # Assessors audit a planned change and the judge merges their registers (./cc council risk). Nothing
            # is written, so web search is the only access beyond reading (docs/kit/decisions/0029-risk-council.md).
            assessors = data.get("assessors")
            if not isinstance(assessors, list) or len(assessors) < 2 or len(set(map(str, assessors))) != len(assessors):
                errors.append(f"{relative}: 'assessors' must list at least two distinct roles")
            else:
                for assessor in assessors:
                    require_role("assessor", assessor, "read-only", "read-only-web")
            require_role("judge", data.get("judge"), "read-only", "read-only-web")
            continue

        if data.get("kind") == "threads":
            # Sceptics judge review threads (./cc prs judge); the judge settles their splits. No writer, approval or retries.
            sceptics = data.get("sceptics")
            if not isinstance(sceptics, list) or not sceptics or len(set(map(str, sceptics))) != len(sceptics):
                errors.append(f"{relative}: 'sceptics' must list one or more distinct roles")
                continue
            for sceptic in sceptics:
                require_role("sceptic", sceptic, "read-only")
            if len(sceptics) > 1 or data.get("judge") is not None:
                require_role("judge", data.get("judge"), "read-only")
            continue
        if not isinstance(data.get("approval"), bool):
            errors.append(f"{relative}: 'approval' must be true or false")
        retries = data.get("maxRetries")
        if not isinstance(retries, int) or isinstance(retries, bool) or not 0 <= retries <= 5:
            errors.append(f"{relative}: 'maxRetries' must be an integer from 0 to 5")

        proposers = data.get("proposers")
        if not isinstance(proposers, list) or len(proposers) < 2 or len(set(map(str, proposers))) != len(proposers):
            errors.append(f"{relative}: 'proposers' must list at least two distinct roles")
        else:
            for proposer in proposers:
                require_role("proposer", proposer, "read-only")
        require_role("judge", data.get("judge"), "read-only")
        require_role("writer", data.get("writer"), "write-worktree")
        # null or [] for none, one role id (catalogs older than the list), or a list of distinct reviewers.
        security = data.get("security")
        reviewers = [security] if isinstance(security, str) else security if isinstance(security, list) else []
        if (security is not None and not isinstance(security, (str, list))) or len(set(map(str, reviewers))) != len(reviewers):
            errors.append(f"{relative}: 'security' must be null, a role or a list of distinct roles")
        for reviewer in reviewers:
            require_role("security", reviewer, "read-only")


def validate_disk(manifest: dict[str, Any], errors: list[str]) -> None:
    """localData, localCaches and backup (all optional): what doctor --disk measures and where ./cc backup writes."""

    def entries(key: str) -> list[dict[str, Any]]:
        value = manifest.get(key, [])
        if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
            errors.append(f"control-center.json: {key} must be a list of objects")
            return []
        names = [entry.get("name") for entry in value]
        for index, name in enumerate(names):
            if not isinstance(name, str) or not ID.fullmatch(name):
                errors.append(f"control-center.json: {key}[{index}].name must use lowercase hyphen-case")
        if len(set(map(str, names))) != len(names):
            errors.append(f"control-center.json: {key} names must be distinct")
        return value

    def user_path(value: Any) -> bool:
        return isinstance(value, str) and value.startswith(USER_ROOTS) and ".." not in re.split(r"[\\/]", value)

    for index, entry in enumerate(entries("localData")):
        where = f"control-center.json: localData[{index}]"
        path = entry.get("path")
        parts = path.split("/") if isinstance(path, str) else []
        if parts[:2] != [".cc-local", "data"] or len(parts) < 3 or ".." in parts or "" in parts:
            errors.append(f"{where}.path must be a folder under .cc-local/data/")
        budget = entry.get("budgetGB")
        if not isinstance(budget, (int, float)) or isinstance(budget, bool) or budget <= 0:
            errors.append(f"{where}.budgetGB must be a positive number")
        rebuild = entry.get("rebuild")
        if rebuild is not None and not (isinstance(rebuild, str) and ID.fullmatch(rebuild)):
            errors.append(f"{where}.rebuild must be null or the name of a flake app")
        # What a rebuild costs or needs (remote hosts, credentials, GPU hours), so a budget warning never reads as "safe to delete".
        if not isinstance(entry.get("rebuildNeeds"), str) or not entry["rebuildNeeds"].strip():
            errors.append(f"{where}.rebuildNeeds must say what a rebuild needs, or 'nothing'")
    for index, entry in enumerate(entries("localCaches")):
        if not user_path(entry.get("path")):
            errors.append(f"control-center.json: localCaches[{index}].path must start with one of {', '.join(USER_ROOTS)}")
    backup = manifest.get("backup")
    if backup is not None:
        if not isinstance(backup, dict) or not user_path(backup.get("path")):
            errors.append(f"control-center.json: backup.path must start with one of {', '.join(USER_ROOTS)}")
        elif "keep" in backup and (not isinstance(backup["keep"], int) or isinstance(backup["keep"], bool) or not 1 <= backup["keep"] <= 50):
            errors.append("control-center.json: backup.keep must be an integer from 1 to 50")


def validate_plans(root: Path, errors: list[str]) -> None:
    plans_root = root / "plans"
    if not plans_root.is_dir():
        errors.append("plans/: directory is missing")
        return
    for path in plans_root.glob("*.md"):
        errors.append(f"{path.relative_to(root)}: plans must live in a lifecycle directory")

    seen: dict[str, Path] = {}
    for lifecycle in PLAN_LIFECYCLES:
        directory = plans_root / lifecycle
        if not directory.is_dir():
            errors.append(f"plans/{lifecycle}: directory is missing")
            continue
        for path in sorted(directory.glob("*.md")):
            relative = path.relative_to(root)
            identifier = path.stem
            if not ID.fullmatch(identifier):
                errors.append(f"{relative}: filename must use lowercase hyphen-case")
            if identifier in seen:
                errors.append(f"{relative}: duplicate plan also exists at {seen[identifier].relative_to(root)}")
            seen[identifier] = path
            text = path.read_text(encoding="utf-8")
            lifecycle_fields = re.findall(r"^Lifecycle:\s+(.+)$", text, re.MULTILINE)
            if lifecycle_fields != [lifecycle]:
                errors.append(f"{relative}: Lifecycle must uniquely equal '{lifecycle}'")
            planning_status = re.findall(r"^Planning status:\s+(.+)$", text, re.MULTILINE)
            if len(planning_status) != 1 or planning_status[0] not in {"draft", "accepted"}:
                errors.append(f"{relative}: Planning status must uniquely equal 'draft' or 'accepted'")
            if lifecycle == "completed" and planning_status != ["accepted"]:
                errors.append(f"{relative}: completed plan must have Planning status 'accepted'")


def validate(root: Path, adapter_targets: dict[str, Any] | None = None) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    manifest = load_json(root / "control-center.json", errors)
    if manifest is None:
        return errors

    if manifest.get("schemaVersion") != 1:
        errors.append("control-center.json: schemaVersion must be 1")
    if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
        errors.append("control-center.json: name must be a non-empty string")
    status = manifest.get("status")
    if status not in CC_STATUSES:
        errors.append(f"control-center.json: unsupported status '{status}'")
    if manifest.get("layout") != EXPECTED_LAYOUT:
        if (manifest.get("layout") or {}).get("repositories", "").startswith(".."):
            errors.append("control-center.json: layout is the old shared ../repos and ../worktrees one; run ./cc migrate-layout")
        else:
            errors.append("control-center.json: layout does not match the per-CC repos/ and worktrees/ contract")
    for field in placeholder_paths(manifest):
        errors.append(f"control-center.json: unresolved placeholder at {field}")
    markdown_dirs = manifest.get("markdownDirs", [])
    if not isinstance(markdown_dirs, list) or not all(
        isinstance(folder, str) and folder.strip("/") and not folder.startswith("/") and ".." not in folder.split("/")
        for folder in markdown_dirs
    ):
        errors.append("control-center.json: markdownDirs must be a list of folders relative to the CC root")
    validate_disk(manifest, errors)

    repositories_dir = root / "catalog" / "repositories"
    if not repositories_dir.is_dir():
        errors.append("catalog/repositories: directory is missing")
        repositories: list[Path] = []
    else:
        repositories = sorted(repositories_dir.glob("*.json"))
    seen: set[str] = set()
    for path in repositories:
        identifier = validate_repository(root, path, errors, adapter_targets)
        if identifier in seen:
            errors.append(f"catalog/repositories: duplicate id '{identifier}'")
        if identifier:
            seen.add(identifier)

    validate_descriptor_group(root, "workflows", errors)
    validate_descriptor_group(root, "benchmarks", errors)
    validate_councils(root, validate_agents(root, errors), errors)
    validate_trello(root, errors)
    validate_plans(root, errors)

    if status == "ready":
        if not repositories:
            errors.append("control-center.json: ready CC must contain at least one repository")
        for path in repositories:
            data = load_json(path, errors)
            if data and data.get("kind", "project") == "project" and data.get("status") != "verified":
                errors.append(f"{path.relative_to(root)}: ready CC requires status 'verified'")
    return errors


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    # Optional JSON written by flake.nix: {"<project>": {"<target>": "check" | "app"}}.
    adapter_targets = None
    if len(sys.argv) > 2:
        adapter_targets = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    errors = validate(root, adapter_targets)
    for warning in floating_models(root.resolve()):
        print(f"warning: {warning}")
    if errors:
        print("control-center validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("control-center bootstrap contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
