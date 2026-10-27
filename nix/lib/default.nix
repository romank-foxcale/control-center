{ lib, pkgs }:
let
  # Must match TARGETS in scripts/repositories.py and scripts/validate-control-center.py.
  supportedTargets = [
    "windows"
    "linux"
    "macos"
  ];

  prefixAttrs =
    prefix: attrs:
    lib.mapAttrs' (name: value: lib.nameValuePair "${prefix}-${name}" value) attrs;

  ensureDerivations =
    label: attrs:
    assert lib.assertMsg (lib.isAttrs attrs) "${label} must be an attribute set";
    assert lib.assertMsg (
      lib.all lib.isDerivation (lib.attrValues attrs)
    ) "every ${label} value must be a derivation";
    attrs;

  validApp = app: lib.isAttrs app && (app.type or null) == "app" && app ? program;

  ensureApps =
    label: attrs:
    assert lib.assertMsg (lib.isAttrs attrs) "${label} must be an attribute set";
    assert lib.assertMsg (lib.all validApp (lib.attrValues attrs)) "every ${label} value must be a flake app";
    attrs;

  hasEntries = attrs: builtins.length (lib.attrNames attrs) > 0;

  # Must match QUALITY_GATES in scripts/verify.py.
  supportedQualityGates = [ "mutation" ];

  # A gate is a pure check (derivation) or, when it needs a real host or Git history, an app.
  ensureGates =
    label: names: attrs:
    assert lib.assertMsg (lib.isAttrs attrs) "${label} must be an attribute set";
    assert lib.assertMsg (lib.all (name: lib.elem name names) (lib.attrNames attrs)) (
      "${label} supports only: ${lib.concatStringsSep ", " names}"
    );
    assert lib.assertMsg (lib.all (gate: lib.isDerivation gate || validApp gate) (lib.attrValues attrs)) (
      "every ${label} value must be a derivation or a flake app"
    );
    attrs;
