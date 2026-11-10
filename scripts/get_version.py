"""
Herramienta auxiliar para consultar la versión actual de MCP2P definida en src/app/version.py,
de forma agnóstica a la plataforma y al shell (usada por Inno Setup y por el workflow de CI/CD
para nombrar artefactos y evitar colisiones de tags).
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

try:
    from src.app.version import __version__
    print(__version__)
except Exception as exc:
    print(f"ERROR leyendo src/app/version.py: {exc}", file=sys.stderr)
    sys.exit(1)
