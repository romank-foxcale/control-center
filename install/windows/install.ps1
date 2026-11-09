<#
.SYNOPSIS
  Sets up the Control Center kit on Windows, natively: Git for Windows, Claude Code, the kit and a first CC.

.DESCRIPTION
  Safe to re-run. Each step checks what is already there and skips it.
  Start it as a normal user. The default path needs no WSL and no Nix
  (docs/kit/decisions/0033-native-core-nix-optional.md):
    1. Git for Windows (git and Git Bash, which Claude Code hooks need), with winget if it is missing.
    2. The command-line AI tools from -Tools (default: claude).
    3. The kit in %USERPROFILE%\.cc-kit and a cc-kit command for PowerShell and cmd.
    4. The first Control Center in %USERPROFILE%\Projects\<name>_CC, with its runtime downloaded.
  -WithNix installs the optional Nix extra instead: WSL2 + Ubuntu, then Nix, the kit and the tools inside
  Ubuntu, as before. It asks for administrator rights only to install WSL and continues by itself after a reboot.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install\windows\install.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install\windows\install.ps1 -Tools claude,codex,opencode
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install\windows\install.ps1 -WithNix
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install\windows\install.ps1 -DryRun -InstallRoot C:\temp\kit -ProjectsRoot C:\temp\projects -Name demo
#>
[CmdletBinding()]
param(
    # Install the optional Nix extra: WSL2 + Ubuntu with Nix, the kit and the tools inside it.
    [switch]$WithNix,
    [string]$Distro = "Ubuntu",
    [string]$KitRepo = "https://github.com/romank-foxcale/command-center.git",
    [string]$KitBranch = "main",
    # For testing: copy the kit from a local Windows folder instead of cloning it.
    [string]$KitSource = "",
    # Command-line AI tools to install: claude, codex, opencode, or none.
    [string[]]$Tools = @("claude"),
    # For testing: the folder for the kit (<InstallRoot>\.cc-kit) and the cc-kit launcher (<InstallRoot>\bin).
    [string]$InstallRoot = "",
    # For testing or another location: where Control Centers are created (default %USERPROFILE%\Projects).
    [string]$ProjectsRoot = "",
    # The first Control Center's name; without it the script asks, if nobody has a CC yet.
    [string]$Name = "",
    # Print the steps that change the system (winget, tool installers, the user PATH) instead of running them.
    # The kit and the CC are still made for real, but only in the folders -InstallRoot and -ProjectsRoot give.
    [switch]$DryRun,
    # Internal: the elevated copy of this script that only installs WSL.
    [switch]$WslOnly
)

$ErrorActionPreference = "Stop"
# Without this, wsl.exe prints UTF-16 text that PowerShell cannot compare.
$env:WSL_UTF8 = "1"

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Note([string]$Message) { Write-Host "    $Message" }
function Write-Warn([string]$Message) { Write-Host "!!  $Message" -ForegroundColor Yellow }

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Runs a native command, dropping its error output. PowerShell 5.1 turns any
# redirected stderr into a terminating error under ErrorActionPreference=Stop.
function Invoke-Quiet([scriptblock]$Block) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Block 2>$null } finally { $ErrorActionPreference = $previous }
}

function Test-Tool([string]$ToolName) {
    [bool](Get-Command $ToolName -ErrorAction SilentlyContinue)
}

