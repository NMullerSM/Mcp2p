"""
Test Suite: Ingesta Cognitiva (Bootstrap) y Auditoría de Deriva (Drift) OKF v2.
Verifica:
1. Escaneo exhaustivo de código y documentación existente.
2. Extracción dinámica de N ADRs (sin límites de 2 a 3).
3. Extracción de Requisitos Funcionales (RF) y No Funcionales (RNF, incluyendo macOS nativo sin Docker obligatorio).
4. Generación del Dossier Unificado de Alineación.
5. Materialización atómica de la gobernanza OKF v2 tras firma Ed25519.
6. Auditoría de Deriva (Drift Audit) en Modo 2.
7. Herramientas MCP correspondientes (scan_and_bootstrap_okf, get_alignment_dossier, audit_okf_drift).
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
import pytest

from src.storage.db import DatabaseManager
from src.storage.code_scanner import CodebaseAnalyzer
from src.storage.okf_adapter import OKFAdapter
from src.mcp.tools import MCPToolsHandler
from src.app.api_bridge import ApiBridge


@pytest.fixture
def temp_workspace():
    tmp_dir = Path(tempfile.mkdtemp(prefix="mcp2p_test_bootstrap_"))
    yield tmp_dir
    shutil.rmtree(str(tmp_dir), ignore_errors=True)


@pytest.fixture
def mock_codebase(temp_workspace):
    """Crea una estructura de proyecto existente sin OKF previo."""
    code_dir = temp_workspace / "sample_app"
    code_dir.mkdir(parents=True)

    # 1. package.json con stack
    (code_dir / "package.json").write_text(json.dumps({
        "name": "sample-fintech-service",
        "version": "1.0.0",
        "dependencies": {
            "react": "^18.2.0",
            "express": "^4.18.2",
            "typescript": "^5.0.0"
        },
        "devDependencies": {
            "vitest": "^1.0.0"
        }
    }), encoding="utf-8")

    # 2. README.md exhaustivo con requerimientos y arquitectura
    readme_text = """# Fintech P2P Core
Sistema de liquidación de pagos locales y transacciones seguras.

## Arquitectura y Diseño
El sistema utiliza una arquitectura basada en microservicios y sincronización local.
Se implementa persistencia transaccional utilizando SQLite para operación sin conexión.
La comunicación entre nodos se efectúa mediante REST y WebSockets.

### Requisitos y Funcionalidades
* Permite a los usuarios emitir órdenes de pago instantáneas.
* Gestiona balances y cuentas locales con verificación de firmas.
* Sincroniza estados mediante WebSockets en tiempo real.
* La latencia de lectura y persistencia local debe ser menor a 5ms.
* La aplicación debe operar de forma nativa en macOS y Linux con alta concurrencia.
"""
    (code_dir / "README.md").write_text(readme_text, encoding="utf-8")

    # 3. Documentación adicional en docs/
    docs_dir = code_dir / "docs"
    docs_dir.mkdir()
    (docs_dir / "decisions.md").write_text("""# Decisiones Técnicas del Proyecto

### ADR-010: Adopción de SQLite en Modo WAL
## Contexto
Se requiere almacenamiento rápido y atómico en disco local sin necesidad de levantar servidores pesados de base de datos.
## Decisión
Usar SQLite con journal_mode=WAL y synchronous=NORMAL en todas las operaciones de balance.
## Consecuencias
Lecturas concurrentes ultrarrápidas y aislamiento transaccional garantizado.

### ADR-011: Cifrado Local de Claves con Argon2
## Contexto
Las credenciales de los usuarios no deben almacenarse en texto plano.
## Decisión
Derivar claves usando Argon2id con salt aleatorio de 16 bytes.
## Consecuencias
Alta resiliencia contra ataques de fuerza bruta.
""", encoding="utf-8")

    # 4. Código fuente simulado
    src_dir = code_dir / "src"
    src_dir.mkdir()
    (src_dir / "index.ts").write_text("""// Punto de entrada
