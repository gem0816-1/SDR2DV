@echo off
chcp 65001 >nul
title SDR to Bilibili Dolby Vision 8.4
setlocal
if "%~1"=="" goto usage
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0SDR2DV.ps1" -InputPath "%~1" -Bilibili -Preset medium
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0SDR2DV.ps1" -InputPath "%~1" -Bilibili -Preset medium
)
set "result=%errorlevel%"
pause
exit /b %result%
:usage
echo Drag an SDR video file onto this batch file.
echo Output: Profile 8.4 / HLG, highlight peak 1000 nits, target 28 Mbps.
echo PCM audio: lossless ALAC. Sample rate and bit depth are preserved.
pause
exit /b 1
