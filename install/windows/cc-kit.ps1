<#
.SYNOPSIS
  cc-kit for native Windows: create, open and check Control Centers on the Windows drive, with no WSL and no Nix.

.DESCRIPTION
  The same commands as install/cc-kit (the bash version for macOS, Linux and WSL2).
  The kit's own pinned uv and Python run the template script, so no system Python is needed.
  install/windows/install.ps1 puts a cc-kit.cmd launcher for this script on the user's PATH.
  Why native: docs/kit/decisions/0033-native-core-nix-optional.md

.EXAMPLE
  cc-kit new shop
.EXAMPLE
  cc-kit open shop codex
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)] [string]$Command = "help",
    [Parameter(Position = 1)] [string]$Target = "",
    [Parameter(Position = 2)] [string]$Tool = ""
)

$ErrorActionPreference = "Stop"

$kitRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if ($env:CC_PROJECTS_ROOT) {
    $projectsRoot = $env:CC_PROJECTS_ROOT
} else {
    $projectsRoot = Join-Path $env:USERPROFILE "Projects"
}

# Opening prompts, so nobody has to know them (the same text as install/cc-kit).
$setupPrompt = "Set up this Control Center. Use the grill-cc-bootstrap skill."
$continuePrompt = "Continue setting up this Control Center. Use the grill-cc-bootstrap skill."
$migratePrompt = "Migrate this folder to the Control Center format. Read cc_template/BOOTSTRAP.md, treat cc_template as read-only, and use the grill-cc-bootstrap skill."

function Show-Usage {
    Write-Host "Usage: cc-kit <command> [arguments]"
    Write-Host ""
    Write-Host "Commands:"
    Write-Host "  new <name> [tool]        Create <projects root>\<name>_CC and start its setup interview"
    Write-Host "  open <name> [tool]       Open a CC (an unfinished setup continues by itself)"
    Write-Host "  migrate <folder> [tool]  Turn an existing control center, catalog or docs folder into a CC"
    Write-Host "  list                     List Control Centers under the projects root"
    Write-Host "  doctor                   Check prerequisites and AI tools"
    Write-Host "  update                   Update the kit itself (existing CCs are not changed)"
    Write-Host ""
    Write-Host "tool: claude, codex, opencode, code, cursor or none."
    Write-Host "Default: CC_KIT_TOOL, else the first installed of claude, codex, opencode."
    Write-Host "Projects root: $projectsRoot (set CC_PROJECTS_ROOT to change it)"
}

function Fail([string]$Message, [int]$Code = 1) {
    [Console]::Error.WriteLine("error: $Message")
    exit $Code
}

