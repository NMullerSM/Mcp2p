"""
Motor de Análisis Estático y Escaneo Exhaustivo de Código y Documentación para MCP2P.

Inspecciona repositorios existentes (tanto código fuente como documentación existente:
README, docs/, specs/, RFCs, wikis, package.json, pyproject.toml, etc.) para:
1. Detectar el stack tecnológico completo y herramientas de testing/empaquetado.
2. Extraer de forma dinámica N Decisiones Arquitectónicas (ADRs) sin límites predeterminados.
3. Extraer Requisitos Funcionales (RF) y Requisitos No Funcionales (RNF).
4. Mapear la estructura real de directorios y componentes en 02_MAPA_DE_CODIGO_Y_FEATURES.md.
5. Inferir convenciones de desarrollo (estilo de código, branching, tests).
6. Compilar un único documento frontal ("Dossier de Alineación OKF") para revisión HITL.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("mcp2p.storage.code_scanner")


@dataclass
class DetectedStack:
    languages: List[str] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)
    build_tools: List[str] = field(default_factory=list)
    testing_tools: List[str] = field(default_factory=list)
    database_hints: List[str] = field(default_factory=list)
    runtime_environment: str = "Multiplataforma (Linux, macOS, Windows)"
    package_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedRequirement:
    req_id: str
    category: str  # "functional" o "non_functional"
    title: str
    description: str
    source_ref: str
    priority: str = "media"  # alta, media, baja


@dataclass
class ExtractedADR:
    adr_id: str
    title: str
    context: str
    decision: str
    consequences: str
    status: str = "accepted"
    source_ref: str = "Análisis estático y documentación"


class CodebaseAnalyzer:
    """
    Analizador exhaustivo de código y documentación para repositorios importados.
    """

    EXCLUDED_DIRS = {
        ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
        ".idea", ".vscode", "target", "coverage", ".next", ".turbo"
    }

    def __init__(self, code_repo_path: str | Path) -> None:
        self.code_path = Path(code_repo_path).resolve()

    def analyze_full_repository(self) -> Dict[str, Any]:
        """Ejecuta el escaneo completo de código, configs, git y documentación."""
        if not self.code_path.exists():
            raise FileNotFoundError(f"Ruta de código no encontrada: {self.code_path}")

        stack = self._detect_stack()
        docs_corpus = self._extract_documentation_corpus()
        adrs = self._extract_adrs(stack, docs_corpus)
        reqs = self._extract_requirements(docs_corpus)
        conventions = self._infer_conventions(stack)
        features_map = self._generate_features_map()
        backlog_suggestions = self._extract_backlog_suggestions()

        return {
            "stack": stack,
            "docs_corpus_summary": {
                "total_docs_analyzed": len(docs_corpus),
                "doc_files": [d["rel_path"] for d in docs_corpus]
            },
            "adrs": adrs,
            "requirements": reqs,
            "conventions": conventions,
            "features_map": features_map,
            "backlog_suggestions": backlog_suggestions,
            "scanned_at": datetime.now(timezone.utc).isoformat()
        }

    # =========================================================================
    # 1. Detección de Stack Tecnológico y Dependencias
    # =========================================================================

    def _detect_stack(self) -> DetectedStack:
        stack = DetectedStack()
        languages: Set[str] = set()
        frameworks: Set[str] = set()
        build_tools: Set[str] = set()
        testing_tools: Set[str] = set()
        databases: Set[str] = set()

        # Python
        pyproject = self.code_path / "pyproject.toml"
        requirements = self.code_path / "requirements.txt"
        setup_py = self.code_path / "setup.py"
        if pyproject.exists() or requirements.exists() or setup_py.exists():
            languages.add("Python")
            build_tools.add("pip")
            if pyproject.exists():
                txt = pyproject.read_text(encoding="utf-8", errors="ignore")
                if "poetry" in txt:
                    build_tools.add("poetry")
                if "hatchling" in txt or "flit" in txt:
                    build_tools.add("hatch/flit")
                if "pytest" in txt:
                    testing_tools.add("pytest")
                if "fastapi" in txt.lower():
                    frameworks.add("FastAPI")
                if "django" in txt.lower():
                    frameworks.add("Django")
                if "flask" in txt.lower():
                    frameworks.add("Flask")
                if "sqlite" in txt.lower():
                    databases.add("SQLite")
                if "sqlalchemy" in txt.lower():
                    frameworks.add("SQLAlchemy")
            if requirements.exists():
                txt = requirements.read_text(encoding="utf-8", errors="ignore")
                if "pytest" in txt:
                    testing_tools.add("pytest")
                if "fastapi" in txt.lower():
                    frameworks.add("FastAPI")
                if "django" in txt.lower():
                    frameworks.add("Django")
                if "flask" in txt.lower():
                    frameworks.add("Flask")
                if "sqlalchemy" in txt.lower():
                    frameworks.add("SQLAlchemy")

        # Node / TypeScript / JavaScript
        package_json = self.code_path / "package.json"
        if package_json.exists():
            languages.add("JavaScript")
            build_tools.add("npm")
            try:
                pkg_data = json.loads(package_json.read_text(encoding="utf-8"))
                stack.package_metadata = {
                    "name": pkg_data.get("name"),
                    "version": pkg_data.get("version"),
                    "description": pkg_data.get("description")
                }
                deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
                if "typescript" in deps:
                    languages.add("TypeScript")
                if "react" in deps:
                    frameworks.add("React")
                if "vue" in deps:
                    frameworks.add("Vue")
                if "next" in deps:
                    frameworks.add("Next.js")
                if "vite" in deps:
                    build_tools.add("Vite")
                if "express" in deps:
                    frameworks.add("Express")
                if "jest" in deps or "vitest" in deps:
                    testing_tools.add("Jest/Vitest")
                if "tailwind" in str(deps):
                    frameworks.add("TailwindCSS")
            except Exception:
                pass

        # Rust
        cargo = self.code_path / "Cargo.toml"
        if cargo.exists():
            languages.add("Rust")
            build_tools.add("cargo")

        # Go
        gomod = self.code_path / "go.mod"
        if gomod.exists():
            languages.add("Go")
            build_tools.add("go modules")

        stack.languages = sorted(list(languages)) if languages else ["Desconocido / Políglota"]
        stack.frameworks = sorted(list(frameworks))
        stack.build_tools = sorted(list(build_tools))
        stack.testing_tools = sorted(list(testing_tools))
        stack.database_hints = sorted(list(databases))
        return stack

    # =========================================================================
    # 2. Extracción Exhaustiva de Documentación Existente
    # =========================================================================

    def _extract_documentation_corpus(self) -> List[Dict[str, Any]]:
        """Recorre recursivamente todos los archivos de documentación existentes en app/."""
        corpus: List[Dict[str, Any]] = []
        doc_patterns = ("*.md", "*.markdown", "*.rst", "*.txt")

        # Priorizar README en la raíz
        for p in ("README.md", "README.rst", "README.txt", "ARCHITECTURE.md"):
            rf = self.code_path / p
            if rf.is_file():
                try:
                    txt = rf.read_text(encoding="utf-8", errors="ignore")
                    corpus.append({
                        "file_name": rf.name,
                        "rel_path": p,
                        "content": txt,
                        "priority": "primary"
                    })
                except Exception:
                    pass

        # Buscar en carpetas docs/, documentation/, specifications/, etc.
        for folder_name in ("docs", "doc", "documentation", "specs", "specifications", "rfcs", "wiki"):
            target_dir = self.code_path / folder_name
            if target_dir.is_dir():
                for root, dirs, files in os.walk(target_dir):
                    dirs[:] = [d for d in dirs if d not in self.EXCLUDED_DIRS]
                    for f in files:
                        if f.lower().endswith((".md", ".rst", ".markdown")):
                            fp = Path(root) / f
                            rel_p = str(fp.relative_to(self.code_path))
                            if not any(c["rel_path"] == rel_p for c in corpus):
                                try:
                                    txt = fp.read_text(encoding="utf-8", errors="ignore")
                                    corpus.append({
                                        "file_name": f,
                                        "rel_path": rel_p,
                                        "content": txt,
                                        "priority": "secondary"
                                    })
                                except Exception:
                                    pass

        return corpus

    # =========================================================================
    # 3. Extracción Dinámica de N Decisiones Arquitectónicas (ADRs)
    # =========================================================================

    def _extract_adrs(self, stack: DetectedStack, docs_corpus: List[Dict[str, Any]]) -> List[ExtractedADR]:
        """
        Extrae dinámicamente TODAS las decisiones arquitectónicas que el proyecto plantee:
        1. Parseo de ADRs ya existentes si el proyecto tenía carpeta adr/ o docs/adr/.
        2. Detección de elecciones de arquitectura en los textos de documentación.
        3. Detección basada en el stack tecnológico y modularidad del código.
        """
        adrs: List[ExtractedADR] = []
        seen_titles = set()
        adr_counter = 1

        # 1. Buscar si ya existían ADRs formales en docs/adr, docs/decisions o doc/adr
        for c in docs_corpus:
            content = c["content"]
            rel = c["rel_path"].lower()
            if "adr" in rel or "decision" in rel:
                # Intentar parsear estructura estándar de ADR
                m_title = re.search(r"^#\s+(?:ADR-?\d*[:\s]*)?(.+)$", content, re.MULTILINE)
                m_ctx = re.search(r"(?:##\s*(?:Contexto|Context|Motivation))([\s\S]*?)(?:##|$)", content, re.IGNORECASE)
                m_dec = re.search(r"(?:##\s*(?:Decisi[oó]n|Decision))([\s\S]*?)(?:##|$)", content, re.IGNORECASE)
                m_cons = re.search(r"(?:##\s*(?:Consecuencias|Consequences|Status))([\s\S]*?)(?:##|$)", content, re.IGNORECASE)

                if m_title and (m_dec or m_ctx):
                    t_str = m_title.group(1).strip()
                    if t_str not in seen_titles:
                        seen_titles.add(t_str)
                        adrs.append(ExtractedADR(
                            adr_id=f"ADR-{adr_counter:03d}",
                            title=t_str,
                            context=(m_ctx.group(1).strip() if m_ctx else "Documentado previamente en el repositorio.")[:500],
                            decision=(m_dec.group(1).strip() if m_dec else "Decisión registrada en documentación existente.")[:500],
                            consequences=(m_cons.group(1).strip() if m_cons else "Alineación y mantenimiento continuo.")[:400],
                            source_ref=c["rel_path"]
                        ))
                        adr_counter += 1

        # 2. Extraer decisiones inferidas de secciones clave de la documentación (Arquitectura, Tecnologías, Base de Datos)
        combined_docs = "\n\n".join([f"--- File: {c['rel_path']} ---\n{c['content']}" for c in docs_corpus])

        # A. Decisión sobre Arquitectura y Stack Base
        if stack.languages:
            langs_str = ", ".join(stack.languages)
            fw_str = f" y frameworks {', '.join(stack.frameworks)}" if stack.frameworks else ""
            t = f"Adopción de Stack Tecnológico Principal ({langs_str})"
            if t not in seen_titles:
                seen_titles.add(t)
                adrs.append(ExtractedADR(
                    adr_id=f"ADR-{adr_counter:03d}",
                    title=t,
                    context=f"El proyecto fue diseñado y estructurado originalmente en {langs_str}{fw_str}.",
                    decision=f"Mantener {langs_str} como base computacional con herramientas de construcción {', '.join(stack.build_tools) or 'nativas'}.",
                    consequences="Consistencia en el desarrollo y compatibilidad con el entorno de ejecución existente.",
                    source_ref="Stack detectado en código y configs"
                ))
                adr_counter += 1

        # B. Detección de Persistencia y Base de Datos en Docs o Código
        db_match = re.search(r"(SQLite|PostgreSQL|MySQL|MongoDB|Redis|Cassandra|Supabase|Firebase)", combined_docs, re.IGNORECASE)
        db_name = db_match.group(1).title() if db_match else (stack.database_hints[0] if stack.database_hints else None)
        if db_name:
            t = f"Estrategia de Persistencia y Acceso a Datos con {db_name}"
            if t not in seen_titles:
                seen_titles.add(t)
                adrs.append(ExtractedADR(
                    adr_id=f"ADR-{adr_counter:03d}",
                    title=t,
                    context=f"Se requiere almacenamiento persistente y transaccional confiable para las entidades de dominio.",
                    decision=f"Utilizar {db_name} como motor principal de datos, aislando las consultas a través de capas de repositorio.",
                    consequences="Garantiza integridad de datos y facilidad de sincronización o respaldo local.",
                    source_ref="Documentación y configuración del proyecto"
                ))
                adr_counter += 1

        # C. Detección de Comunicación y APIs (REST, GraphQL, WebSockets, gRPC, P2P)
        comm_match = re.search(r"(REST|GraphQL|WebSocket[s]?|gRPC|HTTP API|P2P|RPC)", combined_docs, re.IGNORECASE)
        if comm_match:
            proto_name = comm_match.group(1).upper()
            t = f"Protocolo de Comunicación y Exposición de Servicios ({proto_name})"
            if t not in seen_titles:
                seen_titles.add(t)
                adrs.append(ExtractedADR(
                    adr_id=f"ADR-{adr_counter:03d}",
                    title=t,
                    context="Los clientes y servicios colaboradores requieren una interfaz de comunicación desacoplada y predecible.",
                    decision=f"Estandarizar el intercambio de mensajes e interfaces mediante {proto_name}.",
                    consequences="Interoperabilidad simplificada y facilidad de auditoría e inspección técnica.",
                    source_ref="Referencias en documentación del repositorio"
                ))
                adr_counter += 1

        # D. Detección de Estrategia de Testing
        if stack.testing_tools:
            tools_str = ", ".join(stack.testing_tools)
            t = f"Framework de Pruebas Automatizadas y Verificación ({tools_str})"
            if t not in seen_titles:
                seen_titles.add(t)
                adrs.append(ExtractedADR(
                    adr_id=f"ADR-{adr_counter:03d}",
                    title=t,
                    context="Garantizar la calidad y no regresión del código antes de fusionar cambios.",
                    decision=f"Adoptar suite de pruebas automatizadas con {tools_str}.",
                    consequences="Tasa de cobertura verificable y ejecución reproducible en entornos CI/CD.",
                    source_ref="Dependencias de test configuradas en el proyecto"
                ))
                adr_counter += 1

        # E. Decisiones adicionales extraídas de encabezados "Architecture", "Design", "Decision" en Docs
        heading_matches = re.findall(r"^#{1,3}\s+(?:Decisi[oó]n|Diseño|Arquitectura|Strategy)[:\s]*(.+)$", combined_docs, re.MULTILINE | re.IGNORECASE)
        for h in heading_matches:
            clean_h = h.strip()
            if len(clean_h) > 5 and clean_h not in seen_titles:
                seen_titles.add(clean_h)
                adrs.append(ExtractedADR(
                    adr_id=f"ADR-{adr_counter:03d}",
                    title=f"Decisión Documentada: {clean_h}",
                    context="Revisión retrospectiva extraída de la documentación existente del repositorio.",
                    decision=f"Se ratifica el lineamiento de '{clean_h}' según la especificación original.",
                    consequences="Preservación del conocimiento de diseño previo sin pérdida de contexto.",
                    source_ref="Documentación interna del repositorio"
                ))
                adr_counter += 1

        return adrs

    # =========================================================================
    # 4. Extracción de Requisitos Funcionales y No Funcionales
    # =========================================================================

    def _extract_requirements(self, docs_corpus: List[Dict[str, Any]]) -> List[ExtractedRequirement]:
        """Extrae requisitos funcionales (casos de uso) y no funcionales (rendimiento, multiplataforma, seguridad)."""
        reqs: List[ExtractedRequirement] = []
        seen = set()
        rf_idx = 1
        rnf_idx = 1

        combined = "\n\n".join([c["content"] for c in docs_corpus])

        # Buscar listas de features, objetivos o requisitos en los docs
        lines = combined.split("\n")
        for line in lines:
            line_str = line.strip()
            # Detección de viñetas con contenido relevante
            if line_str.startswith(("- ", "* ", "1. ", "2. ", "3. ")) and len(line_str) > 15:
                clean_text = re.sub(r"^[-*0-9.]+\s+", "", line_str).strip()
                if clean_text in seen:
                    continue

                # Clasificar como no funcional si contiene palabras de rendimiento, memoria, seguridad, multiplataforma
                if re.search(r"(rendimiento|performance|latencia|latency|memoria|seguridad|security|multiplataforma|macos|linux|windows|concurrencia|cifrado|offline)", clean_text, re.IGNORECASE):
                    seen.add(clean_text)
                    reqs.append(ExtractedRequirement(
                        req_id=f"RNF-{rnf_idx:03d}",
                        category="non_functional",
                        title=clean_text[:80],
                        description=clean_text,
                        source_ref="Documentación del proyecto",
                        priority="alta"
                    ))
                    rnf_idx += 1
                elif re.search(r"(permite|soporta|gestiona|crea|visualiza|autentica|sincroniza|calcula|exporta|integra|usuario|agente)", clean_text, re.IGNORECASE):
                    seen.add(clean_text)
                    reqs.append(ExtractedRequirement(
                        req_id=f"RF-{rf_idx:03d}",
                        category="functional",
                        title=clean_text[:80],
                        description=clean_text,
                        source_ref="Documentación del proyecto",
                        priority="alta"
                    ))
                    rf_idx += 1

        # Requisitos No Funcionales Base Garantizados para MCP2P (Multiplataforma sin atadura a Docker)
        if not any("multiplataforma" in r.description.lower() for r in reqs):
            reqs.append(ExtractedRequirement(
                req_id=f"RNF-{rnf_idx:03d}",
                category="non_functional",
                title="Compatibilidad Multiplataforma Nativa (macOS, Linux, Windows)",
                description="La aplicación debe operar y ejecutarse de forma nativa en macOS, Linux y Windows sin requerir obligatoriamente virtualización Docker.",
                source_ref="Lineamiento general de arquitectura",
                priority="alta"
            ))
            rnf_idx += 1

        if not any("offline" in r.description.lower() for r in reqs):
            reqs.append(ExtractedRequirement(
                req_id=f"RNF-{rnf_idx:03d}",
                category="non_functional",
                title="Operación Local-First y Resiliencia sin Conexión",
                description="Todas las lecturas y escrituras de estado deben ejecutarse en almacenamiento local con latencias inferiores a 5ms antes de sincronizar en red.",
                source_ref="Lineamiento general de arquitectura",
                priority="alta"
            ))

        return reqs

    # =========================================================================
    # 5. Mapeo de Componentes Reales y Features
    # =========================================================================

    def _generate_features_map(self) -> List[Dict[str, str]]:
        """Mapea los directorios reales de la aplicación a módulos funcionales."""
        features: List[Dict[str, str]] = []
        if not self.code_path.exists():
            return features

        for item in sorted(self.code_path.iterdir()):
            if item.is_dir() and item.name not in self.EXCLUDED_DIRS and not item.name.startswith("."):
                name = item.name
                # Inferir propósito por nombre común
                purpose = "Módulo de código fuente"
                if name in ("src", "app"):
                    purpose = "Núcleo de la aplicación y lógica de negocio"
                elif name in ("frontend", "ui", "web"):
                    purpose = "Interfaz de usuario y componentes visuales"
                elif name in ("backend", "server", "api"):
                    purpose = "Servicios de backend, endpoints y modelos de datos"
                elif name in ("tests", "test"):
                    purpose = "Batería de pruebas unitarias y de integración"
                elif name in ("docs", "doc"):
                    purpose = "Documentación técnica y guías de arquitectura"
                elif name in ("scripts", "tools"):
                    purpose = "Herramientas de automatización y scripts auxiliares"

                features.append({
                    "feature": name.capitalize(),
                    "directory": name,
                    "purpose": purpose
                })
        return features

    # =========================================================================
    # 6. Detección de Convenciones de Desarrollo
    # =========================================================================

    def _infer_conventions(self, stack: DetectedStack) -> Dict[str, Any]:
        """Infiere convenciones reales de estilo, linters y git."""
        conventions: Dict[str, Any] = {
            "git_branching": "Trunk-based con ramas cortas de feature (feature/{card_id}) y fusión en main.",
            "worktrees": "Uso de Git Worktrees aislados para desarrollo paralelo por tarjeta sin ensuciar el árbol activo.",
            "style_guide": "Formatters estándar según lenguaje: " + (", ".join(stack.languages) or "Estándar"),
            "linters": []
        }

        # Detectar linters en disco
        if (self.code_path / ".eslintrc.json").exists() or (self.code_path / ".eslintrc.js").exists():
            conventions["linters"].append("ESLint")
        if (self.code_path / ".prettierrc").exists():
            conventions["linters"].append("Prettier")
        if (self.code_path / "ruff.toml").exists() or (self.code_path / ".ruff.toml").exists():
            conventions["linters"].append("Ruff")
        if (self.code_path / ".flake8").exists():
            conventions["linters"].append("Flake8")

        return conventions

    # =========================================================================
    # 7. Sugerencias de Backlog Inicial a partir de TODOs y Commits
    # =========================================================================

    def _extract_backlog_suggestions(self) -> List[Dict[str, str]]:
        """Extrae tareas iniciales sugeridas a partir de TODO/FIXME en el código."""
        suggestions: List[Dict[str, str]] = []
        count = 1

        for root, dirs, files in os.walk(self.code_path):
            dirs[:] = [d for d in dirs if d not in self.EXCLUDED_DIRS]
            for f in files:
                if f.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go")):
                    fp = Path(root) / f
                    try:
                        txt = fp.read_text(encoding="utf-8", errors="ignore")
                        matches = re.findall(r"(?:#|//)\s*(?:TODO|FIXME)[:\s]*(.+)$", txt, re.MULTILINE | re.IGNORECASE)
                        for m in matches[:2]:  # máximo 2 por archivo
                            clean_t = m.strip()
                            rel = str(fp.relative_to(self.code_path))
                            suggestions.append({
                                "card_id": f"TASK-INIT-{count:02d}",
                                "title": f"Resolver: {clean_t[:60]}",
                                "description": f"Detectado en `{rel}`:\n> {clean_t}",
                                "status": "backlog"
                            })
                            count += 1
                            if len(suggestions) >= 8:
                                break
                    except Exception:
                        pass
            if len(suggestions) >= 8:
                break

        if not suggestions:
            suggestions.append({
                "card_id": "TASK-INIT-01",
                "title": "Verificación de la Suite de Pruebas y Cobertura",
                "description": "Comprobar que todas las pruebas existentes se ejecuten limpiamente en el entorno de trabajo.",
                "status": "backlog"
            })
            suggestions.append({
                "card_id": "TASK-INIT-02",
                "title": "Consolidación de Convenciones y Linter",
                "description": "Asegurar que las herramientas de formateo y validación de código estén configuradas uniformemente.",
                "status": "backlog"
            })

        return suggestions

    # =========================================================================
    # 8. Compilación del Dossier Unificado de Alineación OKF
    # =========================================================================

    def generate_alignment_dossier(self, project_name: str, project_uuid: str) -> str:
        """
        Compila todo el escaneo en un ÚNICO documento Markdown frontal exhaustivo
        para revisión en la bandeja HITL o lectura por otro agente (Claude Pro) vía MCP.
        """
        analysis = self.analyze_full_repository()
        stack: DetectedStack = analysis["stack"]
        adrs: List[ExtractedADR] = analysis["adrs"]
        reqs: List[ExtractedRequirement] = analysis["requirements"]
        conv: Dict[str, Any] = analysis["conventions"]
        features: List[Dict[str, str]] = analysis["features_map"]
        backlog: List[Dict[str, str]] = analysis["backlog_suggestions"]

        dossier = f"""# 📄 Dossier Unificado de Alineación Inicial OKF v2
