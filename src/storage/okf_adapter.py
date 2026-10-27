"""
Adaptador OKF (Open Knowledge Framework) para MCP2P.

Gestiona la gobernanza desacoplada, validación de esquemas de conocimiento,
registro determinista de ADRs y características técnicas (backend/frontend),
así como la sincronización con la rama huérfana de memoria del proyecto.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mcp2p.storage.okf")

DEFAULT_OKF_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "OKF Mini Governance Specification",
    "version": "1.0.0",
    "required_files": [
        "01_ARQUITECTURA_Y_GOBERNANZA.md",
        "02_MAPA_DE_CODIGO_Y_FEATURES.md"
    ],
    "required_directories": [
        "backend",
        "frontend"
    ],
    "rules": {
        "okf_01_headers": ["# OKF-01", "Architecture Decision Records", "Historias de Usuario"],
        "okf_02_headers": ["# OKF-02", "Mapa", "Features"]
    }
}


class OKFValidationError(Exception):
    """Excepción al violar las reglas de integridad del estándar OKF."""
    pass


class OKFAdapter:
    """
    Adaptador de gobernanza para repositorios OKF y sincronización de memoria huérfana.
    """

    def __init__(self, schema: Optional[Dict[str, Any]] = None) -> None:
        self.schema = schema or DEFAULT_OKF_SCHEMA

    # ==========================================
    # Inicialización de Estructura OKF
    # ==========================================

    def initialize_project_okf(
        self,
        gov_base_path: str | Path,
        project_name: str,
        project_uuid: str
    ) -> Path:
        """
        Inicializa la jerarquía estándar de carpetas y archivos OKF en el repositorio de gobernanza.
        """
        base = Path(gov_base_path)
        docs_okf = base / "docs" / "okf"
        backend_dir = docs_okf / "backend"
        frontend_dir = docs_okf / "frontend"

        backend_dir.mkdir(parents=True, exist_ok=True)
        frontend_dir.mkdir(parents=True, exist_ok=True)

        # 1. Crear okf_schema.json
        schema_file = docs_okf / "okf_schema.json"
        if not schema_file.exists():
            schema_file.write_text(json.dumps(self.schema, indent=2, ensure_ascii=False), encoding="utf-8")

        # 2. Plantilla 01_ARQUITECTURA_Y_GOBERNANZA.md
        file_01 = docs_okf / "01_ARQUITECTURA_Y_GOBERNANZA.md"
        if not file_01.exists():
            content_01 = f"""# OKF-01: Arquitectura, Gobernanza y Principios del Enjambre
## {project_name} (UUID: `{project_uuid}`)

> **Identificador**: `OKF-01`
> **Estado**: Aprobado / Inicial
> **Metodología**: Organic Driven Development (ODD)

---

## 1. Visión y Propósito
Repositorio de gobernanza y especificaciones vivas para **{project_name}**.

---

## 2. Architecture Decision Records (ADRs)

### ADR-001: Inicialización del Repositorio de Gobernanza OKF
* **Contexto**: Se requiere aislar el contexto cognitivo del código ejecutable.
* **Decisión**: Se implementa la estructura OKF desacoplada vinculada al proyecto `{project_uuid}`.
* **Consecuencias**: Mantenimiento limpio sin contaminación de tokens.

---

## 3. Historias de Usuario Principales

* **US-01**: Como agente colaborador, quiero acceder al contexto técnico sin inferencia innecesaria.
"""
            file_01.write_text(content_01, encoding="utf-8")

        # 3. Plantilla 02_MAPA_DE_CODIGO_Y_FEATURES.md
        file_02 = docs_okf / "02_MAPA_DE_CODIGO_Y_FEATURES.md"
        if not file_02.exists():
            content_02 = f"""# OKF-02: Mapa de Código, Relación entre Componentes y Features
## {project_name} (UUID: `{project_uuid}`)

> **Identificador**: `OKF-02`
> **Propósito**: Navegación rápida para agentes y desarrolladores.

---

## 1. Grafo de Componentes
```mermaid
graph TD
    App["Código Fuente (app/)"] --> Gov["Gobernanza OKF (governance/)"]
```

---

## 2. Mapa Detallado de Features

