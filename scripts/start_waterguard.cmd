@echo off
cd /d "%~dp0.."
pwsh -NoProfile -ExecutionPolicy Bypass -File apps\web-gpt\scripts\start-demo.ps1 %*
set "wgStartExit=%errorlevel%"
if "%~1"=="" pause
exit /b %wgStartExit%
