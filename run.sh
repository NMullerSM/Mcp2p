#!/usr/bin/env bash
# ==============================================================================
# MCP2P Desktop Launcher (Linux / macOS)
# ==============================================================================

set -e

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

# Compilar frontend React si aún no existe el build
if [ ! -f "$DIR/frontend/dist/index.html" ] && [ -d "$DIR/frontend" ]; then
    echo "[MCP2P] Compilando bundle de React (Vite)..."
    (cd "$DIR/frontend" && npm run build)
fi

echo "[MCP2P] Iniciando aplicación de escritorio..."
exec "$PYTHON_BIN" src/app/main.py "$@"