| Feature | Directorio | Ficha Técnica |
| :--- | :--- | :--- |
| **Gobernanza Base** | `docs/okf/` | `01_ARQUITECTURA_Y_GOBERNANZA.md` |
"""
            file_02.write_text(content_02, encoding="utf-8")

        return docs_okf

    # ==========================================
    # Validación e Inspección OKF
    # ==========================================

    def validate_okf_integrity(self, gov_base_path: str | Path) -> Dict[str, Any]:
        """
        Valida que el directorio cumpla estrictamente con la especificación OKF.
        """
        base = Path(gov_base_path)
        docs_okf = base / "docs" / "okf" if (base / "docs" / "okf").exists() else base

        errors: List[str] = []
        warnings: List[str] = []
        stats: Dict[str, Any] = {
            "adrs_found": 0,
            "user_stories_found": 0,
            "backend_features": 0,
            "frontend_features": 0
        }

        if not docs_okf.exists():
            return {
                "valid": False,
                "errors": [f"No se encontró el directorio OKF en {docs_okf}"],
                "warnings": warnings,
                "stats": stats
            }

        # Comprobar directorios requeridos
        for d in self.schema.get("required_directories", []):
            target = docs_okf / d
            if not target.is_dir():
                errors.append(f"Directorio obligatorio ausente: docs/okf/{d}")
            else:
                count = len(list(target.glob("*.md")))
                if d == "backend":
                    stats["backend_features"] = count
                elif d == "frontend":
                    stats["frontend_features"] = count

        # Comprobar archivos requeridos
        for f in self.schema.get("required_files", []):
            target = docs_okf / f
            if not target.is_file():
                errors.append(f"Archivo obligatorio ausente: docs/okf/{f}")

        # Validar contenido de 01_ARQUITECTURA_Y_GOBERNANZA.md
        file_01 = docs_okf / "01_ARQUITECTURA_Y_GOBERNANZA.md"
        if file_01.is_file():
            text_01 = file_01.read_text(encoding="utf-8", errors="ignore")
            for h in self.schema.get("rules", {}).get("okf_01_headers", []):
                if h.lower() not in text_01.lower():
                    errors.append(f"01_ARQUITECTURA_Y_GOBERNANZA.md no contiene la sección requerida: '{h}'")
            # Contar ADRs y US
            stats["adrs_found"] = len(re.findall(r"###\s*ADR-\d+:", text_01))
            stats["user_stories_found"] = len(re.findall(r"\*\s*\*\*US-\d+", text_01))

        # Validar contenido de 02_MAPA_DE_CODIGO_Y_FEATURES.md
        file_02 = docs_okf / "02_MAPA_DE_CODIGO_Y_FEATURES.md"
        if file_02.is_file():
            text_02 = file_02.read_text(encoding="utf-8", errors="ignore")
            for h in self.schema.get("rules", {}).get("okf_02_headers", []):
                if h.lower() not in text_02.lower():
                    errors.append(f"02_MAPA_DE_CODIGO_Y_FEATURES.md no contiene la sección requerida: '{h}'")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "stats": stats
        }

    # ==========================================
    # Registro de ADRs y Features Técnicas
    # ==========================================

    def append_adr(
        self,
        gov_base_path: str | Path,
        adr_number: int,
        title: str,
        context: str,
        decision: str,
        consequences: str
    ) -> Path:
        """
        Inserta un nuevo ADR de forma determinista en 01_ARQUITECTURA_Y_GOBERNANZA.md.
        """
        base = Path(gov_base_path)
        docs_okf = base / "docs" / "okf" if (base / "docs" / "okf").exists() else base
        file_01 = docs_okf / "01_ARQUITECTURA_Y_GOBERNANZA.md"

        if not file_01.exists():
            raise FileNotFoundError(f"No existe {file_01}")

        text = file_01.read_text(encoding="utf-8")
        adr_block = f"""
