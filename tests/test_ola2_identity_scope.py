"""Ola 2 (Bloque 2 del dossier 36 + D2/D7/D8): identidad estable de panel y alcance por proyecto.

Pruebas adversarias: un agente del proyecto A intenta ver, escribir o leer asignaciones del proyecto B (por nodo,
por ranura, suplantando la identidad de B, por la cola comodin); la identidad debe sobrevivir a cierres y reinicios;
el Auto-Relay es por panel, apagado por defecto y persistido; la guardia `warn` registra sin romper y `enforce`
bloquea antes de ejecutar; la migracion de la tabla nueva es idempotente sobre una BD del esquema anterior.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.a2a.bus import A2ABus
from src.adapters.persistence.sqlite_repositories import SqlitePaneIdentityRepository
from src.core.domain.pane_identity import (
    UNSCOPED_PROJECT_TAG,
    is_valid_pane_node_uuid,
    new_pane_node_uuid,
    project_tag,
)
from src.core.events import DomainEvent, DomainEventBus
from src.core.factory import ApplicationFactory
from src.core.kernel import CoreKernel
from src.core.services import SwarmRelayOrchestrator
from src.mcp.pane_registry import PaneRegistry
from src.mcp.tools import MCPToolsHandler
from src.storage.db import DatabaseManager

ROOT = Path(__file__).resolve().parents[1]


def _j(raw: str) -> Dict[str, Any]:
    return json.loads(raw)


# ----------------------------------------------------------------------------------------------------------------
# T2.1 Identidad estable
# ----------------------------------------------------------------------------------------------------------------


def test_identity_format_and_validation() -> None:
    proj = "3f2a9c1b-0000-4000-8000-000000000001"
    node = new_pane_node_uuid(proj, "pane_2")
    assert is_valid_pane_node_uuid(node)
    assert node.startswith("pane-3f2a9c1b-2-")
    assert project_tag("") == UNSCOPED_PROJECT_TAG
    assert is_valid_pane_node_uuid(new_pane_node_uuid("", "pane_1"))
    # Un identificador no-UUID no se copia tal cual (podria llevar cualquier cosa): se resume con SHA-256.
    assert len(project_tag("proyecto con espacios / y barras")) == 8
    for bad in ("pane_1_abcd", "pane-XYZ-1-0000", "pane-3f2a9c1b-1-zzzzzzzz", "", None, 7):
        assert not is_valid_pane_node_uuid(bad)


def test_same_project_and_slot_keeps_identity_across_close_reopen_and_new_tokens(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "ident.db"))
    try:
        proj = db.create_project("A", str(tmp_path / "a"))["project_uuid"]
        reg = PaneRegistry(identity_repo=SqlitePaneIdentityRepository(db))
        first = reg.register_pane("pane_1", project_uuid=proj)
        reg.mark_pane_closed("pane_1")
        assert reg.authenticate_token(first["session_token"]) is None
        # `1`, `'1'` y `'pane_1'` son la misma ranura (compatibilidad con normalize_pane_id).
        second = reg.register_pane(1, project_uuid=proj)
        third = reg.register_pane("1", project_uuid=proj)
        assert first["node_uuid"] == second["node_uuid"] == third["node_uuid"]
        assert is_valid_pane_node_uuid(first["node_uuid"])
        # La SESION es efimera: token nuevo en cada registro y el anterior deja de valer.
        assert len({first["session_token"], second["session_token"], third["session_token"]}) == 3
        assert reg.authenticate_token(second["session_token"]) is None
        assert reg.authenticate_token(third["session_token"]).node_uuid == first["node_uuid"]
    finally:
        db.close()


def test_identity_survives_a_kernel_restart(tmp_path: Path) -> None:
    db_path = str(tmp_path / "restart.db")
    db1 = DatabaseManager(db_path=db_path)
    proj = db1.create_project("A", str(tmp_path / "a"))["project_uuid"]
    kernel1 = ApplicationFactory.create_mcp_kernel(db_manager=db1)
    assert isinstance(kernel1.pane_identity_repo, SqlitePaneIdentityRepository)
    node_before = PaneRegistry(identity_repo=kernel1.pane_identity_repo).register_pane(
        "pane_3", project_uuid=proj
    )["node_uuid"]
    kernel1.close()
    db1.close()

    db2 = DatabaseManager(db_path=db_path)
    kernel2 = ApplicationFactory.create_mcp_kernel(db_manager=db2)
    try:
        reg = PaneRegistry(identity_repo=kernel2.pane_identity_repo)
        assert reg.register_pane("pane_3", project_uuid=proj)["node_uuid"] == node_before
    finally:
        kernel2.close()
        db2.close()


def test_two_projects_same_slot_get_different_identities(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "two.db"))
    try:
        proj_a = db.create_project("A", str(tmp_path / "a"))["project_uuid"]
        proj_b = db.create_project("B", str(tmp_path / "b"))["project_uuid"]
        reg = PaneRegistry(identity_repo=SqlitePaneIdentityRepository(db))
        node_a = reg.register_pane("pane_1", project_uuid=proj_a)["node_uuid"]
        reg.mark_pane_closed("pane_1")
        node_b = reg.register_pane("pane_1", project_uuid=proj_b)["node_uuid"]
        reg.mark_pane_closed("pane_1")
        assert node_a != node_b
        assert node_a.split("-")[1] == project_tag(proj_a) and node_b.split("-")[1] == project_tag(proj_b)
        # Volver al proyecto A en la misma ranura recupera SU identidad, no la de B.
        assert reg.register_pane("pane_1", project_uuid=proj_a)["node_uuid"] == node_a
        assert {(i["project_uuid"], i["pane_slot"]) for i in db.list_pane_identities()} == {
            (proj_a, "pane_1"),
            (proj_b, "pane_1"),
        }
    finally:
        db.close()


def test_without_repository_identity_is_stable_for_the_process_lifetime() -> None:
    reg = PaneRegistry()
    node = reg.register_pane("pane_2", project_uuid="p")["node_uuid"]
    reg.mark_pane_closed("pane_2")
    assert reg.register_pane("pane_2", project_uuid="p")["node_uuid"] == node
    assert reg.register_pane("pane_2", project_uuid="q")["node_uuid"] != node


# ----------------------------------------------------------------------------------------------------------------
# Migracion idempotente sobre una BD del esquema anterior
# ----------------------------------------------------------------------------------------------------------------


def test_migration_is_idempotent_over_a_database_from_the_previous_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    db = DatabaseManager(db_path=str(db_path))
    proj = db.create_project("Viejo", str(tmp_path / "old"))["project_uuid"]
    db.create_card(project_uuid=proj, card_id="CARD-OLD", title="Tarjeta previa")
    db.close()

    # Esquema anterior: sin `pane_identities`.
    with sqlite3.connect(db_path) as raw:
        raw.execute("DROP TABLE pane_identities;")
    assert "pane_identities" not in _tables(db_path)

    db2 = DatabaseManager(db_path=str(db_path))
    try:
        assert "pane_identities" in _tables(db_path)
        assert [c["card_id"] for c in db2.get_cards(project_uuid=proj)] == ["CARD-OLD"]
        ident = db2.get_or_create_pane_identity(proj, "pane_1")
        assert ident["auto_relay_enabled"] is False
    finally:
        db2.close()

    # Reabrir dos veces no duplica ni pierde nada.
    for _ in range(2):
        db3 = DatabaseManager(db_path=str(db_path))
        try:
            assert db3.get_pane_identity(proj, "pane_1")["node_uuid"] == ident["node_uuid"]
            assert len(db3.list_pane_identities()) == 1
        finally:
            db3.close()


def test_migration_adds_missing_columns_to_an_earlier_pane_identities_table(tmp_path: Path) -> None:
    db_path = tmp_path / "partial.db"
    DatabaseManager(db_path=str(db_path)).close()
    with sqlite3.connect(db_path) as raw:
        raw.execute("DROP TABLE pane_identities;")
        raw.execute(
            "CREATE TABLE pane_identities (project_uuid TEXT NOT NULL, pane_slot TEXT NOT NULL, "
            "node_uuid TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, PRIMARY KEY (project_uuid, pane_slot));"
        )
        raw.execute(
            "INSERT INTO pane_identities VALUES ('p', 'pane_1', 'pane-unscoped-1-0000abcd', '2026-01-01');"
        )
    db = DatabaseManager(db_path=str(db_path))
    try:
        ident = db.get_pane_identity("p", "pane_1")
        assert ident["node_uuid"] == "pane-unscoped-1-0000abcd"
        assert ident["auto_relay_enabled"] is False and ident["last_spawned_at"] is None
    finally:
        db.close()


def _tables(db_path: Path) -> List[str]:
    with sqlite3.connect(db_path) as raw:
        return [r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")]


# ----------------------------------------------------------------------------------------------------------------
# T2.5 Alcance de las herramientas del relevo: A no ve ni toca B
# ----------------------------------------------------------------------------------------------------------------


class _TwoProjects:
    def __init__(self, tmp_path: Path) -> None:
        self.db = DatabaseManager(db_path=str(tmp_path / "scope.db"))
        self.proj_a = self.db.create_project("A", str(tmp_path / "a"))["project_uuid"]
        self.proj_b = self.db.create_project("B", str(tmp_path / "b"))["project_uuid"]
        self.event_bus = DomainEventBus()
        self.bus = A2ABus(self.db)
        self.kernel = ApplicationFactory.create_mcp_kernel(
            db_manager=self.db, bus=self.bus, event_bus=self.event_bus
        )
        self.registry = PaneRegistry(identity_repo=self.kernel.pane_identity_repo)
        self.a1 = self.registry.register_pane("pane_1", project_uuid=self.proj_a)
        self.a2 = self.registry.register_pane("pane_2", project_uuid=self.proj_a)
        self.b3 = self.registry.register_pane("pane_3", project_uuid=self.proj_b)
        self.orch = SwarmRelayOrchestrator(event_bus=self.event_bus, bus=self.bus, pane_registry=self.registry)
        self.handler = MCPToolsHandler(
            kernel=self.kernel, owns_kernel=False, pane_registry=self.registry, relay_orchestrator=self.orch
        )

    def close(self) -> None:
        self.handler.close()
        self.orch.close()
        self.kernel.close()
        self.db.close()


@pytest.fixture
def two(tmp_path: Path):
    t = _TwoProjects(tmp_path)
    try:
        yield t
    finally:
        t.close()


def test_agent_of_project_a_only_lists_panes_of_a(two: _TwoProjects) -> None:
    raw = two.handler.list_swarm_panes(session_token=two.a1["session_token"])
    out = _j(raw)
    assert out["success"] is True and out["project_uuid"] == two.proj_a
    assert {p["pane_id"] for p in out["panes"]} == {"pane_1", "pane_2"}
    assert two.b3["node_uuid"] not in raw and two.proj_b not in raw
    for pane in out["panes"]:
        assert "cwd" not in pane and "pid" not in pane and "session_token" not in pane

    # Pedir el proyecto B explicitamente: prohibido, sin revelar nada de sus paneles.
    raw_b = two.handler.list_swarm_panes(project_uuid=two.proj_b, session_token=two.a1["session_token"])
    out_b = _j(raw_b)
    assert out_b["success"] is False and out_b["code"] == "PROJECT_SCOPE_FORBIDDEN"
    assert two.b3["node_uuid"] not in raw_b


def test_agent_of_project_a_cannot_write_to_panes_of_b_and_existence_is_not_leaked(two: _TwoProjects) -> None:
    tok = two.a1["session_token"]

    def send(**kw: Any) -> Dict[str, Any]:
        return _j(two.handler.send_pane_message(message="hola", session_token=tok, **kw))

    by_node = send(project_uuid=two.proj_a, target_node_uuid=two.b3["node_uuid"])
    by_slot = send(project_uuid=two.proj_a, target_pane_id="pane_3")
    ghost = send(project_uuid=two.proj_a, target_node_uuid="pane-unscoped-9-deadbeef")
    for res in (by_node, by_slot, ghost):
        assert res["success"] is False and res["code"] == "TARGET_NOT_FOUND"
    # La respuesta para un panel real de B es identica a la de uno inexistente.
    assert by_node["error"] == ghost["error"] == by_slot["error"]

    cross = send(project_uuid=two.proj_b, target_node_uuid=two.b3["node_uuid"])
    assert cross["success"] is False and cross["code"] == "PROJECT_SCOPE_FORBIDDEN"

    # Suplantar la identidad estable de B con el token de A.
    spoof = send(project_uuid=two.proj_a, target_pane_id="pane_2", node_uuid=two.b3["node_uuid"])
    assert spoof["code"] == "NODE_UUID_SPOOFING_FORBIDDEN"

    # B no recibio nada; A2 (mismo proyecto) si recibe un envio legitimo.
    b_inbox = _j(two.handler.await_next_assignment(session_token=two.b3["session_token"]))
    assert b_inbox["has_messages"] is False and b_inbox["has_assignment"] is False
    ok = send(project_uuid=two.proj_a, target_pane_id="pane_2")
    assert ok["success"] is True and ok["target_node_uuid"] == two.a2["node_uuid"]
    got = _j(two.handler.await_next_assignment(session_token=two.a2["session_token"]))
    assert [m["text"] for m in got["messages"]] == ["hola"]
    assert got["messages"][0]["from"]["node_uuid"] == two.a1["node_uuid"]


def test_agent_of_project_a_never_receives_assignments_of_b_from_the_wildcard_queue(two: _TwoProjects) -> None:
    two.event_bus.publish(
        DomainEvent(event_type="kanban.card_created", project_uuid=two.proj_b, payload={"card_id": "CARD-B"})
    )
    forbidden = _j(
        two.handler.await_next_assignment(project_uuid=two.proj_b, session_token=two.a1["session_token"])
    )
    assert forbidden["code"] == "PROJECT_SCOPE_FORBIDDEN"
    a_view = _j(two.handler.await_next_assignment(session_token=two.a1["session_token"]))
    assert a_view["has_assignment"] is False
    b_view = _j(two.handler.await_next_assignment(session_token=two.b3["session_token"]))
    assert b_view["has_assignment"] is True and b_view["assignment"]["card_id"] == "CARD-B"


def test_unscoped_pane_sees_nothing_of_any_project(two: _TwoProjects) -> None:
    loose = two.registry.register_pane("pane_4")  # abierto sin proyecto activo
    two.event_bus.publish(
        DomainEvent(event_type="kanban.card_created", project_uuid=two.proj_a, payload={"card_id": "CARD-A"})
    )
    out = _j(two.handler.list_swarm_panes(session_token=loose["session_token"]))
    assert [p["pane_id"] for p in out["panes"]] == ["pane_4"]
    assert _j(two.handler.await_next_assignment(session_token=loose["session_token"]))["has_assignment"] is False
    res = _j(
        two.handler.send_pane_message(
            project_uuid=two.proj_a, target_pane_id="pane_1", message="x", session_token=loose["session_token"]
        )
    )
    assert res["code"] == "PROJECT_SCOPE_FORBIDDEN"


def test_mcp_tools_never_ask_the_adapter_for_all_projects() -> None:
    tree = ast.parse((ROOT / "src" / "mcp" / "tools.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == (
            "KanbanSwarmAdapter"
        ):
            assert all(kw.arg != "allow_all_projects" for kw in node.keywords)


# ----------------------------------------------------------------------------------------------------------------
# D2 Auto-Relay por panel, apagado por defecto y persistido
# ----------------------------------------------------------------------------------------------------------------


def test_auto_relay_is_per_pane_and_off_by_default(two: _TwoProjects) -> None:
    for rec in (two.a1, two.a2, two.b3):
        assert rec["auto_relay_enabled"] is False
    assert two.registry.set_auto_relay("pane_2", True).auto_relay_enabled is True
    for target in (two.a1, two.a2):
        two.event_bus.publish(
            DomainEvent(
                event_type="kanban.card_assigned",
                project_uuid=two.proj_a,
                payload={"card_id": f"CARD-{target['pane_id']}", "target_node_uuid": target["node_uuid"]},
            )
        )
    a1 = _j(two.handler.await_next_assignment(session_token=two.a1["session_token"]))
    a2 = _j(two.handler.await_next_assignment(session_token=two.a2["session_token"]))
    assert all(m["kind"] != "relay_notice" for m in a1["messages"])  # pane_1 lo tiene apagado
    assert [m["kind"] for m in a2["messages"]] == ["relay_notice"]
    assert a1["has_assignment"] and a2["has_assignment"]  # la asignacion en si llega igual
    listing = _j(two.handler.list_swarm_panes(session_token=two.a2["session_token"]))
    assert listing["auto_relay_enabled"] is True
    assert {p["pane_id"]: p["auto_relay_enabled"] for p in listing["panes"]} == {"pane_1": False, "pane_2": True}


def test_auto_relay_notice_never_goes_to_a_pane_of_another_project(two: _TwoProjects) -> None:
    two.registry.set_auto_relay("pane_3", True)
    # Un evento del proyecto A que nombra al nodo de B no deja aviso en B.
    two.event_bus.publish(
        DomainEvent(
            event_type="kanban.card_assigned",
            project_uuid=two.proj_a,
            payload={"card_id": "CARD-X", "target_node_uuid": two.b3["node_uuid"]},
        )
    )
    b_view = _j(two.handler.await_next_assignment(session_token=two.b3["session_token"]))
    assert b_view["has_messages"] is False and b_view["has_assignment"] is False


def test_auto_relay_survives_hub_restart_and_is_scoped_to_project_and_slot(tmp_path: Path) -> None:
    db_path = str(tmp_path / "relay.db")
    db = DatabaseManager(db_path=db_path)
    proj_a = db.create_project("A", str(tmp_path / "a"))["project_uuid"]
    proj_b = db.create_project("B", str(tmp_path / "b"))["project_uuid"]
    reg = PaneRegistry(identity_repo=SqlitePaneIdentityRepository(db))
    reg.register_pane("pane_1", project_uuid=proj_a)
    reg.set_auto_relay("pane_1", True)
    assert reg.set_auto_relay("pane_6", True) is None  # sin panel activo no se toca nada
    db.close()

    db2 = DatabaseManager(db_path=db_path)
    try:
        reg2 = PaneRegistry(identity_repo=SqlitePaneIdentityRepository(db2))
        assert reg2.register_pane("pane_1", project_uuid=proj_a)["auto_relay_enabled"] is True
        reg2.mark_pane_closed("pane_1")
        assert reg2.register_pane("pane_1", project_uuid=proj_b)["auto_relay_enabled"] is False
        assert reg2.register_pane("pane_2", project_uuid=proj_a)["auto_relay_enabled"] is False
    finally:
        db2.close()


# ----------------------------------------------------------------------------------------------------------------
# Puente de la GUI: pty_spawn_pane liga el panel al proyecto y pty_set_auto_relay es por panel
# ----------------------------------------------------------------------------------------------------------------


class _FakeTerminal:
    max_panes = 6

    def __init__(self) -> None:
        self.spawned: List[Dict[str, Any]] = []
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def is_available(self) -> bool:
        return True

    def get_status(self) -> Dict[str, Any]:
        return {"available": True, "degraded": False, "mode": "fake"}

    def list_sessions(self) -> List[Dict[str, Any]]:
        return list(self.sessions.values())

    def spawn_session(self, **kwargs: Any) -> Dict[str, Any]:
        self.spawned.append(kwargs)
        sess = {"pane_id": kwargs["pane_id"], "alive": True, "session_id": f"s{len(self.spawned)}", "pid": 4000}
        self.sessions[kwargs["pane_id"]] = sess
        return {"success": True, **sess}

    def close_session(self, pane_id: Any) -> Dict[str, Any]:
        self.sessions.pop(str(pane_id), None)
        return {"success": True, "pane_id": str(pane_id)}

    def close(self) -> None:
        self.sessions.clear()


@pytest.fixture
def gui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from src.app.api_bridge import ApiBridge
    import src.app.ui_adapters.extensions_lab_ui as lab

    monkeypatch.setattr(lab, "is_unsupported_terminal_platform", lambda: False)
    monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)
    bridge = ApiBridge(db_manager=DatabaseManager(db_path=str(tmp_path / "gui.db")))
    term = _FakeTerminal()
    bridge.core_kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, term)
    try:
        yield bridge, term
    finally:
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()


def test_spawn_binds_the_pane_to_the_requested_project_and_keeps_identity(gui, tmp_path: Path) -> None:
    bridge, term = gui
    proj_a = bridge.db.create_project("A", str(tmp_path / "ga"))["project_uuid"]
    proj_b = bridge.db.create_project("B", str(tmp_path / "gb"))["project_uuid"]

    bad = bridge.pty_spawn_pane("pane_1", "custom_shell", start_hub=False, project_uuid="no-existe")
    assert bad["success"] is False and bad["code"] == "PROJECT_NOT_FOUND"
    assert term.spawned == []

    first = bridge.pty_spawn_pane("pane_1", "custom_shell", start_hub=False, project_uuid=proj_a)
    assert first["success"] is True and first["project_uuid"] == proj_a
    assert first["pane"]["project_uuid"] == proj_a and first["pane"]["auto_relay_enabled"] is False
    assert term.spawned[-1]["node_uuid"] == first["node_uuid"]
    bridge.pty_close_pane("pane_1")

    again = bridge.pty_spawn_pane("pane_1", "custom_shell", start_hub=False, project_uuid=proj_a)
    assert again["node_uuid"] == first["node_uuid"]
    bridge.pty_close_pane("pane_1")
    other = bridge.pty_spawn_pane("pane_1", "custom_shell", start_hub=False, project_uuid=proj_b)
    assert other["node_uuid"] != first["node_uuid"]

    status = bridge.pty_get_matrix_status()
    assert {p["pane_id"]: p["project_uuid"] for p in status["panes"]} == {"pane_1": proj_b}

    # Sin proyecto explicito: el proyecto activo del puente.
    bridge.select_project(proj_a)
    implicit = bridge.pty_spawn_pane("pane_2", "custom_shell", start_hub=False)
    assert implicit["project_uuid"] == proj_a


def test_gui_snapshot_lists_one_project_or_all_only_on_explicit_request(gui, tmp_path: Path) -> None:
    bridge, _term = gui
    proj_a = bridge.db.create_project("A", str(tmp_path / "sa"))["project_uuid"]
    proj_b = bridge.db.create_project("B", str(tmp_path / "sb"))["project_uuid"]
    bridge.pty_spawn_pane("pane_1", "custom_shell", start_hub=False, project_uuid=proj_a)
    bridge.pty_spawn_pane("pane_2", "custom_shell", start_hub=False, project_uuid=proj_b)
    only_a = bridge.pty_get_kanban_swarm_snapshot(proj_a)
    assert [p["pane_id"] for p in only_a["panes"]] == ["pane_1"]
    every = bridge.pty_get_kanban_swarm_snapshot(None, True)
    assert sorted(p["pane_id"] for p in every["panes"]) == ["pane_1", "pane_2"]


def test_gui_auto_relay_toggle_is_per_pane_and_persists(gui, tmp_path: Path) -> None:
    bridge, _term = gui
    proj = bridge.db.create_project("A", str(tmp_path / "ra"))["project_uuid"]
    bridge.pty_spawn_pane("pane_1", "custom_shell", start_hub=False, project_uuid=proj)
    bridge.pty_spawn_pane("pane_2", "custom_shell", start_hub=False, project_uuid=proj)
    bridge.confirmations.dialog = lambda _t, _m: True
    on = bridge.pty_set_auto_relay("pane_2", True)
    assert on["success"] is True and on["auto_relay_enabled"] is True and on["project_uuid"] == proj
    panes = {p["pane_id"]: p["auto_relay_enabled"] for p in bridge.pty_get_matrix_status()["panes"]}
    assert panes == {"pane_1": False, "pane_2": True}
    bridge.pty_close_pane("pane_2")
    reopened = bridge.pty_spawn_pane("pane_2", "custom_shell", start_hub=False, project_uuid=proj)
    assert reopened["pane"]["auto_relay_enabled"] is True
    assert bridge.db.get_pane_identity(proj, "pane_2")["auto_relay_enabled"] is True

