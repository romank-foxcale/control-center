@echo off
rem Run the CC on native Windows from cmd or PowerShell: cc check, cc verify, ...
rem Finds the pinned uv (downloads and checks it on first use), then runs scripts\cc.py with the Python and
rem dependencies locked in uv.lock. Why: docs/kit/decisions/0033-native-core-nix-optional.md
rem Limits of cmd.exe that no batch file can lift: it cuts an argument at its first line break (refused below), and
rem PowerShell 5.1 passes arguments to a .cmd file in a way this file cannot see or repair: it drops the double quotes
rem inside an argument, and it does not quote one without spaces, so cmd.exe acts on its & | < > characters.
rem For text with line breaks, double quotes or those characters, run ./cc from Git Bash.
setlocal
rem The CC root as a clean full path: no trailing backslash to escape a closing quote, no "\." for uv's venv.
for %%i in ("%~dp0.") do set "ROOT=%%~fi"
for /f "tokens=2" %%v in ('findstr /b /c:"version " "%ROOT%\.uv-pin"') do set "UVVERSION=%%v"
set "UV=%ROOT%\.cc-local\tools\uv\%UVVERSION%\uv.exe"
set "VENV=%ROOT%\.cc-local\venvs\windows"
rem An argument with a line break ends this batch line there, and the rest of the command is lost without a word.
rem The full command line in cmdcmdline still has it: refuse when it holds a line feed.
setlocal EnableDelayedExpansion
set LF=^


set "LINE=!cmdcmdline!"
for %%L in ("!LF!") do set "FLAT=!LINE:%%~L=!"
if not "!FLAT!"=="!LINE!" (endlocal & goto multiline)
endlocal
rem A rule hook must answer within Claude Code's 15 s hook timeout. A first download of uv and Python takes longer, fails
rem there and starts again on every tool call, so a hook on a CC that is not set up refuses at once instead.
if /i not "%~1"=="hook" goto ready
rem A rule hook runs on every tool call. After a pull that moves the pin, the new uv is not downloaded yet: a hook then
rem uses a uv already here, so open sessions keep working; the next command outside a hook downloads the pinned one.
if not exist "%UV%" for /d %%d in ("%ROOT%\.cc-local\tools\uv\*") do if exist "%%d\uv.exe" set "UV=%%d\uv.exe"
if not exist "%UV%" goto notready
if exist "%VENV%\pyvenv.cfg" goto ready
:notready
set "EVENT=%~2"
if /i "%EVENT:~0,4%"=="pre-" goto denytool
echo {"systemMessage": "The CC is not set up on this machine yet: run ./cc doctor (or .\\cc.cmd doctor) once in a terminal, then try again."}
exit /b 0
:denytool
echo {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "The CC is not set up on this machine yet: run ./cc doctor (or .\\cc.cmd doctor) once in a terminal, then try again."}}
exit /b 0
:ready
if not exist "%UV%" (
  powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%ROOT%\scripts\uv-bootstrap.ps1" 1>&2
  if errorlevel 1 exit /b 1
)
set "PYTHONUTF8=1"
rem UV_NO_CONFIG: a uv.toml of the user (an index, a cache, a Python preference) must not change how the CC runs.
set "UV_NO_CONFIG=1"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_PYTHON_INSTALL_DIR=%ROOT%\.cc-local\tools\python"
set "UV_PROJECT_ENVIRONMENT=%VENV%"
rem A rule hook goes straight to the guard, so a broken cc.py cannot block its repair (scripts\hook_guard.py).
if /i "%~1"=="hook" goto hook
"%UV%" run --locked --quiet --project "%ROOT%" python -B "%ROOT%\scripts\cc.py" %*
exit /b %errorlevel%
:hook
"%UV%" run --locked --quiet --project "%ROOT%" python -B "%ROOT%\scripts\hook_guard.py" %2
exit /b %errorlevel%
:multiline
echo cc: an argument has a line break, and cmd.exe cuts the command there. Run ./cc from Git Bash for such text. 1>&2
exit /b 2