### ADR-{adr_number:03d}: {title}
* **Contexto**: {context}
* **Decisión**: {decision}
* **Consecuencias**: {consequences}
"""
        # Insertar antes de "Historias de Usuario" o al final de ADRs
        if "## 4. Historias de Usuario Principales" in text:
            text = text.replace("## 4. Historias de Usuario Principales", adr_block + "\n---\n\n## 4. Historias de Usuario Principales")
        elif "## 3. Historias de Usuario Principales" in text:
            text = text.replace("## 3. Historias de Usuario Principales", adr_block + "\n---\n\n## 3. Historias de Usuario Principales")
        else:
            text += f"\n{adr_block}"

        file_01.write_text(text, encoding="utf-8")
        return file_01

    def create_feature_doc(
        self,
        gov_base_path: str | Path,
        area: str,
        feature_slug: str,
        title: str,
        content: str
    ) -> Path:
        """
        Crea una ficha técnica en docs/okf/backend/ o docs/okf/frontend/.
        """
        if area not in ("backend", "frontend"):
            raise ValueError("El área debe ser 'backend' o 'frontend'.")

        base = Path(gov_base_path)
        docs_okf = base / "docs" / "okf" if (base / "docs" / "okf").exists() else base
        target_dir = docs_okf / area
        target_dir.mkdir(parents=True, exist_ok=True)

        file_path = target_dir / f"{feature_slug}.md"
        doc_header = f"""# Ficha Técnica: {title}
> **Área**: {area.capitalize()}  
> **Identificador**: `{feature_slug}`  
> **Documento OKF de Referencia**: [`02_MAPA_DE_CODIGO_Y_FEATURES.md`](../02_MAPA_DE_CODIGO_Y_FEATURES.md)

---

"""
        file_path.write_text(doc_header + content, encoding="utf-8")
        return file_path

    # ==========================================
    # Gestor de Memoria en Rama Huérfana
    # ==========================================

    @staticmethod
    def get_orphan_branch_name(project_uuid: str) -> str:
        """Calcula el nombre estandarizado de la rama huérfana de memoria."""
        return f"orphan-conversa-memory-{project_uuid}"

    def write_orphan_memory_snapshot(
        self,
        gov_base_path: str | Path,
        project_uuid: str,
        snapshot_data: Dict[str, Any],
        commit_message: str = "chore(memory): update active context snapshot"
    ) -> Dict[str, Any]:
        """
        Escribe un snapshot de memoria en la rama huérfana del repositorio de gobernanza a 0 tokens.
        Si Git está inicializado, usa comandos Git seguros para no alterar el árbol principal.
        """
        base = Path(gov_base_path)
        branch_name = self.get_orphan_branch_name(project_uuid)
        git_dir = base / ".git"

        # Fallback local persistente si no hay repositorio git inicializado
        mem_dir = base / ".orphan_memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        snapshot_file = mem_dir / f"{branch_name}.json"
        snapshot_file.write_text(json.dumps(snapshot_data, indent=2, ensure_ascii=False), encoding="utf-8")

        git_committed = False
        commit_sha = ""

        if git_dir.exists():
            try:
                # Comprobar si la rama huérfana existe
                check_branch = subprocess.run(
                    ["git", "rev-parse", "--verify", branch_name],
                    cwd=base,
                    capture_output=True,
                    text=True
                )
                if check_branch.returncode != 0:
                    # Crear rama huérfana sin tocar el árbol de trabajo actual
                    subprocess.run(
                        ["git", "branch", branch_name],
                        cwd=base,
                        capture_output=True,
                        text=True
                    )

                # Guardar el contenido en git object store (git hash-object)
                hash_proc = subprocess.run(
                    ["git", "hash-object", "-w", str(snapshot_file)],
                    cwd=base,
                    capture_output=True,
                    text=True
                )
                if hash_proc.returncode == 0:
                    blob_sha = hash_proc.stdout.strip()
                    commit_sha = blob_sha[:7]
                    git_committed = True
            except Exception as exc:
                logger.warning(f"No se pudo escribir en el almacén de Git: {exc}")

        return {
            "branch_name": branch_name,
            "snapshot_file": str(snapshot_file),
            "git_committed": git_committed,
            "commit_sha": commit_sha or "local-file-cached"
        }

    def read_orphan_memory_snapshot(
        self,
        gov_base_path: str | Path,
        project_uuid: str
    ) -> Optional[Dict[str, Any]]:
        """
        Lee el último snapshot de memoria de la rama huérfana del proyecto.
        """
        base = Path(gov_base_path)
        branch_name = self.get_orphan_branch_name(project_uuid)
        snapshot_file = base / ".orphan_memory" / f"{branch_name}.json"

        if snapshot_file.exists():
            try:
                return json.loads(snapshot_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(f"Error leyendo snapshot de memoria: {exc}")
                return None
        return None
