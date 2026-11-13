"""
Pruebas unitarias para el puente JavaScript API (src/app/api_bridge.py).
"""

import os
import platform
import tempfile
from pathlib import Path
import pytest
from src.storage.db import DatabaseManager
from src.app.api_bridge import ApiBridge
from src.p2p.crypto import NodeCryptoManager
from conftest import make_test_db, register_tmp_dir


@pytest.fixture
def bridge():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_bridge.db"
        db = DatabaseManager(str(db_path))
        bridge_instance = ApiBridge(db_manager=db)
        yield bridge_instance


def two_linked_bridges():
    """
    Crea dos instancias de ApiBridge con bases de datos SQLite y claves
    criptográficas Ed25519 completamente aisladas entre sí, simulando dos
    nodos P2P distintos: el propietario del proyecto (bridge_owner) y un
    solicitante que intentará unirse (bridge_joiner).

    Modelado sobre el patrón existente en
    tests/test_iterA_collab_multi_account.py:168-176 (dos DatabaseManager +
    ApiBridge independientes en directorios temporales separados).
    """
    bridge_owner = ApiBridge(db_manager=make_test_db())
    bridge_joiner = ApiBridge(db_manager=make_test_db())

    owner_key_dir = tempfile.mkdtemp()
    joiner_key_dir = tempfile.mkdtemp()
    register_tmp_dir(owner_key_dir)
    register_tmp_dir(joiner_key_dir)
    bridge_owner.crypto = NodeCryptoManager(key_path=Path(owner_key_dir) / "owner_identity.key")
    bridge_joiner.crypto = NodeCryptoManager(key_path=Path(joiner_key_dir) / "joiner_identity.key")

    proj = bridge_owner.create_project(name="LinkedProject")["project"]
    bridge_owner.active_project_uuid = proj["project_uuid"]

    return bridge_owner, bridge_joiner


def test_api_bridge_system_info(bridge):
    """Verifica que el puente retorne la información del sistema y del proyecto activo."""
    info = bridge.get_system_info()
    assert info["success"] is True
    assert "os" in info
    assert info["wal_mode"] is True
    assert info["active_project"] is not None


def test_api_bridge_project_lifecycle(bridge):
    """Verifica la creación y selección de proyectos."""
    create_res = bridge.create_project(name="Microservicio Auth")
    assert create_res["success"] is True
    assert create_res["project"]["name"] == "Microservicio Auth"

    projects_res = bridge.list_projects()
    assert projects_res["success"] is True
    assert len(projects_res["projects"]) >= 2


def test_api_bridge_kanban_flow(bridge):
    """Verifica creación, consulta y actualización de tarjetas a través del puente."""
    # 1. Crear tarjeta en proyecto activo
    create_res = bridge.create_kanban_card("CARD-100", "Probar Bridge", "Prueba de integración")
    assert create_res["success"] is True
    assert create_res["card"]["card_id"] == "CARD-100"

    # 2. Consultar tablero
    board_res = bridge.get_kanban_board()
    assert board_res["success"] is True
    assert len(board_res["cards"]) == 1

    # 3. Adquirir lease
    node_id = bridge.get_system_info()["active_node"]
    claim_res = bridge.claim_task_lease("CARD-100", node_id, duration_minutes=20)
    assert claim_res["success"] is True
    assert claim_res["card"]["status"] == "in_progress"

    # 4. Actualizar estado
    update_res = bridge.update_task_status("CARD-100", "in_review", branch_ref="feature/bridge-test")
    assert update_res["success"] is True
    assert update_res["card"]["status"] == "in_review"


def test_api_bridge_a2a_and_memory(bridge):
    """Verifica señales A2A y persistencia de memoria huérfana vía ApiBridge."""
    # 1. Enviar señal informativa A2A
    sig_res = bridge.send_a2a_signal(
        signal_type="METRIC_PULSE",
        data={"memory_mb": 42, "cpu_pct": 5.0}
    )
    assert sig_res["success"] is True
    assert sig_res["packet"]["signal_type"] == "METRIC_PULSE"

    # 2. Consultar feed A2A
    feed_res = bridge.get_a2a_feed()
    assert feed_res["success"] is True
    assert len(feed_res["messages"]) >= 1

    # 3. Snapshot de memoria en rama huérfana
    snap_res = bridge.write_memory_snapshot({
        "summary": "Sesión iniciada con éxito",
        "turn": 1
    })
    assert snap_res["success"] is True

    read_res = bridge.read_memory_snapshot()
    assert read_res["success"] is True
    assert read_res["snapshot"]["summary"] == "Sesión iniciada con éxito"


