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
if /I "%~1"=="setup" goto setup
".venv\Scripts\python.exe" -m hhg_control.tc_motor.gui
if errorlevel 1 pause
exit /b

:setup
echo Installing TC motor physical-mode Python dependencies into the project .venv...
rem Keep this requirement aligned with the hardware-smc100 extra in pyproject.toml.
".venv\Scripts\python.exe" -m pip install "pyvisa>=1.14,<2"
if errorlevel 1 goto setup_failed
".venv\Scripts\python.exe" -m hhg_control.tc_motor.check_environment
if errorlevel 1 goto setup_failed
echo Setup passed. No serial ports were opened. Launch TC motor normally to continue.
pause
exit /b 0

:setup_failed
echo Physical-mode setup did not complete. Read the error above.
pause
exit /b 1
