@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0..\..\.."
if /I "%~1"=="check-link-software" goto check_link_software
if /I "%~1"=="check-link-sequence" goto check_link_sequence
if not exist ".venv\Scripts\python.exe" (
    echo The project .venv is missing. Set up the project environment first.
    pause
    exit /b 1
)
set "PYTHONPATH=%CD%\src"
if /I "%~1"=="setup" goto setup
if /I "%~1"=="check-link" goto check_link
if /I "%~1"=="check-link-reference" goto check_link_reference
if /I "%~1"=="check-link-full" goto check_link_full
".venv\Scripts\python.exe" -m hhg_control.tc_motor.gui
if errorlevel 1 pause
exit /b

:check_link
".venv\Scripts\python.exe" -m hhg_control.tc_motor.check_serial_link --physical --port "%~2" --transport compare
goto check_link_done

:check_link_reference
".venv\Scripts\python.exe" -m hhg_control.tc_motor.check_serial_link --physical --port "%~2" --transport labview-reference --duration 120
goto check_link_done

:check_link_full
".venv\Scripts\python.exe" -m hhg_control.tc_motor.check_serial_link --physical --port "%~2" --transport comprehensive
goto check_link_done

:check_link_software
set "native_comparison=-FullComparison"
goto check_link_native

:check_link_sequence
set "native_comparison=-SequenceComparison"

:check_link_native
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0check_ni_max_comparison.ps1" -Physical -Port "%~2" %native_comparison%

:check_link_done
set "check_result=%errorlevel%"
pause
exit /b %check_result%

:setup
echo Installing TC motor physical-mode Python dependencies into the project .venv...
rem Keep this requirement aligned with the hardware-smc100 extra in pyproject.toml.
".venv\Scripts\python.exe" -m pip install "pyvisa>=1.14,<2" "pyserial>=3.5,<4"
if errorlevel 1 goto setup_failed
".venv\Scripts\python.exe" -m hhg_control.tc_motor.check_environment --transport both
if errorlevel 1 goto setup_failed
echo Setup passed. No serial ports were opened. Launch TC motor normally to continue.
pause
exit /b 0

:setup_failed
echo Physical-mode setup did not complete. Read the error above.
pause
exit /b 1
