@echo off
rem Run the CC on native Windows from cmd or PowerShell: cc check, cc verify, ...
rem Finds the pinned uv (downloads and checks it on first use), then runs scripts\cc.py with the Python and
rem dependencies locked in uv.lock. Why: plans/active/native-core.md
setlocal
rem The CC root as a clean full path: no trailing backslash to escape a closing quote, no "\." for uv's venv.
for %%i in ("%~dp0.") do set "ROOT=%%~fi"
for /f "tokens=2" %%v in ('findstr /b /c:"version " "%ROOT%\.uv-pin"') do set "UVVERSION=%%v"
set "UV=%ROOT%\.cc-local\tools\uv\%UVVERSION%\uv.exe"
if not exist "%UV%" (
  powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%ROOT%\scripts\uv-bootstrap.ps1" 1>&2
  if errorlevel 1 exit /b 1
)
set "PYTHONUTF8=1"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_PYTHON_INSTALL_DIR=%ROOT%\.cc-local\tools\python"
set "UV_PROJECT_ENVIRONMENT=%ROOT%\.cc-local\venvs\windows"
rem A rule hook goes straight to the guard, so a broken cc.py cannot block its repair (scripts\hook_guard.py).
if /i "%~1"=="hook" goto hook
"%UV%" run --locked --quiet --project "%ROOT%" python -B "%ROOT%\scripts\cc.py" %*
exit /b %errorlevel%
:hook
"%UV%" run --locked --quiet --project "%ROOT%" python -B "%ROOT%\scripts\hook_guard.py" %2
exit /b %errorlevel%
