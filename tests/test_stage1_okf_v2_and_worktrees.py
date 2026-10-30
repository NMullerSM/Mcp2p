"""
Pruebas exhaustivas para Etapa 1: OKF v2, Dual-Branch Git Worktrees,
Sincronización bidireccional YAML <-> SQLite y Micro-scripts ("Manos").
"""

import json
import subprocess
from pathlib import Path
import pytest
import yaml

from src.storage.db import DatabaseManager
from src.storage.okf_adapter import OKFAdapter, OKFValidationError


@pytest.fixture
def temp_workspace(tmp_path):
    """Crea una estructura de directorios temporal para pruebas."""
    db_path = tmp_path / "metadata.db"
    db_mgr = DatabaseManager(str(db_path))
    okf = OKFAdapter()
    return {
        "db": db_mgr,
        "okf": okf,
        "root": tmp_path
    }


def test_dual_branch_initialization_no_collision(temp_workspace):
    """
    Verifica que al inicializar un proyecto:
    1. app_repo tiene main inicializado y limpio.
    2. gov_repo tiene main como rama activa con commit OKF.
    3. La rama huérfana está desacoplada en .git/orphan_worktree y no colisiona con main.
    4. git status en gov_repo está 100% limpio (zero dirty state).
    """
    db = temp_workspace["db"]
    root = temp_workspace["root"]
    
    proj = db.create_project(
        name="Plataforma P2P Test",
        base_path=str(root / "ws_test"),
        init_git=True
    )
    p_uuid = proj["project_uuid"]
    app_dir = Path(proj["code_repo_path"])
    gov_dir = Path(proj["gov_repo_path"])

    assert app_dir.exists()
    assert gov_dir.exists()

    # Verificar que app_dir está en main
    res_app = subprocess.run(["git", "branch", "--show-current"], cwd=str(app_dir), capture_output=True, text=True)
    assert res_app.stdout.strip() == "main"

    # Verificar que gov_dir está en main y NO en la rama huérfana
    res_gov = subprocess.run(["git", "branch", "--show-current"], cwd=str(gov_dir), capture_output=True, text=True)
    assert res_gov.stdout.strip() == "main"

    # Verificar que git status en gov_dir está limpio
    res_status = subprocess.run(["git", "status", "--porcelain"], cwd=str(gov_dir), capture_output=True, text=True)
    assert res_status.stdout.strip() == ""

    # Verificar existencia y aislamiento del worktree de la rama huérfana
    orphan_wt = gov_dir / ".git" / "orphan_worktree"
    assert orphan_wt.exists()
    res_orphan = subprocess.run(["git", "branch", "--show-current"], cwd=str(orphan_wt), capture_output=True, text=True)
    assert res_orphan.stdout.strip() == f"orphan-conversa-memory-{p_uuid}"

    # Verificar archivo README_CONVERSA.md en el worktree huérfano
    assert (orphan_wt / "README_CONVERSA.md").exists()


def test_kanban_yaml_sqlite_bidirectional_sync(temp_workspace):
    """
    Verifica la sincronización bidireccional entre YAML y SQLite:
    1. Crear tarjeta en SQLite genera archivo YAML en kanban/cards/.
    2. Actualizar tarjeta en SQLite actualiza archivo YAML.
    3. Reconciliación desde OKF restaura/actualiza tarjetas en SQLite sin pérdida de atributos.
    4. Marcar tarjeta como discarded la mueve a kanban/discarded/ y elimina de cards/.
    """
    db = temp_workspace["db"]
    okf = temp_workspace["okf"]
    root = temp_workspace["root"]

    proj = db.create_project(name="Kanban Sync Project", base_path=str(root / "ws_kanban"), init_git=True)
    p_uuid = proj["project_uuid"]
    gov_dir = Path(proj["gov_repo_path"])

    # 1. Crear tarjeta en DB
    card = db.create_card(
        project_uuid=p_uuid,
        card_id="TASK-101",
        title="Implementar Capa Criptográfica",
        description="Firma Ed25519 estricta",
        acceptance_criteria="Pasa 100% pruebas de verificación"
    )
    assert card["status"] == "backlog"

    # Verificar creación de YAML (respetando docs/okf o raíz del repo)
    docs_okf = gov_dir / "docs" / "okf" if (gov_dir / "docs" / "okf").exists() else gov_dir
    yaml_file = docs_okf / "kanban" / "cards" / "TASK-101.yaml"
    assert yaml_file.exists()
    content = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    assert content["id"] == "TASK-101"
    assert content["status"] == "backlog"
    assert content["title"] == "Implementar Capa Criptográfica"

    # Registrar nodo para evitar falla de foreign key en SQLite
    db.register_or_update_node(
        node_uuid="node_alpha",
        developer_name="Alfa Dev",
        agent_id="agent_alpha"
    )

    # 2. Reclamar lease (cambia estado a in_progress)
    db.claim_card_lease(project_uuid=p_uuid, card_id="TASK-101", node_uuid="node_alpha", duration_minutes=45)
    content = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    assert content["status"] == "in_progress"
    assert content["assignee_node_uuid"] == "node_alpha"
    assert content["lease_expires_at"] is not None

    # 3. Mover a discarded
    db.update_card_status(project_uuid=p_uuid, card_id="TASK-101", new_status="discarded")
    assert not yaml_file.exists()
    discarded_file = docs_okf / "kanban" / "discarded" / "TASK-101.yaml"
    assert discarded_file.exists()
    disc_content = yaml.safe_load(discarded_file.read_text(encoding="utf-8"))
    assert disc_content["status"] == "discarded"

    # 4. Modificar manualmente un YAML externo y reconciliar hacia SQLite
    external_card_file = docs_okf / "kanban" / "cards" / "TASK-202.yaml"
    external_card_file.write_text(yaml.safe_dump({
        "id": "TASK-202",
        "project_uuid": p_uuid,
        "title": "Configurar P2P Handshake",
        "status": "in_review",
        "description": "Handshake vía WebSockets LAN",
        "acceptance_criteria": "Descubrimiento mDNS funcionando",
        "related_ids": ["DEC-002", "RNF-02"]
    }), encoding="utf-8")

    rec_result = okf.reconcile_kanban_from_okf(gov_dir, p_uuid, db)
    assert rec_result["cards_loaded"] >= 1

    card_202 = db.get_card_by_id(p_uuid, "TASK-202")
    assert card_202 is not None
    assert card_202["status"] == "in_review"
    assert card_202["title"] == "Configurar P2P Handshake"
    assert "DEC-002" in json.loads(card_202["related_ids_json"])


