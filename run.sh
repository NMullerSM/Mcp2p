#!/usr/bin/env bash
# ==============================================================================
# MCP2P Desktop Launcher (Linux / macOS)
# ==============================================================================

set -e

# Compatibilidad gráfica WebKitGTK (NVIDIA / Wayland) — mitigación preventiva
export WEBKIT_DISABLE_DMABUF_RENDERER=1
export WEBKIT_DISABLE_COMPOSITING_MODE=1

# Obtener directorio raíz del proyecto
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

# Asegurar acceso a las librerías gráficas del sistema (WebKit2GTK / PyGObject) en venv
if [ -f "$DIR/.venv/pyvenv.cfg" ]; then
    if grep -q "include-system-site-packages = false" "$DIR/.venv/pyvenv.cfg" 2>/dev/null; then
        sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' "$DIR/.venv/pyvenv.cfg"
    fi
    PYTHON_BIN="$DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
else
    echo "[Error] No se encontró Python 3 instalado."
    exit 1
fi

export PYTHONPATH="$DIR:$PYTHONPATH"

# Compilar frontend React si no existe el build o si el código fuente ha cambiado
REBUILD_FRONTEND=false
if [ ! -f "$DIR/frontend/dist/index.html" ]; then
    REBUILD_FRONTEND=true
elif [ -d "$DIR/frontend/src" ] && [ -n "$(find "$DIR/frontend/src" "$DIR/frontend/package.json" -newer "$DIR/frontend/dist/index.html" 2>/dev/null | head -n 1)" ]; then
    REBUILD_FRONTEND=true
fi

# MCP2P_SKIP_FRONTEND_BUILD=1 usa el frontend/dist existente sin ejecutar npm
# (lo usa scripts/check_gui_boot.py para no compilar durante la comprobación de arranque).
if [ "${MCP2P_SKIP_FRONTEND_BUILD:-0}" = "1" ]; then
    REBUILD_FRONTEND=false
fi

if [ "$REBUILD_FRONTEND" = true ] && [ -d "$DIR/frontend" ]; then
    echo "[MCP2P] Detectados cambios en el frontend o build ausente. Compilando bundle de React (Vite)..."
    if [ ! -d "$DIR/frontend/node_modules" ]; then
        (cd "$DIR/frontend" && npm install)
    fi
    (cd "$DIR/frontend" && npm run build)
fi

echo "[MCP2P] Iniciando aplicación de escritorio..."
exec "$PYTHON_BIN" src/app/main.py "$@"