## Proyecto: **{project_name}** (`{project_uuid}`)
> **Fecha de Ingesta**: {analysis['scanned_at']}  
> **Estado**: `PENDING_HUMAN_RATIFICATION` (Esperando firma criptográfica Ed25519)  
> **Ámbito**: Repositorio importado sin OKF previo (`app/` intacto, gobernanza en `governance/`)

---

## 1. Resumen Ejecutivo del Stack Tecnológico
* **Lenguajes Detectados**: {', '.join(stack.languages)}
* **Frameworks y Librerías**: {', '.join(stack.frameworks) or 'Estándar / Nativas'}
* **Herramientas de Construcción y Empaquetado**: {', '.join(stack.build_tools) or 'Nativas'}
* **Frameworks de Testing**: {', '.join(stack.testing_tools) or 'Pendiente de configuración'}
* **Entorno de Ejecución**: {stack.runtime_environment} (Operación nativa sin dependencia forzosa de Docker).
* **Documentación Base Analizada**: {analysis['docs_corpus_summary']['total_docs_analyzed']} archivo(s) encontrados ({', '.join(analysis['docs_corpus_summary']['doc_files'][:6]) or 'README base'}).

---

## 2. Decisiones Arquitectónicas Identificadas (Total: {len(adrs)} ADRs)
*A continuación se presentan todas las decisiones de diseño extraídas dinámicamente de la documentación y el código:*

