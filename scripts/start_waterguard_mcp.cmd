@echo off
setlocal

cd /d "%~dp0.."
"C:\ProgramData\anaconda3\envs\waterguard-backend\python.exe" -m services.tool_gateway.server
