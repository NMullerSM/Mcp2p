"""Tests de Tarea 4.2: PaneRegistry, cierre EGM-22, middleware HTTP ASGI y EmbeddedMcpHub."""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Any, Dict, List

import httpx2
import pytest

from src.adapters.persistence.sqlite_repositories import SqliteCollaborationRepository
from src.core.domain.context import ACTOR_AGENT_MCP
from src.core.factory import ApplicationFactory
from src.core.ports.repositories import CollaborationRepositoryPort
from src.mcp.pane_registry import (
    EmbeddedMcpHub,
    PaneRegistry,
    get_current_pane_session,
    pane_session_context,
)
from src.mcp.server import build_streamable_http_server, create_mcp_server
from src.mcp.tools import MCPToolsHandler
from src.storage.db import DatabaseManager


def _j(raw: str) -> Dict[str, Any]:
    return json.loads(raw)


# =========================================================================
# 1. PaneRegistry: registro de panes, tokens en tiempo constante, cierre y keystrokes
# =========================================================================


def test_pane_registry_registers_four_panes_constant_time_auth_and_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 100.0
    db = DatabaseManager(str(tmp_path / "pane_reg.db"))
    try:
        repo = SqliteCollaborationRepository(db)
        reg = PaneRegistry(collab_repo=repo, clock=lambda: now)

        sessions = [
            reg.register_pane(i, agent_name=f"Agent {i}", cli_profile="claude", role="implementer", pid=1000 + i)
            for i in range(1, 5)
        ]
        assert [s["pane_id"] for s in sessions] == ["pane_1", "pane_2", "pane_3", "pane_4"]
        tokens = [s["session_token"] for s in sessions]
        assert len(set(tokens)) == 4

        # list_panes no expone secretos
        listed = reg.list_panes()
        assert len(listed) == 4
        assert all("session_token" not in item for item in listed)

        # authenticate_token recorre todos los tokens activos usando secrets.compare_digest sin cortocircuito
        compare_calls = 0
        real_compare = secrets.compare_digest

        def _counting_compare(a: str, b: str) -> bool:
            nonlocal compare_calls
            compare_calls += 1
            return real_compare(a, b)

        monkeypatch.setattr(secrets, "compare_digest", _counting_compare)
        now = 150.0
        rec1 = reg.authenticate_token(tokens[0])
        assert rec1 is not None
        assert rec1.pane_id == "pane_1"
        assert rec1.last_seen_at == 150.0
        assert compare_calls == 4  # No hizo cortocircuito tras acertar en el 1º

        assert reg.authenticate_token("token-invalido") is None

        # Re-registro de pane_1 reemplaza e invalida el token previo
        old_token = tokens[0]
        rereg = reg.register_pane("1", node_uuid=rec1.node_uuid, agent_name="Agent 1 v2")
        assert rereg["session_token"] != old_token
        assert reg.authenticate_token(old_token) is None
        assert reg.authenticate_token(rereg["session_token"]) is not None

        # resolve_actor_context por token, por pane_id y fallback
        ctx_tok = reg.resolve_actor_context(token=rereg["session_token"])
        assert ctx_tok.kind == ACTOR_AGENT_MCP
        assert ctx_tok.node_uuid == rec1.node_uuid
        assert ctx_tok.pane_id == "pane_1"
        assert ctx_tok.session_id == "pane_1"

        ctx_pane = reg.resolve_actor_context(pane_id="2")
        assert ctx_pane.node_uuid == sessions[1]["node_uuid"]
        assert ctx_pane.session_id == "pane_2"

        ctx_fb = reg.resolve_actor_context(fallback_node_uuid="fb_node")
        assert ctx_fb.kind == ACTOR_AGENT_MCP
        assert ctx_fb.node_uuid == "fb_node"

        # mark_pane_closed invalida token y marca nodo offline en SQLite
        closed = reg.mark_pane_closed(1)
        assert closed is not None and closed.status == "closed"
        assert reg.authenticate_token(rereg["session_token"]) is None
        node_row = db.get_node_by_uuid(rec1.node_uuid)
        assert node_row is not None and node_row["status"] == "offline"
        assert len(reg.list_panes()) == 3
        assert len(reg.list_panes(include_closed=True)) == 4
    finally:
        db.close()


