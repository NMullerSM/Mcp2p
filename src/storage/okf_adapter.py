"""
Adaptador OKF (Open Knowledge Framework) para MCP2P.

Gestiona la gobernanza desacoplada, validación de esquemas de conocimiento v2,
registro determinista de ADRs, Decisiones, Requisitos, Convenios,
sincronización bidireccional YAML <-> SQLite y worktrees por tarea.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.proc_utils import run_hidden
from src.storage.fs_utils import atomic_write_text
from src.storage.okf_freshness import OkfFreshnessEngine
from src.storage.okf_tags import OkfTagEngine
from src.storage.validators import InvalidIdentifierError, ensure_within, validate_safe_id

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
        self.schema = copy.deepcopy(schema) if schema is not None else copy.deepcopy(DEFAULT_OKF_SCHEMA)
        self.tag_engine = OkfTagEngine()
        self.freshness_engine = OkfFreshnessEngine()

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
            "frontend_features": 0,
            "verified_adrs": 0,
            "unverified_adrs": 0,
            "unverified_adr_paths": [],
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

        # Sumar ADRs de architecture/adrs si existen y verificar firmas Ed25519 (EGM-35)
        trusted_nodes = self.get_trusted_nodes_map(gov_base_path)
        adrs_dir = docs_okf / "architecture" / "adrs"
        if adrs_dir.is_dir():
            adr_files = sorted(adrs_dir.glob("*.md"))
            stats["adrs_found"] += len(adr_files)
            for adr_file in adr_files:
                rel_adr = f"architecture/adrs/{adr_file.name}"
                sig_valid, reasons = self.freshness_engine.verify_artifact_signatures(
                    adr_file, rel_adr, trusted_nodes
                )
                if sig_valid is True:
                    stats["verified_adrs"] += 1
                else:
                    stats["unverified_adrs"] += 1
                    stats["unverified_adr_paths"].append(rel_adr)
                    warnings.append(
                        f"ADR sin firma Ed25519 válida ({rel_adr}): {'; '.join(reasons)}"
                    )

        rulings_dir = docs_okf / "architecture" / "rulings"
        if rulings_dir.is_dir():
            for ruling_file in sorted(rulings_dir.glob("*.yaml")):
                rel_ruling = f"architecture/rulings/{ruling_file.name}"
                sig_valid, reasons = self.freshness_engine.verify_artifact_signatures(
                    ruling_file, rel_ruling, trusted_nodes
                )
                if sig_valid is True:
                    stats["verified_adrs"] += 1
                elif sig_valid is False:
                    stats["unverified_adrs"] += 1
                    stats["unverified_adr_paths"].append(rel_ruling)
                    warnings.append(
                        f"Dictamen sin firma Ed25519 válida ({rel_ruling}): {'; '.join(reasons)}"
                    )

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

    def push_gov_repo(self, gov_base_path: str | Path, remote: str = "origin") -> bool:
        """
        Publica los commits locales del repositorio de gobernanza hacia su remoto origin configurado.
        Si no hay remoto configurado o falla la conexión, retorna False sin interrumpir la operación
        (el commit local ya quedó guardado), pero el fallo queda registrado en warning con el motivo
        real — antes se perdía en un log de debug y no había ninguna forma de saber por qué un commit
        de gobernanza se quedaba sin publicar. El commit sin pushear sigue siendo visible y reintentable
        desde la UI (botón "Enviar Cambios" en Control de Cambios) porque incrementa unpushed_count.
        """
        base = Path(gov_base_path)
        if not (base / ".git").is_dir():
            return False
        try:
            from src.storage.github_sync import GitHubSyncManager
            sync = GitHubSyncManager()
            if not sync.has_remote(base, remote):
                logger.warning(
                    f"Auto-push de gobernanza omitido en {base}: no hay remoto '{remote}' configurado todavía."
                )
                return False
            # Detectar rama activa o fallback a main
            res = run_hidden(["git", "branch", "--show-current"], cwd=str(base), capture_output=True, text=True, check=False)
            branch = res.stdout.strip() if res.returncode == 0 else ""
            if not branch:
                branch = "main"
            push_res = sync.git_push_branch(base, branch=branch, remote=remote)
            success = bool(push_res.get("success"))
            if not success:
                logger.warning(
                    f"Auto-push de gobernanza falló en {base} (rama '{branch}'): "
                    f"{push_res.get('error', 'motivo desconocido')}"
                )
            return success
        except Exception as e:
            logger.warning(f"Auto-push de gobernanza falló con excepción en {base}: {e}")
            return False

    def commit_gov_file(
        self,
        gov_base_path: str | Path,
        file_path: str | Path | Any,
        commit_message: str,
        push_now: bool = False
    ) -> bool:
        """
        Comitea atómicamente uno o varios archivos del repositorio de gobernanza (rama main),
        evitando disputas huérfanas y cards/rulings sin historial de versionado real.
        El mensaje de commit debe incluir siempre el título y el contenido/razón de la
        tarjeta o ticket que originó el cambio, para que `git log` sea el historial legible
        de decisiones del proyecto.
        Si push_now es True, empuja automáticamente los commits locales hacia origin.
        """
        base = Path(gov_base_path)
        if not (base / ".git").is_dir():
            return False

        files = [file_path] if isinstance(file_path, (str, Path)) else list(file_path)
        rel_paths = []
        for f in files:
            if not f:
                continue
            try:
                p = Path(f)
                rel = p.relative_to(base) if p.is_absolute() else p
                rel_paths.append(str(rel))
            except ValueError:
                rel_paths.append(str(f))

        if not rel_paths:
            return False

        try:
            for r in rel_paths:
                run_hidden(["git", "add", r], cwd=str(base), capture_output=True, check=False)
            result = run_hidden(
                ["git", "commit", "-m", commit_message],
                cwd=str(base),
                capture_output=True,
                check=False
            )
            success = result.returncode == 0
            if success and push_now:
                self.push_gov_repo(base)
            return success
        except Exception as e:
            logger.debug(f"Git commit de gobernanza omitido o fallido para {rel_paths}: {e}")
            return False

    def write_card_yaml(
        self,
        gov_base_path: str | Path,
        card_dict: Dict[str, Any],
        tags: Optional[Iterable[str] | str] = None,
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

        clean_card: Dict[str, Any] = {
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
        raw_tags = tags if tags is not None else card_dict.get("tags")
        if raw_tags is not None:
            clean_card["tags"] = self.tag_engine.normalize_tags(raw_tags)

        yaml_content = yaml.dump(clean_card, sort_keys=False, allow_unicode=True)

        active_file = cards_dir / f"{card_id}.yaml"
        discarded_file = discarded_dir / f"{card_id}.yaml"
        ensure_within(cards_dir, active_file)
        ensure_within(discarded_dir, discarded_file)

        if status in ("discarded", "rejected"):
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
                        cid = str(data.get("id") or data.get("card_id") or f.stem)
                        p_uuid = data.get("project_uuid") or project_uuid
                        status = data.get("status", "backlog")
                        if status in ("discarded", "rejected"):
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
                        cid = str(data.get("id") or data.get("card_id") or f.stem)
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
        related_ids: Optional[List[str]] = None,
        tags: Optional[Iterable[str] | str] = None,
    ) -> Path:
        """
        Crea un artefacto OKF con encabezado YAML y enlaces cruzados bidireccionales.
        artifact_type: 'decision', 'adr', 'discarded', 'functional', 'non_functional', 'design'.
        """
        validate_safe_id(item_id, "item_id")
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
        ensure_within(docs_okf, target_dir)
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
        norm_tags = self.tag_engine.normalize_tags(tags) if tags is not None else []
        tags_line = f"tags: {json.dumps(norm_tags)}\n" if tags is not None else ""

        frontmatter = (
            f"---\n"
            f"id: {item_id}\n"
            f"title: \"{title}\"\n"
            f"status: {status}\n"
            f"related_ids: {json.dumps(rel_ids)}\n"
            f"{tags_line}"
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
        validate_safe_id(card_id, "card_id")
        code_dir = Path(code_repo_path)
        gov_dir = Path(gov_base_path)

        def _clean_scope(raw: Any) -> List[str]:
            curr = raw
            while isinstance(curr, str):
                curr = curr.strip()
                if (curr.startswith("[") and curr.endswith("]")) or (curr.startswith('"') and curr.endswith('"')):
                    try:
                        curr = json.loads(curr)
                    except Exception:
                        break
                else:
                    break
            if isinstance(curr, list):
                return [str(x) for x in curr if x]
            elif isinstance(curr, str) and curr:
                return [curr]
            return []

        scope = _clean_scope(files_scope)

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
                    other_scope = _clean_scope(info.get("files_scope", []))
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
            res = run_hidden(
                ["git", "worktree", "add", "-B", branch_name, str(wt_dir)],
                cwd=code_dir,
                capture_output=True,
                text=True
            )
            if res.returncode != 0:
                res = run_hidden(
                    ["git", "worktree", "add", str(wt_dir), "-b", branch_name],
                    cwd=code_dir,
                    capture_output=True,
                    text=True
                )
            if res.returncode != 0:
                return {"success": False, "error": f"git worktree add falló: {(res.stderr or '').strip()[:300]}"}

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
                run_hidden(["git", "worktree", "remove", "--force", str(wt_dir)], cwd=code_dir, capture_output=True)
                run_hidden(["git", "branch", "-D", branch_name], cwd=code_dir, capture_output=True)

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
                run_hidden(["git", "add", "."], cwd=orphan_wt, capture_output=True)
                p_c = run_hidden(["git", "commit", "-m", commit_message], cwd=orphan_wt, capture_output=True, text=True)
                rev = run_hidden(["git", "rev-parse", "--short", "HEAD"], cwd=orphan_wt, capture_output=True, text=True)
                if rev.returncode == 0:
                    commit_sha = rev.stdout.strip()
                    git_committed = True
            except Exception as e:
                logger.debug(f"Error escribiendo en orphan_worktree: {e}")

        elif git_dir.exists():
            try:
                check_branch = run_hidden(
                    ["git", "rev-parse", "--verify", branch_name],
                    cwd=base,
                    capture_output=True,
                    text=True
                )
                if check_branch.returncode != 0:
                    run_hidden(
                        ["git", "branch", branch_name],
                        cwd=base,
                        capture_output=True,
                        text=True
                    )

                hash_proc = run_hidden(
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
                    raw_bytes = y_file.read_bytes()
                    file_sha = hashlib.sha256(raw_bytes).hexdigest()
                    data = yaml.safe_load(raw_bytes.decode("utf-8")) or {}

                    verdict_data = data.get("verdict")
                    if not isinstance(verdict_data, dict):
                        v3 = data.get("phases", {}).get("phase_3_voting", {}) if isinstance(data.get("phases"), dict) else {}
                        verdict_data = {
                            "winning_option": data.get("winning_option") or v3.get("winning_option"),
                            "winning_ratio": v3.get("consensus_ratio"),
                            "consensus_reached": (data.get("status") == "ratified") or bool(v3.get("consensus_reached")),
                            "resolution_mode": data.get("resolution_mode"),
                            "override_applied": False
                        }

                    # EGM-10: Indexar únicamente los dictámenes ratificados
                    is_ratified = (data.get("status") == "ratified") or bool(verdict_data.get("consensus_reached"))
                    if not is_ratified:
                        continue

                    ts = data.get("timestamp") or data.get("updated_at") or ""

                    precedents.append({
                        "id": data.get("ruling_id", y_file.stem),
                        "ticket_id": data.get("ticket_id", ""),
                        "verdict": data.get("status") or (data.get("verdict") if isinstance(data.get("verdict"), str) else "ratified"),
                        "type": "JURY_RULING",
                        "title": data.get("title", y_file.stem),
                        "winning_option": verdict_data.get("winning_option"),
                        "winning_ratio": verdict_data.get("winning_ratio"),
                        "consensus_reached": verdict_data.get("consensus_reached"),
                        "card_id": data.get("card_id"),
                        "file": f"architecture/rulings/{y_file.name}",
                        "timestamp": ts,
                        "sha256": file_sha
                    })
                except Exception as e:
                    logger.debug("Error leyendo ruling %s: %s", y_file, e)

        if adrs_dir.is_dir():
            for md_file in sorted(adrs_dir.glob("*.md")):
                try:
                    raw_bytes = md_file.read_bytes()
                    file_sha = hashlib.sha256(raw_bytes).hexdigest()
                    text = raw_bytes.decode("utf-8", errors="ignore")
                    title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
                    mtime_iso = datetime.fromtimestamp(md_file.stat().st_mtime, tz=timezone.utc).isoformat()
                    precedents.append({
                        "id": md_file.stem,
                        "type": "ADR",
                        "title": title_match.group(1).strip() if title_match else md_file.stem,
                        "file": f"architecture/adrs/{md_file.name}",
                        "timestamp": mtime_iso,
                        "sha256": file_sha
                    })
                except Exception as e:
                    logger.debug("Error leyendo ADR %s: %s", md_file, e)

        # EGM-10: Determinismo en generated_at usando max de timestamps indexados
        all_timestamps = [p.get("timestamp") for p in precedents if p.get("timestamp")]
        generated_at = max(all_timestamps) if all_timestamps else "1970-01-01T00:00:00+00:00"

        index_payload = {
            "schema_version": "okf.precedents.v2",
            "generated_at": generated_at,
            "total_precedents": len(precedents),
            "precedents": precedents
        }
        index_path = arch_dir / "precedents_index.yaml"
        from src.storage.fs_utils import atomic_write_text
        atomic_write_text(
            index_path,
            yaml.safe_dump(index_payload, sort_keys=False, allow_unicode=True)
        )
        return index_path

    # =========================================================================
    # Ingesta Cognitiva (Bootstrap) y Auditoría de Deriva (Drift)
    # =========================================================================

    def materialize_bootstrap_dossier(
        self,
        gov_base_path: str | Path,
        analysis_result: Dict[str, Any],
        signature_block: Optional[Dict[str, Any]] = None,
        project_uuid: str = ""
    ) -> Dict[str, Any]:
        """
        Materializa de forma persistente y determinista todo el contenido del
        Dossier de Alineación Inicial OKF v2 en el repositorio de gobernanza:
        1. N ADRs en architecture/adrs/ sin límites artificiales.
        2. Índice de precedentes architecture/precedents_index.yaml.
        3. Requisitos funcionales en requirements/functional/ y no funcionales en requirements/non_functional/.
        4. Convenciones en conventions/DEVELOPMENT_CONVENTIONS.md.
        5. Actualización de 01_ARQUITECTURA_Y_GOBERNANZA.md y 02_MAPA_DE_CODIGO_Y_FEATURES.md.
        6. Registro histórico en history/ALIGNMENT_DOSSIER_RATIFIED.md.
        7. Commit git limpio en la gobernanza.
        """
        base = Path(gov_base_path).resolve()
        docs_okf = base / "docs" / "okf" if (base / "docs" / "okf").exists() else base

        # Asegurar subcarpetas OKF requeridas
        adrs_dir = docs_okf / "architecture" / "adrs"
        rf_dir = docs_okf / "requirements" / "functional"
        rnf_dir = docs_okf / "requirements" / "non_functional"
        conv_dir = docs_okf / "conventions"
        hist_dir = docs_okf / "history"
        for d in (adrs_dir, rf_dir, rnf_dir, conv_dir, hist_dir):
            d.mkdir(parents=True, exist_ok=True)

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        date_short = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 1. Materializar ADRs individuales
        created_adrs: List[str] = []
        raw_adrs = analysis_result.get("adrs", [])
        for adr in raw_adrs:
            a_id = getattr(adr, "adr_id", None) or adr.get("adr_id", "ADR-001")
            a_title = getattr(adr, "title", None) or adr.get("title", "Decisión Arquitectónica")
            a_ctx = getattr(adr, "context", None) or adr.get("context", "")
            a_dec = getattr(adr, "decision", None) or adr.get("decision", "")
            a_cons = getattr(adr, "consequences", None) or adr.get("consequences", "")
            a_source = getattr(adr, "source_ref", None) or adr.get("source_ref", "Análisis de código")

            slug = re.sub(r"[^a-zA-Z0-9]+", "_", a_title.lower()).strip("_")[:50]
            filename = f"{a_id}_{slug}.md"
            file_path = adrs_dir / filename

            content = f"""# {a_id}: {a_title}

