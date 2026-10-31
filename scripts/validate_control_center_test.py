#!/usr/bin/env python3
"""Test the optional disk keys of control-center.json: localData, localCaches and backup. A manifest without them
passes, so older CCs keep passing ./cc check; each wrong shape gives exactly the error that names it."""

from __future__ import annotations

import importlib.util
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

    def test_machine_paths_must_start_at_a_user_root(self) -> None:
        for path in ("/mnt/c/Users/me/cache", "C:\\Users\\me\\cache", "cache", "~/../other", "%LOCALAPPDATA%\\..\\x"):
            with self.subTest(path=path):
                self.assertEqual(len(errors(localCaches=[{"name": "c", "path": path}])), 1)
                self.assertEqual(len(errors(backup={"path": path})), 1)

    def test_backup_keep_range(self) -> None:
        self.assertEqual(errors(backup={"path": "~/b", "keep": 0}), ["control-center.json: backup.keep must be an integer from 1 to 50"])
        self.assertEqual(errors(backup={"path": "~/b", "keep": 50}), [])
        self.assertEqual(len(errors(backup="~/b")), 1)


if __name__ == "__main__":
    unittest.main()