// TODO: optimizar el pool de conexiones del socket
export function startApp() {
    console.log("App ready");
}
""", encoding="utf-8")

    return code_dir


def test_codebase_analyzer_exhaustive_extraction(mock_codebase):
    """Comprueba que CodebaseAnalyzer extraiga N ADRs dinámicamente y clasifique RF/RNF."""
    analyzer = CodebaseAnalyzer(mock_codebase)
    analysis = analyzer.analyze_full_repository()

    # 1. Stack detectado
    stack = analysis["stack"]
    assert "TypeScript" in stack.languages
    assert "JavaScript" in stack.languages
    assert "React" in stack.frameworks
    assert "Express" in stack.frameworks

    # 2. Extracción dinámica de ADRs (NO debe estar limitado a 2-3)
    adrs = analysis["adrs"]
    assert len(adrs) >= 4, f"Se esperaban al menos 4 ADRs dinámicas, se obtuvieron {len(adrs)}"
    adr_titles = [a.title for a in adrs]
    # Comprobar que extrajo las documentadas explícitamente en docs/decisions.md
    assert any("WAL" in t or "SQLite" in t for t in adr_titles)
    assert any("Argon2" in t or "Cifrado" in t for t in adr_titles)

    # 3. Requisitos Funcionales y No Funcionales
    reqs = analysis["requirements"]
    rf_items = [r for r in reqs if r.category == "functional"]
    rnf_items = [r for r in reqs if r.category == "non_functional"]
    assert len(rf_items) >= 2, "Debe extraer los requisitos funcionales del README"
    assert len(rnf_items) >= 2, "Debe extraer los requisitos no funcionales (latencia, multiplataforma)"

    # Verificar que el requisito de compatibilidad nativa (macOS/Linux) existe sin forzar Docker
    assert any("macos" in r.description.lower() or "multiplataforma" in r.description.lower() for r in rnf_items)

    # 4. Compilación del Dossier Unificado
    dossier = analyzer.generate_alignment_dossier("Fintech Core", "proj_test_fintech")
    assert "# 📄 Dossier Unificado de Alineación Inicial OKF v2" in dossier
    assert "Fintech Core" in dossier
    assert "ADR-" in dossier
    assert "Requisitos Funcionales" in dossier
    assert "Requisitos No Funcionales" in dossier


def test_materialize_bootstrap_dossier_and_drift_audit(temp_workspace, mock_codebase):
    """Comprueba la materialización de OKF v2 y la posterior auditoría de deriva."""
    gov_dir = temp_workspace / "okf_gov_repo"
    gov_dir.mkdir()

    adapter = OKFAdapter()
    adapter.initialize_project_okf(gov_dir, "Fintech Core", "proj_test_fintech")

    analyzer = CodebaseAnalyzer(mock_codebase)
    analysis = analyzer.analyze_full_repository()
    dossier = analyzer.generate_alignment_dossier("Fintech Core", "proj_test_fintech")
    analysis["dossier_markdown"] = dossier

    sig_block = {
        "developer_name": "NathaDev",
        "node_uuid": "node_master_01",
        "signature_ed25519": "sig_mock_ed25519_test",
        "signed_at": "2026-11-02T10:00:00Z"
    }

    # Materializar gobernanza
    mat_res = adapter.materialize_bootstrap_dossier(
        gov_base_path=gov_dir,
        analysis_result=analysis,
        signature_block=sig_block,
        project_uuid="proj_test_fintech"
    )

    assert mat_res["success"] is True
    assert mat_res["adrs_created_count"] >= 4

    docs_okf = gov_dir / "docs" / "okf"
    # Verificar que las ADRs se crearon en disco
    adrs_on_disk = list((docs_okf / "architecture" / "adrs").glob("*.md"))
    assert len(adrs_on_disk) >= 4

    # Verificar que precedents_index.yaml se generó y contiene las ADRs
    precedents_file = docs_okf / "architecture" / "precedents_index.yaml"
    assert precedents_file.exists()
    assert "schema_version: okf.precedents.v2" in precedents_file.read_text(encoding="utf-8")

    # Verificar que RF y RNF existen
    assert (docs_okf / "requirements" / "functional" / "RF_INDEX.md").exists()
    assert (docs_okf / "requirements" / "non_functional" / "RNF_INDEX.md").exists()

    # Verificar conventions y dossier histórico
    assert (docs_okf / "conventions" / "DEVELOPMENT_CONVENTIONS.md").exists()
    assert (docs_okf / "history" / "ALIGNMENT_DOSSIER_RATIFIED.md").exists()

    # Auditoría de Deriva Inicial: debe reportar ALINEADO
    drift_initial = adapter.audit_okf_drift(mock_codebase, gov_dir)
    assert drift_initial["is_aligned"] is True
    assert len(drift_initial["unmapped_components"]) == 0

    # Simular deriva: agregar un nuevo módulo 'analytics' en el código fuente
    (mock_codebase / "analytics").mkdir()
    drift_after = adapter.audit_okf_drift(mock_codebase, gov_dir)
    assert drift_after["is_aligned"] is False
    assert "analytics" in drift_after["unmapped_components"]


def test_mcp_tools_and_bridge_bootstrap_flow(temp_workspace, mock_codebase):
    """Comprueba el flujo end-to-end a través de MCPToolsHandler y ApiBridge."""
    DatabaseManager._instance = None
    db_file = temp_workspace / "test_mcp.db"
    db_mgr = DatabaseManager(str(db_file))

    # Crear proyecto con ruta al codebase de prueba
    proj = db_mgr.create_project(
        name="Proyecto Importado",
        base_path=str(temp_workspace),
        okf_alignment_status="pending_bootstrap"
    )
    p_uuid = proj["project_uuid"]

    # Simular que el code_repo_path apunta a nuestro mock_codebase
    with db_mgr._write_lock, db_mgr.get_connection() as conn:
        with conn:
            conn.execute(
                "UPDATE projects SET code_repo_path = ? WHERE project_uuid = ?;",
                (str(mock_codebase), p_uuid)
            )

    # 1. MCP Tool: scan_and_bootstrap_okf
    handler = MCPToolsHandler(db_manager=db_mgr)
    scan_json = handler.scan_and_bootstrap_okf(p_uuid)
    scan_data = json.loads(scan_json)

    assert scan_data["success"] is True
    assert scan_data["status"] == "pending_bootstrap"
    assert scan_data["total_adrs_extracted"] >= 4
    assert "# 📄 Dossier Unificado de Alineación Inicial OKF v2" in scan_data["dossier_markdown"]

    # 2. MCP Tool: get_alignment_dossier
    dossier_json = handler.get_alignment_dossier(p_uuid)
    dossier_data = json.loads(dossier_json)
    assert dossier_data["success"] is True
    assert dossier_data["alignment_status"] == "pending_bootstrap"

    # 3. Ratificación HITL mediante ApiBridge (Simulando firma del usuario)
    bridge = ApiBridge(db_manager=db_mgr)
    ratify_res = bridge.ratify_okf_bootstrap(p_uuid, comment="Alineación aprobada tras contraste con Claude Pro.")
    assert ratify_res["success"] is True
    assert ratify_res["new_status"] == "aligned_okf"
    assert ratify_res["backlog_cards_created"] >= 1

    # Verificar que el proyecto en BD pasó a aligned_okf
    updated_proj = db_mgr.get_project_by_uuid(p_uuid)
    assert updated_proj["okf_alignment_status"] == "aligned_okf"

    # 4. MCP Tool: audit_okf_drift en modo alineado
    drift_json = handler.audit_okf_drift(p_uuid)
    drift_data = json.loads(drift_json)
    assert drift_data["success"] is True
    assert "drift_report" in drift_data
    assert drift_data["drift_report"]["is_aligned"] is True
