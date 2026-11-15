"""Caracterización del flujo de invitar / unirse / reintentar / ingerir solicitudes (EGM-57).

Fija, rama por rama, los contratos observables de `ApiBridge` (mismos `success`/`status`/
mensajes, efectos en BD y llamadas a los colaboradores) para que mover la lógica de
`collab_mesh_ui.py` a `ProjectCollaborationService` no cambie nada visible. Se escribió y se
dejó en verde contra el código previo a la extracción.
"""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.app.api_bridge import ApiBridge
from src.p2p.crypto import NodeCryptoManager
from tests.conftest import make_test_db, register_tmp_dir


class RecordingTransport:
    def __init__(self, connect: bool = True) -> None:
        self.connect = connect
        self.connected_to: List[str] = []
        self.sent: List[Dict[str, Any]] = []
        self.callback = None

    def on_message(self, cb):
        self.callback = cb

    def try_connect(self, rendezvous_id: str) -> bool:
        self.connected_to.append(rendezvous_id)
        return self.connect

    def send(self, msg: Dict[str, Any]) -> bool:
        self.sent.append(msg)
        return True

    def close(self) -> None:
        pass


def _crypto(name: str) -> NodeCryptoManager:
    d = tempfile.mkdtemp()
    register_tmp_dir(d)
    return NodeCryptoManager(key_path=Path(d) / f"{name}.key")


def _bridge(name: str, transport=None) -> ApiBridge:
    b = ApiBridge(db_manager=make_test_db(), transport=transport)
    b.crypto = _crypto(name)
    b.github.get_auth_status = lambda force=False: {"username": f"gh_{name}"}
    return b


@pytest.fixture
def pair(monkeypatch):
    monkeypatch.setenv("MCP2P_NODE_UUID", "node_charac")
    owner = _bridge("owner")
    joiner = _bridge("joiner", transport=RecordingTransport())
    proj = owner.create_project(name="Charac")["project"]
    owner.active_project_uuid = proj["project_uuid"]
    return owner, joiner, proj


def _invite_with(owner: ApiBridge, **overrides) -> str:
    base = owner.generate_p2p_invite()["payload"]
    base.update(overrides)
    return owner.crypto.create_signed_invite_token(base)


# --------------------------- generate_p2p_invite ---------------------------


def test_invite_payload_shape(pair):
    owner, _joiner, proj = pair
    res = owner.generate_p2p_invite()
    assert res["success"] is True and res["project_uuid"] == proj["project_uuid"]
    assert res["invite_url"] == f"mcp2p://join?token={res['token']}"
    assert set(res["payload"]) == {
        "project_uuid", "name", "code_repo_url", "gov_repo_url", "sender_node_uuid",
        "sender_github", "sender_pubkey", "created_at", "rendezvous_nonce",
    }
    assert res["payload"]["sender_github"] == "gh_owner"


def test_invite_refused_for_pending_stub(pair):
    owner, joiner, _proj = pair
    stub = joiner.join_p2p_project(owner.generate_p2p_invite()["token"])["project"]
    res = joiner.generate_p2p_invite(stub["project_uuid"])
    assert res["success"] is False and res["status"] == "join_pending"
    assert "generar invitaciones para este proyecto" in res["error"]


# ----------------------------- join_p2p_project ----------------------------


def test_join_rejects_empty_and_tampered_tokens(pair):
    owner, joiner, _ = pair
    assert joiner.join_p2p_project("   ") == {"success": False, "error": "Token de invitación P2P no válido"}
    assert joiner.join_p2p_project("mcp2p://join?token=") == {
        "success": False, "error": "Token de invitación P2P no válido",
    }
    token = owner.generate_p2p_invite()["token"]
    env = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    env["data"]["name"] = "Otro"
    tampered = base64.urlsafe_b64encode(json.dumps(env).encode()).decode()
    res = joiner.join_p2p_project(tampered)
    assert res["success"] is False and res["error"]


def test_join_rejects_missing_fields_and_unsafe_uuid(pair):
    owner, joiner, _ = pair
    res = joiner.join_p2p_project(_invite_with(owner, name=""))
    assert res == {"success": False, "error": "El token no contiene los datos del proyecto requeridos"}
    res = joiner.join_p2p_project(_invite_with(owner, project_uuid="../evil"))
    assert res["success"] is False and "project_uuid" in res["error"]