def test_pane_registry_human_keystroke_cooldown_even_with_small_clock() -> None:
    now = 1.0
    reg = PaneRegistry(clock=lambda: now)

    # Sin pulsaciones previas nunca debe reportar activo aunque el reloj arranque cerca de 0
    assert reg.is_human_typing_active(1, cooldown_seconds=5.0) is False

    # Pulsación previa al spawn se conserva al registrar el panel
    reg.record_human_keystroke(1)
    assert reg.is_human_typing_active("pane_1", cooldown_seconds=5.0) is True
    rec = reg.register_pane(1, node_uuid="node_1")
    assert rec["last_human_keystroke_at"] == 1.0

    now = 4.5
    assert reg.is_human_typing_active(1, cooldown_seconds=5.0) is True

    now = 6.0
    assert reg.is_human_typing_active(1, cooldown_seconds=5.0) is False


# =========================================================================
# 2. SqliteCollaborationRepository: online_only, list_collaborators, mark_node_offline
# =========================================================================


def test_sqlite_collaboration_repository_online_only_and_mark_offline(tmp_path: Path) -> None:
    db = DatabaseManager(str(tmp_path / "collab_online.db"))
    try:
        repo = SqliteCollaborationRepository(db)
        assert isinstance(repo, CollaborationRepositoryPort)

        proj = db.create_project(name="CollabOnline", base_path=str(tmp_path / "ws"))
        p_uuid = proj["project_uuid"]

        db.register_or_update_node("n_online", "On", "claude", status="online")
        db.register_or_update_node("n_busy", "Busy", "codex", status="busy")
        db.register_or_update_node("n_offline", "Off", "gemini", status="offline")
        for nid in ("n_online", "n_busy", "n_offline", "n_missing_swarm"):
            db.add_or_request_collaborator(p_uuid, nid, status="approved")

        all_collabs = repo.get_project_collaborators(p_uuid)
        assert {"n_online", "n_busy", "n_offline", "n_missing_swarm"} <= {
            c["node_uuid"] for c in all_collabs
        }
        assert repo.list_collaborators(p_uuid) == all_collabs

        online_collabs = repo.get_project_collaborators(p_uuid, online_only=True)
        online_ids = {c["node_uuid"] for c in online_collabs}
        assert "n_online" in online_ids
        assert "n_busy" in online_ids
        assert "n_offline" not in online_ids
        assert "n_missing_swarm" not in online_ids
        assert repo.list_collaborators(p_uuid, online_only=True) == online_collabs

        assert repo.mark_node_offline("n_online") is True
        assert repo.mark_node_offline("node_does_not_exist") is False

        online_after = {c["node_uuid"] for c in repo.get_project_collaborators(p_uuid, online_only=True)}
        assert "n_online" not in online_after
        assert "n_busy" in online_after
    finally:
        db.close()


# =========================================================================
# 3. Cierre EGM-22 en MCPToolsHandler
# =========================================================================


