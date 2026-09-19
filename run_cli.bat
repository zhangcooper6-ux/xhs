@echo off
cd /d "%~dp0"

if not exist "%~dp0temp" mkdir "%~dp0temp"

set PYTHON_EXE=%~dp0venv\Scripts\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=python

echo ============================================================
echo   XHS Video Clone Tool (CLI Mode)
echo ============================================================
echo.
set /p TARGET_URL="Input Video URL or Path: "

if "%TARGET_URL%"=="" (
    echo Input is empty.
    pause
    exit /b
)

echo.
echo Choose Mode:
echo   [1] Fast Auto Dedup (40 Covers + Copywrite + HD)
echo   [2] AI Image Cover Mode (--ai-cover)
echo   [3] Custom Arguments
set /p MODE="Select [Default 1]: "

if "%MODE%"=="2" (
    "%PYTHON_EXE%" "%~dp0xhs_clone.py" %TARGET_URL% --ai-cover --hd-enhance --crf 18
) else if "%MODE%"=="3" (
    set /p EXTRA_ARGS="Arguments: "
    "%PYTHON_EXE%" "%~dp0xhs_clone.py" %TARGET_URL% %EXTRA_ARGS%
) else (
    "%PYTHON_EXE%" "%~dp0xhs_clone.py" %TARGET_URL% --hd-enhance --crf 18
)

echo.
echo Finished! Results saved in output folder.
pause