def test_join_new_project_creates_stub_peers_and_live_request(pair):
    owner, joiner, proj = pair
    invite = owner.generate_p2p_invite()
    res = joiner.join_p2p_project(f"mcp2p://join?token={invite['token']}")
    p_uuid = proj["project_uuid"]

    assert res["success"] is True and res["status"] == "pending_approval"
    stub = joiner.db.get_project_by_uuid(p_uuid)
    assert res["project"] == stub and stub["join_status"] == "pending_approval"
    expected_base = joiner.db.db_path.parent / "workspace" / f"charac_{p_uuid.replace('proj_', '')[:8]}"
    assert Path(stub["base_path"]) == expected_base
    assert Path(stub["code_repo_path"]) == expected_base / "app_charac"

    # Mismo node_uuid local y clave distinta -> el dueño se registra con sufijo.
    collabs = {c["node_uuid"]: c for c in joiner.db.get_project_collaborators(p_uuid)}
    owner_rows = [c for c in collabs.values() if c["role"] == "owner" and c["public_key"] == owner.crypto.public_key_base64]
    assert len(owner_rows) == 1 and owner_rows[0]["node_uuid"].startswith("node_charac_")
    assert owner_rows[0]["status"] == "approved" and owner_rows[0]["github_username"] == "gh_owner"
    assert collabs["node_charac"]["status"] == "pending_approval"
    assert collabs["node_charac"]["public_key"] == joiner.crypto.public_key_base64
    assert joiner.db.get_node_by_uuid(owner_rows[0]["node_uuid"])["node_kind"] == "human"

    ok, payload, _ = NodeCryptoManager.decode_and_verify_invite_token(res["request_code"])
    assert ok and payload["project_uuid"] == p_uuid and payload["github_username"] == "gh_joiner"
    assert payload["rendezvous_nonce"] == invite["payload"]["rendezvous_nonce"]
    transport = joiner.transport
    assert transport.connected_to == [f"{p_uuid}_{invite['payload']['rendezvous_nonce']}"]
    assert transport.sent == [{"type": "join_request", "request_code": res["request_code"]}]
    # El stub pendiente no pasa a ser el proyecto activo (hallazgo C2).
    assert joiner.active_project_uuid != p_uuid


def test_join_without_live_peer_does_not_send(pair, monkeypatch):
    owner, joiner, _ = pair
    joiner.transport.connect = False
    res = joiner.join_p2p_project(owner.generate_p2p_invite()["token"])
    assert res["status"] == "pending_approval" and joiner.transport.sent == []


def test_join_existing_active_project_only_switches_context(pair):
    owner, joiner, proj = pair
    joiner.db.create_project(name="Charac", project_uuid=proj["project_uuid"], init_git=False)
    res = joiner.join_p2p_project(owner.generate_p2p_invite()["token"])
    assert res["success"] is True
    assert res["message"] == "Proyecto ya registrado. Se activó el contexto."
    assert joiner.active_project_uuid == proj["project_uuid"]


def test_join_locked_by_rate_limit(pair):
    owner, joiner, proj = pair
    invite = owner.generate_p2p_invite()["token"]
    p_uuid, pub = proj["project_uuid"], joiner.crypto.public_key_base64
    joiner.join_p2p_project(invite)  # crea el stub para que existan las filas de colaborador
    for _ in range(5):
        joiner.db.record_rejection(p_uuid, "node_charac")
        joiner.db.add_or_request_collaborator(p_uuid, "node_charac", public_key=pub)
    state = joiner.db.get_join_rate_limit_state(p_uuid, pub)
    assert state["allowed"] is False
    res = joiner.join_p2p_project(invite)
    assert res["success"] is False and res["status"] == "locked"
    assert res["error"] == "Solicitud de unión bloqueada temporalmente por rechazos previos."
    assert res["locked_until"] == state["locked_until"]


# --------------------------- ingest_join_request ---------------------------


