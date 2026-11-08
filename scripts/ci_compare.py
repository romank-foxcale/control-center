#!/usr/bin/env python3
"""Compare the ./cc check summaries of several OSes (CC_CHECK_SUMMARY, written by scripts/checks.py) and fail when
they differ: every step must run and pass on every OS, with the same number of tests, and skip only what EXPECTED_SKIPS
allows. A test skipped on purpose on one OS is then visible in CI, not hidden in a passing row (plan native-core, W6).

Usage: ci_compare.py <summary.json>...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# (sys.platform, script) -> tests that may be skipped there. Nothing else may skip. Why: plan native-core, W4.
EXPECTED_SKIPS = {
    ("win32", "scripts/store_gc_test.py"): 5,  # ./cc gc is part of the Nix extra; Nix does not run on Windows
}


def steps(summary: dict) -> dict[str, dict]:
    return {step["script"]: step for group in summary["groups"].values() for step in group.get("steps", [])}


def differences(summaries: list[dict]) -> list[str]:
    found: list[str] = []
    by_platform = {summary["platform"]: steps(summary) for summary in summaries}
    scripts = sorted({script for table in by_platform.values() for script in table})
    for script in scripts:
        runs = {platform: table.get(script) for platform, table in by_platform.items()}
        for platform, step in runs.items():
            if step is None:
                found.append(f"{script}: not run on {platform}")
            elif step["status"] != "pass":
                found.append(f"{script}: {step['status']} on {platform}")
            elif step["skipped"] != EXPECTED_SKIPS.get((platform, script), 0):
                found.append(f"{script}: {platform} skipped {step['skipped']}, expected "
                             f"{EXPECTED_SKIPS.get((platform, script), 0)}")
        counts = {platform: step["ran"] for platform, step in runs.items() if step is not None}
        if len(set(counts.values())) > 1:
            found.append(f"{script}: ran " + ", ".join(f"{platform} {count}" for platform, count in sorted(counts.items())))
    return found


def main(arguments: list[str]) -> int:
    if len(arguments) < 2:
        print("usage: ci_compare.py <summary.json> <summary.json>...", file=sys.stderr)
        return 2
    summaries = [json.loads(Path(argument).read_text(encoding="utf-8")) for argument in arguments]
    for summary in summaries:
        table = steps(summary)
        print(f"{summary['platform']}: {len(table)} steps, {sum(step['ran'] or 0 for step in table.values())} unittest "
              f"tests, {sum(step['skipped'] for step in table.values())} skipped")
    found = differences(summaries)
    for line in found:
        print(f"DIFF {line}")
    print("the OSes ran the same tests" if not found else f"{len(found)} difference(s) between the OSes")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
