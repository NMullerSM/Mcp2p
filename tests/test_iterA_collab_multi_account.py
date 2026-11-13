"""
Suite de Pruebas Automatizadas para Iteración A:
Colaboración Real Multi-Cuenta P2P, Publicación Dual, Rama Huérfana,
Sin Fuga de Credenciales y Replicación Deterministica (Three Tiers).
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.a2a.bus import A2ABus, A2AIntent
from src.app.api_bridge import ApiBridge
from src.p2p.crypto import NodeCryptoManager
from src.p2p.network import P2PNotificationService
from src.storage.db import DatabaseManager
from src.storage.github_sync import GitHubSyncManager
from src.storage.okf_adapter import OKFAdapter


@pytest.fixture
def temp_env():
    """Crea un entorno de pruebas aislado con directorio temporal."""
    temp_dir = tempfile.mkdtemp(prefix="mcp2p_test_itera_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


def create_bare_repo(bare_path: Path) -> Path:
    """Inicializa un repositorio Git bare local para simular GitHub."""
    bare_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare"], cwd=str(bare_path), capture_output=True, check=True)
    return bare_path


def test_no_token_persisted_in_git_config(temp_env):
    """
    Verifica que las credenciales PAT NUNCA queden persistidas en .git/config en disco.
    """
    db_file = temp_env / "test_sec.db"
    db = DatabaseManager(db_file)
    db.set_setting("github_pat_token", "ghp_SECRET_PAT_TOKEN_12345")
    db.set_setting("github_pat_username", "secret_dev")

    gh_sync = GitHubSyncManager(db_manager=db)

    # 1. Verificar clean_url_credentials
    dirty_url = "https://ghp_SECRET_PAT_TOKEN_12345@github.com/secret_dev/demo-repo.git"
    clean = gh_sync.clean_url_credentials(dirty_url)
    assert clean == "https://github.com/secret_dev/demo-repo.git"
    assert "ghp_SECRET_PAT_TOKEN_12345" not in clean

    # 2. Inicializar repo local y setear remote sucio para probar sanitización
    local_repo = temp_env / "local_repo"
    local_repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=str(local_repo), capture_output=True, check=True)

    # Simular que alguien configuró una URL con PAT
    subprocess.run(["git", "remote", "add", "origin", dirty_url], cwd=str(local_repo), capture_output=True, check=True)
    raw_config = (local_repo / ".git" / "config").read_text(encoding="utf-8")
    assert "ghp_SECRET_PAT_TOKEN_12345" in raw_config

    # Ejecutar sanitización
    sanitized = gh_sync.sanitize_remote_url(local_repo, "origin")
    assert sanitized is True

    clean_config = (local_repo / ".git" / "config").read_text(encoding="utf-8")
    assert "ghp_SECRET_PAT_TOKEN_12345" not in clean_config
    assert "https://github.com/secret_dev/demo-repo.git" in clean_config


def test_publish_both_pushes_app_gov_and_orphan(temp_env):
    """
    Verifica que publish_project_to_github con repo_target='both' empuja el repositorio
    de app, el de gobernanza OKF y la rama huérfana desacoplada.
    """
    db_file = temp_env / "node_a.db"
    db_a = DatabaseManager(db_file)
    api_a = ApiBridge(db_manager=db_a)

    # Crear proyecto
    proj = api_a.create_project(name="DualProject", auto_publish=False)["project"]
    p_uuid = proj["project_uuid"]

    # Crear dos remotos bare locales para simular repos de GitHub
    bare_app = create_bare_repo(temp_env / "remote_app.git")
    bare_gov = create_bare_repo(temp_env / "remote_gov.git")

    # Configurar remotos origin
    api_a.github.set_remote_url(proj["code_repo_path"], str(bare_app), "origin")
    api_a.github.set_remote_url(proj["gov_repo_path"], str(bare_gov), "origin")

    # Mockear auto_create_and_publish_repo para que use nuestros bare locales
    with patch.object(api_a.github, "auto_create_and_publish_repo") as mock_publish:
        def side_effect(repo_path, repo_name, description, private):
            p = Path(repo_path)
            branch_proc = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=p, capture_output=True, text=True)
            branch = branch_proc.stdout.strip() or "main"
            push_res = api_a.github.git_push_branch(p, branch=branch, remote="origin")
            return {
                "success": push_res.get("success", False),
                "repo_url": str(bare_gov) if "okf" in repo_name else str(bare_app),
                "branch": branch
            }
        mock_publish.side_effect = side_effect

        res = api_a.publish_project_to_github(project_uuid=p_uuid, repo_target="both")
        assert res["success"] is True
        assert res["app"]["success"] is True
        assert res["gov"]["success"] is True
        assert res["orphan"]["success"] is True

    # Verificar que el remoto bare de gov contiene la rama huérfana
    orphan_branch = f"orphan-conversa-memory-{p_uuid}"
    heads = subprocess.run(["git", "ls-remote", "--heads", str(bare_gov)], capture_output=True, text=True)
    assert orphan_branch in heads.stdout
    assert "refs/heads/main" in heads.stdout


def test_join_clones_both_repos_and_mounts_orphan_worktree(temp_env):
    """
    Verifica que un segundo nodo (Node B) se une a un proyecto mediante clone de ambos repos,
    monta la rama huérfana e importa las tareas del OKF.
    """
    # 1. Configurar Node A con un proyecto y una tarjeta Kanban
    db_a = DatabaseManager(temp_env / "node_a.db")
    api_a = ApiBridge(db_manager=db_a)
    proj_a = api_a.create_project(name="CollaborativeApp", reviewer_sla_hours=24)["project"]
    p_uuid = proj_a["project_uuid"]

    # Añadir tarjeta en Node A (create_card sincroniza automáticamente el YAML al OKF)
    card = api_a.db.create_card(
        project_uuid=p_uuid,
        card_id="TASK-101",
        title="Implement P2P Sync Tier 1",
        description="Write orphan worktree replication",
        acceptance_criteria="Replicates dialogue"
    )
    gov_path = Path(proj_a["gov_repo_path"])
    subprocess.run(["git", "add", "."], cwd=gov_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "feat(kanban): add TASK-101"], cwd=gov_path, capture_output=True, check=True)

    # 2. Publicar a remotos bare
    bare_app = create_bare_repo(temp_env / "remote_app.git")
    bare_gov = create_bare_repo(temp_env / "remote_gov.git")

    api_a.github.set_remote_url(proj_a["code_repo_path"], str(bare_app), "origin")
    api_a.github.set_remote_url(proj_a["gov_repo_path"], str(bare_gov), "origin")

    # Push inicial
    api_a.github.git_push_branch(proj_a["code_repo_path"], branch="main", remote="origin")
    api_a.github.git_push_branch(proj_a["gov_repo_path"], branch="main", remote="origin")
    wt_a = api_a.ensure_orphan_worktree(proj_a["gov_repo_path"], p_uuid)
    orphan_branch = f"orphan-conversa-memory-{p_uuid}"
    api_a.github.git_push_branch(proj_a["gov_repo_path"], branch=orphan_branch, remote="origin")

    # Actualizar URLs en db_a
    with db_a._write_lock, db_a.get_connection() as conn:
        with conn:
            conn.execute("UPDATE projects SET code_repo_url = ?, gov_repo_url = ? WHERE project_uuid = ?;", (str(bare_app), str(bare_gov), p_uuid))

    # 3. Generar token de invitación firmado
    invite_res = api_a.generate_p2p_invite(p_uuid)
    assert invite_res["success"] is True
    invite_token = invite_res["token"]

    # 4. Configurar Node B en espacio aislado y unirse
    db_b = DatabaseManager(temp_env / "node_b.db")
    api_b = ApiBridge(db_manager=db_b)

    join_res = api_b.join_p2p_project(invite_token)
    assert join_res["success"] is True
    assert join_res["status"] == "pending_approval"

    # 5. Node A (dueño) ingiere la solicitud manualmente, la aprueba, y Node B (solicitante)
    # reintenta la unión -- el nuevo flujo de tres pasos que sustituye al clonado
    # automático que join_p2p_project hacía antes de la Tarea 5.
    api_a.active_project_uuid = p_uuid
    ingest_res = api_a.ingest_join_request(join_res["request_code"])
    assert ingest_res["success"] is True
    node_b_row = api_a.db.get_collaborator_by_pubkey(p_uuid, api_b.crypto.public_key_base64)
    assert node_b_row is not None
    node_b_uuid_on_owner = node_b_row["node_uuid"]
    approve_res = api_a.approve_collaborator(p_uuid, node_b_uuid_on_owner)
    assert approve_res["success"] is True

    retry_res = api_b.retry_p2p_join(p_uuid)
    assert retry_res["success"] is True

    # 6. Comprobar que Node B tiene el proyecto clonado
    proj_b = api_b.db.get_project_by_uuid(p_uuid)
    assert proj_b is not None
    assert Path(proj_b["code_repo_path"]).exists()
    assert Path(proj_b["gov_repo_path"]).exists()
    assert (Path(proj_b["gov_repo_path"]) / ".git" / "orphan_worktree").exists()

    # 7. Comprobar que Node B tiene la tarjeta TASK-101 en su SQLite gracias a la reconciliación OKF
    cards_b = api_b.db.get_cards(p_uuid)
    assert len(cards_b) >= 1
    card_ids = [c["card_id"] for c in cards_b]
    assert "TASK-101" in card_ids


def test_sync_three_tiers_replicates_dialogue_between_two_nodes(temp_env):
    """
    Prueba el criterio de aceptación central:
    En dos DB locales y un remoto bare, el nodo B emite un diálogo, ejecuta sync_three_tiers,
    y el nodo A lo lee tras ejecutar sync_three_tiers.
    """
    # 1. Configurar Node A
    db_a = DatabaseManager(temp_env / "node_a.db")
    api_a = ApiBridge(db_manager=db_a)
    proj_a = api_a.create_project(name="DialecticApp")["project"]
    p_uuid = proj_a["project_uuid"]

    bare_app = create_bare_repo(temp_env / "bare_app.git")
    bare_gov = create_bare_repo(temp_env / "bare_gov.git")

    api_a.github.set_remote_url(proj_a["code_repo_path"], str(bare_app), "origin")
    api_a.github.set_remote_url(proj_a["gov_repo_path"], str(bare_gov), "origin")
    api_a.github.git_push_branch(proj_a["code_repo_path"], branch="main", remote="origin")
    api_a.github.git_push_branch(proj_a["gov_repo_path"], branch="main", remote="origin")
    api_a.ensure_orphan_worktree(proj_a["gov_repo_path"], p_uuid)
    orphan_branch = f"orphan-conversa-memory-{p_uuid}"
    api_a.github.git_push_branch(proj_a["gov_repo_path"], branch=orphan_branch, remote="origin")

    with db_a._write_lock, db_a.get_connection() as conn:
        with conn:
            conn.execute("UPDATE projects SET code_repo_url = ?, gov_repo_url = ? WHERE project_uuid = ?;", (str(bare_app), str(bare_gov), p_uuid))

    # 2. Node B se une
    invite_res = api_a.generate_p2p_invite(p_uuid)
    db_b = DatabaseManager(temp_env / "node_b.db")
    api_b = ApiBridge(db_manager=db_b)
    join_res = api_b.join_p2p_project(invite_res["token"])
    assert join_res["success"] is True
    assert join_res["status"] == "pending_approval"

    # 2b. Node A (dueño) ingiere la solicitud, la aprueba, y Node B (solicitante) reintenta
    # la unión -- el nuevo flujo de tres pasos que sustituye al clonado automático que
    # join_p2p_project hacía antes de la Tarea 5.
    api_a.active_project_uuid = p_uuid
    ingest_res = api_a.ingest_join_request(join_res["request_code"])
    assert ingest_res["success"] is True
    node_b_row = api_a.db.get_collaborator_by_pubkey(p_uuid, api_b.crypto.public_key_base64)
    assert node_b_row is not None
    approve_res = api_a.approve_collaborator(p_uuid, node_b_row["node_uuid"])
    assert approve_res["success"] is True

    retry_res = api_b.retry_p2p_join(p_uuid)
    assert retry_res["success"] is True

    # 3. Node B emite un diálogo / propuesta dialéctica en A2A
    node_b_uuid = "node_b_tester_uuid"
    msg_packet = api_b.bus.send_proposal(
        project_uuid=p_uuid,
        sender_node_uuid=node_b_uuid,
        session_id="SESSION-001",
        component="networking",
        intent="ARCH_IMPROVEMENT",
        rationale="Propuesta de arquitectura distribuida para WebSockets",
        diff_ref="diffs/ws.patch",
        impact="low",
        card_id="TASK-101"
    )
    b_msg_uuid = msg_packet["message_uuid"]

    # Comprobar que en Node A todavía no existe el mensaje
    with db_a.get_connection() as conn:
        assert conn.execute("SELECT 1 FROM a2a_messages WHERE message_uuid = ?;", (b_msg_uuid,)).fetchone() is None

    # 4. Node B ejecuta sync_three_tiers (vuelca a rama huérfana y empuja)
    sync_b = api_b.sync_three_tiers(p_uuid)
    assert sync_b["success"] is True
    assert sync_b["tier1_orphan"]["flushed"] >= 1

    # 5. Node A ejecuta sync_three_tiers (hace pull de rama huérfana e importa)
    sync_a = api_a.sync_three_tiers(p_uuid)
    assert sync_a["success"] is True
    assert sync_a["tier1_orphan"]["imported"] >= 1

    # 6. Comprobar que el mensaje de Node B ahora está en la base de datos de Node A
    with db_a.get_connection() as conn:
        imported_row = conn.execute("SELECT * FROM a2a_messages WHERE message_uuid = ?;", (b_msg_uuid,)).fetchone()
        assert imported_row is not None
        assert imported_row["sender_node_uuid"] == node_b_uuid
        assert "websocket" in imported_row["payload_rfc_yaml"].lower()


def test_event_import_is_idempotent(temp_env):
    """
    Verifica que ejecuciones consecutivas de sync_three_tiers son idempotentes y no duplican eventos.
    """
    db = DatabaseManager(temp_env / "idempotent.db")
    api = ApiBridge(db_manager=db)
    proj = api.create_project(name="IdempotentApp")["project"]
    p_uuid = proj["project_uuid"]

    # Emitir mensaje
    api.bus.send_informative(
        project_uuid=p_uuid,
        sender_node_uuid="node_local_1",
        signal_type="TEST_HEARTBEAT",
        data={"status": "alive"}
    )

    # Ejecutar sync varias veces
    res1 = api.sync_three_tiers(p_uuid)
    res2 = api.sync_three_tiers(p_uuid)

    assert res1["success"] is True
    assert res2["success"] is True

    with db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) as cnt FROM a2a_messages WHERE project_uuid = ?;", (p_uuid,)).fetchone()["cnt"]
        assert count == 1


def test_lan_webhook_verifies_signature(tmp_path):
    """
    Verifica que el servicio HTTP LAN de P2P valida firmas Ed25519 y rechaza paquetes adulterados (401).
    """
    crypto_sender = NodeCryptoManager(key_path=tmp_path / "sender.key")
    events_received = []

    service = P2PNotificationService(
        node_uuid="receiver_node",
        lan_port=0,
        on_event_callback=lambda evt: events_received.append(evt),
        crypto_manager=crypto_sender,
        is_trusted_pubkey=lambda pk: pk == crypto_sender.public_key_base64,
    )
    started = service.start_lan_server()
    assert started is True

    port = service.lan_port

    try:
        import urllib.request

        url = f"http://127.0.0.1:{port}/webhook/p2p"

        # 1. Enviar paquete válidamente firmado
        payload = {"type": "CARD_UPDATE", "card_id": "TASK-101", "status": "in_review"}
        sig = crypto_sender.sign_payload(payload)
        valid_packet = {
            "payload": payload,
            "sig": sig,
            "sender_pubkey": crypto_sender.public_key_base64,
            "from_node": "sender_node"
        }
        req_valid = urllib.request.Request(
            url,
            data=json.dumps(valid_packet).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_valid, timeout=3) as resp:
            assert resp.status == 200

        assert len(events_received) == 1
        assert events_received[0]["card_id"] == "TASK-101"

        # 2. Enviar paquete con firma adulterada -> Debe responder 401 Unauthorized
        tampered_packet = dict(valid_packet)
        tampered_packet["sig"] = "tampered_invalid_signature_xyz"
        req_tampered = urllib.request.Request(
            url,
            data=json.dumps(tampered_packet).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req_tampered, timeout=3)
        assert exc_info.value.code == 401

    finally:
        service.stop_lan_server()


def test_human_dominant_jury_verdict(temp_env):
    """
    Verifica el principio de gobernanza del usuario:
    Los humanos siempre tienen peso superior decisorio. Si los robots votan por una opción y
    un humano vota por otra contraria, la decisión humana prevalece siempre.
    """
    db = DatabaseManager(temp_env / "jury.db")
    api = ApiBridge(db_manager=db)
    proj = api.create_project(name="JuryProject")["project"]
    p_uuid = proj["project_uuid"]

    # Obtener el nodo dueño registrado automáticamente al crear el proyecto (EGM-05)
    collabs = db.get_project_collaborators(p_uuid)
    owner_node = collabs[0]["node_uuid"] if collabs else "node_human_owner"

    # Crear ticket de disputa
    ticket_id = "TICKET-DISPUTE-01"
    api.bus.open_dispute_ticket(
        project_uuid=p_uuid,
        ticket_id=ticket_id,
        title="SQL vs NoSQL debate",
        collision_reason="Architectural divergence",
        contender_a_node="node_bot_a",
        contender_b_node="node_bot_b",
        proposal_a_ref="proposals/a.md",
        proposal_b_ref="proposals/b.md"
    )

    # 10 bots votan masivamente por OPTION_A con puntajes perfectos (100 pts Borda)
    for i in range(10):
        api.bus.submit_agent_borda_evaluation(
            ticket_id=ticket_id,
            juror_agent_id=f"bot_juror_{i}",
            scores_per_option={
                "OPTION_A": {
                    "kiss": 100.0,
                    "performance": 100.0,
                    "security": 100.0,
                    "ux_hicks": 100.0,
                    "testability": 100.0
                }
            },
            rationale="Bots unanimously prefer OPTION_A"
        )

    # 1 solo humano vota por OPTION_B (con peso estándar)
    api.bus.submit_human_jury_vote(
        ticket_id=ticket_id,
        node_uuid=owner_node,
        developer_name="Architect Nathaniel",
        voted_option="OPTION_B",
        signature_ed25519="valid_test_signature"
    )

    # Calcular veredicto
    verdict = api.bus.compute_jury_verdict(p_uuid, ticket_id)

    # OPTION_B DEBE ganar a pesar de la masa de bots por override de unanimidad humana (EGM-02 / EGM-03)
    assert verdict["winning_option"] == "OPTION_B"
    assert verdict["human_votes_count"] == 1
    assert verdict["consensus_reached"] is True
    assert verdict["resolution_mode"] == "human_unanimity_override"


def test_collaborator_invitation_flow(temp_env):
    """
    Verifica que al aprobar una solicitud de colaborador, se actualiza conventions/trusted_nodes.yaml
    y se disparan las invitaciones a los repositorios de GitHub.
    """
    db = DatabaseManager(temp_env / "collab.db")
    api = ApiBridge(db_manager=db)
    proj = api.create_project(name="CollabTest")["project"]
    p_uuid = proj["project_uuid"]

    # Agregar solicitud de colaborador
    api.db.add_or_request_collaborator(
        project_uuid=p_uuid,
        node_uuid="node_peer_beta",
        github_username="peer_developer",
        public_key="ed25519_test_pubkey_12345",
        role="collaborator",
        status="pending_approval"
    )

    # Mockear add_repo_collaborator
    with patch.object(api.github, "add_repo_collaborator") as mock_invite:
        mock_invite.return_value = {"success": True, "status_code": 201, "message": "Invited"}

        with db._write_lock, db.get_connection() as conn:
            with conn:
                conn.execute(
                    "UPDATE projects SET code_repo_url = ?, gov_repo_url = ? WHERE project_uuid = ?;",
                    ("https://github.com/owner/app.git", "https://github.com/owner/gov.git", p_uuid)
                )

        app_res = api.approve_collaborator(p_uuid, "node_peer_beta")
        assert app_res["success"] is True

        # Verificar que se llamó add_repo_collaborator dos veces (app y gov)
        assert mock_invite.call_count == 2
        mock_invite.assert_any_call("owner", "app", "peer_developer")
        mock_invite.assert_any_call("owner", "gov", "peer_developer")

    # Verificar que trusted_nodes.yaml contiene la clave del nuevo colaborador
    trusted_map = api.okf.get_trusted_nodes_map(proj["gov_repo_path"])
    assert "node_peer_beta" in trusted_map
    assert trusted_map["node_peer_beta"]["public_key"] == "ed25519_test_pubkey_12345"
