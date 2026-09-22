@echo off
setlocal
set PYTHONIOENCODING=utf-8

:: Prioritize Python 3.12, then Python launcher py, then python in PATH
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m hhg_control.ui.camera_gui
) else (
    py -m hhg_control.ui.camera_gui
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application exited with an error code: %ERRORLEVEL%
    pause
)
endlocal
