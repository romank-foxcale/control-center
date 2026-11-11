#!/usr/bin/env python3
"""Test ./cc changes with a fake gh: file classes, the source-only patch, a kept map.json, map checks and the page."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "changes.py"
VENDOR = Path(__file__).resolve().parent / "change_map" / "vendor" / "mermaid.min.js"
MERMAID_SHA256 = "a43bc1afd446f9c4cc66ac5dd45d02e8d65e26fc5344ec0ef787f88d6ddb6f9e"  # mermaid@11.4.1 dist/mermaid.min.js

DIFF = """diff --git a/app/rank.py b/app/rank.py
new file mode 100644
--- /dev/null
+++ b/app/rank.py
@@ -0,0 +1,4 @@
+def rank(items):
+    return sorted(items)
+class Ranker:
+    pass
diff --git a/app/search.py b/app/search.py
--- a/app/search.py
+++ b/app/search.py
@@ -10,2 +10,3 @@ def search():
-def score(a):
+def score(a, b):
+    # weights
     return a
diff --git a/tests/test_rank.py b/tests/test_rank.py
new file mode 100644
--- /dev/null
+++ b/tests/test_rank.py
@@ -0,0 +1,2 @@
+def test_rank_sorts():
+    assert True
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
diff --git a/.claude/skills/x/SKILL.md b/.claude/skills/x/SKILL.md
--- a/.claude/skills/x/SKILL.md
+++ b/.claude/skills/x/SKILL.md
@@ -1 +1 @@
-a
+b
diff --git a/app/settings.toml b/app/settings.toml
--- a/app/settings.toml
+++ b/app/settings.toml
@@ -1 +1 @@
-k = 1
+k = 2
"""

VIEW = {"number": 27, "title": "Rank results </script>", "url": "https://github.com/acme/shop/pull/27", "body": "Adds ranking.",
        "baseRefName": "main", "headRefName": "rank", "additions": 12, "deletions": 5,
        "commits": [{"messageHeadline": "Add ranker"}, {"messageHeadline": "Tests"}]}

FAKE_GH = f"""#!{sys.executable}
import sys
if sys.argv[1:3] == ["pr", "view"]:
    print({json.dumps(json.dumps(VIEW))})
elif sys.argv[1:3] == ["pr", "diff"]:
    sys.stdout.write({DIFF!r})
else:
    sys.exit("unexpected gh call: " + " ".join(sys.argv[1:]))
