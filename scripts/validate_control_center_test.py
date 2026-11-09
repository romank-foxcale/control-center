#!/usr/bin/env python3
"""Test the optional disk keys of control-center.json: localData, localCaches and backup. A manifest without them
passes, so older CCs keep passing ./cc check; each wrong shape gives exactly the error that names it."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("validate_control_center", Path(__file__).with_name("validate-control-center.py"))
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)

DATA = {"name": "embeddings", "path": ".cc-local/data/embeddings", "budgetGB": 6, "rebuild": None,
        "rebuildNeeds": "EC2 GPU host, about 5 h"}


def errors(**manifest: object) -> list[str]:
    found: list[str] = []
    validator.validate_disk(manifest, found)
    return found


class DiskKeys(unittest.TestCase):
    def test_missing_keys_pass(self) -> None:
        self.assertEqual(errors(), [])

    def test_valid_entries_pass(self) -> None:
        self.assertEqual(errors(
            localData=[DATA, {**DATA, "name": "features", "path": ".cc-local/data/features", "budgetGB": 0.5,
                              "rebuild": "flar-replay", "rebuildNeeds": "nothing"}],
            localCaches=[{"name": "gate", "path": "%LOCALAPPDATA%\\di-cc\\ai"}, {"name": "uv", "path": "~/.cache/uv"}],
            backup={"path": "%USERPROFILE%\\cc-backups", "keep": 5},
        ), [])

    def test_data_outside_data_folder_fails(self) -> None:
        for path in (".cc-local/opensearch", ".cc-local/runs/data", ".cc-local/data", ".cc-local/data/../runs", "/home/u/data", None):
            with self.subTest(path=path):
                self.assertEqual(errors(localData=[{**DATA, "path": path}]),
                                 ["control-center.json: localData[0].path must be a folder under .cc-local/data/"])

    def test_budget_rebuild_and_needs_fail(self) -> None:
        self.assertEqual(errors(localData=[{**DATA, "budgetGB": 0}]),
                         ["control-center.json: localData[0].budgetGB must be a positive number"])
        self.assertEqual(errors(localData=[{**DATA, "budgetGB": True}]), errors(localData=[{**DATA, "budgetGB": "6"}]))
        self.assertIn("rebuild must be null or the name of a flake app", errors(localData=[{**DATA, "rebuild": "Run Me"}])[0])
        self.assertIn("rebuildNeeds", errors(localData=[{**DATA, "rebuildNeeds": " "}])[0])
        self.assertIn("rebuildNeeds", errors(localData=[{k: v for k, v in DATA.items() if k != "rebuildNeeds"}])[0])

    def test_names_must_be_ids_and_distinct(self) -> None:
        self.assertEqual(errors(localData=[DATA, {**DATA, "path": ".cc-local/data/x"}]),
                         ["control-center.json: localData names must be distinct"])
        self.assertEqual(errors(localCaches=[{"name": "Gate", "path": "~/.cache/x"}]),
                         ["control-center.json: localCaches[0].name must use lowercase hyphen-case"])

    def test_not_a_list_fails(self) -> None:
        self.assertEqual(errors(localCaches={"name": "uv"}), ["control-center.json: localCaches must be a list of objects"])
        self.assertEqual(errors(localData=5), ["control-center.json: localData must be a list of objects"])

    def test_machine_paths_must_start_at_a_user_root(self) -> None:
        for path in ("/mnt/c/Users/me/cache", "C:\\Users\\me\\cache", "cache", "~/../other", "%LOCALAPPDATA%\\..\\x"):
            with self.subTest(path=path):
                self.assertEqual(len(errors(localCaches=[{"name": "c", "path": path}])), 1)
                self.assertEqual(len(errors(backup={"path": path})), 1)

    def test_tags_quiesce_and_purge(self) -> None:
        tagged = {**DATA, "tags": ["irreplaceable", "sensitive"], "quiesce": ["opensearch", "ai-vectors-load"],
                  "purge": ["ai-embeddings-purge"]}
        self.assertEqual(errors(localData=[tagged]), [])
        self.assertEqual(errors(localData=[{**DATA, "tags": []}]), [])
        for tags in (["precious"], ["sensitive", "sensitive"], "irreplaceable", [None]):
            with self.subTest(tags=tags):
                self.assertEqual(errors(localData=[{**DATA, "tags": tags}]),
                                 ["control-center.json: localData[0].tags must be a list of distinct tags from irreplaceable, sensitive"])
        for quiesce in ("opensearch", [""], ["("], [3]):
            with self.subTest(quiesce=quiesce):
                self.assertEqual(errors(localData=[{**DATA, "quiesce": quiesce}]),
                                 ["control-center.json: localData[0].quiesce must be a list of process patterns (pgrep -f)"])
        for purge in ("ai-embeddings-purge", ["Purge Me"], []):
            with self.subTest(purge=purge):
                self.assertEqual(errors(localData=[{**DATA, "purge": purge}]),
                                 ["control-center.json: localData[0].purge must be a list of flake app names"])

    def test_data_paths_must_not_overlap(self) -> None:
        nested = {**DATA, "name": "part", "path": ".cc-local/data/embeddings/part"}
        same = {**DATA, "name": "copy", "path": ".cc-local/data/embeddings"}
        for other in (nested, same):
            with self.subTest(path=other["path"]):
                self.assertEqual(errors(localData=[DATA, other]), [
                    f"control-center.json: localData paths must not overlap: {DATA['path']} and {other['path']}"])
        sibling = {**DATA, "name": "embeddings-old", "path": ".cc-local/data/embeddings-old"}
        self.assertEqual(errors(localData=[DATA, sibling]), [])

    def test_data_path_may_name_a_drive(self) -> None:
        for path in ("E:\\cc-data", "e:/cc-data", "/mnt/e/cc-data", "%USERPROFILE%\\cc-data", "~/cc-data"):
            with self.subTest(path=path):
                self.assertEqual(errors(backup={"path": "~/b", "dataPath": path}), [])
        for path in ("cc-data", "/home/u/cc-data", "/mnt/e", "E:\\", "E:\\..\\x", "/mnt/e/../x", "//server/share", 5):
            with self.subTest(path=path):
                self.assertEqual(errors(backup={"path": "~/b", "dataPath": path}), [
                    "control-center.json: backup.dataPath must be a drive folder (E:\\... or /mnt/e/...) or start with one of "
                    + ", ".join(validator.USER_ROOTS)])
        self.assertEqual(len(errors(backup={"path": "E:\\cc-backups"})), 1)  # only dataPath may name a drive

    def test_backup_keep_range(self) -> None:
        self.assertEqual(errors(backup={"path": "~/b", "keep": 0}), ["control-center.json: backup.keep must be an integer from 1 to 50"])
        self.assertEqual(errors(backup={"path": "~/b", "keep": 50}), [])
        self.assertEqual(errors(backup={"path": "~/b", "push": True}), [])
        self.assertEqual(errors(backup={"path": "~/b", "push": "yes"}), ["control-center.json: backup.push must be true or false"])
        self.assertEqual(len(errors(backup="~/b")), 1)


def project(identifier: str = "app", **fields: object) -> dict[str, object]:
    return {"schemaVersion": 1, "id": identifier, "checkout": identifier, "remote": "https://example.invalid/app.git",
            "defaultBranch": "main", "kind": "project", "role": "app", "targets": ["linux"], "stack": ["python"],
            "sourceInput": identifier, "adapter": f"nix/projects/{identifier}.nix", "status": "discovered", **fields}


def catalog_errors(status: str, *descriptors: dict[str, object]) -> list[str]:
    """validate() on a CC holding these repository descriptors; only the catalog's and the ready rule's errors."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        manifest = {"schemaVersion": 1, "name": "shop", "status": status, "layout": validator.EXPECTED_LAYOUT}
        (root / "control-center.json").write_text(json.dumps(manifest), encoding="utf-8")
        repositories = root / "catalog" / "repositories"
        repositories.mkdir(parents=True)
        for descriptor in descriptors:
            (repositories / f"{descriptor['id']}.json").write_text(json.dumps(descriptor), encoding="utf-8")
            adapter = root / str(descriptor["adapter"])
            adapter.parent.mkdir(parents=True, exist_ok=True)
            adapter.write_text("{ }\n", encoding="utf-8")
        found = [error.replace("\\", "/") for error in validator.validate(root)]  # Windows prints catalog\...
    return [error for error in found if error.startswith("catalog/repositories") or "ready CC" in error]


