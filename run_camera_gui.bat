@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo This checkout needs its Python 3.12 virtual environment.
    echo Create .venv and install requirements.txt before launching the GUI.
    pause
    exit /b 1
)

set "PYTHONPATH=%~dp0src"
".venv\Scripts\python.exe" -m hhg_control.ui.camera_gui
if errorlevel 1 (
    echo.
    echo Application exited with an error.
    pause
)
endlocal