# ---------------------------------------------------------------------------------------------
# The optional Nix extra: WSL2 + Ubuntu, then Nix and the kit inside WSL (the installer before 0033).
# ---------------------------------------------------------------------------------------------
function Install-WithNix {
    function Get-Distros {
        $output = Invoke-Quiet { wsl.exe --list --quiet }
        if ($LASTEXITCODE -ne 0) { return @() }
        @($output | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    }

    function Invoke-Wsl([string]$Command) {
        & wsl.exe -d $Distro -- bash -lc $Command
        if ($LASTEXITCODE -ne 0) { throw "WSL command failed ($LASTEXITCODE): $Command" }
    }

    # 1. Windows version: WSL2 needs Windows 10 build 19041+ or Windows 11.
    Write-Step "Checking Windows"
    $build = [Environment]::OSVersion.Version.Build
    if ($build -lt 19041) {
        throw "Windows build $build is too old for WSL2. Update Windows first (build 19041 or newer)."
    }
    Write-Note "Windows build $build OK"

    # 2. WSL itself and the Linux distribution.
    Write-Step "Checking WSL2 and $Distro"

    # A copy of this script that survives a reboot: the elevated WSL step and the resume after sign-in run it.
    $persisted = Join-Path $env:LOCALAPPDATA "cc-kit\install.ps1"
    if (-not $WslOnly) {
        New-Item -ItemType Directory -Force (Split-Path $persisted) | Out-Null
        if ($PSCommandPath -and ($PSCommandPath -ne $persisted)) {
            Copy-Item -Force $PSCommandPath $persisted
        } elseif (-not $PSCommandPath) {
            Invoke-RestMethod "https://raw.githubusercontent.com/romank-foxcale/command-center/$KitBranch/install/windows/install.ps1" -OutFile $persisted
        }
    }
    $rerun = "-ExecutionPolicy Bypass -File `"$persisted`" -WithNix -Distro $Distro -KitRepo $KitRepo -KitBranch $KitBranch -Tools $($Tools -join ',')"
    if ($KitSource) { $rerun += " -KitSource `"$KitSource`"" }

    function Test-WslReady {
        if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { return $false }
        Invoke-Quiet { wsl.exe --status } | Out-Null
        ($LASTEXITCODE -eq 0) -and ((Get-Distros) -contains $Distro)
    }

    if (-not (Test-WslReady)) {
        if ($WslOnly -or (Test-Admin)) {
            Write-Note "Installing WSL2 with $Distro (this can take several minutes)"
            Write-Note "When $Distro asks for a UNIX username and password, create them: the username in lowercase,"
            Write-Note "the password stays invisible while you type. Then type 'exit' to come back here."
            # No --no-launch: on current WSL it can leave the distro unregistered.
            & wsl.exe --install -d $Distro
            if ($LASTEXITCODE -ne 0) { throw "wsl --install failed. See the message above." }
        } else {
            Write-Note "Installing WSL needs administrator rights: Windows asks for them now, in a second window."
            Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList "$rerun -WslOnly"
        }
        if ($WslOnly) { exit 0 }
        if (-not (Test-WslReady)) {
            # WSL only works after a reboot: continue by itself after the next sign-in.
            Set-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" "CcKitInstall" "powershell.exe -NoExit $rerun"
            Write-Warn "Restart Windows to finish installing WSL. After you sign in, this setup continues by itself."
            exit 0
        }
    }
    if ($WslOnly) { exit 0 }
    & wsl.exe --set-default-version 2 | Out-Null

    $version = (Invoke-Quiet { wsl.exe --list --verbose } | Where-Object { $_ -match "\b$([regex]::Escape($Distro))\b" }) -replace '.*\s(\d)\s*$', '$1'
    if ($version -eq "1") {
        Write-Note "Converting $Distro from WSL1 to WSL2"
        & wsl.exe --set-version $Distro 2
    }

    # 3. The distro must have a normal user; Nix and the tools must not run as root.
    $linuxUser = (Invoke-Quiet { wsl.exe -d $Distro -- whoami } | Select-Object -First 1)
    if (-not $linuxUser -or $linuxUser.Trim() -eq "root") {
        Write-Warn "$Distro has no regular Linux user yet."
        Write-Note "Run 'wsl -d $Distro', create a user and password, type 'exit', and run this script again."
        exit 1
    }
    Write-Note "$Distro is ready (user: $($linuxUser.Trim()))"

    # 4. Clone or update the kit inside the Linux filesystem, never under /mnt/c.
    Write-Step "Getting the Control Center kit inside WSL (~/.cc-kit)"
    Invoke-Wsl "command -v git >/dev/null || (sudo apt-get update && sudo apt-get install -y git)"
    if ($KitSource) {
        # Copies the folder as-is, including uncommitted changes, replacing any previous kit.
        if (-not (Test-Path (Join-Path $KitSource "install\cc-kit"))) { throw "$KitSource is not a kit folder" }
        $linuxSource = (& wsl.exe -d $Distro -- wslpath -a ((Resolve-Path $KitSource).Path -replace '\\', '/')).Trim()
        Write-Note "Copying the kit from $KitSource"
        Invoke-Wsl "rm -rf ~/.cc-kit && cp -r '$linuxSource' ~/.cc-kit && git -C ~/.cc-kit config core.fileMode false"
    } else {
        Invoke-Wsl ("if [ -d ~/.cc-kit/.git ]; then git -C ~/.cc-kit pull --ff-only; " +
            "else git clone --branch '$KitBranch' '$KitRepo' ~/.cc-kit; fi")
    }

    # 5. Everything else happens inside Linux. sudo will ask for the Linux password.
    Write-Step "Running the Linux installer inside $Distro"
    $toolArgument = if ($Tools.Count) { "--tools " + ($Tools -join ",") } else { "--tools none" }
    # The first Control Center is offered at the end of this script instead, once cc-kit works from PowerShell.
    $linuxInstall = "CC_KIT_NO_FIRST=1 ~/.cc-kit/install/install.sh $toolArgument"
    & wsl.exe -d $Distro -- bash -lc $linuxInstall
    if ($LASTEXITCODE -eq 10) {
        # install.sh turned on systemd, which only takes effect after WSL restarts.
        Write-Note "Restarting WSL to turn on systemd"
        & wsl.exe --shutdown
        & wsl.exe -d $Distro -- bash -lc $linuxInstall
    }
    if ($LASTEXITCODE -ne 0) { throw "The Linux installer failed. Read the message above, fix it, and run this script again." }

    # 6. Windows-side editors: make sure they can open folders inside WSL.
    Write-Step "Checking editors and AI apps on Windows"
    if (Get-Command code -ErrorAction SilentlyContinue) {
        & code --install-extension ms-vscode-remote.remote-wsl --force | Out-Null
        Write-Note "VS Code: WSL extension installed"
    } else {
        Write-Note "VS Code: not found (optional)"
    }
    if (Get-Command cursor -ErrorAction SilentlyContinue) {
        Invoke-Quiet { cursor --install-extension anysphere.remote-wsl --force } | Out-Null
        Write-Note "Cursor: WSL extension installed"
    } else {
        Write-Note "Cursor: not found (optional)"
    }
    # Newer builds install as an app package; older ones as a per-user folder.
    $claudeDesktop = [bool](Get-AppxPackage -Name "Claude" -ErrorAction SilentlyContinue) -or
        (Test-Path "$env:LOCALAPPDATA\AnthropicClaude")
    Write-Note ("Claude desktop: " + $(if ($claudeDesktop) { "found" } else { "not found (optional)" }))

    # 7. cc-kit from PowerShell and cmd: a launcher on the user's PATH that runs it inside WSL.
    Write-Step "Adding the cc-kit command to Windows"
    $binDir = Join-Path $env:LOCALAPPDATA "cc-kit\bin"
    New-Item -ItemType Directory -Force $binDir | Out-Null
    $launcher = @"
@echo off
rem Runs cc-kit inside WSL ($Distro), where the kit, Nix and the Control Centers live.
wsl.exe -d $Distro --cd ~ -e bash -lc "exec cc-kit \"`$@\"" cc-kit %*
exit /b %errorlevel%
"@
    Set-Content -Path (Join-Path $binDir "cc-kit.cmd") -Value $launcher -Encoding Ascii
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (($userPath -split ";") -contains $binDir)) {
        [Environment]::SetEnvironmentVariable("Path", (@($userPath, $binDir) | Where-Object { $_ }) -join ";", "User")
    }
    $env:Path = "$env:Path;$binDir"
    Write-Note "cc-kit works in any new PowerShell or cmd window"

    Write-Step "Done"
    $existing = Invoke-Quiet { wsl.exe -d $Distro -- bash -lc "ls -d ~/Projects/*_CC 2>/dev/null" }
    if (-not $existing) {
        Write-Note "Now create your first Control Center."
        Write-Note "Name it after the product or system it covers, in lowercase with dashes (for example: shop)."
        $name = Read-Host "    Name (Enter to skip)"
        if ($name) {
            & (Join-Path $binDir "cc-kit.cmd") new $name.Trim()
            exit 0
        }
    }
    Write-Note "Create a Control Center any time, in PowerShell:"
    Write-Host  "      cc-kit new my-project"
    Write-Note "It opens Claude and the agent walks you through the setup. More: GUIDE.md in the kit."
}

