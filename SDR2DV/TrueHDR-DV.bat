@echo off
chcp 65001 >nul
title NVIDIA TrueHDR to Dolby Vision 8.4
setlocal
if "%~1"=="" goto usage
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0SDR2DV.ps1" -InputPath "%~1" -HdrMode TrueHDR -Bilibili -Preset medium
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0SDR2DV.ps1" -InputPath "%~1" -HdrMode TrueHDR -Bilibili -Preset medium
)
set "result=%errorlevel%"
pause
exit /b %result%
:usage
echo Drag an SDR video here.
echo Output: Dolby Vision 8.4, target 28 Mbps. PCM audio: lossless ALAC.
echo First-time setup: run Setup-TrueHDR.ps1
pause
exit /b 1
