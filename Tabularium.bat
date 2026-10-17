@echo off
rem Starts Tabularium using the virtual environment in %LOCALAPPDATA%\Tabularium\venv
set "PY=%LOCALAPPDATA%\Tabularium\venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Virtual environment not found. Run scripts\setup-dev.ps1 first.
    pause
    exit /b 1
)
"%PY%" -m tabularium %*
if errorlevel 1 pause
