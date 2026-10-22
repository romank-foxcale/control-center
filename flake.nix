{
  description = "Agent-facing control center template";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs =
    inputs@{ self, nixpkgs, ... }:
    let
      systems = [
        "aarch64-darwin"
        "x86_64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      mkSystem =
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          ccLib = import ./nix/lib {
            inherit pkgs;
            inherit (pkgs) lib;
          };
          projects = import ./nix/projects { inherit pkgs ccLib inputs; };
          workflows = import ./nix/workflows { inherit pkgs ccLib projects; };
          validator = pkgs.writeShellApplication {
            name = "cc-validate";
            runtimeInputs = [ pkgs.python3 ];
            text = ''
              exec python3 ${self}/scripts/validate-knowledge.py "$PWD"
            '';
          };
          uiPython = pkgs.python3.withPackages (ps: [ ps.textual ]);
          ui = pkgs.writeShellApplication {
            name = "cc-settings";
            runtimeInputs = [
              uiPython
              pkgs.git
            ];
            text = ''
              exec python3 ${self}/scripts/settings/app.py "$@"
            '';
          };
          adapterTargets = pkgs.lib.mapAttrs (_: project: project.targets or { }) projects;
          adapterTargetsFile = pkgs.writeText "cc-adapter-targets.json" (builtins.toJSON adapterTargets);
          adapterQuality = pkgs.lib.mapAttrs (_: project: project.quality or { }) projects;
          adapterQualityChecks = pkgs.lib.mapAttrs (_: project: project.qualityChecks or { }) projects;
        in
        {
          packages =
            ccLib.collect "packages" projects
            // ccLib.collect "artifacts" workflows;

          checks =
            {
              knowledge = pkgs.runCommand "control-center-knowledge" { nativeBuildInputs = [ pkgs.git ]; } ''
                export HOME=$TMPDIR
                ${pkgs.python3}/bin/python ${self}/scripts/validate-knowledge.py ${self}
                ${pkgs.python3}/bin/python -B ${self}/scripts/validate_knowledge_test.py
                touch "$out"
              '';
              agent-configs = pkgs.runCommand "control-center-agent-configs" { } ''
                ${pkgs.python3}/bin/python ${self}/scripts/agent-configs.py ${self} check
                ${pkgs.python3}/bin/python ${self}/scripts/agent_configs_test.py
                touch "$out"
              '';
              council = pkgs.runCommand "control-center-council" { nativeBuildInputs = [ pkgs.git ]; } ''
                export HOME=$TMPDIR
                cd ${self}/scripts/council
                ${pkgs.python3}/bin/python -B providers_test.py
                ${pkgs.python3}/bin/python -B council_test.py
                touch "$out"
              '';
              rule-hooks = pkgs.runCommand "control-center-rule-hooks" { nativeBuildInputs = [ pkgs.git ]; } ''
                export HOME=$TMPDIR
                ${pkgs.python3}/bin/python ${self}/scripts/rule_hooks_test.py
                touch "$out"
              '';
              trello = pkgs.runCommand "control-center-trello" { } ''
                ${pkgs.python3}/bin/python ${self}/scripts/trello_test.py
                touch "$out"
              '';
              events = pkgs.runCommand "control-center-events" { } ''
                ${pkgs.python3}/bin/python ${self}/scripts/events_test.py
                ${pkgs.python3}/bin/python -B ${self}/scripts/verify_test.py
                touch "$out"
              '';
              repositories = pkgs.runCommand "control-center-repositories" { nativeBuildInputs = [ pkgs.git ]; } ''
                export HOME=$TMPDIR
                ${pkgs.python3}/bin/python ${self}/scripts/repositories_test.py
                ${pkgs.python3}/bin/python -B ${self}/scripts/migrate_layout_test.py
                touch "$out"
              '';
              prs = pkgs.runCommand "control-center-prs" { nativeBuildInputs = [ pkgs.git ]; } ''
                export HOME=$TMPDIR
                ${pkgs.python3}/bin/python ${self}/scripts/prs_test.py
                ${pkgs.python3}/bin/python -B ${self}/scripts/threads_test.py
                ${pkgs.python3}/bin/python -B ${self}/scripts/lessons_test.py
                touch "$out"
              '';
              eval = pkgs.runCommand "control-center-eval" { nativeBuildInputs = [ pkgs.git ]; } ''
                export HOME=$TMPDIR
                ${pkgs.python3}/bin/python -B ${self}/scripts/eval/eval_test.py
                touch "$out"
              '';
              settings-smoke = pkgs.runCommand "control-center-settings-smoke" { } ''
                export HOME=$TMPDIR
                ${uiPython}/bin/python ${self}/scripts/settings/smoke.py
                touch "$out"
              '';
              # ccLib.mkContractCheck passes when the artifact matches its spec and fails on drift.
              contract-check =
                let
                  contract =
                    artifact:
                    ccLib.mkContractCheck {
                      name = "fixture";
                      spec = pkgs.writeText "spec.txt" "get /orders\n";
                      inherit artifact;
                      generate = ''tr a-z A-Z < "$spec" > "$generated"'';
                    };
                  inSync = contract (pkgs.writeText "artifact.txt" "GET /ORDERS\n");
                  drifted = pkgs.testers.testBuildFailure (contract (pkgs.writeText "artifact.txt" "GET /ORDER\n"));
                in
                pkgs.runCommand "control-center-contract-check" { } ''
                  test -e ${inSync}
                  grep -q "contract fixture drifted" ${drifted}/testBuildFailure.log
                  grep -q "^+GET /ORDERS" ${drifted}/testBuildFailure.log
                  touch "$out"
                '';
            }
            // pkgs.lib.optionalAttrs (builtins.pathExists "${toString ./.}/control-center.json") {
              bootstrap = pkgs.runCommand "control-center-bootstrap" { } ''
                ${pkgs.python3}/bin/python ${self}/scripts/validate-control-center.py ${self} ${adapterTargetsFile}
                touch "$out"
              '';
            }
            // ccLib.collect "checks" projects
            // ccLib.collect "checks" workflows;

          apps =
            {
              default = ccLib.mkApp validator "cc-validate";
              validate = ccLib.mkApp validator "cc-validate";
              settings = ccLib.mkApp ui "cc-settings";
            }
            // ccLib.collect "apps" projects
            // ccLib.collect "apps" workflows;

          inherit adapterTargets adapterQuality adapterQualityChecks;

          devShell = pkgs.mkShell {
            packages = [
              pkgs.git
              pkgs.nixfmt-rfc-style
              pkgs.python3
            ];
          };

          formatter = pkgs.nixfmt-rfc-style;
        };
      perSystem = forAllSystems mkSystem;
    in
    {
      packages = nixpkgs.lib.mapAttrs (_: value: value.packages) perSystem;
      checks = nixpkgs.lib.mapAttrs (_: value: value.checks) perSystem;
      apps = nixpkgs.lib.mapAttrs (_: value: value.apps) perSystem;
      # Not a package set: legacyPackages is the flake output that tolerates plain data.
      legacyPackages = nixpkgs.lib.mapAttrs (_: value: {
        ccTargets = value.adapterTargets;
        ccQuality = value.adapterQuality;
        ccQualityChecks = value.adapterQualityChecks;
      }) perSystem;
      devShells = nixpkgs.lib.mapAttrs (_: value: { default = value.devShell; }) perSystem;
      formatter = nixpkgs.lib.mapAttrs (_: value: value.formatter) perSystem;
    };
}