if ($WithNix -or $WslOnly) {
    if ($DryRun -or $InstallRoot -or $ProjectsRoot -or $Name) {
        throw "-DryRun, -InstallRoot, -ProjectsRoot and -Name apply to the native path only, not to -WithNix."
    }
    Install-WithNix
    exit 0
}

# ---------------------------------------------------------------------------------------------
# The native path (default): no WSL, no Nix.
# ---------------------------------------------------------------------------------------------

# A step that changes the system outside the kit and project folders. -DryRun prints it instead.
function Invoke-SystemStep([string]$Description, [scriptblock]$Action) {
    if ($DryRun) {
        Write-Note "[dry-run] would: $Description"
        return
    }
    Write-Note $Description
    & $Action
}

# Runs a native command and stops the script when it fails.
function Invoke-Checked([string]$What, [scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "$What failed ($LASTEXITCODE). Read the message above, fix it, and run this script again." }
}

# Rereads PATH, so a program installed a moment ago is found without a new window.
function Update-SessionPath {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($machinePath, $userPath) | Where-Object { $_ }) -join ";"
}

function Add-UserPath([string]$Folder) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($userPath -split ";") -contains $Folder) { return }
    Invoke-SystemStep "add $Folder to your user PATH" {
        [Environment]::SetEnvironmentVariable("Path", (@($userPath, $Folder) | Where-Object { $_ }) -join ";", "User")
    }
}

