{
  pkgs,
  ccLib,
  inputs ? { },
}:

# One adapter per onboarded catalog repository; the attribute name equals the catalog id, for example:
#   api = import ./api.nix { inherit pkgs ccLib; src = inputs.api; };
# A repo made with ./cc repo new has no remote yet: its flake input is
#   api = { url = "path:./nix/placeholders/api"; flake = false; };
# and ./cc overrides it with repos/project/api (docs/kit/decisions/0031-new-project-cc.md).
# ./cc repo add records the adapter path; templates/project.nix is the starting point for one.
{ }