def test_egm22_blocks_node_uuid_spoofing_across_governed_mcp_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = DatabaseManager(str(tmp_path / "egm22.db"))
    try:
        proj = db.create_project(name="EGM22Proj", base_path=str(tmp_path / "ws_egm22"))
        p_uuid = proj["project_uuid"]
        repo = SqliteCollaborationRepository(db)
        reg = PaneRegistry(collab_repo=repo)

        p1 = reg.register_pane(1, node_uuid="node_alpha", agent_name="Alpha")
        reg.register_pane(2, node_uuid="node_beta", agent_name="Beta")
        db.add_or_request_collaborator(p_uuid, "node_alpha", status="approved")
        db.add_or_request_collaborator(p_uuid, "node_beta", status="approved")

        handler = MCPToolsHandler(db_manager=db, pane_registry=reg)
        handler.bind_session_token(p1["session_token"])
        assert handler._agent().pane_id == "pane_1"
        assert handler._agent().session_id == "pane_1"
        assert _j(handler.create_kanban_task(p_uuid, "CARD-EGM22", "Validar EGM-22"))["success"] is True

        # 1) claim_kanban_task con node_uuid suplantado -> rechazado antes de tocar DB
        spoof_claim = _j(handler.claim_kanban_task(p_uuid, "CARD-EGM22", node_uuid="node_beta"))
        assert spoof_claim["success"] is False
        assert spoof_claim["code"] == "NODE_UUID_SPOOFING_FORBIDDEN"
        assert db.get_card_by_id(p_uuid, "CARD-EGM22")["status"] == "backlog"

        # Omitiendo node_uuid reclama como node_alpha
        ok_claim = _j(handler.claim_kanban_task(p_uuid, "CARD-EGM22"))
        assert ok_claim["success"] is True
        assert ok_claim["card"]["assignee_node_uuid"] == "node_alpha"

        # 2) release_kanban_task con node_uuid suplantado -> rechazado
        spoof_rel = _j(
            handler.release_kanban_task(
                p_uuid, "CARD-EGM22", node_uuid="node_beta", target_status="in_review"
            )
        )
        assert spoof_rel["success"] is False
        assert spoof_rel["code"] == "NODE_UUID_SPOOFING_FORBIDDEN"
        assert db.get_card_by_id(p_uuid, "CARD-EGM22")["status"] == "in_progress"

        # Coincidiendo con node_alpha funciona normalmente
        ok_rel = _j(
            handler.release_kanban_task(
                p_uuid, "CARD-EGM22", node_uuid="node_alpha", target_status="in_review"
            )
        )
        assert ok_rel["success"] is True
        assert ok_rel["card"]["status"] == "in_review"

        # 3) send_coordination_rfc con sender_node_uuid o node_uuid suplantado -> rechazado
        rfc_payload = {"component": "hub", "intent": "auth", "rationale": "EGM-22"}
        spoof_rfc_sender = _j(
            handler.send_coordination_rfc(
                p_uuid,
                sender_node_uuid="node_beta",
                stage="PROPOSAL",
                session_id="rfc-egm22",
                payload_json=rfc_payload,
            )
        )
        assert spoof_rfc_sender["success"] is False
        assert spoof_rfc_sender["code"] == "NODE_UUID_SPOOFING_FORBIDDEN"

        spoof_rfc_kw = _j(
            handler.send_coordination_rfc(
                p_uuid,
                stage="PROPOSAL",
                session_id="rfc-egm22",
                payload_json=rfc_payload,
                node_uuid="node_beta",
            )
        )
        assert spoof_rfc_kw["success"] is False
        assert spoof_rfc_kw["code"] == "NODE_UUID_SPOOFING_FORBIDDEN"
        assert handler.bus.get_session_turns(p_uuid, "rfc-egm22") == []

        ok_rfc = _j(
            handler.send_coordination_rfc(
                p_uuid,
                stage="PROPOSAL",
                session_id="rfc-egm22",
                payload_json=rfc_payload,
            )
        )
        assert ok_rfc["success"] is True
        assert ok_rfc["event"]["sender_node_uuid"] == "node_alpha"

        # 4) submit_agent_jury_evaluation con node_uuid suplantado vs omitido/coincidente
        ticket_res = _j(
            handler.open_dispute_ticket(
                project_uuid=p_uuid,
                title="Disputa EGM-22",
                collision_reason="Prueba",
                contender_a_node="node_alpha",
                contender_b_node="node_beta",
                proposal_a_ref="a",
                proposal_b_ref="b",
            )
        )
        t_id = ticket_res["ticket"]["ticket_id"]
        prompt_res = _j(handler.get_jury_evaluation_prompt(p_uuid, t_id))
        juror_1 = prompt_res["evaluation_packages"][0]["juror_id"]
        juror_2 = prompt_res["evaluation_packages"][1]["juror_id"]
        evals = json.dumps({
            "OPTION_A": {"kiss": 85, "performance": 80, "security": 90, "ux_hicks": 80, "testability": 85},
            "OPTION_B": {"kiss": 70, "performance": 75, "security": 70, "ux_hicks": 75, "testability": 70},
        })

        spoof_eval = _j(
            handler.submit_agent_jury_evaluation(
                p_uuid, t_id, juror_1, evals, rationale="spoof", node_uuid="node_beta"
            )
        )
        assert spoof_eval["success"] is False
        assert spoof_eval["code"] == "NODE_UUID_SPOOFING_FORBIDDEN"

        ok_eval_omit = _j(handler.submit_agent_jury_evaluation(p_uuid, t_id, juror_1, evals))
        assert ok_eval_omit["success"] is True

        ok_eval_match = _j(
            handler.submit_agent_jury_evaluation(p_uuid, t_id, juror_2, evals, node_uuid="node_alpha")
        )
        assert ok_eval_match["success"] is True

        # Resolución vía MCP2P_SESSION_TOKEN en entorno cuando no hay token vinculado
        handler.bind_session_token(None)
        monkeypatch.setenv("MCP2P_SESSION_TOKEN", p1["session_token"])
        env_spoof = _j(handler.claim_kanban_task(p_uuid, "CARD-EGM22", node_uuid="node_beta"))
        assert env_spoof["success"] is False
        assert env_spoof["code"] == "NODE_UUID_SPOOFING_FORBIDDEN"
        monkeypatch.delenv("MCP2P_SESSION_TOKEN", raising=False)

        # Token inválido o cerrado -> INVALID_PANE_SESSION_TOKEN
        handler.bind_session_token("invalid-token")
        bad_tok = _j(handler.claim_kanban_task(p_uuid, "CARD-EGM22"))
        assert bad_tok["success"] is False
        assert bad_tok["code"] == "INVALID_PANE_SESSION_TOKEN"

        handler.bind_session_token(p1["session_token"])
        reg.mark_pane_closed(1)
        revoked = _j(handler.claim_kanban_task(p_uuid, "CARD-EGM22"))
        assert revoked["success"] is False
        assert revoked["code"] == "INVALID_PANE_SESSION_TOKEN"
    finally:
        db.close()


