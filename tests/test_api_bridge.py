"""
Pruebas unitarias para el puente JavaScript API (src/app/api_bridge.py).
"""

import os
import platform
import subprocess
import tempfile
from pathlib import Path
import pytest
from src.storage.db import DatabaseManager
from src.app.api_bridge import ApiBridge
from src.p2p.crypto import NodeCryptoManager
from src.p2p.transport import NullTransportAdapter
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
    join_res = bridge_joiner.join_p2p_project(invite)
    # Hallazgo C2: el stub pendiente ya no pasa a ser el proyecto activo del solicitante.
    p_uuid = join_res["project"]["project_uuid"]
    node_uuid = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
    for _ in range(3):
        bridge_joiner.db.record_rejection(p_uuid, node_uuid)
    res2 = bridge_joiner.join_p2p_project(invite)
    assert res2["success"] is False
    assert res2["status"] == "locked"


def test_join_stub_persists_invite_repo_urls_without_cloning():
    """
    Review finding (Important #1): la fila stub de proyecto debe conservar las URLs de
    repos remotos de la invitación, ya que nadie más persiste esas URLs (ni el token
    original, ni request_code) y Task 7's retry_p2p_join las necesitará para clonar tras
    la aprobación.

    Hallazgo C2 de la revisión final: las RUTAS locales ya NO quedan vacías (una ruta
    vacía resolvía al directorio de trabajo del proceso); son las rutas absolutas FUTURAS
    donde retry_p2p_join clonará, y todavía no existen en disco.
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
    for key in ("code_repo_path", "gov_repo_path"):
        assert stub[key] != ""
        assert Path(stub[key]).is_absolute()
        assert not Path(stub[key]).exists()
        assert Path(stub[key]).resolve() != Path.cwd().resolve()


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


def test_ingest_join_request_rejects_code_for_a_different_project_than_the_active_one():
    """
    Hallazgo diferido de la Tarea 6 (cerrado junto con C1 en la revisión final): un
    request_code generado para el proyecto X, pegado mientras el proyecto Y es el activo
    del dueño, debe rechazarse con un error limpio -- nunca registrar en silencio al
    solicitante en el proyecto equivocado (ni en X, que no es el destino de la llamada).
    """
    bridge_owner, bridge_joiner = two_linked_bridges()
    project_x = bridge_owner.active_project_uuid
    invite_x = bridge_owner.generate_p2p_invite(project_x)["token"]
    join_res = bridge_joiner.join_p2p_project(invite_x)
    assert invite_project_uuid(join_res["request_code"]) == project_x

    project_y = bridge_owner.create_project(name="OtherProject")["project"]["project_uuid"]
    assert project_y != project_x
    bridge_owner.active_project_uuid = project_y
    joiner_pk = bridge_joiner.crypto.public_key_base64
    y_rows_before = bridge_owner.db.get_project_collaborators(project_y)

    res = bridge_owner.ingest_join_request(join_res["request_code"])
    assert res["success"] is False
    assert res.get("error")
    assert res.get("status") != "pending_approval"

    assert bridge_owner.db.get_collaborator_by_pubkey(project_y, joiner_pk) is None
    assert bridge_owner.db.get_collaborator_by_pubkey(project_x, joiner_pk) is None
    assert bridge_owner.db.get_project_collaborators(project_y) == y_rows_before


def test_ingest_join_request_explicit_project_uuid_targets_that_project_and_still_enforces_match():
    """
    C1: CollaboratorsView pasa explícitamente el project_uuid del proyecto que el dueño
    tiene abierto. Ese destino explícito sustituye a active_project_uuid, pero la
    comprobación contra el project_uuid FIRMADO en el código sigue aplicando.
    """
    bridge_owner, bridge_joiner = two_linked_bridges()
    project_x = bridge_owner.active_project_uuid
    invite_x = bridge_owner.generate_p2p_invite(project_x)["token"]
    join_res = bridge_joiner.join_p2p_project(invite_x)
    project_y = bridge_owner.create_project(name="OtherProject")["project"]["project_uuid"]
    joiner_pk = bridge_joiner.crypto.public_key_base64

    # Activo = X, destino explícito = Y -> rechazado, nada registrado en ninguno.
    bridge_owner.active_project_uuid = project_x
    wrong = bridge_owner.ingest_join_request(join_res["request_code"], project_y)
    assert wrong["success"] is False
    assert bridge_owner.db.get_collaborator_by_pubkey(project_y, joiner_pk) is None
    assert bridge_owner.db.get_collaborator_by_pubkey(project_x, joiner_pk) is None

    # Activo = Y, destino explícito = X (el proyecto correcto) -> registrado en X.
    bridge_owner.active_project_uuid = project_y
    ok = bridge_owner.ingest_join_request(join_res["request_code"], project_x)
    assert ok["success"] is True
    assert ok["status"] == "pending_approval"
    assert bridge_owner.db.get_collaborator_by_pubkey(project_x, joiner_pk)["status"] == "pending_approval"
    assert bridge_owner.db.get_collaborator_by_pubkey(project_y, joiner_pk) is None


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
    # Tightened from `<= 1` (review finding, Important #4): record_join_attempt must run
    # AFTER the row is inserted, or the very first attempt silently fails to increment
    # the window counter (it's a no-op when no row yet exists for that public_key) --
    # `<= 1` would pass even when the true count is 0, which is how that bug slipped
    # through originally.
    assert pending[0]["request_window_count"] == 1


def test_ingest_join_request_leaves_owner_row_untouched_and_adds_exactly_one_pending_row():
    """
    Review finding (Important #6): una regresión que absorbiera/degradara la fila del
    dueño en lugar de insertar una fila NUEVA para el solicitante seguiría pasando las
    3 pruebas anteriores (que solo comprueban que *alguna* fila quedó pending_approval).
    Aquí se comprueba explícitamente que la fila del dueño permanece intacta y que el
    proyecto termina con exactamente 2 filas (dueño + solicitante), no 1.
    """
    bridge_owner, bridge_joiner = two_linked_bridges()
    p_uuid = bridge_owner.active_project_uuid
    owner_node_uuid = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
    owner_pubkey_before = bridge_owner.crypto.public_key_base64

    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    res = bridge_owner.ingest_join_request(join_res["request_code"])
    assert res["success"] is True

    owner_row = bridge_owner.db.get_collaborator(p_uuid, owner_node_uuid)
    assert owner_row is not None
    assert owner_row["role"] == "owner"
    assert owner_row["status"] == "approved"
    assert owner_row["public_key"] == owner_pubkey_before

    collabs = bridge_owner.db.get_project_collaborators(p_uuid)
    assert len(collabs) == 2
    pending = [c for c in collabs if c["status"] == "pending_approval"]
    assert len(pending) == 1
    assert pending[0]["public_key"] == bridge_joiner.crypto.public_key_base64


def test_ingest_join_request_rejects_payload_claiming_a_different_signers_identity():
    """
    Review finding (Critical #1): decode_and_verify_invite_token solo prueba que ALGUNA
    clave firmó el sobre -- nunca ata payload["public_key"] a esa clave firmante. Como la
    clave pública y el node_uuid del dueño son información pública (viajan en cada
    invitación como sender_pubkey/sender_node_uuid), un atacante podía firmar su PROPIO
    sobre con su PROPIA clave pero declarar en el payload la identidad del dueño,
    degradando su fila (role=owner/approved -> collaborator/pending_approval) exactamente
    como el bug que la Tarea 5 ya había corregido en otro punto de entrada.
    """
    bridge_owner, _ = two_linked_bridges()
    p_uuid = bridge_owner.active_project_uuid
    owner_node_uuid = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
    owner_pubkey_before = bridge_owner.crypto.public_key_base64

    attacker_key_dir = tempfile.mkdtemp()
    register_tmp_dir(attacker_key_dir)
    attacker_crypto = NodeCryptoManager(key_path=Path(attacker_key_dir) / "attacker_identity.key")

    forged_request_code = attacker_crypto.create_signed_invite_token({
        "project_uuid": p_uuid,
        "rendezvous_nonce": "",
        "github_username": "mallory",
        "public_key": owner_pubkey_before,  # clave del DUEÑO, pero firmado por el atacante
        "node_uuid": owner_node_uuid,
        "created_at": "2026-01-01T00:00:00+00:00",
    })

    res = bridge_owner.ingest_join_request(forged_request_code)
    assert res["success"] is False

    owner_row_after = bridge_owner.db.get_collaborator(p_uuid, owner_node_uuid)
    assert owner_row_after["role"] == "owner"
    assert owner_row_after["status"] == "approved"
    assert owner_row_after["public_key"] == owner_pubkey_before
    assert owner_row_after["github_username"] != "mallory"


def test_ingest_join_request_does_not_demote_an_already_approved_collaborator():
    """
    Review finding (Important #3): si la solicitud ya llegó en vivo por WebRTC y el
    dueño ya la aprobó, y el MISMO request_code llega después por la vía manual (p. ej.
    el solicitante lo pegó por si acaso, sin saber que ya fue aprobado en vivo), no debe
    revertir silenciosamente la aprobación a pending_approval.
    """
    bridge_owner, bridge_joiner = two_linked_bridges()
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    bridge_owner.active_project_uuid = invite_project_uuid(invite)

    first = bridge_owner.ingest_join_request(join_res["request_code"])
    assert first["success"] is True

    node_uuid = guess_joiner_node_uuid(bridge_owner, bridge_joiner)
    approved = bridge_owner.db.approve_collaborator(bridge_owner.active_project_uuid, node_uuid)
    assert approved is True

    row_before = bridge_owner.db.get_collaborator_by_pubkey(
        bridge_owner.active_project_uuid, bridge_joiner.crypto.public_key_base64
    )
    assert row_before["status"] == "approved"

    second = bridge_owner.ingest_join_request(join_res["request_code"])
    assert second["success"] is True
    assert second["status"] == "approved"

    row_after = bridge_owner.db.get_collaborator_by_pubkey(
        bridge_owner.active_project_uuid, bridge_joiner.crypto.public_key_base64
    )
    assert row_after["status"] == "approved"
    assert row_after["node_uuid"] == row_before["node_uuid"]


def test_ingest_join_request_does_not_demote_approved_row_when_same_pubkey_has_an_unrelated_rejected_row():
    """
    Review finding (Task 11, ronda 3): get_collaborator_by_pubkey's deterministic
    ORDER BY (added earlier in this fix round to stop rejection history from being
    hidden behind a clean duplicate row -- correct for get_join_rate_limit_state)
    introduced a REGRESSION in ingest_join_request's idempotency check if it reused
    that same query: a single public key can legitimately span multiple rows under
    different node_uuid values (one per device, per Tareas 5/6's disambiguation
    mechanism). Here PK ends up with an 'approved' row under node_A (rejection_count
    reset to 0 by approve_collaborator) AND an unrelated 'rejected' row under node_B
    (rejection_count=1). get_collaborator_by_pubkey's rejection-history-prioritizing
    ordering would return node_B (it has rejection history) instead of node_A (the
    real target of this request_code) -- so re-ingesting node_A's own request_code
    would wrongly conclude "unresolved" and silently demote the already-approved
    node_A row back to pending_approval. ingest_join_request must use the separate
    get_unresolved_or_approved_collaborator_by_pubkey query instead, which filters by
    status BEFORE ordering and so can never return node_B's rejected row here.
    """
    bridge_owner, bridge_joiner = two_linked_bridges()
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    p_uuid = invite_project_uuid(invite)
    bridge_owner.active_project_uuid = p_uuid

    first = bridge_owner.ingest_join_request(join_res["request_code"])
    assert first["success"] is True
    node_a = guess_joiner_node_uuid(bridge_owner, bridge_joiner)
    assert bridge_owner.db.approve_collaborator(p_uuid, node_a) is True

    public_key = bridge_joiner.crypto.public_key_base64
    # An unrelated rejected row for the SAME public key, under a DIFFERENT node_uuid --
    # reachable in reality via the same "retry from a different device" path exercised
    # by test_get_collaborator_by_pubkey_prefers_row_with_rejection_history_over_a_clean_duplicate
    # in tests/test_join_rate_limit.py.
    bridge_owner.db.add_or_request_collaborator(p_uuid, "node_B_other_device", public_key=public_key, status="pending_approval")
    bridge_owner.db.record_rejection(p_uuid, "node_B_other_device")

    # Sanity check reproducing the regression's precondition: the rate-limiting lookup
    # (unchanged, intentionally) now does return the rejected row, not the approved one.
    rate_lookup_row = bridge_owner.db.get_collaborator_by_pubkey(p_uuid, public_key)
    assert rate_lookup_row["node_uuid"] == "node_B_other_device"
    assert rate_lookup_row["status"] == "rejected"

    # Re-ingesting node_A's own request_code must still recognize it as already
    # approved and must NOT touch node_A's row.
    second = bridge_owner.ingest_join_request(join_res["request_code"])
    assert second["success"] is True
    assert second["status"] == "approved"

    node_a_row = bridge_owner.db.get_collaborator(p_uuid, node_a)
    assert node_a_row["status"] == "approved"
    node_b_row = bridge_owner.db.get_collaborator(p_uuid, "node_B_other_device")
    assert node_b_row["status"] == "rejected"
    assert node_b_row["rejection_count"] == 1


def test_ingest_join_request_disambiguates_collision_with_legacy_empty_pubkey_row():
    """
    Review finding (Critical #2): la desambiguación de node_uuid solo disparaba cuando la
    fila existente tenía una clave pública no vacía Y distinta. Una fila legada (de antes
    del fix de Tarea 5, o de cualquier otra vía que registre un colaborador sin clave
    pública) con la clave pública vacía quedaba fuera de esa condición -- y
    add_or_request_collaborator's propio guard de colisión TAMPOCO dispara sobre una
    clave existente vacía -- así que un solicitante legítimo con el mismo node_uuid por
    defecto absorbía y degradaba esa fila en silencio, sin ningún error.
    """
    bridge_owner, bridge_joiner = two_linked_bridges()
    p_uuid = bridge_owner.active_project_uuid
    owner_node_uuid = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")

    # Simular una fila legada con clave pública vacía (p. ej. un proyecto creado antes
    # del fix de Tarea 5).
    with bridge_owner.db._write_lock, bridge_owner.db.get_connection() as conn:
        with conn:
            conn.execute(
                "UPDATE project_collaborators SET public_key = '' WHERE project_uuid = ? AND node_uuid = ?;",
                (p_uuid, owner_node_uuid)
            )

    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    res = bridge_owner.ingest_join_request(join_res["request_code"])
    assert res["success"] is True

    owner_row_after = bridge_owner.db.get_collaborator(p_uuid, owner_node_uuid)
    assert owner_row_after["role"] == "owner"
    assert owner_row_after["public_key"] != bridge_joiner.crypto.public_key_base64

    collabs = bridge_owner.db.get_project_collaborators(p_uuid)
    assert len(collabs) == 2


# ==========================
# retry_p2p_join / check_join_request_status (Tarea 7: clonado tras aprobación HITL)
# ==========================

def two_linked_bridges_with_local_git_remotes():
    """
    Extiende two_linked_bridges() con repositorios Git bare locales reales como
    code_repo_url/gov_repo_url del proyecto del dueño, replicando el patrón ya usado en
    tests/test_stage2_p2p_auth_prs_and_sync.py (test_p2p_wan_remote_watcher: `git init
    --bare` + un clon semilla que hace commit y push) para ejercitar clones reales de
    retry_p2p_join sin tocar GitHub real.
    """
    bridge_owner, bridge_joiner = two_linked_bridges()
    p_uuid = bridge_owner.active_project_uuid

    root = Path(tempfile.mkdtemp())
    register_tmp_dir(root)

    bare_code = root / "remote_code.git"
    bare_gov = root / "remote_gov.git"
    bare_code.mkdir()
    bare_gov.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "main"], cwd=str(bare_code), capture_output=True, check=True)
    subprocess.run(["git", "init", "--bare", "-b", "main"], cwd=str(bare_gov), capture_output=True, check=True)

    # Semillas: clonar cada bare repo, hacer un commit inicial y empujarlo, para que
    # retry_p2p_join tenga algo real que clonar (un bare repo vacío también es clonable,
    # pero con contenido se ejerce mejor el camino real de ensure_orphan_worktree/OKF).
    seed_code = root / "seed_code"
    seed_gov = root / "seed_gov"
    subprocess.run(["git", "clone", str(bare_code), str(seed_code)], capture_output=True, check=True)
    (seed_code / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(seed_code), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=str(seed_code), capture_output=True, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=str(seed_code), capture_output=True, check=True)

    subprocess.run(["git", "clone", str(bare_gov), str(seed_gov)], capture_output=True, check=True)
    (seed_gov / ".okf_project.json").write_text(f'{{"project_uuid": "{p_uuid}"}}\n', encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(seed_gov), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=str(seed_gov), capture_output=True, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=str(seed_gov), capture_output=True, check=True)

    with bridge_owner.db._write_lock, bridge_owner.db.get_connection() as conn:
        with conn:
            conn.execute(
                "UPDATE projects SET code_repo_url = ?, gov_repo_url = ? WHERE project_uuid = ?;",
                (str(bare_code), str(bare_gov), p_uuid)
            )

    return bridge_owner, bridge_joiner


def test_retry_without_remote_repos_reports_no_remote_repos():
    """
    Review finding (Crítico #1): un stub sin ninguna URL de repositorio (el caso por
    defecto de two_linked_bridges(), que no publica a GitHub) NUNCA se resuelve
    reintentando -- join_p2p_project solo persiste code_repo_url/gov_repo_url una vez, al
    crear el stub, y no hay ningún camino que las rellene después. Por eso este caso debe
    reportar el status terminal distinto 'no_remote_repos', NO 'still_pending' (que
    implicaría, engañosamente, que reintentar más tarde ayudará).
    """
    bridge_owner, bridge_joiner = two_linked_bridges()
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    res = bridge_joiner.retry_p2p_join(join_res["project"]["project_uuid"])
    assert res["success"] is False
    assert res["status"] == "no_remote_repos"


def test_retry_with_real_pending_github_access_reports_still_pending():
    """
    Review finding (Importante #3): a diferencia del caso anterior, un project_uuid que SÍ
    tiene URLs de repositorio reales, pero cuyo clonado en GitHub todavía devuelve
    'pending_access' (p. ej. la invitación de colaborador de GitHub aún no propagó),
    representa el caso genuinamente transitorio -- 'still_pending' es la respuesta
    correcta aquí, y debe seguir siendo distinguible de 'no_remote_repos'.
    """
    bridge_owner, bridge_joiner = two_linked_bridges_with_local_git_remotes()
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)

    def fake_pending_access_clone(*args, **kwargs):
        return {"success": False, "status": "pending_access", "error": "Acceso denegado o repositorio no encontrado en GitHub."}

    bridge_joiner.github.git_clone = fake_pending_access_clone

    res = bridge_joiner.retry_p2p_join(join_res["project"]["project_uuid"])
    assert res["success"] is False
    assert res["status"] == "still_pending"
    assert res["status"] != "no_remote_repos"


def test_retry_after_approval_clones_and_activates_project():
    bridge_owner, bridge_joiner = two_linked_bridges_with_local_git_remotes()
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    bridge_owner.active_project_uuid = join_res["project"]["project_uuid"]
    bridge_owner.ingest_join_request(join_res["request_code"])
    node_uuid = guess_joiner_node_uuid(bridge_owner, bridge_joiner)
    approve_res = bridge_owner.approve_collaborator(bridge_owner.active_project_uuid, node_uuid)
    assert approve_res["success"] is True
    res = bridge_joiner.retry_p2p_join(join_res["project"]["project_uuid"])
    assert res["success"] is True
    proj = bridge_joiner.db.get_project_by_uuid(join_res["project"]["project_uuid"])
    assert proj["join_status"] == "active"


def test_retry_after_approval_approves_own_local_collaborator_row():
    """
    Review finding (Importante #2): la referencia original en git history (c650a47)
    aprobaba, como último paso, la propia fila de colaborador LOCAL del solicitante (la
    que join_p2p_project insertó con status='pending_approval'). Omitir esto es una
    regresión real: sin esa fila en 'approved', _validate_assignable_node rechazaría que
    el propio solicitante se autoasigne una tarjeta Kanban, y el cálculo de cuórum del
    jurado lo dejaría fuera de las votaciones sobre su propio proyecto.
    """
    bridge_owner, bridge_joiner = two_linked_bridges_with_local_git_remotes()
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    bridge_owner.active_project_uuid = join_res["project"]["project_uuid"]
    bridge_owner.ingest_join_request(join_res["request_code"])
    node_uuid = guess_joiner_node_uuid(bridge_owner, bridge_joiner)
    approve_res = bridge_owner.approve_collaborator(bridge_owner.active_project_uuid, node_uuid)
    assert approve_res["success"] is True

    joiner_local_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
    before = bridge_joiner.db.get_collaborator(join_res["project"]["project_uuid"], joiner_local_node)
    assert before["status"] == "pending_approval"

    res = bridge_joiner.retry_p2p_join(join_res["project"]["project_uuid"])
    assert res["success"] is True

    after = bridge_joiner.db.get_collaborator(join_res["project"]["project_uuid"], joiner_local_node)
    assert after["status"] == "approved"


def test_check_join_request_status_wraps_retry_without_raising():
    bridge_owner, bridge_joiner = two_linked_bridges()
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    res = bridge_joiner.check_join_request_status(join_res["project"]["project_uuid"])
    assert res["success"] is False
    assert res["status"] == "no_remote_repos"


def test_retry_is_idempotent_against_already_active_project():
    bridge_owner, bridge_joiner = two_linked_bridges_with_local_git_remotes()
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    bridge_owner.active_project_uuid = join_res["project"]["project_uuid"]
    bridge_owner.ingest_join_request(join_res["request_code"])
    node_uuid = guess_joiner_node_uuid(bridge_owner, bridge_joiner)
    approve_res = bridge_owner.approve_collaborator(bridge_owner.active_project_uuid, node_uuid)
    assert approve_res["success"] is True
    first = bridge_joiner.retry_p2p_join(join_res["project"]["project_uuid"])
    assert first["success"] is True

    second = bridge_joiner.retry_p2p_join(join_res["project"]["project_uuid"])
    assert second["success"] is True
    assert second["status"] == "already_active"


# --- Tarea 9: cableado del transporte en __init__ y despacho de mensajes en vivo ---

class FakeTransport:
    """Doble mínimo de P2PTransport (no hereda de la ABC a propósito: solo necesita
    exponer on_message para que los tests puedan observar, de forma directa, QUÉ callback
    quedó registrado -- en lugar de inferirlo indirectamente disparando un mensaje real).
    Review Focus (hallazgo Importante #1): sin un test que capture el callback mismo, se
    podría borrar la línea `self.transport.on_message(self._handle_transport_message)` (o
    la re-registro en set_transport) y los 41 tests existentes seguirían en verde --
    porque todos ellos llaman a _handle_transport_message directamente, nunca a través de
    un transporte inyectado."""

    def __init__(self):
        self.registered_callback = None

    def try_connect(self, peer_rendezvous_id, timeout_s=8.0):
        return False

    def send(self, message):
        return False

    def on_message(self, callback):
        self.registered_callback = callback

    def close(self):
        return None


def test_api_bridge_defaults_to_null_transport_when_none_given():
    bridge = ApiBridge(db_manager=make_test_db())
    assert isinstance(bridge.transport, NullTransportAdapter)


def test_init_registers_handle_transport_message_on_injected_transport():
    """Hallazgo Importante #1: verifica el MECANISMO exacto, no solo su efecto indirecto
    -- que ApiBridge.__init__ efectivamente llama a on_message(self._handle_transport_message)
    sobre el transporte inyectado, capturando el callback registrado y comprobando que
    invocarlo llega al mismo despacho que _handle_transport_message."""
    fake = FakeTransport()
    bridge = ApiBridge(db_manager=make_test_db(), transport=fake)

    assert fake.registered_callback is not None
    assert fake.registered_callback == bridge._handle_transport_message

    # Comprobación comportamental adicional: invocar el callback capturado debe llegar
    # exactamente al mismo despacho que llamar a _handle_transport_message directamente.
    bridge.active_project_uuid = "proj_cb"
    bridge.db.create_project(name="p", project_uuid="proj_cb")
    invite = bridge.generate_p2p_invite("proj_cb")["token"]
    joiner = ApiBridge(db_manager=make_test_db())
    join_res = joiner.join_p2p_project(invite)
    fake.registered_callback({"type": "join_request", "request_code": join_res["request_code"]})
    collabs = bridge.db.get_project_collaborators("proj_cb")
    assert any(c["status"] == "pending_approval" for c in collabs)


def test_set_transport_reregisters_handle_transport_message_on_new_transport(monkeypatch):
    """Hallazgo Importante #1: verifica que set_transport no deja el callback huérfano en
    el transporte VIEJO (fake1) ni ausente en el NUEVO (fake2) -- debe quedar registrado en
    fake2, y disparar fake2's callback debe seguir llegando al despacho real."""
    fake1 = FakeTransport()
    fake2 = FakeTransport()
    bridge = ApiBridge(db_manager=make_test_db(), transport=fake1)
    assert fake1.registered_callback == bridge._handle_transport_message

    bridge.set_transport(fake2)

    assert bridge.transport is fake2
    assert fake2.registered_callback == bridge._handle_transport_message

    calls = []
    monkeypatch.setattr(bridge, "retry_p2p_join", lambda project_uuid: calls.append(project_uuid))
    # Disparar el callback capturado en fake2 (el transporte NUEVO) debe llegar al
    # despacho real de join_decision -- confirmando que la re-registro ocurrió de verdad,
    # no solo que el atributo bridge.transport cambió.
    fake2.registered_callback({"type": "join_decision", "decision": "approved", "project_uuid": "proj_y"})
    assert calls == ["proj_y"]


def test_get_retry_lock_is_stable_per_project_and_distinct_across_projects():
    """La clave de serialización es el project_uuid: la misma clave debe devolver siempre
    el MISMO objeto Lock (para que dos hilos distintos de verdad se bloqueen entre sí), y
    proyectos distintos deben obtener locks distintos (para no serializar innecesariamente
    retries de proyectos sin relación)."""
    bridge = ApiBridge(db_manager=make_test_db())
    lock_a1 = bridge._get_retry_lock("proj_a")
    lock_a2 = bridge._get_retry_lock("proj_a")
    lock_b = bridge._get_retry_lock("proj_b")
    assert lock_a1 is lock_a2
    assert lock_a1 is not lock_b


def test_retry_p2p_join_serializes_concurrent_calls_for_same_project(monkeypatch):
    """Hallazgo Importante #2 de revisión: una llamada en vivo (que llegaría vía
    _handle_transport_message, en el hilo del transporte/event-loop WebRTC) y una llamada
    manual simultánea (botón "Reintentar" o el polling de check_join_request_status, en el
    hilo de la UI) para el MISMO project_uuid nunca deben ejecutar el cuerpo real de
    retry_p2p_join (_do_retry_p2p_join) al mismo tiempo. Sin el lock por-proyecto, la
    segunda llamada podría observar el `.git` parcial que `git clone` crea muy temprano,
    tratarlo como "ya clonado", y reconciliar OKF / marcar 'active' sobre un checkout
    incompleto -- la race descrita en la revisión. Se usan threading.Event (no sleeps) para
    que el test sea determinista: la primera llamada señala que ya entró y queda bloqueada
    hasta que el test la libera; mientras tanto se lanza la segunda y se comprueba que NO
    logra entrar en esa ventana."""
    import threading

    bridge = ApiBridge(db_manager=make_test_db())

    entered = threading.Event()
    release = threading.Event()
    counter_lock = threading.Lock()
    state = {"inside": 0, "overlap": False}

    def fake_body(project_uuid):
        with counter_lock:
            state["inside"] += 1
            if state["inside"] > 1:
                state["overlap"] = True
        entered.set()
        release.wait(timeout=5)
        with counter_lock:
            state["inside"] -= 1
        return {"success": True, "status": "active", "project": {}}

    monkeypatch.setattr(bridge, "_do_retry_p2p_join", fake_body)

    results = []

    def call_once():
        results.append(bridge.retry_p2p_join("proj_same"))

    t1 = threading.Thread(target=call_once)
    t1.start()
    assert entered.wait(timeout=5), "la primera llamada nunca entró a la sección crítica"
    entered.clear()

    t2 = threading.Thread(target=call_once)
    t2.start()
    # Darle a la segunda llamada toda oportunidad de "colarse" si el lock no existiera.
    entered_in_time = entered.wait(timeout=0.3)
    assert not entered_in_time, (
        "la segunda llamada concurrente para el MISMO project_uuid entró a la sección "
        "crítica antes de que la primera liberase su lock"
    )

    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not state["overlap"], "ambas llamadas estuvieron dentro de _do_retry_p2p_join simultáneamente"
    assert len(results) == 2
    assert all(r["status"] == "active" for r in results)


def test_incoming_join_request_message_is_ingested():
    """Un mensaje {"type": "join_request", ...} recibido por el canal en vivo (Tarea 5/6)
    debe llegar hasta ingest_join_request exactamente igual que la vía manual, dejando
    una fila pending_approval en la BD del dueño."""
    bridge_owner, bridge_joiner = two_linked_bridges()
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    bridge_owner.active_project_uuid = invite_project_uuid(invite)

    bridge_owner._handle_transport_message({"type": "join_request", "request_code": join_res["request_code"]})

    collabs = bridge_owner.db.get_project_collaborators(bridge_owner.active_project_uuid)
    assert any(c["status"] == "pending_approval" for c in collabs)


def test_incoming_join_decision_triggers_retry(monkeypatch):
    bridge = ApiBridge(db_manager=make_test_db())
    calls = []
    monkeypatch.setattr(bridge, "retry_p2p_join", lambda project_uuid: calls.append(project_uuid))
    bridge._handle_transport_message({"type": "join_decision", "decision": "approved", "project_uuid": "proj_x"})
    assert calls == ["proj_x"]


def test_incoming_join_decision_pending_does_not_trigger_retry(monkeypatch):
    """Una decision distinta de 'approved' (p.ej. 'rejected' o ausente) nunca debe
    disparar retry_p2p_join -- solo la aprobación explícita."""
    bridge = ApiBridge(db_manager=make_test_db())
    calls = []
    monkeypatch.setattr(bridge, "retry_p2p_join", lambda project_uuid: calls.append(project_uuid))
    bridge._handle_transport_message({"type": "join_decision", "decision": "rejected", "project_uuid": "proj_x"})
    assert calls == []


def test_handle_transport_message_ignores_malformed_input():
    bridge = ApiBridge(db_manager=make_test_db())
    # Ninguna de estas llamadas debe lanzar una excepción.
    bridge._handle_transport_message({"type": "unknown"})
    bridge._handle_transport_message({})
    bridge._handle_transport_message({"type": "join_request"})  # falta request_code
    bridge._handle_transport_message({"type": "join_decision", "decision": "approved"})  # falta project_uuid
    bridge._handle_transport_message(None)  # tipo totalmente inesperado
    bridge._handle_transport_message("not-a-dict")  # tipo totalmente inesperado


# --- Tarea 8: rate-limit real y notificación en vivo cableadas en approve/reject ---

def seed_pending_collaborator(bridge, project_uuid, node_uuid, public_key=""):
    """Crea un proyecto local y una fila pending_approval para node_uuid, sin pasar por el
    protocolo P2P completo de invitación/join -- estos tests ejercitan approve_collaborator/
    reject_collaborator en aislamiento, no el flujo de ingesta."""
    bridge.db.create_project(name=project_uuid, project_uuid=project_uuid)
    bridge.active_project_uuid = project_uuid
    bridge.db.add_or_request_collaborator(project_uuid, node_uuid, public_key=public_key)


class FakeConnectedTransport:
    """Doble de P2PTransport que SÍ registra los mensajes enviados (a diferencia de
    FakeTransport, arriba, cuyo send() siempre devuelve False sin registrar nada) -- para
    que los tests de notificación en vivo puedan inspeccionar qué se envió realmente."""

    def __init__(self):
        self.sent = []

    def try_connect(self, peer_rendezvous_id, timeout_s=8.0):
        return True

    def send(self, message):
        self.sent.append(message)
        return True

    def on_message(self, callback):
        self.registered_callback = callback

    def close(self):
        return None


def test_reject_collaborator_returns_rejection_count_and_lock():
    bridge = ApiBridge(db_manager=make_test_db())
    seed_pending_collaborator(bridge, "proj_x", "node_1", public_key="PK1")
    for i in range(3):
        res = bridge.reject_collaborator("proj_x", "node_1")
    assert res["rejection_count"] == 3
    assert res["locked_until"] is not None


def test_get_project_collaborators_includes_locked_until_for_locked_rejected_row():
    """Task 11 review finding (Important #1/#2): the state that ACTUALLY happens after
    3+ rejections through the real application flow is a 'rejected' row with an active
    lock, never 'pending_approval' + locked -- record_rejection() flips the row to
    'rejected' in the SAME operation that crosses the lock threshold, and
    ingest_join_request checks the rate-limit BEFORE touching the row, so a locked
    public key's resubmission is turned away before the row is ever written again.
    This test reaches that real state through the real API (two_linked_bridges +
    ingest_join_request with a genuine signed request_code, the same pattern as
    test_ingest_join_request_rejects_when_locked below) instead of fabricating a
    'pending_approval + locked' combination that a real user flow cannot produce.
    """
    bridge_owner, bridge_joiner = two_linked_bridges()
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    p_uuid = invite_project_uuid(invite)
    bridge_owner.active_project_uuid = p_uuid
    bridge_owner.ingest_join_request(join_res["request_code"])
    node_uuid = guess_joiner_node_uuid(bridge_owner, bridge_joiner)

    for _ in range(3):
        bridge_owner.reject_collaborator(p_uuid, node_uuid)

    res = bridge_owner.get_project_collaborators(p_uuid)
    assert res["success"] is True
    row = next(c for c in res["collaborators"] if c["node_uuid"] == node_uuid)
    assert row["status"] == "rejected"
    assert row["rejection_count"] == 3
    assert row["locked_until"] is not None

    # A real resubmission while locked must be turned away without touching the row --
    # confirms the 'pending_approval + locked' combination the old test fabricated is
    # genuinely unreachable here, and that the lock the owner now sees actually governs
    # the requester's ability to retry.
    res2 = bridge_owner.ingest_join_request(join_res["request_code"])
    assert res2["success"] is False
    assert res2["status"] == "locked"
    row_after = next(c for c in bridge_owner.get_project_collaborators(p_uuid)["collaborators"] if c["node_uuid"] == node_uuid)
    assert row_after["status"] == "rejected"
    assert row_after["rejection_count"] == 3


def test_get_project_collaborators_locked_until_is_none_when_not_locked():
    bridge = ApiBridge(db_manager=make_test_db())
    seed_pending_collaborator(bridge, "proj_x", "node_1", public_key="PK1")

    res = bridge.get_project_collaborators("proj_x")
    assert res["success"] is True
    row = next(c for c in res["collaborators"] if c["node_uuid"] == "node_1")
    assert row["locked_until"] is None


def test_get_project_collaborators_locked_until_is_none_for_approved_rows():
    """An 'approved' row (e.g. the owner's own row, or any collaborator the owner has
    accepted) must never show a lock, even though it still carries rejection history
    from before it was approved -- approve_collaborator() resets rejection_count to 0,
    but the real guard here is the 'approved' status check itself, not the counters."""
    bridge = ApiBridge(db_manager=make_test_db())
    res = bridge.get_project_collaborators(bridge.active_project_uuid)
    assert res["success"] is True
    owner_row = next(c for c in res["collaborators"] if c["role"] == "owner")
    assert owner_row["locked_until"] is None


def test_get_project_collaborators_locked_until_is_none_for_rejected_row_without_active_lock():
    """A rejected row with only 1-2 rejections (below the lock threshold) must show
    locked_until: None -- the lock badge is informational and must not appear when
    there is genuinely no active lock."""
    bridge = ApiBridge(db_manager=make_test_db())
    seed_pending_collaborator(bridge, "proj_x", "node_1", public_key="PK1")
    bridge.reject_collaborator("proj_x", "node_1")

    res = bridge.get_project_collaborators("proj_x")
    row = next(c for c in res["collaborators"] if c["node_uuid"] == "node_1")
    assert row["status"] == "rejected"
    assert row["locked_until"] is None


def test_approve_collaborator_sends_live_notification_when_transport_connected():
    fake_transport = FakeConnectedTransport()
    bridge = ApiBridge(db_manager=make_test_db(), transport=fake_transport)
    seed_pending_collaborator(bridge, "proj_x", "node_1", public_key="PK1")
    bridge.approve_collaborator("proj_x", "node_1")
    assert fake_transport.sent[-1]["type"] == "join_decision"
    assert fake_transport.sent[-1]["decision"] == "approved"


def test_approve_collaborator_does_not_fail_if_transport_send_raises():
    class ExplodingTransport:
        def send(self, message):
            raise RuntimeError("boom")

        def on_message(self, callback):
            self.registered_callback = callback

    bridge = ApiBridge(db_manager=make_test_db(), transport=ExplodingTransport())
    seed_pending_collaborator(bridge, "proj_x", "node_1", public_key="PK1")
    res = bridge.approve_collaborator("proj_x", "node_1")
    assert res["success"] is True
