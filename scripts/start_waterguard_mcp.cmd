@echo off
setlocal

cd /d "%~dp0.."
if defined WATERGUARD_PYTHON (
    set "WG_PYTHON=%WATERGUARD_PYTHON%"
) else (
    set "WG_PYTHON=%~dp0..\.venv\Scripts\python.exe"
)
if not exist "%WG_PYTHON%" (
    echo WaterGuard Python not found. Create .venv or set WATERGUARD_PYTHON to an executable path. 1>&2
    exit /b 1
)
"%WG_PYTHON%" -m services.tool_gateway.server
exit /b %ERRORLEVEL%
