"""
Adaptador OKF (Open Knowledge Framework) para MCP2P.

Gestiona la gobernanza desacoplada, validación de esquemas de conocimiento v2,
registro determinista de ADRs, Decisiones, Requisitos, Convenios,
sincronización bidireccional YAML <-> SQLite y worktrees por tarea.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mcp2p.storage.okf")

DEFAULT_OKF_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "OKF v2 Full Governance Specification",
    "version": "2.0.0",
    "required_files": [
        "01_ARQUITECTURA_Y_GOBERNANZA.md",
        "02_MAPA_DE_CODIGO_Y_FEATURES.md"
    ],
    "required_directories": [
        "backend",
        "frontend"
    ],
    "v2_directories": [
        "architecture/decisions",
        "architecture/adrs",
        "architecture/discarded",
        "design/heuristics",
        "design/design_system",
        "requirements/functional",
        "requirements/non_functional",
        "kanban/cards",
        "kanban/discarded",
        "conventions",
        "skills",
        "mcps"
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
    # Inicialización de Estructura OKF v2
    # ==========================================

    def initialize_project_okf(
        self,
        gov_base_path: str | Path,
        project_name: str,
        project_uuid: str
    ) -> Path:
        """
        Inicializa la jerarquía estándar de carpetas y archivos OKF v2 en el repositorio de gobernanza.
        """
        base = Path(gov_base_path)
        docs_okf = base / "docs" / "okf"

        # Directorios base y v2 completos
        all_dirs = [
            "backend",
            "frontend",
            "architecture/decisions",
            "architecture/adrs",
            "architecture/rulings",
            "architecture/discarded",
            "architecture/diagrams/src",
            "design/heuristics",
            "design/design_system",
            "requirements/functional",
            "requirements/non_functional",
            "requirements/contracts",
            "kanban/cards",
            "kanban/discarded",
            "kanban/archive",
            "conventions",
            "skills",
            "mcps",
            "history"
        ]
        for d in all_dirs:
            (docs_okf / d).mkdir(parents=True, exist_ok=True)

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
        else:
            text_01 = file_01.read_text(encoding="utf-8", errors="ignore")
            missing_headers = [h for h in self.schema.get("rules", {}).get("okf_01_headers", []) if h.lower() not in text_01.lower()]
            if missing_headers:
                extra = ""
                for h in missing_headers:
                    if "architecture decision" in h.lower():
                        extra += "\n\n## Architecture Decision Records (ADRs)\n\n### ADR-001: Estructura OKF\n* **Contexto**: Gobernanza viva.\n* **Decisión**: Separación app y governance.\n* **Consecuencias**: Cero contaminación de tokens.\n"
                    elif "historias de usuario" in h.lower():
                        extra += "\n\n## Historias de Usuario Principales\n* **US-01**: Colaboración determinista multi-agente.\n"
                    elif "# okf-01" in h.lower():
                        extra = f"# OKF-01: Arquitectura y Principios\n\n" + extra
                file_01.write_text(text_01 + extra, encoding="utf-8")

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

        # 4. Plantillas mínimas de convenios, skills y mcps si no existen
        conv_dir = docs_okf / "conventions"
        git_conv = conv_dir / "git_and_worktrees.md"
        if not git_conv.exists():
            git_conv.write_text("# Convenciones de Git y Worktrees\n- main: consensuada\n- wt/{card_id}: worktrees aislados\n", encoding="utf-8")

        trusted_nodes = conv_dir / "trusted_nodes.yaml"
        if not trusted_nodes.exists():
            trusted_nodes.write_text(f"version: '1.0'\nnodes:\n  - node_uuid: 'local_node'\n    role: 'owner'\n", encoding="utf-8")

        skills_dir = docs_okf / "skills"
        skills_reg = skills_dir / "registry.yaml"
        if not skills_reg.exists():
            skills_reg.write_text("version: '1.0'\nskills: []\n", encoding="utf-8")

        mcps_dir = docs_okf / "mcps"
        mcp_man = mcps_dir / "mcp_manifest.yaml"
        if not mcp_man.exists():
            mcp_man.write_text("version: '2.0'\nserver_name: 'mcp2p-server'\ntools: []\n", encoding="utf-8")

        return docs_okf

    def ensure_okf_structure(
        self,
        gov_base_path: str | Path,
        project_name: str = "Proyecto",
        project_uuid: str = ""
    ) -> Path:
        """
        Asegura que la estructura OKF exista en gov_base_path; si falta algún componente, lo inicializa o repara automáticamente.
        """
        return self.initialize_project_okf(gov_base_path, project_name, project_uuid)

    def add_trusted_node_pubkey(
        self,
        gov_base_path: str | Path,
        node_uuid: str,
        public_key: str,
        username: str = "",
        role: str = "collaborator"
    ) -> bool:
        """Registra o actualiza la clave pública y rol de un nodo de confianza en conventions/trusted_nodes.yaml."""
        docs_okf = Path(gov_base_path) / "docs" / "okf"
        trusted_nodes_path = docs_okf / "conventions" / "trusted_nodes.yaml"
        trusted_nodes_path.parent.mkdir(parents=True, exist_ok=True)

        data = {"version": "1.0", "nodes": []}
        if trusted_nodes_path.exists():
            try:
                loaded = yaml.safe_load(trusted_nodes_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and "nodes" in loaded:
                    data = loaded
            except Exception:
                pass

        # Buscar si ya existe el nodo
        found = False
        for node in data.get("nodes", []):
            if node.get("node_uuid") == node_uuid:
                node["public_key"] = public_key
                node["role"] = role
                if username:
                    node["username"] = username
                found = True
                break

        if not found:
            data.setdefault("nodes", []).append({
                "node_uuid": node_uuid,
                "role": role,
                "public_key": public_key,
                "username": username
            })

        trusted_nodes_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return True

    def get_trusted_nodes_map(self, gov_base_path: str | Path) -> Dict[str, Dict[str, Any]]:
        """Retorna un diccionario mapeando node_uuid a su información (public_key, role, username)."""
        docs_okf = Path(gov_base_path) / "docs" / "okf"
        trusted_nodes_path = docs_okf / "conventions" / "trusted_nodes.yaml"
        if not trusted_nodes_path.exists():
            return {}
        try:
            loaded = yaml.safe_load(trusted_nodes_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and "nodes" in loaded:
                return {n.get("node_uuid"): n for n in loaded["nodes"] if n.get("node_uuid")}
        except Exception:
            pass
        return {}

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
            # Contar ADRs y US en archivo 01 o en carpetas dedicadas
            stats["adrs_found"] = len(re.findall(r"###\s*ADR-\d+:", text_01))
            stats["user_stories_found"] = len(re.findall(r"\*\s*\*\*US-\d+", text_01))

        # Sumar ADRs de architecture/adrs si existen
        adrs_dir = docs_okf / "architecture" / "adrs"
        if adrs_dir.is_dir():
            stats["adrs_found"] += len(list(adrs_dir.glob("*.md")))

        # Sumar US de requirements/functional si existen
        us_dir = docs_okf / "requirements" / "functional"
        if us_dir.is_dir():
            stats["user_stories_found"] += len(list(us_dir.glob("*.md")))

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

    def get_okf_adrs(self, gov_base_path: str | Path) -> List[Dict[str, Any]]:
        """Extrae la lista de ADRs de 01_ARQUITECTURA_Y_GOBERNANZA.md y de architecture/adrs/."""
        base = Path(gov_base_path)
        docs_okf = base / "docs" / "okf" if (base / "docs" / "okf").exists() else base
        adrs = []

        # 1. Extraer del archivo 01
        file_01 = docs_okf / "01_ARQUITECTURA_Y_GOBERNANZA.md"
        if file_01.is_file():
            text = file_01.read_text(encoding="utf-8", errors="ignore")
            pattern = r"###\s*(ADR-\d+):\s*([^\n]+)\n\*\s*\*\*Contexto\*\*:\s*([^\n]+)\n\*\s*\*\*Decisión\*\*:\s*([^\n]+)\n\*\s*\*\*Consecuencias\*\*:\s*([^\n]+)"
            for match in re.finditer(pattern, text):
                adrs.append({
                    "id": match.group(1).strip(),
                    "title": match.group(2).strip(),
                    "context": match.group(3).strip(),
                    "decision": match.group(4).strip(),
                    "consequences": match.group(5).strip()
                })

        # 2. Extraer de architecture/adrs/
        adrs_dir = docs_okf / "architecture" / "adrs"
        if adrs_dir.is_dir():
            for f in sorted(adrs_dir.glob("*.md")):
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore")
                    id_m = re.search(r'id:\s*["\']?([^"\']+)["\']?', content)
                    title_m = re.search(r'title:\s*["\']?([^"\']+)["\']?', content)
                    adr_id = id_m.group(1) if id_m else f.stem
                    if not any(a["id"] == adr_id for a in adrs):
                        adrs.append({
                            "id": adr_id,
                            "title": title_m.group(1) if title_m else f.stem,
                            "context": "Ver ficha técnica",
                            "decision": "Aprobado",
                            "consequences": "Registrado en architecture/adrs",
                            "path": str(f)
                        })
                except Exception:
                    pass

        return adrs

    def get_okf_skills(self, gov_base_path: str | Path) -> List[Dict[str, Any]]:
        """Lista las fichas técnicas y habilidades documentadas en docs/okf/backend y docs/okf/frontend."""
        base = Path(gov_base_path)
        docs_okf = base / "docs" / "okf" if (base / "docs" / "okf").exists() else base
        skills = []
        for area in ("backend", "frontend"):
            area_dir = docs_okf / area
            if area_dir.is_dir():
                for f in sorted(area_dir.glob("*.md")):
                    content = f.read_text(encoding="utf-8", errors="ignore")
                    first_line = content.splitlines()[0] if content.splitlines() else f.stem
                    title = first_line.replace("#", "").replace("Ficha Técnica:", "").strip()
                    skills.append({
                        "area": area,
                        "slug": f.stem,
                        "title": title or f.stem,
                        "path": str(f)
                    })
        return skills

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
    # Micro-Scripts ("Manos del Agente"): Kanban YAML <-> SQLite
    # ==========================================

    def write_card_yaml(
        self,
        gov_base_path: str | Path,
        card_dict: Dict[str, Any]
    ) -> Path:
        """
        Escribe o actualiza el archivo YAML de una tarjeta Kanban en el repositorio de gobernanza.
        Si status == 'discarded', se traslada a kanban/discarded/{card_id}.yaml.
        En caso contrario, se ubica en kanban/cards/{card_id}.yaml.
        """
        import yaml
        base = Path(gov_base_path)
        docs_okf = base / "docs" / "okf" if (base / "docs" / "okf").exists() else base
        kanban_dir = docs_okf / "kanban"
        cards_dir = kanban_dir / "cards"
        discarded_dir = kanban_dir / "discarded"

        cards_dir.mkdir(parents=True, exist_ok=True)
        discarded_dir.mkdir(parents=True, exist_ok=True)

        card_id = card_dict.get("card_id") or card_dict.get("id")
        if not card_id:
            raise ValueError("La tarjeta debe contener un 'card_id' o 'id'.")

        status = card_dict.get("status", "backlog")
        rel_ids = card_dict.get("related_ids")
        if rel_ids is None and "related_ids_json" in card_dict:
            try:
                rel_ids = json.loads(card_dict["related_ids_json"]) if isinstance(card_dict["related_ids_json"], str) else card_dict["related_ids_json"]
            except Exception:
                rel_ids = []

        clean_card = {
            "id": card_id,
            "project_uuid": card_dict.get("project_uuid", ""),
            "title": card_dict.get("title", ""),
            "description": card_dict.get("description", ""),
            "acceptance_criteria": card_dict.get("acceptance_criteria", ""),
            "status": status,
            "assignee_node_uuid": card_dict.get("assignee_node_uuid"),
            "lease_expires_at": card_dict.get("lease_expires_at"),
            "branch_ref": card_dict.get("branch_ref"),
            "commit_refs": card_dict.get("commit_refs") or [],
            "related_ids": rel_ids or [],
            "created_at": card_dict.get("created_at") or datetime.now(timezone.utc).isoformat(),
            "updated_at": card_dict.get("updated_at") or datetime.now(timezone.utc).isoformat()
        }

        yaml_content = yaml.dump(clean_card, sort_keys=False, allow_unicode=True)

        active_file = cards_dir / f"{card_id}.yaml"
        discarded_file = discarded_dir / f"{card_id}.yaml"

        if status == "discarded":
            if active_file.exists():
                try:
                    active_file.unlink()
                except OSError:
                    pass
            discarded_file.write_text(yaml_content, encoding="utf-8")
            return discarded_file
        else:
            if discarded_file.exists():
                try:
                    discarded_file.unlink()
                except OSError:
                    pass
            active_file.write_text(yaml_content, encoding="utf-8")
            return active_file

    def reconcile_kanban_from_okf(
        self,
        gov_base_path: str | Path,
        project_uuid: str,
        db_manager: Optional[Any] = None
    ) -> Dict[str, int]:
        """
        Reconcilia todos los archivos YAML del Kanban en OKF hacia la base de datos SQLite.
        Actúa como hidratador determinista a 0 tokens.
        """
        if db_manager is None:
            from src.storage.db import DatabaseManager
            db = DatabaseManager.get_instance()
        else:
            db = db_manager

        base = Path(gov_base_path)
        docs_okf = base / "docs" / "okf" if (base / "docs" / "okf").exists() else base
        kanban_dir = docs_okf / "kanban"
        cards_dir = kanban_dir / "cards"
        discarded_dir = kanban_dir / "discarded"

        cards_loaded = 0
        discarded_loaded = 0

        # 1. Tarjetas activas (backlog, in_progress, in_review, etc.)
        if cards_dir.is_dir():
            for f in sorted(cards_dir.glob("*.yaml")) + sorted(cards_dir.glob("*.yml")):
                try:
                    data = yaml.safe_load(f.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        cid = data.get("id") or data.get("card_id") or f.stem
                        p_uuid = data.get("project_uuid") or project_uuid
                        status = data.get("status", "backlog")
                        if status == "discarded":
                            status = "backlog"
                        db_manager.upsert_card_from_okf(
                            project_uuid=p_uuid,
                            card_id=cid,
                            title=data.get("title", f.stem),
                            status=status,
                            description=data.get("description", ""),
                            acceptance_criteria=data.get("acceptance_criteria", ""),
                            assignee_node_uuid=data.get("assignee_node_uuid"),
                            lease_expires_at=data.get("lease_expires_at"),
                            branch_ref=data.get("branch_ref"),
                            related_ids_json=json.dumps(data.get("related_ids", [])),
                            updated_at=data.get("updated_at")
                        )
                        cards_loaded += 1
                except Exception as exc:
                    logger.warning(f"Error cargando tarjeta YAML {f}: {exc}")

        # 2. Tarjetas descartadas
        if discarded_dir.is_dir():
            for f in sorted(discarded_dir.glob("*.yaml")) + sorted(discarded_dir.glob("*.yml")):
                try:
                    data = yaml.safe_load(f.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        cid = data.get("id") or data.get("card_id") or f.stem
                        p_uuid = data.get("project_uuid") or project_uuid
                        db_manager.upsert_card_from_okf(
                            project_uuid=p_uuid,
                            card_id=cid,
                            title=data.get("title", f.stem),
                            status="discarded",
                            description=data.get("description", ""),
                            acceptance_criteria=data.get("acceptance_criteria", ""),
                            assignee_node_uuid=data.get("assignee_node_uuid"),
                            lease_expires_at=data.get("lease_expires_at"),
                            branch_ref=data.get("branch_ref"),
                            related_ids_json=json.dumps(data.get("related_ids", [])),
                            updated_at=data.get("updated_at")
                        )
                        discarded_loaded += 1
                except Exception as exc:
                    logger.warning(f"Error cargando tarjeta descartada YAML {f}: {exc}")

        return {
            "cards_loaded": cards_loaded,
            "discarded_loaded": discarded_loaded,
            "total_loaded": cards_loaded + discarded_loaded
        }

    # ==========================================
    # Micro-Scripts ("Manos"): Ciclo de Vida de Artefactos OKF
    # ==========================================

    def scaffold_okf_artifact(
        self,
        gov_base_path: str | Path,
        artifact_type: str,
        item_id: str,
        title: str,
        content: str,
        related_ids: Optional[List[str]] = None
    ) -> Path:
        """
        Crea un artefacto OKF con encabezado YAML y enlaces cruzados bidireccionales.
        artifact_type: 'decision', 'adr', 'discarded', 'functional', 'non_functional', 'design'.
        """
        base = Path(gov_base_path)
        docs_okf = base / "docs" / "okf" if (base / "docs" / "okf").exists() else base

        type_map = {
            "decision": docs_okf / "architecture" / "decisions",
            "decisions": docs_okf / "architecture" / "decisions",
            "adr": docs_okf / "architecture" / "adrs",
            "adrs": docs_okf / "architecture" / "adrs",
            "discarded": docs_okf / "architecture" / "discarded",
            "functional": docs_okf / "requirements" / "functional",
            "us": docs_okf / "requirements" / "functional",
            "non_functional": docs_okf / "requirements" / "non_functional",
            "rnf": docs_okf / "requirements" / "non_functional",
            "design": docs_okf / "design" / "heuristics",
            "heuristics": docs_okf / "design" / "heuristics"
        }
        target_dir = type_map.get(artifact_type.lower(), docs_okf / artifact_type)
        target_dir.mkdir(parents=True, exist_ok=True)

        slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in title.lower().replace(" ", "_")).strip("_")[:40]
        filename = f"{item_id}-{slug}.md" if slug else f"{item_id}.md"
        file_path = target_dir / filename

        status_map = {
            "decision": "in_discussion",
            "adr": "approved",
            "discarded": "discarded"
        }
        status = status_map.get(artifact_type.lower(), "approved")
        rel_ids = related_ids or []
        now_str = datetime.now(timezone.utc).isoformat()

        frontmatter = (
            f"---\n"
            f"id: {item_id}\n"
            f"title: \"{title}\"\n"
            f"status: {status}\n"
            f"related_ids: {json.dumps(rel_ids)}\n"
            f"created_at: \"{now_str}\"\n"
            f"---\n"
        )
        body = f"# {item_id}: {title}\n\n{content}\n"
        file_path.write_text(frontmatter + body, encoding="utf-8")

        # Enlace inverso en archivos referenciados
        for rid in rel_ids:
            self._add_bidirectional_link(docs_okf, rid, item_id)

        return file_path

    def _add_bidirectional_link(self, docs_okf: Path, target_id: str, new_link_id: str) -> None:
        """Añade new_link_id a la lista related_ids de cualquier archivo que contenga id: target_id."""
        for md in docs_okf.glob("**/*.md"):
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
                if f"id: {target_id}" in text or f"id: \"{target_id}\"" in text:
                    match = re.search(r"related_ids:\s*(\[[^\]]*\])", text)
                    if match:
                        current_list = json.loads(match.group(1).replace("'", '"'))
                        if new_link_id not in current_list:
                            current_list.append(new_link_id)
                            updated = text.replace(match.group(0), f"related_ids: {json.dumps(current_list)}")
                            md.write_text(updated, encoding="utf-8")
            except Exception:
                pass

    def move_decision_lifecycle(
        self,
        gov_base_path: str | Path,
        dec_id: str,
        target_state: str,
        reason_or_consequences: str,
        signature_block: Optional[Dict[str, Any]] = None
    ) -> Path:
        """
        Mueve una decisión técnica a 'approved' (ADR en architecture/adrs/) o a 'discarded' (architecture/discarded/).
        """
        base = Path(gov_base_path)
        docs_okf = base / "docs" / "okf" if (base / "docs" / "okf").exists() else base
        dec_dir = docs_okf / "architecture" / "decisions"

        # Buscar archivo de la decisión
        source_file = None
        if dec_dir.is_dir():
            for f in dec_dir.glob("*.md"):
                text = f.read_text(encoding="utf-8", errors="ignore")
                if f"id: {dec_id}" in text or f.name.startswith(f"{dec_id}-") or f.stem == dec_id:
                    source_file = f
                    break

        if not source_file:
            raise FileNotFoundError(f"No se encontró decisión con ID '{dec_id}' en {dec_dir}")

        content = source_file.read_text(encoding="utf-8")
        title_m = re.search(r'title:\s*["\']?([^"\']+)["\']?', content)
        title = title_m.group(1).strip() if title_m else dec_id

        rel_ids = []
        rel_m = re.search(r"related_ids:\s*(\[[^\]]*\])", content)
        if rel_m:
            try:
                rel_ids = json.loads(rel_m.group(1).replace("'", '"'))
            except Exception:
                pass

        slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in title.lower().replace(" ", "_")).strip("_")[:40]

        if target_state == "approved":
            target_dir = docs_okf / "architecture" / "adrs"
            target_dir.mkdir(parents=True, exist_ok=True)
            existing_adrs = len(list(target_dir.glob("*.md"))) + 1
            new_id = f"ADR-{existing_adrs:03d}"
            filename = f"{new_id}-{slug}.md" if slug else f"{new_id}.md"
            target_file = target_dir / filename

            sig_yaml = ""
            if signature_block:
                import yaml
                sig_yaml = f"\nsignatures:\n  - " + yaml.dump(signature_block, sort_keys=False).strip().replace("\n", "\n    ")

            now_str = datetime.now(timezone.utc).isoformat()
            new_frontmatter = (
                f"---\n"
                f"id: {new_id}\n"
                f"title: \"{title}\"\n"
                f"status: approved\n"
                f"related_ids: {json.dumps(rel_ids + [dec_id])}\n"
                f"promoted_from: {dec_id}\n"
                f"approved_at: \"{now_str}\"{sig_yaml}\n"
                f"---\n"
            )
            body = f"# {new_id}: {title}\n\n## Decisión Original ({dec_id})\n{content}\n\n## Consecuencias y Resolución\n{reason_or_consequences}\n"
            target_file.write_text(new_frontmatter + body, encoding="utf-8")
            source_file.unlink()
            return target_file

        elif target_state == "discarded":
            target_dir = docs_okf / "architecture" / "discarded"
            target_dir.mkdir(parents=True, exist_ok=True)
            existing_disc = len(list(target_dir.glob("*.md"))) + 1
            new_id = f"DIS-{existing_disc:03d}"
            filename = f"{new_id}-{slug}.md" if slug else f"{new_id}.md"
            target_file = target_dir / filename

            now_str = datetime.now(timezone.utc).isoformat()
            new_frontmatter = (
                f"---\n"
                f"id: {new_id}\n"
                f"title: \"{title}\"\n"
                f"status: discarded\n"
                f"related_ids: {json.dumps(rel_ids + [dec_id])}\n"
                f"discarded_at: \"{now_str}\"\n"
                f"---\n"
            )
            body = f"# {new_id}: {title}\n\n## Propuesta Evaluada ({dec_id})\n{content}\n\n## Razones del Descarte\n{reason_or_consequences}\n"
            target_file.write_text(new_frontmatter + body, encoding="utf-8")
            source_file.unlink()
            return target_file

        else:
            raise ValueError(f"target_state no reconocido: '{target_state}'. Use 'approved' o 'discarded'.")

    # ==========================================
    # Micro-Scripts ("Manos"): Git Worktrees Aislados
    # ==========================================

    def manage_task_worktree(
        self,
        code_repo_path: str | Path,
        gov_base_path: str | Path,
        project_uuid: str,
        card_id: str,
        node_uuid: str,
        action: str,
        files_scope: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Gestiona worktrees aislados por tarea en app/.worktrees/{card_id} coordinados mediante
        la rama huérfana de gobernanza para evitar colisiones entre agentes.
        action: 'create' o 'remove'.
        """
        code_dir = Path(code_repo_path)
        gov_dir = Path(gov_base_path)
        scope = files_scope or []

        # Localizar registro de worktrees en rama huérfana (worktree o .orphan_memory)
        orphan_wt = gov_dir / ".git" / "orphan_worktree"
        reg_dir = orphan_wt if orphan_wt.is_dir() else (gov_dir / ".orphan_memory")
        reg_dir.mkdir(parents=True, exist_ok=True)
        reg_file = reg_dir / "worktrees_registry.json"

        registry = {"active_worktrees": {}}
        if reg_file.exists():
            try:
                registry = json.loads(reg_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        if action == "create":
            # Comprobar colisiones de alcance con otros nodos
            for other_card, info in registry.get("active_worktrees", {}).items():
                if other_card != card_id and info.get("node_uuid") != node_uuid:
                    other_scope = info.get("files_scope", [])
                    intersection = [f for f in scope if any(f.startswith(o.replace("*", "")) or o.startswith(f.replace("*", "")) for o in other_scope)]
                    if intersection:
                        return {
                            "success": False,
                            "collision": True,
                            "colliding_card": other_card,
                            "colliding_node": info.get("node_uuid"),
                            "overlapping_scope": intersection,
                            "error": f"Conflicto de alcance: módulo bloqueado por {info.get('node_uuid')} en tarea {other_card}"
                        }

            wt_dir = code_dir / ".worktrees" / card_id
            branch_name = f"wt/{card_id}"

            if not (code_dir / ".git").is_dir():
                return {"success": False, "error": f"No es un repositorio Git: {code_dir}"}

            wt_dir.parent.mkdir(parents=True, exist_ok=True)
            res = subprocess.run(
                ["git", "worktree", "add", "-B", branch_name, str(wt_dir)],
                cwd=code_dir,
                capture_output=True,
                text=True
            )
            if res.returncode != 0:
                res = subprocess.run(
                    ["git", "worktree", "add", str(wt_dir), "-b", branch_name],
                    cwd=code_dir,
                    capture_output=True,
                    text=True
                )

            registry.setdefault("active_worktrees", {})[card_id] = {
                "node_uuid": node_uuid,
                "branch": branch_name,
                "files_scope": scope,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            reg_file.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")

            return {
                "success": True,
                "card_id": card_id,
                "worktree_path": str(wt_dir),
                "branch": branch_name,
                "files_scope": scope
            }

        elif action == "remove":
            wt_dir = code_dir / ".worktrees" / card_id
            branch_name = f"wt/{card_id}"

            if (code_dir / ".git").is_dir():
                subprocess.run(["git", "worktree", "remove", "--force", str(wt_dir)], cwd=code_dir, capture_output=True)
                subprocess.run(["git", "branch", "-D", branch_name], cwd=code_dir, capture_output=True)

            if card_id in registry.get("active_worktrees", {}):
                del registry["active_worktrees"][card_id]
                reg_file.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")

            return {
                "success": True,
                "removed_card_id": card_id
            }

        else:
            raise ValueError(f"Acción no reconocida: '{action}'. Use 'create' o 'remove'.")

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

        # Si existe .git/orphan_worktree, escribir y comitear en el worktree
        orphan_wt = git_dir / "orphan_worktree"
        if orphan_wt.is_dir():
            try:
                wt_file = orphan_wt / f"{branch_name}.json"
                wt_file.write_text(json.dumps(snapshot_data, indent=2, ensure_ascii=False), encoding="utf-8")
                subprocess.run(["git", "add", "."], cwd=orphan_wt, capture_output=True)
                p_c = subprocess.run(["git", "commit", "-m", commit_message], cwd=orphan_wt, capture_output=True, text=True)
                rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=orphan_wt, capture_output=True, text=True)
                if rev.returncode == 0:
                    commit_sha = rev.stdout.strip()
                    git_committed = True
            except Exception as e:
                logger.debug(f"Error escribiendo en orphan_worktree: {e}")

        elif git_dir.exists():
            try:
                check_branch = subprocess.run(
                    ["git", "rev-parse", "--verify", branch_name],
                    cwd=base,
                    capture_output=True,
                    text=True
                )
                if check_branch.returncode != 0:
                    subprocess.run(
                        ["git", "branch", branch_name],
                        cwd=base,
                        capture_output=True,
                        text=True
                    )

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

        # 1. Intentar desde orphan_worktree
        orphan_wt = base / ".git" / "orphan_worktree"
        wt_file = orphan_wt / f"{branch_name}.json"
        if wt_file.exists():
            try:
                return json.loads(wt_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        # 2. Fallback .orphan_memory
        snapshot_file = base / ".orphan_memory" / f"{branch_name}.json"
        if snapshot_file.exists():
            try:
                return json.loads(snapshot_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(f"Error leyendo snapshot de memoria: {exc}")
                return None
        return None

    def export_precedents_index(self, gov_base_path: str | Path) -> Path:
        """
        Compila todos los dictámenes del Jurado (architecture/rulings/*.yaml) y ADRs
        (architecture/adrs/*.md) en un índice estructurado docs/okf/architecture/precedents_index.yaml
        para consulta de precedentes a 0 tokens de inferencia.
        """
        base = Path(gov_base_path)
        docs_okf = base / "docs" / "okf" if (base / "docs" / "okf").exists() else base
        arch_dir = docs_okf / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)

        rulings_dir = arch_dir / "rulings"
        adrs_dir = arch_dir / "adrs"

        precedents: List[Dict[str, Any]] = []

        if rulings_dir.is_dir():
            for y_file in sorted(rulings_dir.glob("*.yaml")):
                try:
                    data = yaml.safe_load(y_file.read_text(encoding="utf-8")) or {}
                    precedents.append({
                        "id": data.get("ruling_id", y_file.stem),
                        "type": "JURY_RULING",
                        "title": data.get("title", y_file.stem),
                        "winning_option": data.get("verdict", {}).get("winning_option"),
                        "winning_ratio": data.get("verdict", {}).get("winning_ratio"),
                        "consensus_reached": data.get("verdict", {}).get("consensus_reached"),
                        "card_id": data.get("card_id"),
                        "file": f"architecture/rulings/{y_file.name}",
                        "timestamp": data.get("timestamp")
                    })
                except Exception as e:
                    logger.debug("Error leyendo ruling %s: %s", y_file, e)

        if adrs_dir.is_dir():
            for md_file in sorted(adrs_dir.glob("*.md")):
                try:
                    text = md_file.read_text(encoding="utf-8", errors="ignore")
                    title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
                    precedents.append({
                        "id": md_file.stem,
                        "type": "ADR",
                        "title": title_match.group(1).strip() if title_match else md_file.stem,
                        "file": f"architecture/adrs/{md_file.name}"
                    })
                except Exception as e:
                    logger.debug("Error leyendo ADR %s: %s", md_file, e)

        index_payload = {
            "schema_version": "okf.precedents.v2",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_precedents": len(precedents),
            "precedents": precedents
        }
        index_path = arch_dir / "precedents_index.yaml"
        index_path.write_text(
            yaml.safe_dump(index_payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8"
        )
        return index_path