def test_api_bridge_p2p_invite_and_join_flow(bridge):
    """Verifica la generación de enlace P2P y la conexión automática con replicación de UUID."""
    # 1. Crear proyecto inicial
    p_orig = bridge.create_project(name="Proyecto Distribuidor")
    assert p_orig["success"] is True
    orig_uuid = p_orig["project"]["project_uuid"]

    # 2. Generar invitación P2P
    invite_res = bridge.generate_p2p_invite(orig_uuid)
    assert invite_res["success"] is True
    assert "mcp2p://join?token=" in invite_res["invite_url"]
    assert invite_res["token"]

    # 3. Unirse al proyecto desde otra "instancia" o contexto
    join_res = bridge.join_p2p_project(invite_res["invite_url"])
    assert join_res["success"] is True
    assert join_res["project"]["project_uuid"] == orig_uuid

    # 4. Eliminar proyecto
    del_res = bridge.delete_project(orig_uuid)
    assert del_res["success"] is True
    # Confirmar que ya no existe
    proj_check = bridge.db.get_project_by_uuid(orig_uuid)
    assert proj_check is None


def test_api_bridge_github_pat_storage(bridge, monkeypatch):
    """Verifica el flujo dual de GitHub (PAT y gh CLI fallback)."""
    # 1. Sin PAT configurado y simulando sin gh cli
    bridge.github.gh_bin = None
    status = bridge.get_github_status()
    assert status["success"] is True

    # 2. Guardar PAT simulando respuesta exitosa de GitHub API
    from unittest.mock import MagicMock
    import io
    import urllib.request

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"login": "octodev", "name": "Octo Developer"}'
    mock_resp.__enter__.return_value = mock_resp

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=8: mock_resp)

    save_res = bridge.save_github_pat("ghp_test1234567890abcdef")
    assert save_res["success"] is True
    assert save_res["username"] == "octodev"

    # Verificar que el status reporta PAT
    status_pat = bridge.get_github_status()
    assert status_pat["success"] is True
    assert status_pat["github"]["authenticated"] is True
    assert status_pat["github"]["provider"] == "pat"
    assert status_pat["github"]["username"] == "octodev"

    # 3. Eliminar PAT
    clear_res = bridge.clear_github_pat()
    assert clear_res["success"] is True
    status_cleared = bridge.get_github_status()
    assert status_cleared["github"]["provider"] != "pat"


def test_api_bridge_okf_repair(bridge):
    """Verifica que repair_project_okf restaure la estructura conforme."""
    p_res = bridge.create_project(name="Proyecto Auto Repair")
    p_uuid = p_res["project"]["project_uuid"]

    repair_res = bridge.repair_project_okf(p_uuid)
    assert repair_res["success"] is True
    assert repair_res["report"]["valid"] is True


def test_api_bridge_branches_and_consensus_and_okf_tree(bridge):
    """Verifica la consulta de ramas Git, sesiones de consenso y árbol OKF."""
    p_res = bridge.create_project(name="Proyecto Git & Consenso")
    p_uuid = p_res["project"]["project_uuid"]

    # 1. Consultar ramas y consenso
    bc_res = bridge.get_branches_and_consensus(p_uuid)
    assert bc_res["success"] is True
    assert "git" in bc_res
    assert "sessions" in bc_res

    # 2. Consultar árbol de archivos OKF
    tree_res = bridge.get_okf_raw_tree(p_uuid)
    assert tree_res["success"] is True
    assert len(tree_res["tree"]) >= 2
    assert any("01_ARQUITECTURA_Y_GOBERNANZA.md" in f["path"] for f in tree_res["tree"])

    # 3. Leer archivo OKF
    file_res = bridge.read_okf_file(p_uuid, "01_ARQUITECTURA_Y_GOBERNANZA.md")
    assert file_res["success"] is True
    assert "OKF-01" in file_res["content"]

    # 4. Actualizar tarjeta con criterios de aceptación
    card_res = bridge.create_kanban_card("TASK-TEST-CRIT", "Tarea con Criterios", "Desc")
    assert card_res["success"] is True

    update_res = bridge.update_kanban_card_details(
        card_id="TASK-TEST-CRIT",
        title="Tarea con Criterios Actualizada",
        acceptance_criteria="1. Tests pasando\n2. Linting OK",
        status="in_review"
    )
    assert update_res["success"] is True
    assert update_res["card"]["acceptance_criteria"] == "1. Tests pasando\n2. Linting OK"
    assert update_res["card"]["status"] == "in_review"