function Add-SessionPath([string]$Folder) {
    if (-not (($env:Path -split ";") -contains $Folder)) { $env:Path = "$env:Path;$Folder" }
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

# Where everything goes. The -InstallRoot and -ProjectsRoot overrides exist for tests.
if ($InstallRoot) {
    $kitDir = Join-Path $InstallRoot ".cc-kit"
    $binDir = Join-Path $InstallRoot "bin"
} else {
    $kitDir = Join-Path $env:USERPROFILE ".cc-kit"
    $binDir = Join-Path $env:LOCALAPPDATA "cc-kit\bin"
}
if ($ProjectsRoot) {
    $projectsDir = $ProjectsRoot
} elseif ($env:CC_PROJECTS_ROOT) {
    $projectsDir = $env:CC_PROJECTS_ROOT
} else {
    $projectsDir = Join-Path $env:USERPROFILE "Projects"
}
# Under -DryRun the default folders in your profile are not touched; only the overridden ones are.
$realKit = (-not $DryRun) -or [bool]$InstallRoot
$realProjects = (-not $DryRun) -or [bool]$ProjectsRoot
if ($DryRun) {
    Write-Warn "Dry run: system changes are only printed."
    Write-Note "kit: $kitDir$(if (-not $realKit) { ' (printed only)' })"
    Write-Note "projects: $projectsDir$(if (-not $realProjects) { ' (printed only)' })"
}

# 1. Git for Windows: git for the kit and the CCs, Git Bash for the Claude Code hooks.
Write-Step "Checking Git for Windows"
if (-not ((Test-Tool git) -and (Find-GitBash))) {
    if (Test-Tool winget) {
        Invoke-SystemStep "install Git for Windows with winget" {
            Invoke-Checked "winget install Git.Git" { winget install --id Git.Git -e --source winget }
        }
        Update-SessionPath
    } else {
        Write-Warn "Git for Windows is missing and winget is not available."
        Write-Note "Install Git for Windows from https://git-scm.com/download/win (keep the default options),"
        Write-Note "then open a new PowerShell window and run this script again."
        exit 1
    }
}
if ((Test-Tool git) -and (Find-GitBash)) {
    Write-Note "$(& git --version); Git Bash: $(Find-GitBash)"
} elseif ($DryRun) {
    Write-Note "[dry-run] Git for Windows would be installed here"
} else {
    Write-Warn "Git for Windows is installed but this window cannot see it yet."
    Write-Note "Open a new PowerShell window and run this script again."
    exit 1
}

# 2. Command-line AI tools, installed on Windows itself. Desktop apps are installed by each person.
$requestedTools = @($Tools | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -ne "none" })
if ($requestedTools.Count) {
    Write-Step "Checking AI command-line tools: $($requestedTools -join ', ')"
    # The native Claude Code installer puts claude.exe here.
    $claudeBin = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path -LiteralPath (Join-Path $claudeBin "claude.exe")) { Add-SessionPath $claudeBin }
}
foreach ($toolName in $requestedTools) {
    if (Test-Tool $toolName) {
        Write-Note "$toolName is already installed"
        continue
    }
    switch ($toolName) {
        "claude" {
            Invoke-SystemStep "install Claude Code with its native Windows installer (https://claude.ai/install.ps1)" {
                & ([scriptblock]::Create((Invoke-RestMethod "https://claude.ai/install.ps1")))
            }
            Add-UserPath $claudeBin
            Add-SessionPath $claudeBin
        }
        "codex" {
            if (Test-Tool npm) {
                Invoke-SystemStep "install Codex with npm (@openai/codex)" {
                    Invoke-Checked "npm install -g @openai/codex" { npm install -g "@openai/codex" }
                }
            } else {
                Write-Warn "Codex needs Node.js. Install Node.js LTS, then run: npm install -g @openai/codex"
            }
        }
        "opencode" {
            if (Test-Tool npm) {
                Invoke-SystemStep "install OpenCode with npm (opencode-ai)" {
                    Invoke-Checked "npm install -g opencode-ai" { npm install -g opencode-ai }
                }
            } else {
                Write-Warn "OpenCode needs Node.js. Install Node.js LTS, then run: npm install -g opencode-ai"
            }
        }
        default { throw "unknown tool '$toolName' (choose from claude, codex, opencode, none)" }
    }
}

