#!/usr/bin/env python3
"""./cc eval: measure a change to the agent context (AGENTS.md, skills, hooks) before keeping it.

Each variant is a git ref of this repository. The same fixture CC is built from each variant, every scenario
runs headless with `claude -p` in a fresh copy of it, and deterministic checks grade the result. Tokens,
cost, time and turns come from the CLI's own result event. See docs/kit/decisions/0019-lean-agent-context.md.

  ./cc eval run <label> [--ref <git-ref>] [--scenarios a,b] [--reps N] [--jobs N] [--model <model>]
  ./cc eval compare <label-a> <label-b>
  ./cc eval trace <label> <scenario>
  ./cc eval regrade <label>

Runs use the user's own claude login and skip permission prompts, inside throwaway copies under
$CC_EVAL_WORK (default cc-eval in the system temp folder, /tmp/cc-eval on Linux). Results go to .cc-local/eval/<label>.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixture import remove  # noqa: E402
from scenarios import SCENARIOS, head, hostos, parse_stream  # noqa: E402

HERE = Path(__file__).resolve().parent
TIMEOUT = 25 * 60


def work_root(label: str) -> Path:
    return Path(os.environ.get("CC_EVAL_WORK", Path(tempfile.gettempdir()) / "cc-eval")) / label


def results_path(root: Path, label: str) -> Path:
    return root / ".cc-local" / "eval" / f"{label}.jsonl"


def build_fixture(root: Path, label: str, ref: str) -> Path:
    work = work_root(label)
    kit = work / "kit"
    remove(kit)
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(root), str(kit)], check=True)
    subprocess.run(["git", "-C", str(kit), "checkout", "-q", ref], check=True)
    fixture = work / "fixture"
    print(f"{label}: building the fixture from {ref} ({subprocess.run(['git', '-C', str(kit), 'rev-parse', '--short', 'HEAD'], capture_output=True, encoding='utf-8', errors='replace').stdout.strip()})", flush=True)
    built = subprocess.run([sys.executable, str(HERE / "fixture.py"), str(kit), str(fixture)], capture_output=True,
                           encoding="utf-8", errors="replace")
    if built.returncode != 0:
        raise ValueError(f"fixture build failed:\n{built.stdout}{built.stderr}")
    return fixture / "Projects" / "eval_CC"


def grade(scenario: str, cc: Path, trace: dict, before: dict) -> dict:
    try:
        return SCENARIOS[scenario][2](cc, trace, before)
    except Exception as error:  # a crashed grader fails the run, not the eval
        return {"grader": False, "error": str(error)}


def passed(checks: dict) -> bool:
    return all(value is True for key, value in checks.items() if key != "error")


def run_one(label: str, fixture: Path, model: str, scenario: str, rep: int) -> dict:
    prompt, setup, _ = SCENARIOS[scenario]
    run_dir = work_root(label) / "runs" / f"{scenario}-{rep}"
    remove(run_dir)  # git's read-only pack files included
    run_dir.mkdir(parents=True)
    cc = run_dir / "eval_CC"
    shutil.copytree(fixture, cc, symlinks=True)
    if setup:
        setup(cc)
    before = {"head": head(cc), "base_head": head(cc / "repos/project/calc")}
    stream = run_dir / "stream.jsonl"
    started = time.time()
    with open(stream, "w", encoding="utf-8") as out:
        try:
            exit_code: int | str = subprocess.run(
                [hostos.resolve_exe("claude") or "claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", model,
                 "--dangerously-skip-permissions", "--max-turns", "80"],
                cwd=cc, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            exit_code = "timeout"
    wall = time.time() - started
    trace = parse_stream(stream.read_text(encoding="utf-8", errors="replace"))
    checks = grade(scenario, cc, trace, before)
    result = trace["result"]
    usage = result.get("usage", {})
    record = {
        "variant": label, "scenario": scenario, "rep": rep, "exit": exit_code, "model": trace["model"],
        "pass": passed(checks), "checks": checks, "before": before,
        "cost_usd": result.get("total_cost_usd"),
        "duration_s": round((result.get("duration_ms") or wall * 1000) / 1000, 1),
        "turns": result.get("num_turns"), "tool_calls": trace["tools"],
        "input_tokens": usage.get("input_tokens"), "cache_creation": usage.get("cache_creation_input_tokens"),
        "cache_read": usage.get("cache_read_input_tokens"), "output_tokens": usage.get("output_tokens"),
        "skills": trace["skills"],
    }
    print(f"{label} {scenario} #{rep}: {'pass' if record['pass'] else 'FAIL'} ${record['cost_usd']} "
          f"{record['duration_s']}s {record['turns']} turns {'' if record['pass'] else checks}", flush=True)
    return record


def command_run(root: Path, args: argparse.Namespace) -> int:
    unknown = [name for name in args.scenarios.split(",") if name not in SCENARIOS]
    if unknown:
        raise ValueError(f"unknown scenario(s): {', '.join(unknown)}; known: {', '.join(SCENARIOS)}")
    if shutil.which("claude") is None:
        raise ValueError("the claude CLI is not installed or not on PATH")
    fixture = build_fixture(root, args.label, args.ref)
    jobs = [(name, rep) for rep in range(1, args.reps + 1) for name in args.scenarios.split(",")]
    out = results_path(root, args.label)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("")
    with ThreadPoolExecutor(args.jobs) as pool:
        for record in pool.map(lambda job: run_one(args.label, fixture, args.model, *job), jobs):
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
    records = load(out)
    print(f"{args.label}: {sum(r['pass'] for r in records)}/{len(records)} passed; results in {out}")
    return 0


def load(path: Path) -> list[dict]:
    if not path.is_file():
        raise ValueError(f"no results: {path}; run ./cc eval run first")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def metrics(runs: list[dict]) -> dict:
    def mean(values: list) -> float:
        return statistics.mean(values) if values else 0.0
    return {
        "n": len(runs),
        "pass": sum(r["pass"] for r in runs),
        "cost": mean([r["cost_usd"] or 0 for r in runs]),
        "time": mean([r["duration_s"] or 0 for r in runs]),
        "turns": mean([r["turns"] or 0 for r in runs]),
        "prompt": mean([(r["input_tokens"] or 0) + (r["cache_creation"] or 0) + (r["cache_read"] or 0) for r in runs]),
        "output": mean([r["output_tokens"] or 0 for r in runs]),
    }


def compare(a: list[dict], b: list[dict]) -> list[str]:
    """One row per scenario and a total: pass counts, then the mean of each metric with B's change from A."""
    scenarios = list(dict.fromkeys([r["scenario"] for r in a] + [r["scenario"] for r in b]))
    lines = [f"{'scenario':10} {'pass':>12} {'cost $':>20} {'time s':>18} {'turns':>16} {'prompt tok':>22} {'output tok':>18}"]
    for name, ra, rb in [(s, [r for r in a if r["scenario"] == s], [r for r in b if r["scenario"] == s]) for s in scenarios] + [("TOTAL", a, b)]:
        ma, mb = metrics(ra), metrics(rb)
        cells = [f"{ma['pass']}/{ma['n']} -> {mb['pass']}/{mb['n']}".rjust(12)]
        for key, fmt, width in (("cost", "{:.3f}", 20), ("time", "{:.1f}", 18), ("turns", "{:.1f}", 16), ("prompt", "{:.0f}", 22), ("output", "{:.0f}", 18)):
            change = f"{(mb[key] - ma[key]) / ma[key] * 100:+.0f}%" if ma[key] else "n/a"
            cells.append(f"{fmt.format(ma[key])}->{fmt.format(mb[key])} {change}".rjust(width))
        lines.append(f"{name:10} " + " ".join(cells))
    for side, runs in (("A", a), ("B", b)):
        lines += [f"FAIL {side} {r['scenario']} #{r['rep']}: {r['checks']}" for r in runs if not r["pass"]]
    return lines


