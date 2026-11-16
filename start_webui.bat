@echo off
setlocal
title LitAgent
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project Python is missing. Create .venv and install requirements.txt first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m scripts.launch
if errorlevel 1 pause
