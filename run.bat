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

set "REBUILD_FRONTEND=0"
if not exist "%DIR%frontend\dist\index.html" (
    set "REBUILD_FRONTEND=1"
) else (
    REM Verificar si algun archivo de src es mas reciente que dist\index.html
    powershell -NoProfile -Command "if (Get-ChildItem -Path '%DIR%frontend\src', '%DIR%frontend\package.json' -Recurse | Where-Object { $_.LastWriteTime -gt (Get-Item '%DIR%frontend\dist\index.html').LastWriteTime }) { exit 1 } else { exit 0 }" >nul 2>&1
    if !ERRORLEVEL! neq 0 (
        set "REBUILD_FRONTEND=1"
    )
)

if "!REBUILD_FRONTEND!"=="1" (
    if exist "%DIR%frontend\package.json" (
        if not exist "%DIR%frontend\node_modules" (
            echo [MCP2P] Instalando dependencias del Frontend con npm install...
            cd /d "%DIR%frontend"
            call npm install
            cd /d "%DIR%"
        )
        echo [MCP2P] Detectados cambios en el frontend o build ausente. Compilando bundle de React (Vite)...
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