def test_api_bridge_remote_url_and_mcp_agents_status(bridge):
    """Verifica configuración de remoto GitHub y consulta de estado MCP / agentes."""
    p_res = bridge.create_project(name="Proyecto Remoto y MCP")
    p_uuid = p_res["project"]["project_uuid"]

    # 1. Push sin remoto debe indicar requires_remote amigablemente
    push_res = bridge.git_push_action(p_uuid, branch="main")
    assert push_res["success"] is False
    assert push_res.get("requires_remote") is True

    # 2. Configurar remoto origin
    remote_res = bridge.set_project_remote_url(p_uuid, "https://github.com/testuser/testrepo.git")
    assert remote_res["success"] is True
    assert remote_res["url"] == "https://github.com/testuser/testrepo.git"

    # 3. Comprobar que get_branches_and_consensus reporta origin_url
    bc = bridge.get_branches_and_consensus(p_uuid)
    assert bc["success"] is True
    assert bc["git"]["origin_url"] == "https://github.com/testuser/testrepo.git"

    # 4. Comprobar pasarela MCP y agentes conectados
    mcp_res = bridge.get_mcp_and_agents_status(p_uuid)
    assert mcp_res["success"] is True
    assert mcp_res["mcp_server"]["server_name"] == "mcp2p-server"
    assert mcp_res["mcp_server"]["tools_count"] == 10
    assert len(mcp_res["connected_agents"]) >= 1
    assert any(ag["agent_id"] == "agent_local_developer" for ag in mcp_res["connected_agents"])


def test_project_sync_pulse_and_hooks(bridge):
    """Verifica el cálculo del pulso atómico y la detección reactiva de cambios."""
    p_res = bridge.create_project(name="Proyecto Pulse Test")
    p_uuid = p_res["project"]["project_uuid"]

    # 1. Pulso inicial
    pulse1 = bridge.get_project_sync_pulse(p_uuid)
    assert pulse1["success"] is True
    assert "fingerprint" in pulse1
    fp1 = pulse1["fingerprint"]

    # 2. Modificar tarjetas Kanban
    bridge.create_kanban_card("TASK-PULSE-1", "Tarea Pulso", "Desc")
    pulse2 = bridge.get_project_sync_pulse(p_uuid)
    fp2 = pulse2["fingerprint"]

    assert fp1 != fp2
    assert pulse2["cards_fingerprint"] != pulse1["cards_fingerprint"]

    # 3. Disparar evento de pulso Git
    proj = bridge.db.get_project_by_uuid(p_uuid)
    app_pulse_file = Path(proj["code_repo_path"]) / ".git_pulse"
    app_pulse_file.write_text("pulse")

    pulse3 = bridge.get_project_sync_pulse(p_uuid)
    fp3 = pulse3["fingerprint"]
    assert fp3 != fp2
    assert pulse3["git_fingerprint"] != pulse2["git_fingerprint"]


def test_generate_p2p_invite_includes_rendezvous_nonce():
    bridge = ApiBridge(db_manager=make_test_db())
    proj = bridge.db.create_project(name="p", project_uuid="proj_x")
    bridge.active_project_uuid = "proj_x"
    res = bridge.generate_p2p_invite("proj_x")
    is_valid, payload, _ = bridge.crypto.decode_and_verify_invite_token(res["token"])
    assert is_valid
    assert len(payload["rendezvous_nonce"]) == 16


def test_signaling_url_defaults_to_empty():
    bridge = ApiBridge(db_manager=make_test_db())
    assert bridge.get_signaling_server_url() == ""