"""


def run(root: Path, env: dict, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", str(SCRIPT), str(root), *arguments], capture_output=True, text=True, env=env)


def main() -> int:
    assert hashlib.sha256(VENDOR.read_bytes()).hexdigest() == MERMAID_SHA256, "the kept Mermaid copy changed"
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        root = base / "alpha_CC"
        entry = root / "catalog" / "repositories" / "shop.json"
        entry.parent.mkdir(parents=True)
        entry.write_text(json.dumps({"id": "shop", "remote": "https://github.com/acme/shop.git"}))
        bin_dir = base / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(FAKE_GH)
        (bin_dir / "gh").chmod(0o755)
        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
        out = root / ".cc-local" / "changes" / "shop-27"

        result = run(root, env, "collect", "shop", "27")
        assert result.returncode == 0, result.stderr
        facts = json.loads((out / "facts.json").read_text())
        classes = {f["path"]: f["class"] for f in facts["files"]}
        assert classes == {"app/rank.py": "source", "app/search.py": "source", "tests/test_rank.py": "test", "README.md": "docs",
                           ".claude/skills/x/SKILL.md": "generated", "app/settings.toml": "config"}, classes
        files = {f["path"]: f for f in facts["files"]}
        assert files["app/rank.py"]["status"] == "added" and files["app/rank.py"]["symbols"]["added"] == ["Ranker", "rank"]
        assert files["app/search.py"]["symbols"] == {"added": [], "removed": [], "changed": ["score"]}, files["app/search.py"]["symbols"]
        assert files["app/search.py"]["hunks"] == [[10, 12]], files["app/search.py"]["hunks"]
        assert files["tests/test_rank.py"]["tests"] == ["test_rank_sorts"]
        assert facts["commits"] == ["Add ranker", "Tests"]
        patch = (out / "source.patch").read_text()
        assert "app/rank.py" in patch and "app/settings.toml" in patch, "source and config hunks are what the agent reads"
        assert "test_rank" not in patch and "README" not in patch and ".claude/skills" not in patch, patch
        assert json.loads((out / "map.json").read_text())["intent"] == "", "a skeleton to fill"

        # A map with every kind of mistake: all are reported in one round, and no page is written.
        bad = {"intent": "x" * 700, "groups": [{"title": "Ranking", "files": ["app/rank.py", "nope.py"], "why": "w",
                                                "before_after": [{"case": "c", "before": "", "after": "a"}]},
                                               {"title": "Again", "files": ["app/rank.py"], "why": "w"}],
               "reading_order": ["app/search.py:99"], "risks": [{"ref": "gone.py:1", "note": "n"}], "reclass": {"README.md": "weird"}, "diagram": ""}
        (out / "map.json").write_text(json.dumps(bad))
        result = run(root, env, "render", "shop", "27")
        assert result.returncode == 1, result.stdout
        for expected in ("intent has 700 characters", "nope.py is not in this PR", "app/rank.py is in two groups", "before is empty",
                         "app/search.py (source) is in no group", "app/search.py:99 is outside the changed lines", "gone.py:1 names a file",
                         "unknown class 'weird'", "diagram: Mermaid text or null"):
            assert expected in result.stderr, (expected, result.stderr)
        assert not (out / "index.html").exists()

        # A good map: the page carries the data, escapes markup in it, and links nothing on the network.
        good = {"intent": "Adds a ranker and uses it in search.", "groups": [
            {"title": "Ranking", "files": ["app/rank.py", "app/search.py"], "why": "Search results are sorted by a new ranker.",
             "before_after": [{"case": "search('a')", "before": "unsorted", "after": "sorted"}]}],
                "reading_order": ["app/rank.py:1", "app/search.py"], "risks": [{"ref": "app/search.py:11", "note": "b has no default"}],
                "reclass": {"app/settings.toml": "source"}, "diagram": None}
        (out / "map.json").write_text(json.dumps(good))
        result = run(root, env, "render", "shop", "27")
        assert result.returncode == 1 and "app/settings.toml (source) is in no group" in result.stderr, "a reclassed file counts as source"
        good["groups"][0]["files"].append("app/settings.toml")
        (out / "map.json").write_text(json.dumps(good))
        result = run(root, env, "render", "shop", "27")
        assert result.returncode == 0, result.stderr
        assert "1. Ranking  (3 files)" in result.stdout and "Risk  app/search.py:11: b has no default" in result.stdout, result.stdout
        page = (out / "index.html").read_text()
        assert "Rank results \\u003c/script>" in page and "Rank results </script>" not in page, "data cannot close the script tag"
        assert not re.search(r"<(script|link)[^>]+(src|href)=\"https?://", page), "the page loads nothing from the network"
        assert not (out.parent / "vendor").exists(), "no diagram, no Mermaid copy"

        good["diagram"] = "flowchart LR\n  search --> rank"
        (out / "map.json").write_text(json.dumps(good))
        assert run(root, env, "render", "shop", "27").returncode == 0
        assert (out.parent / "vendor" / "mermaid.min.js").read_bytes() == VENDOR.read_bytes()

        # collect again: a filled map is kept, the facts are refreshed.
        result = run(root, env, "collect", "shop", "27")
        assert result.returncode == 0 and "kept: already filled" in result.stdout, result.stdout
        assert json.loads((out / "map.json").read_text())["intent"] == good["intent"]

        result = run(root, env, "render", "shop", "28")
        assert result.returncode == 1 and "run ./cc changes collect shop 28 first" in result.stderr, result.stderr
    print("changes tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
