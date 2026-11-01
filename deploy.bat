@echo off
setlocal
title SDR2DV Deployment
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0Deploy.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Deploy.ps1" %*
)
set "result=%errorlevel%"
pause
exit /b %result%