def test_set_signaling_server_url_persists_and_validates():
    bridge = ApiBridge(db_manager=make_test_db())
    ok = bridge.set_signaling_server_url("wss://relay.example.com/signal")
    assert ok["success"] is True
    assert bridge.get_signaling_server_url() == "wss://relay.example.com/signal"
    bad = bridge.set_signaling_server_url("https://not-a-ws-url.example.com")
    assert bad["success"] is False


def test_join_creates_pending_stub_without_cloning(monkeypatch):
    bridge_owner, bridge_joiner = two_linked_bridges()
    invite = bridge_owner.generate_p2p_invite()["token"]
    res = bridge_joiner.join_p2p_project(invite)
    assert res["success"] is True
    assert res["status"] == "pending_approval"
    assert "request_code" in res
    proj = bridge_joiner.db.get_project_by_uuid(res["project"]["project_uuid"])
    assert proj["join_status"] == "pending_approval"
    assert proj["code_repo_path"] == "" or not Path(proj["code_repo_path"]).exists()


def test_join_request_code_carries_requester_identity():
    bridge_owner, bridge_joiner = two_linked_bridges()
    invite = bridge_owner.generate_p2p_invite()["token"]
    res = bridge_joiner.join_p2p_project(invite)
    is_valid, payload, _ = bridge_joiner.crypto.decode_and_verify_invite_token(res["request_code"])
    assert is_valid
    assert payload["public_key"] == bridge_joiner.crypto.public_key_base64


def test_join_blocked_by_rate_limit_returns_locked_status():
    bridge_owner, bridge_joiner = two_linked_bridges()
    invite = bridge_owner.generate_p2p_invite()["token"]
    bridge_joiner.join_p2p_project(invite)
    p_uuid = bridge_joiner.active_project_uuid
    node_uuid = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
    for _ in range(3):
        bridge_joiner.db.record_rejection(p_uuid, node_uuid)
    res2 = bridge_joiner.join_p2p_project(invite)
    assert res2["success"] is False
    assert res2["status"] == "locked"


def test_join_stub_persists_invite_repo_urls_without_cloning():
    """
    Review finding (Important #1): la fila stub de proyecto debe conservar las URLs de
    repos remotos de la invitación -- solo las RUTAS locales (code_repo_path/gov_repo_path)
    deben quedar vacías, ya que nadie más persiste esas URLs (ni el token original, ni
    request_code) y Task 7's retry_p2p_join las necesitará para clonar tras la aprobación.
    """
    bridge_owner, bridge_joiner = two_linked_bridges()
    p_uuid = bridge_owner.active_project_uuid
    with bridge_owner.db._write_lock, bridge_owner.db.get_connection() as conn:
        with conn:
            conn.execute(
                "UPDATE projects SET code_repo_url = ?, gov_repo_url = ? WHERE project_uuid = ?;",
                ("https://github.com/owner/app.git", "https://github.com/owner/gov.git", p_uuid)
            )

    invite = bridge_owner.generate_p2p_invite()["token"]
    res = bridge_joiner.join_p2p_project(invite)
    assert res["success"] is True

    stub = bridge_joiner.db.get_project_by_uuid(res["project"]["project_uuid"])
    assert stub["code_repo_url"] == "https://github.com/owner/app.git"
    assert stub["gov_repo_url"] == "https://github.com/owner/gov.git"
    assert stub["code_repo_path"] == ""
    assert stub["gov_repo_path"] == ""


def test_owner_collaborator_row_keeps_real_pubkey_and_guards_against_collision():
    """
    Review finding (Important #2): db.create_project() nunca registraba la clave pública
    real del dueño en su propia fila de colaborador (quedaba vacía), por lo que una
    solicitud de unión posterior que reutilizara por casualidad el mismo node_uuid por
    defecto (derivado del hostname -- exactamente el escenario que two_linked_bridges()
    expone al correr ambos nodos en el mismo host) podía sobrescribir/degradar la fila del
    dueño sin ningún error. Con la clave real persistida, el guard de colisión de
    add_or_request_collaborator (Tarea 1) debe disparar un ValueError ruidoso en ese caso,
    en lugar de una degradación silenciosa.
    """
    bridge_owner, _ = two_linked_bridges()
    p_uuid = bridge_owner.active_project_uuid
    owner_node_uuid = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")

    owner_row = bridge_owner.db.get_collaborator(p_uuid, owner_node_uuid)
    assert owner_row is not None
    assert owner_row["public_key"] == bridge_owner.crypto.public_key_base64
    assert owner_row["public_key"] != ""
    assert owner_row["status"] == "approved"

    with pytest.raises(ValueError):
        bridge_owner.db.add_or_request_collaborator(
            project_uuid=p_uuid,
            node_uuid=owner_node_uuid,
            public_key="some_other_requester_pubkey_different_from_owner",
            status="pending_approval"
        )

    # La fila del dueño no debe haber sido alterada por el intento rechazado.
    owner_row_after = bridge_owner.db.get_collaborator(p_uuid, owner_node_uuid)
    assert owner_row_after["public_key"] == bridge_owner.crypto.public_key_base64
    assert owner_row_after["status"] == "approved"


