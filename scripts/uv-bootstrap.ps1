# Download the uv release pinned in .uv-pin into .cc-local/tools/uv/<version>/ on Windows, after checking its SHA-256.
# Run by cc.cmd and, in Git Bash, by cc when that uv is missing. Why: docs/kit/decisions/0033-native-core-nix-optional.md
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pin = Get-Content -LiteralPath (Join-Path $root '.uv-pin') | Where-Object { $_ -and -not $_.StartsWith('#') }
$version = ($pin | Where-Object { $_ -like 'version *' }).Split(' ')[1]
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
$asset = "uv-$arch-pc-windows-msvc.zip"
$expected = (($pin | Where-Object { $_ -like "$asset *" }) -split ' ')[1]
if (-not $expected) { throw "no pinned hash for $asset in .uv-pin" }
$target = Join-Path $root ".cc-local\tools\uv\$version"
$temp = Join-Path ([IO.Path]::GetTempPath()) ("cc-uv-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $zip = Join-Path $temp $asset
    Write-Host "cc: downloading uv $version ($asset)"
    $ProgressPreference = 'SilentlyContinue'
    # A short network fault (GitHub answered 504 in CI, 2026-11-08) must not stop a first run: try 5 times.
    for ($attempt = 1; ; $attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/astral-sh/uv/releases/download/$version/$asset" -OutFile $zip
            break
        } catch {
            if ($attempt -ge 5) { throw }
            Start-Sleep -Seconds 3
        }
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "SHA-256 mismatch for ${asset}: expected $expected, got $actual" }
    Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $temp 'x')
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Move-Item -Force -LiteralPath (Join-Path $temp 'x\uv.exe') -Destination (Join-Path $target 'uv.exe')
} catch {
    [Console]::Error.WriteLine("cc: $($_.Exception.Message)")
    exit 1
} finally {
    Remove-Item -Recurse -Force -LiteralPath $temp -ErrorAction SilentlyContinue
}