# =========================================================================
# 4. Middleware ASGI de token HTTP + EmbeddedMcpHub (EGM-60)
# =========================================================================


def test_http_asgi_token_middleware_and_embedded_mcp_hub_lifecycle(tmp_path: Path) -> None:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    desktop_db_path = str(tmp_path / "desktop_hub.db")
    desktop_kernel = ApplicationFactory.create_desktop_kernel(db_path=desktop_db_path)
    p_uuid = desktop_kernel.db.list_projects()[0]["project_uuid"]
    reg = PaneRegistry(collab_repo=desktop_kernel.collab_repo)
    p1 = reg.register_pane(1, node_uuid="node_alpha", agent_name="Alpha Hub")
    desktop_kernel.db.add_or_request_collaborator(p_uuid, "node_alpha", status="approved")

    observed_events: List[Any] = []
    desktop_kernel.event_bus.subscribe("*", observed_events.append)

    # Verificar el middleware ASGI directo (token válido establece get_current_pane_session)
    server_for_asgi = create_mcp_server(
        kernel=desktop_kernel, transport="streamable-http", pane_registry=reg
    )
    uv_asgi, sock_asgi, _ = build_streamable_http_server(server_for_asgi, "127.0.0.1", 0)
    sock_asgi.close()
    seen_session: List[Any] = []

    async def _probe_asgi_middleware() -> None:
        from src.mcp.server import _PaneTokenAuthMiddleware

        async def _inner(scope, receive, send):
            seen_session.append(get_current_pane_session())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        mw = _PaneTokenAuthMiddleware(_inner, reg)
        responses: List[Dict[str, Any]] = []

        async def _recv():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def _send(msg):
            responses.append(msg)

        # Token inválido -> 401
        await mw(
            {"type": "http", "headers": [(b"authorization", b"Bearer wrong-secret")]},
            _recv,
            _send,
        )
        assert responses[0]["status"] == 401

        # Token válido -> setea ContextVar
        responses.clear()
        await mw(
            {
                "type": "http",
                "headers": [(b"authorization", f"Bearer {p1['session_token']}".encode("ascii"))],
            },
            _recv,
            _send,
        )
        assert responses[0]["status"] == 200
        assert seen_session[-1] is not None and seen_session[-1].node_uuid == "node_alpha"
        assert get_current_pane_session() is None

    asyncio.run(_probe_asgi_middleware())
    assert getattr(uv_asgi.config.app, "pane_registry", None) is reg

    # Ahora probar EmbeddedMcpHub real sobre 127.0.0.1:0
    hub = EmbeddedMcpHub(kernel=desktop_kernel, pane_registry=reg, host="127.0.0.1", port=0)
    try:
        url = hub.start(timeout_s=10.0)
        assert hub.is_running() is True
        assert hub.url == url
        assert url.startswith("http://127.0.0.1:") and url.endswith("/mcp")
        # start() es idempotente
        assert hub.start() == url

        async def _http_e2e() -> None:
            # 1) Token inválido -> 401 Unauthorized JSON
            async with httpx2.AsyncClient() as http:
                r = await http.post(
                    url,
                    headers={"Authorization": "Bearer invalid-pane-token"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                )
                assert r.status_code == 401
                assert r.json()["code"] == "INVALID_PANE_SESSION_TOKEN"

            # 2) Cliente MCP real autenticado como pane_1 (node_alpha)
            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {p1['session_token']}"}
            ) as http_client:
                async with streamable_http_client(url, http_client=http_client) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await session.call_tool(
                            "create_kanban_task",
                            {"project_uuid": p_uuid, "card_id": "CARD-HUB-1", "title": "Hub E2E"},
                        )
                        spoof = await session.call_tool(
                            "claim_kanban_task",
                            {"project_uuid": p_uuid, "card_id": "CARD-HUB-1", "node_uuid": "node_beta"},
                        )
                        spoof_data = json.loads(spoof.content[0].text)
                        assert spoof_data["success"] is False
                        assert spoof_data["code"] == "NODE_UUID_SPOOFING_FORBIDDEN"

                        ok_claim = await session.call_tool(
                            "claim_kanban_task",
                            {"project_uuid": p_uuid, "card_id": "CARD-HUB-1"},
                        )
                        ok_data = json.loads(ok_claim.content[0].text)
                        assert ok_data["success"] is True
                        assert ok_data["card"]["assignee_node_uuid"] == "node_alpha"

            # 3) X-MCP2P-Pane-Token inválido -> 401; válido -> 200 MCP initialize
            async with httpx2.AsyncClient() as http:
                r_bad_pane = await http.post(
                    url,
                    headers={"X-MCP2P-Pane-Token": "bad-x-token"},
                    json={"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}},
                )
                assert r_bad_pane.status_code == 401
                assert r_bad_pane.json()["code"] == "INVALID_PANE_SESSION_TOKEN"

        asyncio.run(asyncio.wait_for(_http_e2e(), timeout=20.0))

        # Eventos producidos por el Hub llegan al DomainEventBus del desktop kernel (EGM-60)
        assert any(
            e.event_type == "kanban.lease_claimed" and e.payload.get("node_uuid") == "node_alpha"
            for e in observed_events
        )
    finally:
        hub.stop(timeout_s=5.0)

    assert hub.is_running() is False
    assert hub.url is None

    # EGM-60: la DB del desktop kernel sigue abierta y operativa tras hub.stop()
    try:
        assert desktop_kernel.db.get_card_by_id(p_uuid, "CARD-HUB-1")["assignee_node_uuid"] == "node_alpha"
        assert len(desktop_kernel.db.list_projects()) >= 1
    finally:
        desktop_kernel.db.close()


def test_pane_session_context_isolates_concurrent_contexts() -> None:
    reg = PaneRegistry()
    p1 = reg.register_pane(1, node_uuid="node_1")
    p2 = reg.register_pane(2, node_uuid="node_2")
    rec1 = reg.authenticate_token(p1["session_token"])
    rec2 = reg.authenticate_token(p2["session_token"])

    assert get_current_pane_session() is None
    with pane_session_context(rec1):
        assert get_current_pane_session() is rec1
        with pane_session_context(rec2):
            assert get_current_pane_session() is rec2
        assert get_current_pane_session() is rec1
    assert get_current_pane_session() is None