class NewProjectRepos(unittest.TestCase):
    """A repo made with ./cc repo new has remote null until set-remote (docs/kit/decisions/0031-new-project-cc.md)."""

    def test_remote_shapes(self) -> None:
        self.assertEqual(catalog_errors("adapting", project(remote=None)), [])
        self.assertEqual(catalog_errors("adapting", project()), [])
        message = ("catalog/repositories/app.json: 'remote' must be a non-empty string, or null for a project repo "
                   "with no remote yet")
        self.assertEqual(catalog_errors("adapting", project(remote="")), [message])
        absent = project()
        del absent["remote"]
        self.assertEqual(catalog_errors("adapting", absent), [message])
        reference = {**project(remote=None), "kind": "reference"}
        self.assertEqual(catalog_errors("adapting", reference), [message])

    def test_blocked_reason_must_not_be_empty(self) -> None:
        self.assertEqual(catalog_errors("adapting", project(status="blocked", blockedReason=" ")),
                         ["catalog/repositories/app.json: 'blockedReason' must be a non-empty string"])

    def test_ready_needs_verified_or_blocked_with_reason(self) -> None:
        self.assertEqual(catalog_errors("ready", project(status="verified")), [])
        self.assertEqual(catalog_errors("ready", project(remote=None, status="blocked", blockedReason="no native gate yet")), [])
        self.assertEqual(catalog_errors("ready", project(status="blocked")), [
            'catalog/repositories/app.json: ready CC needs a blockedReason for a blocked repo; '
            'run ./cc repo set-status app blocked --reason "<why>"'])
        self.assertEqual(catalog_errors("ready", project(remote=None)), [
            "catalog/repositories/app.json: ready CC requires status 'verified', or 'blocked' with a reason"])

    def test_ready_needs_a_project_repo(self) -> None:
        message = "control-center.json: ready CC must contain at least one project repository"
        self.assertEqual(catalog_errors("ready"), [message])
        self.assertEqual(catalog_errors("ready", {**project(), "kind": "reference"}), [message])


if __name__ == "__main__":
    unittest.main()
