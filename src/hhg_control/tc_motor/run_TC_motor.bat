@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0..\..\.."
if not exist ".venv\Scripts\python.exe" (
    echo The project .venv is missing. Set up the project environment first.
    pause
    exit /b 1
)
set "PYTHONPATH=%CD%\src"
".venv\Scripts\python.exe" -m hhg_control.tc_motor.gui
if errorlevel 1 pause
