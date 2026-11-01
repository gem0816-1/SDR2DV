@echo off
chcp 65001 >nul
title NVIDIA TrueHDR to Dolby Vision 8.4
setlocal
if "%~1"=="" goto usage
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0SDR2DV.ps1" -InputPath "%~1" -HdrMode TrueHDR -Bilibili -Encoder NVENC -NvencPreset p6
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0SDR2DV.ps1" -InputPath "%~1" -HdrMode TrueHDR -Bilibili -Encoder NVENC -NvencPreset p6
)
set "result=%errorlevel%"
pause
exit /b %result%
:usage
echo Drag an SDR video here.
echo Output: Dolby Vision 8.4, target 28 Mbps. PCM audio: lossless ALAC.
echo Encoder: NVIDIA NVENC Main10, P6 HQ. Compatible NVIDIA GPU required.
echo Reference brightness: 1000 nits. Warm skin correction: 0.5.
echo Output name: movie_TrueHDR_Ref_DV84.mp4
echo First-time setup: run ..\deploy.bat -Mode TrueHDR
pause
exit /b 1
