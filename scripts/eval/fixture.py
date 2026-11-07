#!/usr/bin/env python3
"""fixture.py <kit_clone> <out_dir>: a ready CC "eval_CC" with one verified repo "calc"
(Python, linux target, Nix adapter running pytest), committed and with a warm Nix store.

Used by scripts/eval/run.py; the same fixture is built from each variant of the kit. Plain Python with no shell,
so it starts the same on every OS; ./cc check and ./cc verify inside it still need Nix for the calc adapter.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenarios import cc_argv  # noqa: E402

CALC = '''def add(a, b):
    return a + b


def mean(values):
    return sum(values) / len(values)


def divide(a, b):
    # The legacy billing API expects None for a zero divisor.
    if b == 0:
        return None
    return a / b
'''
TEST_CALC = '''from calc import add, divide, mean


def test_add():
    assert add(2, 3) == 5


def test_mean():
    assert mean([1, 2, 3]) == 2


def test_divide_by_zero():
    assert divide(1, 0) is None
'''
CALC_NIX = """{
  pkgs,
  ccLib,
  src,
}:
let
  tests = pkgs.runCommand "calc-tests" { nativeBuildInputs = [ (pkgs.python3.withPackages (ps: [ ps.pytest ])) ]; } ''
    cp -r ${src}/. .
    python -m pytest -q -p no:cacheprovider
    touch $out
  '';
in
ccLib.mkProject {
  name = "calc";
  inherit src;
  checks.tests = tests;
  targets.linux = tests;
  metadata = {
    role = "arithmetic library";
    owner = "eval";
  };
}
"""
PROJECTS_NIX = """{
  pkgs,
  ccLib,
  inputs ? { },
}:
{
  calc = import ./calc.nix {
    inherit pkgs ccLib;
    src = inputs.calc;
  };
}
"""
NIXPKGS = '    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";\n'
IDENTITY = ["-c", "user.name=eval", "-c", "user.email=eval@example.invalid"]


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def writable(top: Path, on: bool) -> None:
    """chmod -R u+w (on) or a-w (off). On Windows only a file's read-only flag follows."""
    for folder, folders, files in os.walk(top):
        for path in [Path(folder), *(Path(folder) / name for name in folders + files)]:
            mode = path.stat().st_mode
            path.chmod(mode | stat.S_IWUSR if on else mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def remove(top: Path) -> None:
    """rm -rf that also removes read-only files: git's pack files, a previous fixture's remotes."""
    if top.exists():
        writable(top, True)
        shutil.rmtree(top)


def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run quietly; stop the build with the command's output when it fails."""
    result = subprocess.run(argv, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(argv)} failed:\n{result.stdout}{result.stderr}")
    return result


def gate(cc: Path, name: str, arguments: list[str], lines: int) -> None:
    """A ./cc gate whose failure ends the build with the last lines of its output."""
    result = subprocess.run([*cc_argv(cc), *arguments], cwd=cc, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-lines:])
        raise SystemExit(f"{name} ./cc {' '.join(arguments)} failed\n{tail}")


def set_manifest(cc: Path, **changes: str) -> None:
    path = cc / "control-center.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    write(path, json.dumps(data, indent=2) + "\n")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: fixture.py <kit_clone> <out_dir>", file=sys.stderr)
        return 2
    kit, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    remove(out)  # a previous fixture's remotes are read-only
    (out / "remotes").mkdir(parents=True)
    (out / "Projects").mkdir()

    source = Path(tempfile.mkdtemp())
    run(["git", "init", "-q", "-b", "main"], source)
    write(source / "calc.py", CALC)
    write(source / "test_calc.py", TEST_CALC)
    run(["git", "add", "-A"], source)
    run(["git", *IDENTITY, "commit", "-qm", "Initial calc"], source)
    remote = out / "remotes" / "calc.git"
    run(["git", "clone", "-q", "--bare", str(source), str(remote)], out)
    remove(source)

    cc = out / "Projects" / "eval_CC"
    run([sys.executable, str(kit / "scripts" / "materialize-template.py"), str(cc)], out)
    run(["git", "config", "user.name", "eval"], cc)
    run(["git", "config", "user.email", "eval@example.invalid"], cc)
    set_manifest(cc, name="eval", status="adapting")
    run([*cc_argv(cc), "repo", "add", "calc", "--remote", remote.as_uri(), "--targets", "linux", "--stack", "python",
         "--role", "arithmetic library", "--clone"], cc)

    write(cc / "nix" / "projects" / "calc.nix", CALC_NIX)
    write(cc / "nix" / "projects" / "default.nix", PROJECTS_NIX)
    flake = cc / "flake.nix"
    text = flake.read_text(encoding="utf-8")
    assert NIXPKGS in text, "flake.nix has no nixpkgs input to add calc after"
    write(flake, text.replace(NIXPKGS, NIXPKGS + f'    calc = {{\n      url = "git+{remote.as_uri()}";\n      flake = false;\n    }};\n'))
    run([*cc_argv(cc), "repo", "set-status", "calc", "adapted"], cc)
    run(["git", "add", "-A"], cc)
    run(["git", "commit", "-qm", "Eval fixture"], cc)
    gate(cc, "fixture", ["check"], 20)
    gate(cc, "fixture", ["verify"], 20)
    run([*cc_argv(cc), "repo", "set-status", "calc", "verified"], cc)
    set_manifest(cc, status="ready")
    gate(cc, "ready", ["check"], 8)
    run(["git", "add", "-A"], cc)
    subprocess.run(["git", "commit", "-qm", "calc verified"], cwd=cc, capture_output=True)  # nothing may be left to commit
    # Agents must not push into the shared remote from parallel runs.
    writable(out / "remotes", False)
    print(f"fixture ready: {cc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