def test_ingest_branches(pair):
    owner, joiner, proj = pair
    p_uuid = proj["project_uuid"]
    req = joiner.join_p2p_project(owner.generate_p2p_invite()["token"])["request_code"]

    bad = owner.ingest_join_request("no-es-un-token")
    assert bad["success"] is False and bad["error"]

    missing = joiner.crypto.create_signed_invite_token({"project_uuid": p_uuid})
    assert owner.ingest_join_request(missing) == {
        "success": False, "error": "El request_code no contiene los datos del solicitante requeridos",
    }

    impostor = joiner.crypto.create_signed_invite_token(
        {"project_uuid": p_uuid, "public_key": owner.crypto.public_key_base64, "node_uuid": "node_x"}
    )
    assert owner.ingest_join_request(impostor) == {
        "success": False,
        "error": "La identidad declarada en la solicitud no coincide con la clave que firmó el token",
    }

    owner.active_project_uuid = None
    assert owner.ingest_join_request(req) == {
        "success": False, "error": "No hay un proyecto activo para procesar la solicitud de unión",
    }
    assert owner.ingest_join_request(req, project_uuid="proj_noexiste") == {
        "success": False, "error": "Proyecto no encontrado",
    }
    other = owner.create_project(name="Otro")["project"]
    assert owner.ingest_join_request(req, project_uuid=other["project_uuid"]) == {
        "success": False, "error": "El código de solicitud corresponde a un proyecto distinto a este",
    }

    first = owner.ingest_join_request(req, project_uuid=p_uuid)
    assert first["success"] is True and first["status"] == "pending_approval"
    row = first["collaborator"]
    # Colisión de node_uuid con el propio dueño -> sufijo derivado de la clave del solicitante.
    assert row["node_uuid"].startswith("node_charac_") and row["public_key"] == joiner.crypto.public_key_base64
    again = owner.ingest_join_request(req, project_uuid=p_uuid)
    assert again == {"success": True, "status": "pending_approval", "collaborator": again["collaborator"]}
    assert again["collaborator"]["node_uuid"] == row["node_uuid"]


def test_ingest_refused_on_pending_stub(pair):
    owner, joiner, proj = pair
    joiner.join_p2p_project(owner.generate_p2p_invite()["token"])
    third = _bridge("third")
    req = third.crypto.create_signed_invite_token({
        "project_uuid": proj["project_uuid"], "public_key": third.crypto.public_key_base64,
        "node_uuid": "node_third",
    })
    res = joiner.ingest_join_request(req, project_uuid=proj["project_uuid"])
    assert res["success"] is False and res["status"] == "join_pending"
    assert "registrar solicitudes de unión en este proyecto" in res["error"]


def test_collaborators_listing_exposes_lock(pair):
    owner, joiner, proj = pair
    req = joiner.join_p2p_project(owner.generate_p2p_invite()["token"])["request_code"]
    node = owner.ingest_join_request(req)["collaborator"]["node_uuid"]
    res = owner.get_project_collaborators()
    assert res["success"] is True
    by_node = {c["node_uuid"]: c for c in res["collaborators"]}
    assert by_node[node]["locked_until"] is None
    assert all("locked_until" in c for c in res["collaborators"])


# ------------------------- pending code / retry / live -------------------------


def test_pending_join_request_code(pair):
    owner, joiner, proj = pair
    assert joiner.get_pending_join_request_code("proj_noexiste") == {"success": False, "error": "Proyecto no encontrado"}
    assert owner.get_pending_join_request_code(proj["project_uuid"]) == {
        "success": False, "error": "El proyecto no tiene una solicitud de unión pendiente",
    }
    joiner.join_p2p_project(owner.generate_p2p_invite()["token"])
    res = joiner.get_pending_join_request_code(proj["project_uuid"])
    assert res["success"] is True and res["project"]["project_uuid"] == proj["project_uuid"]
    ok, payload, _ = NodeCryptoManager.decode_and_verify_invite_token(res["request_code"])
    assert ok and payload["rendezvous_nonce"] == "" and payload["github_username"] == "gh_joiner"


def _stub_with_urls(owner, joiner, code_url, gov_url):
    invite = _invite_with(owner, code_repo_url=code_url, gov_repo_url=gov_url)
    return joiner.join_p2p_project(invite)["project"]["project_uuid"]


