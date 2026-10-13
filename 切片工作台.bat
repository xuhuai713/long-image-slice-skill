@echo off
chcp 65001 >nul
title Slice Studio
setlocal
set "PY=C:\Users\user\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if "%~1"=="" (
  "%PY%" slice_studio.py
) else (
  "%PY%" slice_studio.py "%~1"
)
if errorlevel 1 (
  echo.
  echo   [Launch failed] See the error message above.
  pause
)