> **Estado**: Aceptado / Ratificado mediante HITL
> **Fecha de Ratificación**: {date_short}
> **Fuente Original**: `{a_source}`

---

## Contexto
{a_ctx}

## Decisión
{a_dec}

## Consecuencias
{a_cons}
"""
            if signature_block:
                content += f"""
---

### Firma Criptográfica HITL (Supremacía Humana)
* **Firmante**: `{signature_block.get('developer_name', 'Desarrollador')}` (`{signature_block.get('node_uuid', 'local')}`)
* **Algoritmo**: `Ed25519`
* **Firma**: `{signature_block.get('signature_ed25519', '')}`
* **Fecha de Firma**: `{signature_block.get('signed_at', now_str)}`
"""
            file_path.write_text(content, encoding="utf-8")
            created_adrs.append(filename)

        # 2. Actualizar índice de precedentes
        self.export_precedents_index(base)

        # 3. Materializar Requisitos Funcionales y No Funcionales
        raw_reqs = analysis_result.get("requirements", [])
        rf_list: List[Dict[str, Any]] = []
        rnf_list: List[Dict[str, Any]] = []

        for req in raw_reqs:
            r_id = getattr(req, "req_id", None) or req.get("req_id", "")
            r_cat = getattr(req, "category", None) or req.get("category", "functional")
            r_title = getattr(req, "title", None) or req.get("title", "")
            r_desc = getattr(req, "description", None) or req.get("description", "")
            r_src = getattr(req, "source_ref", None) or req.get("source_ref", "")
            r_prio = getattr(req, "priority", None) or req.get("priority", "media")

            item_data = {
                "id": r_id,
                "title": r_title,
                "description": r_desc,
                "source": r_src,
                "priority": r_prio
            }
            if r_cat == "non_functional" or r_id.startswith("RNF-"):
                rnf_list.append(item_data)
                slug = re.sub(r"[^a-zA-Z0-9]+", "_", r_title.lower()).strip("_")[:40]
                rf_file = rnf_dir / f"{r_id}_{slug}.md"
                rf_file.write_text(
                    f"# {r_id}: {r_title}\n\n* **Categoría**: Requisito No Funcional\n* **Prioridad**: {r_prio.capitalize()}\n* **Fuente**: `{r_src}`\n\n## Descripción\n{r_desc}\n",
                    encoding="utf-8"
                )
            else:
                rf_list.append(item_data)
                slug = re.sub(r"[^a-zA-Z0-9]+", "_", r_title.lower()).strip("_")[:40]
                rf_file = rf_dir / f"{r_id}_{slug}.md"
                rf_file.write_text(
                    f"# {r_id}: {r_title}\n\n* **Categoría**: Requisito Funcional\n* **Prioridad**: {r_prio.capitalize()}\n* **Fuente**: `{r_src}`\n\n## Descripción y Criterios de Aceptación\n{r_desc}\n",
                    encoding="utf-8"
                )

        # Índices de Requisitos
        rf_index = rf_dir / "RF_INDEX.md"
        rf_table = "# Requisitos Funcionales (RF) Ratificados\n\n| ID | Requisito / Caso de Uso | Prioridad | Fuente |\n| :--- | :--- | :--- | :--- |\n"
        for rf in rf_list:
            rf_table += f"| `{rf['id']}` | {rf['description']} | {rf['priority'].capitalize()} | `{rf['source']}` |\n"
        rf_index.write_text(rf_table, encoding="utf-8")

        rnf_index = rnf_dir / "RNF_INDEX.md"
        rnf_table = "# Requisitos No Funcionales (RNF) Ratificados\n\n| ID | Criterio de Calidad | Prioridad | Fuente |\n| :--- | :--- | :--- | :--- |\n"
        for rnf in rnf_list:
            rnf_table += f"| `{rnf['id']}` | {rnf['description']} | {rnf['priority'].capitalize()} | `{rnf['source']}` |\n"
        rnf_index.write_text(rnf_table, encoding="utf-8")

        # 4. Materializar Convenciones de Desarrollo
        conv_file = conv_dir / "DEVELOPMENT_CONVENTIONS.md"
        conv_data = analysis_result.get("conventions", {})
        conv_text = f"""# Convenciones de Desarrollo del Proyecto
