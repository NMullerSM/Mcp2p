"""Regresión para F6: tres NameError latentes por imports faltantes que se tragaban en
silencio dentro de bloques `except Exception` genéricos (yaml en api_bridge.py, logger
en db.py, uuid en mcp/tools.py)."""
import logging

import yaml

from src.app.api_bridge import ApiBridge
from src.mcp.tools import MCPToolsHandler
from src.p2p.transport import NullTransportAdapter
from src.storage.db import DatabaseManager


def test_pending_approvals_loads_precedents_history(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    bridge = ApiBridge(db_manager=db)
    proj = db.create_project("Precedents", str(tmp_path / "ws"), init_git=False)
    p_uuid = proj["project_uuid"]
    gov_dir = proj["gov_repo_path"]

    from pathlib import Path
    docs_okf = Path(gov_dir) / "docs" / "okf"
    arch_dir = docs_okf / "architecture"
    arch_dir.mkdir(parents=True, exist_ok=True)
    (arch_dir / "precedents_index.yaml").write_text(
        yaml.safe_dump({"precedents": [{"id": "RULING-X", "title": "t"}]}), encoding="utf-8"
    )

    res = bridge.get_pending_human_approvals(p_uuid)
    assert res["success"] is True
    assert any(x.get("id") == "RULING-X" for x in res["history_precedents"])


def test_hitl_ruling_detail_loads_yaml(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    bridge = ApiBridge(db_manager=db)
    proj = db.create_project("Rulings", str(tmp_path / "ws"), init_git=False)
    p_uuid = proj["project_uuid"]
    gov_dir = proj["gov_repo_path"]

    from pathlib import Path
    docs_okf = Path(gov_dir) / "docs" / "okf"
    rulings_dir = docs_okf / "architecture" / "rulings"
    rulings_dir.mkdir(parents=True, exist_ok=True)
    (rulings_dir / "RULING-T9.yaml").write_text(
        yaml.safe_dump({"ticket_id": "T9"}), encoding="utf-8"
    )

    res = bridge.get_hitl_item_detail(p_uuid, "ruling", "T9")
    assert res["success"] is True
    assert res["item"]["ticket_id"] == "T9"


def test_rfc_tools_generate_session_id_without_nameerror(tmp_path):
    # Se llama a MCPToolsHandler directamente (no vía server.call_tool): el wrapper de
    # server.py ya resuelve session_id antes de delegar, así que la línea con `uuid.uuid4()`
    # de tools.py solo se ejercita cuando session_id llega vacío hasta el handler.
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    handler = MCPToolsHandler(db_manager=db)
    proj = db.create_project("RFC", str(tmp_path / "ws"), init_git=False)
    p_uuid = proj["project_uuid"]
    db.register_or_update_node("node_rfc_1", "RFC Node", "agent_rfc_1")

    out = handler.send_coordination_rfc(p_uuid, "node_rfc_1", "PROPOSAL", "", "{}", None)
    assert "is not defined" not in out

    out2 = handler.propose_or_apply_task_distribution(p_uuid, "node_rfc_1", "", "{}", False)
    assert "is not defined" not in out2


def test_card_yaml_sync_failure_is_logged_not_nameerror(tmp_path, monkeypatch, caplog):
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    proj = db.create_project("YamlFail", str(tmp_path / "ws"), init_git=False)
    p_uuid = proj["project_uuid"]

    def raiser(self, *args, **kwargs):
        raise OSError("disco lleno simulado")

    monkeypatch.setattr("src.storage.okf_adapter.OKFAdapter.write_card_yaml", raiser)

    caplog.set_level(logging.WARNING, logger="mcp2p.storage.db")
    card = db.create_card(p_uuid, "CARD-YAMLFAIL-01", "titulo")
    assert card.get("card_id") == "CARD-YAMLFAIL-01"
    assert any("disco lleno simulado" in r.getMessage() for r in caplog.records)


def test_main_falls_back_to_null_transport_if_webrtc_adapter_construction_raises(monkeypatch):
    """Tarea 9: _build_p2p_transport debe degradar a NullTransportAdapter ante CUALQUIER
    excepción al construir el adaptador real -- incluido un ImportError por un wheel de
    aiortc ausente o roto -- en vez de dejar que un NameError/ImportError silencioso tumbe
    el arranque de la app (el mismo patrón de regresión que motiva este archivo)."""
    import src.app.main as main_mod

    def _raise_import_error(*_args, **_kwargs):
        raise ImportError("no aiortc wheel available")

    monkeypatch.setattr(main_mod, "WebRTCTransportAdapter", _raise_import_error)
    adapter = main_mod._build_p2p_transport(signaling_url="wss://x", node_crypto=object())
    assert isinstance(adapter, NullTransportAdapter)
