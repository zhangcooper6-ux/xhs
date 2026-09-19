@echo off
cd /d "%~dp0"

if not exist "%~dp0temp" mkdir "%~dp0temp"

set PYTHON_EXE=%~dp0venv\Scripts\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=python

echo ============================================================
echo Starting XHS Clone Web UI...
echo URL: http://127.0.0.1:5000
echo ============================================================
echo.

start "" http://127.0.0.1:5000

"%PYTHON_EXE%" "%~dp0web_ui.py"

echo.
echo Program exited.
pause