def test_retry_branches_before_activation(pair, monkeypatch):
    owner, joiner, _ = pair
    assert joiner.retry_p2p_join("proj_noexiste") == {"success": False, "error": "Proyecto no encontrado"}
    assert owner.retry_p2p_join(owner.active_project_uuid)["status"] == "already_active"

    p_none = _stub_with_urls(owner, joiner, "", "")
    res = joiner.retry_p2p_join(p_none)
    assert res["status"] == "no_remote_repos" and res["success"] is False
    assert res["error"].startswith("El dueño del proyecto todavía no ha publicado")


@pytest.mark.parametrize(
    "failing,expected",
    [
        ("gov", "Fallo al clonar repositorio de gobernanza: boom"),
        ("app", "Fallo al clonar repositorio de código: boom"),
    ],
)
def test_retry_clone_failures(pair, monkeypatch, failing, expected):
    owner, joiner, _ = pair
    joiner2 = joiner
    p_uuid = _stub_with_urls(owner, joiner2, "https://github.com/o/app.git", "https://github.com/o/gov")
    accepted = []
    monkeypatch.setattr(joiner2.github, "accept_pending_invitations", lambda repo_full_names=None: accepted.append(repo_full_names))

    def fake_clone(url, dest):
        if (failing == "gov" and url.endswith("/gov")) or (failing == "app" and url.endswith("app.git")):
            return {"success": False, "error": "boom"}
        Path(dest, ".git").mkdir(parents=True, exist_ok=True)
        return {"success": True}

    monkeypatch.setattr(joiner2.github, "git_clone", fake_clone)
    monkeypatch.setattr(joiner2, "ensure_orphan_worktree", lambda *a, **k: None)
    assert joiner2.retry_p2p_join(p_uuid) == {"success": False, "error": expected}
    assert accepted == [{"o/app", "o/gov"}]


def test_retry_pending_access_then_activation(pair, monkeypatch):
    owner, joiner, _ = pair
    p_uuid = _stub_with_urls(owner, joiner, "https://github.com/o/app", "https://github.com/o/gov")
    monkeypatch.setattr(joiner.github, "accept_pending_invitations", lambda repo_full_names=None: None)
    monkeypatch.setattr(joiner.github, "git_clone", lambda url, dest: {"status": "pending_access"})
    assert joiner.retry_p2p_join(p_uuid) == {
        "success": False, "status": "still_pending",
        "error": "Todavía no disponible, inténtalo de nuevo en un momento.",
    }

    calls: List[Any] = []

    def ok_clone(url, dest):
        Path(dest, ".git").mkdir(parents=True, exist_ok=True)
        calls.append(("clone", url))
        return {"success": True}

    monkeypatch.setattr(joiner.github, "git_clone", ok_clone)
    monkeypatch.setattr(joiner, "ensure_orphan_worktree", lambda gov, p: calls.append(("orphan", p)))
    monkeypatch.setattr(joiner.okf, "reconcile_kanban_from_okf", lambda gov, p, db: calls.append(("reconcile", p, db is joiner.db)) or {})
    monkeypatch.setattr(joiner, "sync_three_tiers", lambda project_uuid=None: calls.append(("sync", project_uuid)))

    res = joiner.retry_p2p_join(p_uuid)
    assert res["success"] is True and res["status"] == "active"
    assert res["project"]["join_status"] == "active"
    assert calls == [
        ("clone", "https://github.com/o/gov"),
        ("clone", "https://github.com/o/app"),
        ("orphan", p_uuid),
        ("reconcile", p_uuid, True),
        ("sync", p_uuid),
    ]
    assert joiner.active_project_uuid == p_uuid
    assert joiner.check_join_request_status(p_uuid)["status"] == "already_active"


def test_live_rejection_records_only_for_local_node(pair, monkeypatch):
    owner, joiner, proj = pair
    joiner.join_p2p_project(owner.generate_p2p_invite()["token"])
    seen = []
    monkeypatch.setattr(joiner.db, "record_rejection", lambda p, n: seen.append((p, n)) or {"success": True})
    joiner._handle_transport_message({"type": "join_decision", "decision": "rejected",
                                      "project_uuid": proj["project_uuid"], "node_uuid": "node_otro"})
    assert seen == []
    joiner._handle_transport_message({"type": "join_decision", "decision": "rejected",
                                      "project_uuid": proj["project_uuid"]})
    assert seen == [(proj["project_uuid"], "node_charac")]
