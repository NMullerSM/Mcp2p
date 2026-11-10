"""
Utilidades de sistema de archivos seguras y atómicas para MCP2P.
Garantiza persistencia ACID sin escrituras parciales o corrupciones en cortes abruptos.
"""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> Path:
    """
    Escribe de forma atómica una cadena de texto en un archivo.
    - Escribe en un archivo temporal en el mismo directorio (para asegurar mismo sistema de archivos).
    - Ejecuta flush() y os.fsync() para asegurar volcado a disco físico.
    - Realiza os.replace() atómico para sustituir el archivo destino.
    """
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(target.suffix + ".tmp")

    try:
        with open(temp_target, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_target, target)
    finally:
        if temp_target.exists():
            try:
                temp_target.unlink()
            except OSError:
                pass
    return target