"""
        for adr in adrs:
            dossier += f"""### {adr.adr_id}: {adr.title}
* **Contexto**: {adr.context}
* **Decisión**: {adr.decision}
* **Consecuencias**: {adr.consequences}
* **Fuente**: `{adr.source_ref}`

---
"""

        dossier += f"""## 3. Requisitos Funcionales y No Funcionales (Total: {len(reqs)})

### Requisitos Funcionales (RF)
| Identificador | Requisito / Caso de Uso | Prioridad | Fuente |
| :--- | :--- | :--- | :--- |
"""
        rf_list = [r for r in reqs if r.category == "functional"]
        for rf in rf_list:
            dossier += f"| `{rf.req_id}` | {rf.description} | {rf.priority.capitalize()} | {rf.source_ref} |\n"
        if not rf_list:
            dossier += "| `RF-001` | Operación y casos de uso estándar del repositorio. | Alta | Análisis base |\n"

        dossier += """
### Requisitos No Funcionales (RNF)
| Identificador | Criterio de Calidad / Restricción Técnica | Prioridad | Fuente |
| :--- | :--- | :--- | :--- |
"""
        rnf_list = [r for r in reqs if r.category == "non_functional"]
        for rnf in rnf_list:
            dossier += f"| `{rnf.req_id}` | {rnf.description} | {rnf.priority.capitalize()} | {rnf.source_ref} |\n"

        dossier += f"""
