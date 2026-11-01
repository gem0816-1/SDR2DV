@echo off
chcp 65001 >nul
title SDR to Dolby Vision 8.1
setlocal

if "%~1"=="" goto usage

echo.
echo   Input: %~1
echo.

where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0SDR2DV.ps1" -InputPath "%~1"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0SDR2DV.ps1" -InputPath "%~1"
)
set "result=%errorlevel%"

echo.
pause
exit /b %result%

:usage
echo.
echo  ============================================================
echo    SDR to Dolby Vision 8.1   one-click converter
echo  ============================================================
echo.
echo    Usage 1: drag an SDR video file (or a folder) onto this .bat
echo    PowerShell: .\SDR2DV.ps1 -InputPath "D:\movie.mkv"
echo.
echo    Default output: xxx_DV81.mp4 next to the source
echo    Need MKV (keep all audio/subtitles): run SDR2DV.ps1 -Format both
echo.
pause
exit /b 1