> Ratificado en ingesta OKF v2 el {now_str}.

## 1. Estrategia Git y Ramas
* **Flujo**: {conv_data.get('git_branching', 'Trunk-based con ramas de feature')}
* **Aislamiento**: {conv_data.get('worktrees', 'Git Worktrees')}

## 2. Guía de Estilo y Linters
* **Estilo**: {conv_data.get('style_guide', 'Estándar')}
* **Linters activos**: {', '.join(conv_data.get('linters', [])) or 'Por configurar'}
"""
        conv_file.write_text(conv_text, encoding="utf-8")

        # 5. Actualizar 01_ARQUITECTURA_Y_GOBERNANZA.md
        file_01 = docs_okf / "01_ARQUITECTURA_Y_GOBERNANZA.md"
        stack = analysis_result.get("stack")
        langs = getattr(stack, "languages", []) if stack else []
        fws = getattr(stack, "frameworks", []) if stack else []
        builds = getattr(stack, "build_tools", []) if stack else []
        tests = getattr(stack, "testing_tools", []) if stack else []
        env = getattr(stack, "runtime_environment", "Multiplataforma") if stack else "Multiplataforma"

        adrs_summary_md = ""
        for adr in raw_adrs:
            a_id = getattr(adr, "adr_id", None) or adr.get("adr_id", "")
            a_title = getattr(adr, "title", None) or adr.get("title", "")
            a_dec = getattr(adr, "decision", None) or adr.get("decision", "")
            adrs_summary_md += f"* **[{a_id}] {a_title}**: {a_dec[:180]}...\n"

        content_01 = f"""# OKF-01: Arquitectura, Gobernanza y Principios del Enjambre