# 3. The kit in a user folder on the Windows drive, and a cc-kit command.
Write-Step "Getting the Control Center kit ($kitDir)"
if (-not $realKit) {
    Write-Note "[dry-run] would clone or update $KitRepo ($KitBranch) in $kitDir"
} elseif ($KitSource) {
    # Copies the folder as-is, including uncommitted changes, replacing any previous kit.
    if (-not (Test-Path (Join-Path $KitSource "install\windows\cc-kit.ps1"))) { throw "$KitSource is not a kit folder" }
    Write-Note "Copying the kit from $KitSource"
    if (Test-Path -LiteralPath $kitDir) { Remove-Item -Recurse -Force -LiteralPath $kitDir }
    New-Item -ItemType Directory -Force -Path $kitDir | Out-Null
    # Local state of the source (its runtime, clones and worktrees) stays behind.
    Get-ChildItem -Force -LiteralPath $KitSource |
        Where-Object { @(".cc-local", "repos", "worktrees") -notcontains $_.Name } |
        Copy-Item -Recurse -Force -Destination $kitDir
} elseif (Test-Path -LiteralPath (Join-Path $kitDir ".git")) {
    Invoke-Checked "git pull of the kit" { git -C $kitDir pull --ff-only }
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path $kitDir) | Out-Null
    Invoke-Checked "git clone of the kit" { git clone --branch $KitBranch $KitRepo $kitDir }
}

$ccKitScript = Join-Path $kitDir "install\windows\cc-kit.ps1"
$launcherPath = Join-Path $binDir "cc-kit.cmd"
if ($realKit) {
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    $launcher = @"
@echo off
rem Runs the native Windows cc-kit of the kit in $kitDir.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ccKitScript" %*
exit /b %errorlevel%
"@
    Set-Content -Path $launcherPath -Value $launcher -Encoding Ascii
    Write-Note "launcher: $launcherPath"
} else {
    Write-Note "[dry-run] would write $launcherPath"
}
Add-UserPath $binDir
Add-SessionPath $binDir
Write-Note "cc-kit works in any new PowerShell or cmd window"

# 4. The first Control Center, unless one exists. -Name answers the question without asking.
Write-Step "Your first Control Center ($projectsDir)"
$existing = @()
if (Test-Path -LiteralPath $projectsDir) {
    $existing = @(Get-ChildItem -Directory -LiteralPath $projectsDir -Filter "*_CC" |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "control-center.json") })
}
$interactive = -not ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected)
if (-not $Name -and -not $existing -and $interactive -and -not $DryRun) {
    Write-Note "Name it after the product or system it covers, in lowercase with dashes (for example: shop)."
    $Name = (Read-Host "    Name (Enter to skip)").Trim()
}

if ($Name) {
    $ccName = $Name.Trim() -replace '_CC$', ''
    $ccDir = Join-Path $projectsDir "$($ccName)_CC"
    if (-not $realProjects) {
        Write-Note "[dry-run] would create $ccDir, run .\cc.cmd doctor there and open Claude Code"
    } else {
        # cc-kit reads the projects root from CC_PROJECTS_ROOT; set it only for this window.
        $env:CC_PROJECTS_ROOT = $projectsDir
        if (Test-Path -LiteralPath (Join-Path $ccDir "control-center.json")) {
            # A re-run: the CC is there; only make sure its runtime is downloaded.
            Write-Note "$ccDir exists; checking its runtime"
            Push-Location -LiteralPath $ccDir
            try { & (Join-Path $ccDir "cc.cmd") doctor } finally { Pop-Location }
        } else {
            # "none": create and prepare it here; the tool opens below, once everything is ready.
            Invoke-Checked "cc-kit new $ccName" { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ccKitScript new $ccName none }
        }
        if ($interactive -and -not $DryRun) {
            Write-Step "Done. Opening the setup interview."
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ccKitScript open $ccName
            exit $LASTEXITCODE
        }
        Write-Note "Open it any time with: cc-kit open $ccName"
    }
}

Write-Step "Done"
Write-Note "Create a Control Center any time, in PowerShell:"
Write-Host  "      cc-kit new my-project"
Write-Note "It opens Claude and the agent walks you through the setup. More: GUIDE.md in the kit."
Write-Note "Project build and test gates need the optional Nix extra until milestone 2: install.ps1 -WithNix."