---

## 4. Mapa de Módulos y Features del Código
| Feature / Módulo | Directorio Físico en `app/` | Propósito Funcional |
| :--- | :--- | :--- |
"""
        for feat in features:
            dossier += f"| **{feat['feature']}** | `{feat['directory']}` | {feat['purpose']} |\n"

        dossier += f"""
---

## 5. Convenciones de Desarrollo Inferidas
* **Estrategia Git**: {conv.get('git_branching')}
* **Aislamiento de Trabajo**: {conv.get('worktrees')}
* **Guía de Estilo**: {conv.get('style_guide')}
* **Linters Detectados**: {', '.join(conv.get('linters', [])) or 'Por configurar'}

---

## 6. Backlog Inicial Sugerido para el Tablero Kanban ({len(backlog)} Tarjetas)
"""
        for item in backlog:
            dossier += f"- **[{item['card_id']}] {item['title']}**: {item['description']}\n"

        dossier += """
---

## 7. Instrucción de Aprobación Humana (HITL)
Al ratificar este Dossier en la bandeja HITL bajo firma **Ed25519 con Doble Confirmación**:
1. Se materializarán automáticamente todas las carpetas y archivos en `governance/docs/okf/`.
2. Las ADRs se registrarán en `docs/okf/architecture/adrs/` y actualizarán el índice de precedentes.
3. Las tarjetas sugeridas se insertarán en el tablero Kanban en estado `backlog`.
4. El proyecto pasará a estado oficial **`ALIGNED_OKF`** y en lo sucesivo operará en modo de auditoría de deriva continua.
"""
        return dossier