## Gobernanza Viva del Proyecto (UUID: `{project_uuid}`)

> **Identificador**: `OKF-01`
> **Estado**: Aprobado / Ratificado mediante Ingesta Cognitiva Bootstrap
> **Metodología**: Organic Driven Development (ODD)
> **Última Actualización**: {now_str}

---

## 1. Stack Tecnológico Base
* **Lenguajes**: {', '.join(langs) or 'Políglota'}
* **Frameworks**: {', '.join(fws) or 'Nativos'}
* **Herramientas de Construcción**: {', '.join(builds) or 'Nativas'}
* **Testing**: {', '.join(tests) or 'Configurado'}
* **Entorno de Ejecución**: {env}

---

## 2. Architecture Decision Records (ADRs) ({len(created_adrs)} Ratificados)
{adrs_summary_md or '* Ver directorio architecture/adrs/ para detalle completo.'}

---

## 3. Historias de Usuario y Requisitos
* Se registran {len(rf_list)} Requisitos Funcionales y {len(rnf_list)} Requisitos No Funcionales en `requirements/`.
"""
        file_01.write_text(content_01, encoding="utf-8")

        # 6. Actualizar 02_MAPA_DE_CODIGO_Y_FEATURES.md
        file_02 = docs_okf / "02_MAPA_DE_CODIGO_Y_FEATURES.md"
        features = analysis_result.get("features_map", [])
        mermaid_nodes = "\n".join([f"    App --> Mod_{re.sub(r'[^a-zA-Z0-9]', '', f['feature'])}[\"{f['feature']} ({f['directory']})\"]" for f in features])
        feat_table = "\n".join([f"| **{f['feature']}** | `{f['directory']}` | {f['purpose']} |" for f in features])

        content_02 = f"""# OKF-02: Mapa de Código, Relación entre Componentes y Features
