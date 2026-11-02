@echo off
chcp 65001 >nul
title NVIDIA TrueHDR to Dolby Vision 8.4
setlocal
set "PYTHONUTF8=1"
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Tui.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Tui.ps1" %*
)
set "result=%errorlevel%"
pause
exit /b %result%
