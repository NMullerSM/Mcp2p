#!/usr/bin/env python3
"""
Script de exportación de Precedentes Arquitectónicos OKF v2 (0 tokens).

Compila todos los dictámenes del Jurado de Honor (architecture/rulings/*.yaml)
y los Architecture Decision Records (architecture/adrs/*.md) en un índice YAML
estructurado (docs/okf/architecture/precedents_index.yaml) para consulta rápida
sin consumo de ventana de contexto LLM.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.storage.okf_adapter import OKFAdapter


def main() -> int:
    parser = argparse.ArgumentParser(description="Exportador de precedentes arquitectónicos OKF v2")
    parser.add_argument(
        "--gov-path",
        required=True,
        help="Ruta base del repositorio de gobernanza (donde reside docs/okf)"
    )
    args = parser.parse_args()

    adapter = OKFAdapter()
    index_path = adapter.export_precedents_index(args.gov_path)
    print(f"[OKF] Índice de precedentes exportado en: {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