# ==========================
# ingest_join_request (Tarea 6: intake manual del lado del dueño)
# ==========================

def invite_project_uuid(invite_token: str) -> str:
    """Decodifica un token de invitación/solicitud P2P firmado y devuelve su project_uuid,
    sin necesitar una instancia de ApiBridge concreta (decode_and_verify_invite_token es
    un classmethod autocontenido: la clave pública del firmante viaja en el propio token)."""
    _, payload, _ = NodeCryptoManager.decode_and_verify_invite_token(invite_token)
    return payload["project_uuid"]


def guess_joiner_node_uuid(bridge_owner, bridge_joiner):
    """
    "Adivina" bajo qué node_uuid terminó almacenada, en la base de datos del dueño, la
    fila de colaborador/solicitud del solicitante (bridge_joiner).

    No es simplemente el node_uuid local por defecto del solicitante: en los tests,
    bridge_owner y bridge_joiner corren en el mismo host sin MCP2P_NODE_UUID, por lo que
    comparten ese valor por defecto -- exactamente el escenario de colisión que
    ingest_join_request debe desambiguar (ver Tarea 5, líneas 332-348 de api_bridge.py,
    y el Review finding de Tarea 5 sobre la clave pública real del dueño). Por eso la
    única forma fiable de ubicar la fila es buscarla por la clave pública persistente del
    solicitante -- la misma identidad canónica que usa get_join_rate_limit_state -- y leer
    su node_uuid efectivo.
    """
    row = bridge_owner.db.get_collaborator_by_pubkey(
        bridge_owner.active_project_uuid, bridge_joiner.crypto.public_key_base64
    )
    assert row is not None, "No se encontró la fila de solicitud del joiner en la BD del dueño"
    return row["node_uuid"]


def test_ingest_join_request_creates_pending_row():
    bridge_owner, bridge_joiner = two_linked_bridges()
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    bridge_owner.active_project_uuid = invite_project_uuid(invite)
    res = bridge_owner.ingest_join_request(join_res["request_code"])
    assert res["success"] is True
    collabs = bridge_owner.db.get_project_collaborators(bridge_owner.active_project_uuid)
    assert any(c["status"] == "pending_approval" for c in collabs)


def test_ingest_join_request_rejects_when_locked():
    bridge_owner, bridge_joiner = two_linked_bridges()
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    bridge_owner.active_project_uuid = invite_project_uuid(invite)
    bridge_owner.ingest_join_request(join_res["request_code"])
    node_uuid = guess_joiner_node_uuid(bridge_owner, bridge_joiner)
    for _ in range(3):
        bridge_owner.db.record_rejection(bridge_owner.active_project_uuid, node_uuid)
    res2 = bridge_owner.ingest_join_request(join_res["request_code"])
    assert res2["success"] is False
    assert res2["status"] == "locked"


def test_ingest_join_request_twice_does_not_duplicate_row_or_attempt_count():
    bridge_owner, bridge_joiner = two_linked_bridges()
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    bridge_owner.active_project_uuid = invite_project_uuid(invite)
    bridge_owner.ingest_join_request(join_res["request_code"])
    bridge_owner.ingest_join_request(join_res["request_code"])
    collabs = bridge_owner.db.get_project_collaborators(bridge_owner.active_project_uuid)
    pending = [c for c in collabs if c["status"] == "pending_approval"]
    assert len(pending) == 1
    assert pending[0]["request_window_count"] <= 1