def test_decision_to_adr_and_discarded_promotion(temp_workspace):
    """
    Verifica el flujo de decisiones técnicas:
    - Crear DEC-010 en decisions/
    - Promover DEC-010 a ADR-010 en adrs/
    - Promover DEC-011 a DIS-011 en discarded/
    """
    okf = temp_workspace["okf"]
    root = temp_workspace["root"]
    gov_dir = root / "gov_decisions"
    gov_dir.mkdir(parents=True)
    okf.initialize_project_okf(gov_dir, "Decisions Project", "proj_dec_test")
    docs_okf = gov_dir / "docs" / "okf" if (gov_dir / "docs" / "okf").exists() else gov_dir

    # 1. Crear DEC-010
    dec_path = okf.scaffold_okf_artifact(
        gov_base_path=gov_dir,
        artifact_type="decision",
        item_id="DEC-010",
        title="Uso de SQLite WAL como Cache de Consultas",
        content="Se evalúa el rendimiento de lectura de SQLite frente a escaneo directo de YAML.",
        related_ids=["RNF-01"]
    )
    assert dec_path.exists()
    assert (docs_okf / "architecture" / "decisions" / dec_path.name).exists()

    # 2. Promover a ADR
    adr_path = okf.move_decision_lifecycle(
        gov_base_path=gov_dir,
        dec_id="DEC-010",
        target_state="approved",
        reason_or_consequences="Aprobado por consenso unánime del enjambre."
    )
    assert adr_path.exists()
    assert "ADR-" in adr_path.name
    assert not (docs_okf / "architecture" / "decisions" / dec_path.name).exists()
    assert "Aprobado por consenso" in adr_path.read_text(encoding="utf-8")

    # 3. Crear DEC-011 y descartar a discarded/
    dec11_path = okf.scaffold_okf_artifact(
        gov_base_path=gov_dir,
        artifact_type="decision",
        item_id="DEC-011",
        title="Uso de MongoDB Local",
        content="Propuesta de motor NoSQL local para las tarjetas.",
        related_ids=[]
    )
    dis_path = okf.move_decision_lifecycle(
        gov_base_path=gov_dir,
        dec_id="DEC-011",
        target_state="discarded",
        reason_or_consequences="Descartado por requerir un runtime de 200MB adicional."
    )
    assert dis_path.exists()
    assert "DIS-" in dis_path.name
    assert not (docs_okf / "architecture" / "decisions" / dec11_path.name).exists()


def test_worktree_creation_and_scope_collision(temp_workspace):
    """
    Verifica la gestión de Worktrees por tarea:
    - Crear worktree para tarea TASK-100.
    - Prevenir colisión de alcance (scope overlap) entre distintos agentes.
    - Remover worktree de forma limpia una vez finalizado.
    """
    db = temp_workspace["db"]
    okf = temp_workspace["okf"]
    root = temp_workspace["root"]

    proj = db.create_project(name="Worktree Project", base_path=str(root / "ws_wt"), init_git=True)
    p_uuid = proj["project_uuid"]
    app_dir = Path(proj["code_repo_path"])
    gov_dir = Path(proj["gov_repo_path"])

    # 1. Crear worktree para TASK-100 con scope en src/storage/
    res1 = okf.manage_task_worktree(
        code_repo_path=app_dir,
        gov_base_path=gov_dir,
        project_uuid=p_uuid,
        card_id="TASK-100",
        node_uuid="node_alpha",
        action="create",
        files_scope=["src/storage/*"]
    )
    assert res1["success"] is True
    wt_path = Path(res1["worktree_path"])
    assert wt_path.exists()

    # 2. Intentar crear otro worktree por otro nodo con alcance en conflicto -> Debe fallar por colisión
    res_collision = okf.manage_task_worktree(
        code_repo_path=app_dir,
        gov_base_path=gov_dir,
        project_uuid=p_uuid,
        card_id="TASK-101",
        node_uuid="node_beta",
        action="create",
        files_scope=["src/storage/db.py"]
    )
    assert res_collision["success"] is False
    assert res_collision.get("collision") is True

    # 3. Remover worktree
    res_remove = okf.manage_task_worktree(
        code_repo_path=app_dir,
        gov_base_path=gov_dir,
        project_uuid=p_uuid,
        card_id="TASK-100",
        node_uuid="node_alpha",
        action="remove"
    )
    assert res_remove["success"] is True
    assert not wt_path.exists()