in
rec {
  mkApp = package: program: {
    type = "app";
    program = "${package}/bin/${program}";
  };

  # Runs `script` (PowerShell) on the Windows host from WSL, in a copy of `src`,
  # so the gate uses the real Windows toolchain. Impure, hence an app, not a check.
  mkWindowsHostGate =
    {
      name,
      src,
      script,
    }:
    let
      scriptFile = pkgs.writeText "${name}-windows-gate.ps1" ''
        $ErrorActionPreference = 'Stop'
        ${script}
      '';
      runner = pkgs.writeShellApplication {
        name = "${name}-windows-gate";
        runtimeInputs = [ pkgs.coreutils ];
        text = ''
          # Every powershell.exe call otherwise consumes the caller's stdin; gates are non-interactive.
          exec </dev/null
          if ! command -v powershell.exe >/dev/null 2>&1 || ! command -v wslpath >/dev/null 2>&1; then
            echo "error: the windows gate for ${name} needs WSL on a Windows host (powershell.exe not found)" >&2
            exit 1
          fi
          temp="$(wslpath "$(powershell.exe -NoProfile -Command '[IO.Path]::GetTempPath()' | tr -d '\r')")"
          work="$temp/cc-gate-${name}"
          rm -rf "$work"
          mkdir -p "$work"
          cp -r --no-preserve=mode ${src}/. "$work/src"
          cp ${scriptFile} "$work/gate.ps1"
          cd "$work/src"
          echo "running windows gate for ${name} in $(wslpath -w "$work/src")"
          exec powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$(wslpath -w "$work/gate.ps1")" "$@"
        '';
      };
    in
    assert lib.assertMsg (builtins.match "[a-z0-9]+(-[a-z0-9]+)*" name != null) (
      "windows gate name must use lowercase hyphen-case"
    );
    mkApp runner "${name}-windows-gate";

  # A Python gate on a real host (windows from WSL, linux, macos) that installs `requirements`
  # (paths in `src`) from PyPI into a venv reused between runs (kit decision 0027): one venv per
  # dependency files, interpreter and install step, rebuilt when it is older than `maxAgeDays`
  # or on CC_GATE_FRESH=1. The sources are copied fresh on every run and pip freeze is printed.
  # `install` replaces the default `pip install -r`; it sees "$py" (the venv's interpreter) and
  # "$src" (the sources as that interpreter sees them). `test` runs in the copy, extra arguments
  # reach it as "$@". Network and host side effects, hence an app.
  # Env: CC_GATE_FRESH=1 rebuilds the venv, CC_GATE_KEEP=1 keeps the work directory.
  mkPythonHostGate =
    {
      name,
      src,
      platform,
      requirements ? [ "requirements.txt" ],
      install ? null,
      test ? ''"$py" -m pytest -q -p no:cacheprovider "$@"'',
      maxAgeDays ? 7,
      # linux and macos: the Nix interpreter that makes the venv.
      python ? pkgs.python3,
      # windows: the command that finds the host's Python, such as "py.exe -3.13".
      windowsPython ? "py.exe",
      runtimeInputs ? [ ],
    }:
    let
      gateName = "${name}-${platform}-gate";
      installStep =
        if install == null then
          ''"$py" -m pip install --disable-pip-version-check -q''
          + lib.concatMapStrings (file: " -r \"$src/${file}\"") requirements
        else
          install;
      requirementFiles = lib.concatMapStringsSep " " (file: lib.escapeShellArg "${src}/${file}") requirements;
      windowsHost = ''
        # Interop calls otherwise consume the caller's stdin; gates are non-interactive.
        exec </dev/null
        if ! command -v wslpath >/dev/null 2>&1; then
          echo "error: the windows gate for ${name} needs WSL on a Windows host (wslpath not found)" >&2
          exit 1
        fi
        win_python="$(wslpath -u "$(${windowsPython} -c 'import sys; print(sys.executable)' | tr -d '\r')")"
        interpreter="$("$win_python" -c 'import sys; print(sys.executable, sys.version)' | tr -d '\r')"
        # realpath expands 8.3 names (KOZYRE~1); venv warns about those.
        cache="$(wslpath -u "$("$win_python" -c 'import os; print(os.path.realpath(os.environ["LOCALAPPDATA"]))' | tr -d '\r')")/control-center/${name}"
        win_temp="$(wslpath -u "$("$win_python" -c 'import os, tempfile; print(os.path.realpath(tempfile.gettempdir()))' | tr -d '\r')")"
        work="$(mktemp -d "$win_temp/${gateName}.XXXXXX")"
        src="$(wslpath -w ${src})"
        venv_python=Scripts/python.exe
        cc_make_venv() { "$win_python" -m venv "$(wslpath -w "$1")"; }
        # Native delete: rm -rf over the WSL mount takes minutes for a venv.
        cc_remove_dir() { (cd /mnt/c && cmd.exe /d /c rmdir /s /q "$(wslpath -w "$1")") || rm -rf "$1"; }
      '';
      posixHost = ''
        ${lib.optionalString pkgs.stdenv.isLinux ''
          # PyPI's manylinux wheels need the C++ runtime and zlib, which a Nix interpreter does not search for.
          export LD_LIBRARY_PATH="${
            lib.makeLibraryPath [
              pkgs.stdenv.cc.cc.lib
              pkgs.zlib
            ]
          }''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        ''}
        interpreter=${python}
        cache="''${XDG_CACHE_HOME:-$HOME/.cache}/control-center/${name}"
        work="$(mktemp -d "''${TMPDIR:-/tmp}/${gateName}.XXXXXX")"
        src=${src}
        venv_python=bin/python
        cc_make_venv() { ${python}/bin/python3 -m venv "$1"; }
        cc_remove_dir() { rm -rf "$1"; }
      '';
      runner = pkgs.writeShellApplication {
        name = gateName;
        runtimeInputs = [
          pkgs.coreutils
          pkgs.findutils
        ] ++ runtimeInputs;
        text = ''
          ${if platform == "windows" then windowsHost else posixHost}
          export src
          ${builtins.readFile ./gate-venv.sh}
          cleanup() {
            if [[ "''${CC_GATE_KEEP:-}" == 1 ]]; then
              echo "kept: $work"
            else
              cc_remove_dir "$work"
            fi
          }
          trap cleanup EXIT
          cc_build_venv() {
            cc_make_venv "$1"
            py="$1/$venv_python"
            ${installStep}
          }
          key="$(
            cat ${requirementFiles}
            echo "$interpreter"
            echo ${builtins.hashString "sha256" installStep}
          )"
          cc_gate_venv ${gateName} "$cache" "$key" ${toString maxAgeDays}
          py="$venv/$venv_python"
          cp -r --no-preserve=mode ${src}/. "$work/"
          cd "$work"
          "$py" -c 'import platform, sys; print("python", sys.version.split()[0], "on", platform.platform())'
          # Most requirements are lower bounds: record what this venv resolved.
          echo "== pip freeze"
          "$py" -m pip freeze --disable-pip-version-check
          ${test}
        '';
      };
    in
    assert lib.assertMsg (builtins.match "[a-z0-9]+(-[a-z0-9]+)*" name != null) (
      "python gate name must use lowercase hyphen-case"
    );
    assert lib.assertMsg (lib.elem platform supportedTargets) (
      "python gate platform must be one of: ${lib.concatStringsSep ", " supportedTargets}"
    );
    assert lib.assertMsg (requirements != [ ]) "python gate ${name} needs at least one requirements file";
    mkApp runner gateName;

  mkProject =
    {
      name,
      src,
      packages ? { },
      checks ? { },
      apps ? { },
      targets ? { },
      quality ? { },
      metadata ? { },
    }:
    assert lib.assertMsg (name != "") "project name must not be empty";
    assert lib.assertMsg (
      hasEntries packages || hasEntries checks || hasEntries apps || hasEntries targets
    ) "project ${name} must expose at least one package, check, app or target";
    let
      gates = ensureGates "project ${name} targets" supportedTargets targets;
      qualityGates = ensureGates "project ${name} quality" supportedQualityGates quality;
      kind = gate: if lib.isDerivation gate then "check" else "app";
    in
    {
      inherit name src metadata;
      packages = ensureDerivations "project ${name} packages" packages;
      checks =
        ensureDerivations "project ${name} checks" checks
        // prefixAttrs "target" (lib.filterAttrs (_: lib.isDerivation) gates);
      apps =
        ensureApps "project ${name} apps" apps
        // prefixAttrs "target" (lib.filterAttrs (_: validApp) gates)
        // prefixAttrs "quality" (lib.filterAttrs (_: validApp) qualityGates);
      # Gate kind per target, read by ./cc verify and the catalog validator.
      targets = lib.mapAttrs (_: kind) gates;
      # Quality gates are slow (mutation testing), so they stay out of `checks` and
      # run only through ./cc verify --quality.
      quality = lib.mapAttrs (_: kind) qualityGates;
      qualityChecks = lib.filterAttrs (_: lib.isDerivation) qualityGates;
    };

  mkWorkflow =
    {
      name,
      artifacts ? { },
      checks ? { },
      apps ? { },
      metadata ? { },
    }:
    assert lib.assertMsg (name != "") "workflow name must not be empty";
    assert lib.assertMsg (hasEntries artifacts || hasEntries checks || hasEntries apps) (
      "workflow ${name} must expose at least one artifact, check or app"
    );
    {
      inherit name metadata;
      artifacts = ensureDerivations "workflow ${name} artifacts" artifacts;
      checks = ensureDerivations "workflow ${name} checks" checks;
      apps = ensureApps "workflow ${name} apps" apps;
    };

  # A cross-repo contract: regenerate the consumer's committed artifact from the producer's
  # source of truth and fail on any difference, so drift is caught by a check, not by review.
  # `generate` is a shell snippet that reads "$spec" and writes "$generated"; it must produce
  # byte-identical output for an unchanged spec (run the consumer's formatter in it if needed).
  mkContractCheck =
    {
      name,
      spec,
      artifact,
      generate,
      nativeBuildInputs ? [ ],
    }:
    assert lib.assertMsg (builtins.match "[a-z0-9]+(-[a-z0-9]+)*" name != null) (
      "contract name must use lowercase hyphen-case"
    );
    pkgs.runCommand "contract-${name}" { inherit nativeBuildInputs; } ''
      export HOME="$TMPDIR"
      spec=${spec}
      generated="$TMPDIR/generated"
      ${generate}
      if ! diff -u --label committed --label generated ${artifact} "$generated"; then
        echo "contract ${name} drifted: the consumer's committed artifact differs from what the producer's spec generates" >&2
        exit 1
      fi
      touch "$out"
    '';

  collect =
    field: definitions:
    lib.foldl'
      (
        result: name:
        result // prefixAttrs name (definitions.${name}.${field} or { })
      )
      { }
      (lib.attrNames definitions);
}