def command_trace(root: Path, args: argparse.Namespace) -> int:
    for run_dir in sorted((work_root(args.label) / "runs").glob(f"{args.scenario}-*")):
        print(f"--- {run_dir.name}")
        for line in (run_dir / "stream.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            for part in event.get("message", {}).get("content", []) if isinstance(event.get("message"), dict) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_use":
                    given = part.get("input", {})
                    detail = given.get("command") or given.get("skill") or given.get("file_path") or ""
                    print(f"  {part.get('name')} {str(detail)[:150]}".replace("\n", " | "))
                if part.get("type") == "tool_result" and part.get("is_error"):
                    print(f"    ERROR {str(part.get('content'))[:200]}".replace("\n", " | "))
    return 0


def command_regrade(root: Path, args: argparse.Namespace) -> int:
    path = results_path(root, args.label)
    records = load(path)
    for record in records:
        run_dir = work_root(args.label) / "runs" / f"{record['scenario']}-{record['rep']}"
        trace = parse_stream((run_dir / "stream.jsonl").read_text(encoding="utf-8", errors="replace"))
        record["checks"] = grade(record["scenario"], run_dir / "eval_CC", trace, record["before"])
        record["pass"] = passed(record["checks"])
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    print(f"{args.label}: {sum(r['pass'] for r in records)}/{len(records)} passed after regrading")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="./cc eval")
    parser.add_argument("root", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the scenarios against a variant of this repository")
    run.add_argument("label")
    run.add_argument("--ref", default="HEAD", help="git ref of the variant (committed); default HEAD")
    run.add_argument("--scenarios", default=",".join(SCENARIOS))
    run.add_argument("--reps", type=int, default=3)
    run.add_argument("--jobs", type=int, default=4)
    run.add_argument("--model", default="opus")
    comp = sub.add_parser("compare", help="compare two labels: A is the baseline")
    comp.add_argument("a")
    comp.add_argument("b")
    trace = sub.add_parser("trace", help="tool calls and errors of each run of a scenario")
    trace.add_argument("label")
    trace.add_argument("scenario")
    regrade = sub.add_parser("regrade", help="grade the saved runs of a label again after a grader fix")
    regrade.add_argument("label")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "run":
            return command_run(root, args)
        if args.command == "compare":
            print("\n".join(compare(load(results_path(root, args.a)), load(results_path(root, args.b)))))
            return 0
        if args.command == "trace":
            return command_trace(root, args)
        return command_regrade(root, args)
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