## Estructura Física y Módulos (UUID: `{project_uuid}`)

> **Identificador**: `OKF-02`
> **Propósito**: Navegación rápida para agentes y desarrolladores sin consumo innecesario de tokens.
> **Última Actualización**: {now_str}

---

## 1. Grafo de Componentes
```mermaid
graph TD
    App["Código Fuente (app/)"]
{mermaid_nodes or '    App --> Core["Módulos de la Aplicación"]'}
```

---

## 2. Mapa Detallado de Features
| Feature / Módulo | Directorio Físico en `app/` | Propósito Funcional |
| :--- | :--- | :--- |
{feat_table or '| Core | src/ | Módulo principal |'}
"""
        file_02.write_text(content_02, encoding="utf-8")

        # 7. Registrar histórico del Dossier Ratificado
        dossier_ratified_file = hist_dir / "ALIGNMENT_DOSSIER_RATIFIED.md"
        dossier_md = analysis_result.get("dossier_markdown", "")
        if signature_block:
            dossier_md += f"""

---

## 8. Certificado de Ratificación Criptográfica Ed25519
* **Firmante**: `{signature_block.get('developer_name')}` (`{signature_block.get('node_uuid')}`)
* **Clave Pública**: `{signature_block.get('public_key', '')}`
* **Firma Criptográfica**: `{signature_block.get('signature_ed25519')}`
* **Fecha y Hora**: `{signature_block.get('signed_at', now_str)}`
"""
        dossier_ratified_file.write_text(dossier_md, encoding="utf-8")

        # 8. Git commit en el repositorio de gobernanza
        if (base / ".git").is_dir():
            creationflags = 0
            try:
                run_hidden(["git", "add", "."], cwd=str(base), capture_output=True, check=False)
                run_hidden(
                    ["git", "commit", "-m", f"feat(okf): bootstrap and ratify initial OKF v2 governance dossier for {project_uuid}"],
                    cwd=str(base),
                    capture_output=True,
                    check=False
                )
            except Exception as ge:
                logger.debug(f"Git commit tras materializar OKF omitido o fallido: {ge}")

        return {
            "success": True,
            "project_uuid": project_uuid,
            "adrs_created_count": len(created_adrs),
            "rf_count": len(rf_list),
            "rnf_count": len(rnf_list),
            "conventions_path": str(conv_file.relative_to(base)),
            "precedents_index": str((docs_okf / "architecture" / "precedents_index.yaml").relative_to(base)),
            "ratified_at": now_str
        }

    def audit_okf_drift(
        self,
        code_repo_path: str | Path,
        gov_base_path: str | Path
    ) -> Dict[str, Any]:
        """
        Modo 2: Auditoría Continua de Deriva (Drift) entre el código real y el OKF ratificado.
        Compara directorios físicos de app/ con 02_MAPA_DE_CODIGO_Y_FEATURES.md y
        dependencias con 01_ARQUITECTURA_Y_GOBERNANZA.md, reportando componentes no mapeados.
        """
        code_path = Path(code_repo_path).resolve()
        gov_path = Path(gov_base_path).resolve()
        docs_okf = gov_path / "docs" / "okf" if (gov_path / "docs" / "okf").exists() else gov_path

        excluded = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".idea", ".vscode", "target"}

        # 1. Leer directorios físicos reales en app/
        physical_dirs: List[str] = []
        if code_path.exists():
            for item in sorted(code_path.iterdir()):
                if item.is_dir() and item.name not in excluded and not item.name.startswith("."):
                    physical_dirs.append(item.name)

        # 2. Leer mapa de features en OKF-02
        file_02 = docs_okf / "02_MAPA_DE_CODIGO_Y_FEATURES.md"
        doc_02_text = file_02.read_text(encoding="utf-8", errors="ignore") if file_02.exists() else ""

        unmapped_components: List[str] = []
        for d in physical_dirs:
            if f"`{d}`" not in doc_02_text and f"({d})" not in doc_02_text and f"/{d}/" not in doc_02_text:
                unmapped_components.append(d)

        # 3. Leer dependencias de código vs OKF-01
        file_01 = docs_okf / "01_ARQUITECTURA_Y_GOBERNANZA.md"
        doc_01_text = file_01.read_text(encoding="utf-8", errors="ignore") if file_01.exists() else ""

        stack_discrepancies: List[str] = []
        pkg_json = code_path / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
                deps = list(data.get("dependencies", {}).keys())
                for dep in deps[:10]:
                    if dep.lower() not in doc_01_text.lower():
                        stack_discrepancies.append(f"Dependencia npm '{dep}' no documentada en OKF-01")
            except Exception:
                pass

        pyproject = code_path / "pyproject.toml"
        if pyproject.exists():
            try:
                txt = pyproject.read_text(encoding="utf-8", errors="ignore")
                matches = re.findall(r'([a-zA-Z0-9_\-]+)\s*=', txt)
                for m in matches[:10]:
                    if m.lower() not in ("name", "version", "description", "authors") and m.lower() not in doc_01_text.lower():
                        # filtrar nombres comunes
                        pass
            except Exception:
                pass

        # 4. Total de ADRs registradas
        adrs_dir = docs_okf / "architecture" / "adrs"
        adrs_count = len(list(adrs_dir.glob("*.md"))) if adrs_dir.exists() else 0

        is_aligned = len(unmapped_components) == 0 and len(stack_discrepancies) == 0

        # Compilar informe Markdown
        now_iso = datetime.now(timezone.utc).isoformat()
        status_badge = "✅ ALINEADO CON EL OKF" if is_aligned else "⚠️ SE DETECTÓ DERIVA (DRIFT)"
        summary_md = f"""# 🔍 Informe de Auditoría de Deriva OKF (Drift Audit)
