@echo off
REM ==============================================================================
REM MCP2P Desktop Launcher (Windows)
REM ==============================================================================

setlocal enabledelayedexpansion
set "DIR=%~dp0"
cd /d "%DIR%"

if not exist "%DIR%.venv\Scripts\python.exe" (
    echo [MCP2P] Entorno virtual no detectado. Inicializando .venv...
    python -m venv "%DIR%.venv"
    if exist "%DIR%.venv\Scripts\python.exe" (
        echo [MCP2P] Instalando dependencias de Python...
        "%DIR%.venv\Scripts\pip" install -e "%DIR%"
    ) else (
        echo [WARN] No se pudo crear .venv de forma automatica. Se intentara con el python global...
        set "PYTHON_BIN=python"
    )
)

if exist "%DIR%.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%DIR%.venv\Scripts\python.exe"
) else (
    set "PYTHON_BIN=python"
)

set "PYTHONPATH=%DIR%;%PYTHONPATH%"

if not exist "%DIR%frontend\dist\index.html" (
    if exist "%DIR%frontend\package.json" (
        if not exist "%DIR%frontend\node_modules" (
            echo [MCP2P] Instalando dependencias del Frontend con npm install...
            cd /d "%DIR%frontend"
            call npm install
            cd /d "%DIR%"
        )
        echo [MCP2P] Compilando bundle de React con npm run build...
        cd /d "%DIR%frontend"
        call npm run build
        cd /d "%DIR%"
    )
)

echo [MCP2P] Iniciando aplicacion de escritorio en Windows...
"%PYTHON_BIN%" src\app\main.py %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo [MCP2P Error] La aplicacion se cerro con error. Codigo: %ERRORLEVEL%
    pause
)

endlocal
