# Reuses one venv per key under a cache folder (kit decision 0027). Inlined into
# ccLib.mkPythonHostGate and tested by the gate-venv check in flake.nix.
# The caller defines cc_build_venv DIR (creates and fills the venv) and cc_remove_dir DIR.
# Usage: cc_gate_venv LABEL CACHE KEY MAX_AGE_DAYS; sets venv to the venv's folder.
cc_gate_venv() {
  local label="$1" cache="$2" key="$3" max_age_days="$4" old
  venv="$cache/gate-$(printf '%s' "$key" | sha256sum | cut -c1-12)"
  # No .complete marker: a build that did not finish, so the venv is not trusted.
  if [[ "${CC_GATE_FRESH:-}" == 1 || ! -f "$venv/.complete" || -n "$(find "$venv/.complete" -mmin "+$((max_age_days * 1440))")" ]]; then
    for old in "$cache"/gate-*; do
      if [[ -d "$old" ]]; then
        cc_remove_dir "$old"
      fi
    done
    mkdir -p "$cache"
    echo "$label: building venv $venv"
    cc_build_venv "$venv"
    touch "$venv/.complete"
  else
    echo "$label: reusing venv $venv (CC_GATE_FRESH=1 rebuilds it)"
  fi
}