> **Fecha de Inspección**: {now_iso}  
> **Estado**: **{status_badge}**  
> **Total ADRs Ratificadas**: {adrs_count}

---

## 1. Componentes Físicos en `app/` vs `OKF-02`
* **Directorios detectados**: {', '.join(physical_dirs) or 'Ninguno'}
* **Componentes no documentados en el mapa**: {', '.join(unmapped_components) if unmapped_components else 'Ninguno (todos mapeados)'}

## 2. Discrepancias Tecnológicas vs `OKF-01`
{chr(10).join(['* ' + s for s in stack_discrepancies]) if stack_discrepancies else '* Ninguna discrepancia de librerías detectada.'}

## 3. Recomendaciones de Gobernanza
"""
        if is_aligned:
            summary_md += "* El repositorio se mantiene en perfecta sintonía con la gobernanza OKF. No se requieren acciones correctivas.\n"
        else:
            if unmapped_components:
                summary_md += f"* Registrar los nuevos módulos `{', '.join(unmapped_components)}` en `docs/okf/02_MAPA_DE_CODIGO_Y_FEATURES.md`.\n"
            if stack_discrepancies:
                summary_md += "* Formalizar una decisión arquitectónica (ADR) para las nuevas dependencias identificadas.\n"

        return {
            "is_aligned": is_aligned,
            "status": "aligned" if is_aligned else "drift_detected",
            "unmapped_components": unmapped_components,
            "stack_discrepancies": stack_discrepancies,
            "total_adrs_registered": adrs_count,
            "audit_timestamp": now_iso,
            "summary_markdown": summary_md
        }

    # =========================================================================
    # Gestión Dinámica de Carpetas OKF (okf_schema.json) y Tags YAML
    # =========================================================================

    @staticmethod
    def _resolve_docs_okf(gov_base_path: str | Path) -> Path:
        base = Path(gov_base_path)
        if (base / "docs" / "okf").exists():
            return (base / "docs" / "okf").resolve()
        if (base / "okf_schema.json").exists() or (base / "01_ARQUITECTURA_Y_GOBERNANZA.md").exists():
            return base.resolve()
        return (base / "docs" / "okf").resolve()

    @staticmethod
    def _validate_folder_path(docs_okf: Path, folder_path: str) -> Tuple[Path, str]:
        if not isinstance(folder_path, str):
            raise InvalidIdentifierError("folder_path debe ser una cadena de texto.")
        raw = folder_path.strip()
        if not raw or "\x00" in raw or "\\" in raw or ":" in raw or raw.startswith("/"):
            raise InvalidIdentifierError(f"Ruta de carpeta OKF inválida: {folder_path!r}")
        raw_clean = raw.strip("/")
        segments = [seg for seg in raw_clean.split("/") if seg != ""]
        if not segments or any(seg in (".", "..") for seg in segments):
            raise InvalidIdentifierError(f"Ruta de carpeta OKF inválida: {folder_path!r}")
        validated_segments = [validate_safe_id(seg, "folder_path") for seg in segments]
        clean_rel = "/".join(validated_segments)
        target = docs_okf / clean_rel
        resolved = ensure_within(docs_okf, target)
        if resolved == docs_okf.resolve():
            raise InvalidIdentifierError("folder_path no puede apuntar a la raíz docs/okf.")
        return resolved, clean_rel

    def _load_okf_schema_data(self, docs_okf: Path) -> Dict[str, Any]:
        schema_file = docs_okf / "okf_schema.json"
        data = copy.deepcopy(self.schema)
        if schema_file.is_file():
            try:
                loaded = json.loads(schema_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data.update(loaded)
            except Exception as exc:
                logger.warning(f"No se pudo parsear {schema_file}, usando esquema base: {exc}")
        data.setdefault("required_directories", list(DEFAULT_OKF_SCHEMA["required_directories"]))
        data.setdefault("v2_directories", list(DEFAULT_OKF_SCHEMA["v2_directories"]))
        data.setdefault("custom_folders", [])
        data.setdefault("active_folders", [])
        return data

    def _save_okf_schema_data(self, docs_okf: Path, schema_data: Dict[str, Any]) -> Path:
        schema_file = docs_okf / "okf_schema.json"
        atomic_write_text(schema_file, json.dumps(schema_data, indent=2, ensure_ascii=False))
        return schema_file

    def list_okf_folders(self, gov_base_path: str | Path) -> List[Dict[str, Any]]:
        """Lista las carpetas activas, requeridas y personalizadas definidas en okf_schema.json."""
        docs_okf = self._resolve_docs_okf(gov_base_path)
        schema_data = self._load_okf_schema_data(docs_okf)

        required_set = set(schema_data.get("required_directories", []))
        entries_by_path: Dict[str, Dict[str, Any]] = {}

        def _upsert_entry(path_str: str, category: str, description: str, required: bool) -> None:
            norm = path_str.strip("/")
            if not norm:
                return
            existing = entries_by_path.get(norm)
            if existing is None:
                entries_by_path[norm] = {
                    "path": norm,
                    "category": category,
                    "description": description,
                    "required": required,
                }
            else:
                if category and existing.get("category") in ("", "standard"):
                    existing["category"] = category
                if description and not existing.get("description"):
                    existing["description"] = description
                existing["required"] = bool(existing.get("required") or required)

        for req_dir in schema_data.get("required_directories", []):
            _upsert_entry(str(req_dir), "required", "Directorio obligatorio del estándar OKF", True)
        for v2_dir in schema_data.get("v2_directories", []):
            _upsert_entry(str(v2_dir), "v2_standard", "Directorio estándar OKF v2", str(v2_dir) in required_set)
        for item in schema_data.get("active_folders", []):
            if isinstance(item, str):
                _upsert_entry(item, "active", "", item in required_set)
            elif isinstance(item, dict) and item.get("path"):
                _upsert_entry(
                    str(item["path"]),
                    str(item.get("category") or "active"),
                    str(item.get("description") or ""),
                    bool(item.get("required") or item["path"] in required_set),
                )
        for item in schema_data.get("custom_folders", []):
            if isinstance(item, str):
                _upsert_entry(item, "custom", "", False)
            elif isinstance(item, dict) and item.get("path"):
                _upsert_entry(
                    str(item["path"]),
                    str(item.get("category") or "custom"),
                    str(item.get("description") or ""),
                    bool(item.get("required", False)),
                )

        result: List[Dict[str, Any]] = []
        for path_str in sorted(entries_by_path.keys()):
            entry = entries_by_path[path_str]
            folder_dir = docs_okf / path_str
            file_count = 0
            if folder_dir.is_dir():
                file_count = sum(1 for p in folder_dir.iterdir() if p.is_file())
            result.append({
                "path": entry["path"],
                "category": entry["category"],
                "description": entry["description"],
                "file_count": file_count,
                "required": entry["required"],
            })
        return result

    def add_okf_folder(
        self,
        gov_base_path: str | Path,
        folder_path: str,
        category: str = "custom",
        description: str = "",
    ) -> Dict[str, Any]:
        """Crea una nueva carpeta OKF validada y la registra en okf_schema.json."""
        docs_okf = self._resolve_docs_okf(gov_base_path)
        docs_okf.mkdir(parents=True, exist_ok=True)
        target_dir, clean_rel = self._validate_folder_path(docs_okf, folder_path)
        target_dir.mkdir(parents=True, exist_ok=True)

        schema_data = self._load_okf_schema_data(docs_okf)
        folder_entry = {
            "path": clean_rel,
            "category": str(category or "custom"),
            "description": str(description or ""),
        }

        custom_folders = [
            f for f in schema_data.get("custom_folders", [])
            if not (isinstance(f, dict) and f.get("path") == clean_rel) and f != clean_rel
        ]
        custom_folders.append(folder_entry)
        schema_data["custom_folders"] = custom_folders

        active_folders = [
            f for f in schema_data.get("active_folders", [])
            if not (isinstance(f, dict) and f.get("path") == clean_rel) and f != clean_rel
        ]
        active_folders.append(dict(folder_entry))
        schema_data["active_folders"] = active_folders

        self._save_okf_schema_data(docs_okf, schema_data)
        folders = self.list_okf_folders(gov_base_path)
        created = next((f for f in folders if f["path"] == clean_rel), {**folder_entry, "file_count": 0, "required": False})
        return {"folder": created, "folders": folders}

    def remove_okf_folder(
        self,
        gov_base_path: str | Path,
        folder_path: str,
        archive_if_not_empty: bool = True,
    ) -> Dict[str, Any]:
        """
        Elimina una carpeta OKF no obligatoria. Si contiene archivos y archive_if_not_empty=True,
        la mueve de forma segura a docs/okf/history/archived_folders/.
        """
        docs_okf = self._resolve_docs_okf(gov_base_path)
        target_dir, clean_rel = self._validate_folder_path(docs_okf, folder_path)
        schema_data = self._load_okf_schema_data(docs_okf)

        protected = set(schema_data.get("required_directories", [])) | {"history", "conventions"}
        if clean_rel in protected or clean_rel.startswith("history/"):
            raise OKFValidationError(f"No se puede eliminar la carpeta protegida: '{clean_rel}'")

        archived_to: Optional[str] = None
        if target_dir.is_dir():
            has_entries = any(target_dir.iterdir())
            if has_entries:
                if not archive_if_not_empty:
                    raise OKFValidationError(f"La carpeta '{clean_rel}' no está vacía.")
                archive_root = docs_okf / "history" / "archived_folders"
                archive_root.mkdir(parents=True, exist_ok=True)
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                safe_slug = clean_rel.replace("/", "__")
                dest = ensure_within(archive_root, archive_root / f"{safe_slug}_{ts}")
                shutil.move(str(target_dir), str(dest))
                archived_to = dest.relative_to(docs_okf).as_posix()
            else:
                target_dir.rmdir()

        schema_data["custom_folders"] = [
            f for f in schema_data.get("custom_folders", [])
            if not (isinstance(f, dict) and f.get("path") == clean_rel) and f != clean_rel
        ]
        schema_data["active_folders"] = [
            f for f in schema_data.get("active_folders", [])
            if not (isinstance(f, dict) and f.get("path") == clean_rel) and f != clean_rel
        ]
        schema_data["v2_directories"] = [
            d for d in schema_data.get("v2_directories", [])
            if str(d).strip("/") != clean_rel
        ]
        self._save_okf_schema_data(docs_okf, schema_data)

        return {
            "archived_to": archived_to,
            "folders": self.list_okf_folders(gov_base_path),
        }

    def rename_okf_folder(
        self,
        gov_base_path: str | Path,
        old_path: str,
        new_path: str,
    ) -> Dict[str, Any]:
        """Renombra o mueve una carpeta OKF dentro de docs/okf actualizando okf_schema.json."""
        docs_okf = self._resolve_docs_okf(gov_base_path)
        old_dir, clean_old = self._validate_folder_path(docs_okf, old_path)
        new_dir, clean_new = self._validate_folder_path(docs_okf, new_path)

        schema_data = self._load_okf_schema_data(docs_okf)
        protected = set(schema_data.get("required_directories", [])) | {"history", "conventions"}
        if clean_old in protected or clean_old.startswith("history/"):
            raise OKFValidationError(f"No se puede renombrar la carpeta protegida: '{clean_old}'")
        if not old_dir.is_dir():
            raise FileNotFoundError(f"La carpeta origen '{clean_old}' no existe en docs/okf.")
        if new_dir.exists():
            raise FileExistsError(f"La carpeta destino '{clean_new}' ya existe en docs/okf.")

        new_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_dir), str(new_dir))

        def _replace_list(items: List[Any]) -> List[Any]:
            updated: List[Any] = []
            for item in items:
                if isinstance(item, dict) and item.get("path") == clean_old:
                    new_item = dict(item)
                    new_item["path"] = clean_new
                    updated.append(new_item)
                elif isinstance(item, str) and item.strip("/") == clean_old:
                    updated.append(clean_new)
                else:
                    updated.append(item)
            return updated

        schema_data["custom_folders"] = _replace_list(schema_data.get("custom_folders", []))
        schema_data["active_folders"] = _replace_list(schema_data.get("active_folders", []))
        schema_data["v2_directories"] = _replace_list(schema_data.get("v2_directories", []))
        self._save_okf_schema_data(docs_okf, schema_data)

        return {
            "old_path": clean_old,
            "new_path": clean_new,
            "folders": self.list_okf_folders(gov_base_path),
        }

    def update_artifact_tags(
        self,
        gov_base_path: str | Path,
        rel_path: str | Path,
        tags: Iterable[str] | str,
    ) -> Dict[str, Any]:
        """Actualiza los tags YAML de un artefacto OKF y recompila tags_index.yaml."""
        docs_okf = self._resolve_docs_okf(gov_base_path)
        norm_tags = self.tag_engine.set_artifact_tags(docs_okf, rel_path, tags)
        index_data = self.tag_engine.compile_tags_index(docs_okf)
        norm_rel = Path(str(rel_path).strip()).as_posix()
        return {
            "path": norm_rel,
            "tags": norm_tags,
            "index": index_data,
        }

    def get_tags_index(
        self,
        gov_base_path: str | Path,
        rebuild: bool = True,
    ) -> Dict[str, Any]:
        """Obtiene (y opcionalmente recompila) el índice determinista de tags OKF."""
        docs_okf = self._resolve_docs_okf(gov_base_path)
        index_path = docs_okf / "tags_index.yaml"
        if rebuild or not index_path.is_file():
            return self.tag_engine.compile_tags_index(docs_okf)
        loaded = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            return self.tag_engine.compile_tags_index(docs_okf)
        loaded.setdefault("tags", {})
        loaded.setdefault("items", [])
        loaded["index_path"] = str(index_path)
        return loaded

    def filter_by_tags(
        self,
        gov_base_path: str | Path,
        tags: Iterable[str] | str,
        match_all: bool = False,
    ) -> List[Dict[str, Any]]:
        """Filtra artefactos OKF por uno o más tags YAML."""
        docs_okf = self._resolve_docs_okf(gov_base_path)
        return self.tag_engine.filter_artifacts_by_tags(docs_okf, tags, match_all=match_all)

    def get_okf_freshness_report(
        self,
        code_repo_path: Optional[str | Path],
        gov_base_path: str | Path,
        sla_days: int = 14,
    ) -> Dict[str, Any]:
        """Calcula el reporte de Freshness determinista y verificación de firmas Ed25519."""
        return self.freshness_engine.compute_project_freshness(
            code_repo_path=code_repo_path,
            gov_base_path=gov_base_path,
            sla_days=sla_days,
        )
