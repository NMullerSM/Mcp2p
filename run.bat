@echo off
REM ==============================================================================
REM MCP2P Desktop Launcher (Windows)
REM ==============================================================================

setlocal enabledelayedexpansion
set "DIR=%~dp0"
cd /d "%DIR%"

if exist "%DIR%.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%DIR%.venv\Scripts\python.exe"
) else (
    set "PYTHON_BIN=python"
)

set "PYTHONPATH=%DIR%;%PYTHONPATH%"

if not exist "%DIR%frontend\dist\index.html" (
    if exist "%DIR%frontend\package.json" (
        echo [MCP2P] Compilando bundle de React...
        cd /d "%DIR%frontend"
        call npm run build
        cd /d "%DIR%"
    )
)

echo [MCP2P] Iniciando aplicacion de escritorio en Windows...
"%PYTHON_BIN%" src\app\main.py %*

endlocal