function Test-Tool([string]$Name) {
    [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-DefaultTool {
    if ($env:CC_KIT_TOOL) { return $env:CC_KIT_TOOL }
    foreach ($candidate in @("claude", "codex", "opencode")) {
        if (Test-Tool $candidate) { return $candidate }
    }
    return "none"
}

# Claude Code runs its hooks with Git Bash, not with the WSL bash.exe in WindowsApps.
function Find-GitBash {
    $candidates = @()
    if ($env:CLAUDE_CODE_GIT_BASH_PATH) { $candidates += $env:CLAUDE_CODE_GIT_BASH_PATH }
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        # git.exe is in <Git>\cmd or <Git>\mingw64\bin; bash.exe is in <Git>\bin.
        $gitHome = Split-Path (Split-Path $git.Source)
        $candidates += (Join-Path $gitHome "bin\bash.exe")
        $candidates += (Join-Path (Split-Path $gitHome) "bin\bash.exe")
    }
    $candidates += (Join-Path $env:ProgramFiles "Git\bin\bash.exe")
    $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Git\bin\bash.exe")
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Get-CcStatus([string]$Folder) {
    try {
        $json = Get-Content -Raw -LiteralPath (Join-Path $Folder "control-center.json") | ConvertFrom-Json
        if ($json.status) { return [string]$json.status }
    } catch { }
    return "unknown"
}

function Get-CcDir([string]$Name) {
    if (-not $Name) { Fail "name is required" 2 }
    if ($Name.EndsWith("_CC")) { $Name = $Name.Substring(0, $Name.Length - 3) }
    if ($Name -cnotmatch '^[a-z0-9][a-z0-9-]*$') {
        Fail "name must use lowercase letters, digits and dashes: $Name" 2
    }
    Join-Path $projectsRoot "$($Name)_CC"
}

# Runs a Python script of the kit with the kit's pinned uv and Python, the way cc.cmd runs scripts\cc.py.
# The environment is put back afterwards, so a tool started later does not inherit the kit's venv.
function Invoke-KitPython([string[]]$Arguments) {
    $pinLine = Get-Content -LiteralPath (Join-Path $kitRoot ".uv-pin") | Where-Object { $_ -like "version *" } | Select-Object -First 1
    $uvVersion = $pinLine.Split(" ")[1]
    $uv = Join-Path $kitRoot ".cc-local\tools\uv\$uvVersion\uv.exe"
    if (-not (Test-Path -LiteralPath $uv)) {
        & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $kitRoot "scripts\uv-bootstrap.ps1") | Out-Host
        if ($LASTEXITCODE -ne 0) { Fail "could not download the kit's uv" }
    }
    $settings = @{
        "PYTHONUTF8" = "1"
        "UV_PYTHON_PREFERENCE" = "only-managed"
        "UV_PYTHON_INSTALL_DIR" = (Join-Path $kitRoot ".cc-local\tools\python")
        "UV_PROJECT_ENVIRONMENT" = (Join-Path $kitRoot ".cc-local\venvs\windows")
    }
    $saved = @{}
    foreach ($key in $settings.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
        [Environment]::SetEnvironmentVariable($key, $settings[$key], "Process")
    }
    try {
        & $uv run --locked --quiet --project $kitRoot python -B @Arguments | Out-Host
        $code = $LASTEXITCODE
    } finally {
        foreach ($key in $settings.Keys) {
            [Environment]::SetEnvironmentVariable($key, $saved[$key], "Process")
        }
    }
    return $code
}

# Starts the tool in the folder with the opening prompt, where the tool takes one.
function Start-Tool([string]$Folder, [string]$ToolName, [string]$Prompt) {
    if ($ToolName -eq "none") {
        Write-Host "Open $Folder in your AI tool (see $kitRoot\GUIDE.md)."
        if ($Prompt) { Write-Host "If the agent does not start on its own, tell it: $Prompt" }
        return
    }
    if (@("claude", "codex", "opencode", "code", "cursor") -notcontains $ToolName) {
        Fail "unknown tool: $ToolName (claude, codex, opencode, code, cursor or none)" 2
    }
    if (-not (Test-Tool $ToolName)) {
        Fail "$ToolName is not installed. Install it with: $kitRoot\install\windows\install.ps1 -Tools $ToolName"
    }
    if (@("code", "cursor") -contains $ToolName) {
        if ($Prompt) { Write-Host "If the agent chat does not start on its own, tell it: $Prompt" }
        & $ToolName $Folder
        return
    }
    if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
        Write-Host "Not in an interactive terminal. Run: cd `"$Folder`"; $ToolName"
        return
    }
    Set-Location -LiteralPath $Folder
    # Claude Code and Codex take the first message as an argument and stay interactive.
    if ($Prompt -and ($ToolName -ne "opencode")) {
        & $ToolName $Prompt
    } else {
        & $ToolName
    }
    exit $LASTEXITCODE
}

function Get-ToolOrDefault {
    if ($Tool) { return $Tool }
    return (Get-DefaultTool)
}

function Invoke-New {
    $folder = Get-CcDir $Target
    if ((Test-Path -LiteralPath $folder) -and (Get-ChildItem -Force -LiteralPath $folder | Select-Object -First 1)) {
        [Console]::Error.WriteLine("error: $folder already exists and is not empty.")
        [Console]::Error.WriteLine("To turn an existing folder into a CC: cc-kit migrate <folder>")
        exit 1
    }
    New-Item -ItemType Directory -Force -Path $projectsRoot | Out-Null
    $code = Invoke-KitPython @((Join-Path $kitRoot "scripts\materialize-template.py"), $folder)
    if ($code -ne 0) { Fail "could not create $folder" }
    # Nix flakes (the optional extra) only see files git knows about, so stage everything now.
    & git -C $folder add -A
    if ($LASTEXITCODE -ne 0) { Fail "git add failed in $folder" }

    # The first run downloads the CC's own uv and Python; do it now, not inside the first rule hook.
    Write-Host ""
    Write-Host "Preparing the CC's runtime (the first run downloads uv and Python, about a minute)"
    Push-Location -LiteralPath $folder
    try {
        & (Join-Path $folder "cc.cmd") doctor
        if ($LASTEXITCODE -ne 0) { Write-Host "!!  cc doctor reported a problem; read the message above." -ForegroundColor Yellow }
    } finally {
        Pop-Location
    }

    $name = (Split-Path -Leaf $folder) -replace '_CC$', ''
    Write-Host ""
    Write-Host "Created $folder"
    Write-Host "Starting the setup interview: the agent asks about your repos and sets everything up."
    Write-Host "Stop any time; `"cc-kit open $name`" continues where you left off."
    Write-Host ""
    Start-Tool $folder (Get-ToolOrDefault) $setupPrompt
}

function Invoke-List {
    $found = $false
    if (Test-Path -LiteralPath $projectsRoot) {
        foreach ($directory in Get-ChildItem -Directory -LiteralPath $projectsRoot -Filter "*_CC") {
            if (-not (Test-Path -LiteralPath (Join-Path $directory.FullName "control-center.json"))) { continue }
            $status = Get-CcStatus $directory.FullName
            if ($status -ne "ready") {
                $status = "$status (setup not finished: cc-kit open $($directory.Name -replace '_CC$', ''))"
            }
            Write-Host ("{0,-40} {1}" -f $directory.Name, $status)
            $found = $true
        }
    }
    if (-not $found) { Write-Host "No Control Centers in $projectsRoot yet. Create one with: cc-kit new <name>" }
}

function Invoke-Open {
    $folder = Get-CcDir $Target
    if (-not (Test-Path -LiteralPath (Join-Path $folder "control-center.json"))) {
        Fail "no Control Center at $folder; see cc-kit list"
    }
    $prompt = ""
    switch (Get-CcStatus $folder) {
        "discovery" { $prompt = $setupPrompt }
        "adapting" { $prompt = $continuePrompt }
    }
    Start-Tool $folder (Get-ToolOrDefault) $prompt
}

function Invoke-Migrate {
    if (-not $Target) { Fail "usage: cc-kit migrate <folder> [tool]" 2 }
    if (-not (Test-Path -LiteralPath $Target -PathType Container)) { Fail "$Target is not a folder" }
    $folder = (Resolve-Path -LiteralPath $Target).Path
    if ((Test-Path (Join-Path $folder "control-center.json")) -and (Test-Path (Join-Path $folder "cc.cmd"))) {
        Fail "$folder is already a Control Center; use cc-kit open"
    }
    $template = Join-Path $folder "cc_template"
    if (Test-Path -LiteralPath $template) {
        Write-Host "Reusing $template"
    } else {
        # The agent reads the template from here and says when it can be deleted.
        & git clone --quiet $kitRoot $template
        if ($LASTEXITCODE -ne 0) { Fail "could not copy the kit into $template" }
    }
    Write-Host "Starting the migration interview. Nothing is changed before a read-only inventory."
    Write-Host ""
    Start-Tool $folder (Get-ToolOrDefault) $migratePrompt
}

function Invoke-Doctor {
    $script:failures = 0
    function Ok([string]$Text) { Write-Host "  ok    $Text" }
    function Warn([string]$Text) { Write-Host "  warn  $Text" }
    function Bad([string]$Text) { Write-Host "  FAIL  $Text"; $script:failures++ }

    Write-Host "System"
    Ok "Windows $([Environment]::OSVersion.Version) (native core, decision 0033)"
    if ($projectsRoot -like "\\wsl*") {
        Bad "projects root is inside WSL ($projectsRoot); native CCs live on the Windows drive"
    } else {
        Ok "projects root: $projectsRoot"
    }

    Write-Host ""
    Write-Host "Required"
    if (Test-Tool git) { Ok ((& git --version) -replace '^git version ', 'git ') } else { Bad "git is missing; install Git for Windows" }
    $gitBash = Find-GitBash
    if ($gitBash) { Ok "Git Bash: $gitBash (Claude Code hooks use it)" } else { Bad "Git Bash is missing; install Git for Windows" }
    $pinLine = Get-Content -LiteralPath (Join-Path $kitRoot ".uv-pin") | Where-Object { $_ -like "version *" } | Select-Object -First 1
    $uvVersion = $pinLine.Split(" ")[1]
    if (Test-Path -LiteralPath (Join-Path $kitRoot ".cc-local\tools\uv\$uvVersion\uv.exe")) {
        Ok "kit runtime: uv $uvVersion"
    } else {
        Warn "kit runtime: uv $uvVersion is downloaded on the first cc-kit new"
    }

    Write-Host ""
    Write-Host "Optional"
    if (Test-Tool nix) {
        Ok "Nix (the optional extra)"
    } else {
        Warn "Nix is absent: project gates need the optional Nix extra (install.ps1 -WithNix) until milestone 2"
    }
    if (Test-Tool docker) { Ok "docker command" } else { Warn "no Docker: only needed for container workflows (see GUIDE.md)" }
    foreach ($name in @("claude", "codex", "opencode")) {
        if (Test-Tool $name) { Ok "$name CLI" } else { Warn "$name CLI not installed" }
    }
    foreach ($name in @("code", "cursor")) {
        if (Test-Tool $name) { Ok "$name command" } else { Warn "$name command not on PATH" }
    }
    $head = "not a git clone"
    if (Test-Tool git) {
        $short = & git -C $kitRoot rev-parse --short HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $short) { $head = $short }
    }
    Write-Host ""
    Write-Host "Kit: $kitRoot ($head)"
    if ($script:failures -gt 0) {
        Write-Host "$($script:failures) required check(s) failed."
        exit 1
    }
    Write-Host "All required checks passed."
}

switch ($Command) {
    "new" { Invoke-New }
    "list" { Invoke-List }
    "open" { Invoke-Open }
    "migrate" { Invoke-Migrate }
    "doctor" { Invoke-Doctor }
    "update" {
        & git -C $kitRoot pull --ff-only
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Host "Kit updated. Existing Control Centers are separate repos and were not changed."
    }
    { @("help", "-h", "--help") -contains $_ } {
        Show-Usage
        Write-Host ""
        Write-Host "Your Control Centers:"
        Invoke-List
    }
    default {
        [Console]::Error.WriteLine("error: unknown command: $Command")
        Show-Usage
        exit 2
    }
}
