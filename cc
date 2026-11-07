#!/bin/sh
# Run the CC on Linux, macOS and in Git Bash on Windows: find the pinned uv (download and check it on first use),
# then run scripts/cc.py with the Python and dependencies locked in uv.lock. Why: plans/active/native-core.md
set -eu
root=$(cd "$(dirname "$0")" && pwd)
version=$(awk '$1 == "version" { print $2 }' "$root/.uv-pin")
dir="$root/.cc-local/tools/uv/$version"
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) os=windows uv="$dir/uv.exe" ;;
  Darwin) os=macos uv="$dir/uv" ;;
  Linux) os=linux uv="$dir/uv" ;;
  *) echo "cc: unsupported OS $(uname -s)" >&2; exit 1 ;;
esac

fetch_uv() {
  case "$(uname -m)" in
    x86_64 | amd64) arch=x86_64 ;;
    arm64 | aarch64) arch=aarch64 ;;
    *) echo "cc: unsupported CPU $(uname -m)" >&2; exit 1 ;;
  esac
  case "$os" in
    macos) asset="uv-$arch-apple-darwin.tar.gz" ;;
    linux) asset="uv-$arch-unknown-linux-gnu.tar.gz" ;;
  esac
  expected=$(awk -v a="$asset" '$1 == a { print $2 }' "$root/.uv-pin")
  [ -n "$expected" ] || { echo "cc: no pinned hash for $asset in .uv-pin" >&2; exit 1; }
  temp=$(mktemp -d)
  trap 'rm -rf "$temp"' EXIT
  url="https://github.com/astral-sh/uv/releases/download/$version/$asset"
  echo "cc: downloading uv $version ($asset)" >&2
  if command -v curl >/dev/null 2>&1; then curl -fsSL -o "$temp/$asset" "$url"; else wget -q -O "$temp/$asset" "$url"; fi
  if command -v sha256sum >/dev/null 2>&1; then actual=$(sha256sum "$temp/$asset" | cut -d' ' -f1)
  else actual=$(shasum -a 256 "$temp/$asset" | cut -d' ' -f1); fi
  if [ "$actual" != "$expected" ]; then
    echo "cc: SHA-256 mismatch for $asset: expected $expected, got $actual" >&2
    exit 1
  fi
  tar -xzf "$temp/$asset" -C "$temp"
  mkdir -p "$dir"
  mv "$temp/${asset%.tar.gz}/uv" "$uv.part"
  chmod +x "$uv.part"
  mv "$uv.part" "$uv"
}

if [ ! -x "$uv" ]; then
  if [ "$os" = windows ]; then
    powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$(cygpath -w "$root/scripts/uv-bootstrap.ps1")" >&2
  else
    fetch_uv
  fi
fi

export PYTHONUTF8=1 UV_PYTHON_PREFERENCE=only-managed
export UV_PYTHON_INSTALL_DIR="$root/.cc-local/tools/python" UV_PROJECT_ENVIRONMENT="$root/.cc-local/venvs/$os"
exec "$uv" run --locked --quiet --project "$root" python -B "$root/scripts/cc.py" "$@"
