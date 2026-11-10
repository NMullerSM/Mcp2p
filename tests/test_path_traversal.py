"""F5: ningún identificador controlado por un agente MCP, un par P2P o la UI debía poder
contener separadores de ruta, '..', bytes nulos ni saltos de línea, porque varios de ellos
terminan en rutas de filesystem o refs de git (card_id, project_uuid, item_id)."""
import json
import os
from pathlib import Path

import pytest

from src.app.api_bridge import ApiBridge
from src.mcp.server import create_mcp_server
from src.mcp.tools import MCPToolsHandler
from src.storage.db import DatabaseManager
from src.storage.okf_adapter import OKFAdapter
from src.storage.validators import InvalidIdentifierError, is_safe_id

BAD_IDS = [
    "",
    "../../../../tmp/mcp2p_pwn",
    "..\\..\\evil",
    "a/b",
    "sub/../x",
    "CON",
    "x" * 65,
    "ok\nnl",
    "nul\x00b",
    " lead",
    "..hidden",
]

GOOD_IDS = ["TASK-INIT-01", "CARD-123", "c-101", "T_1"]


@pytest.mark.parametrize("bad_id", BAD_IDS)
def test_card_id_rejected_at_creation_entry_points(tmp_path, bad_id):
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    proj = db.create_project("Traversal", str(tmp_path / "ws"), init_git=False)
    p_uuid = proj["project_uuid"]
    cards_dir = Path(proj["gov_repo_path"]) / "docs" / "okf" / "kanban" / "cards"

    with pytest.raises(ValueError):
        db.create_card(p_uuid, bad_id, "x")
    assert db.get_card_by_id(p_uuid, bad_id) is None

    bridge = ApiBridge(db_manager=db)
    bridge.active_project_uuid = p_uuid
    res = bridge.create_kanban_card(bad_id, "x")
    # create_kanban_card hace .strip() antes de validar: un id que SOLO es inválido por
    # espacios al borde (p.ej. " lead") se sanea legítimamente ahí, no es un bypass.
    if not is_safe_id(bad_id.strip()):
        assert res["success"] is False

    handler = MCPToolsHandler(db_manager=db)
    out = json.loads(handler.create_kanban_task(p_uuid, bad_id, "x"))
    assert out["success"] is False

    assert not list(tmp_path.rglob("*mcp2p_pwn*"))
    if bad_id and "/" not in bad_id and "\\" not in bad_id and "\x00" not in bad_id:
        assert not (cards_dir / f"{bad_id}.yaml").exists()


def test_card_id_relpath_escape_rejected(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    proj = db.create_project("Traversal2", str(tmp_path / "ws"), init_git=False)
    p_uuid = proj["project_uuid"]
    cards_dir = Path(proj["gov_repo_path"]) / "docs" / "okf" / "kanban" / "cards"

    escape_target = tmp_path / "escape_target"
    rel = os.path.relpath(escape_target, cards_dir)

    with pytest.raises(ValueError):
        db.create_card(p_uuid, rel, "x")
    assert not escape_target.with_suffix(".yaml").exists()


@pytest.mark.parametrize("good_id", GOOD_IDS)
def test_valid_card_ids_still_accepted(tmp_path, good_id):
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    proj = db.create_project("ValidIds", str(tmp_path / "ws"), init_git=False)
    p_uuid = proj["project_uuid"]
    cards_dir = Path(proj["gov_repo_path"]) / "docs" / "okf" / "kanban" / "cards"

    card = db.create_card(p_uuid, good_id, "x")
    assert card["card_id"] == good_id
    assert (cards_dir / f"{good_id}.yaml").exists()


@pytest.mark.anyio
async def test_create_kanban_task_without_card_id_leaves_no_phantom_row(tmp_path):
    """Regresión exacta de docs/BUG_REPORT_CREATE_KANBAN_TASK_CARD_ID_VACIO.md: la primera
    llamada sin card_id ya no debe insertar una fila fantasma con card_id='', y la segunda
    llamada no debe fallar por UNIQUE constraint (porque la primera nunca escribió nada)."""
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    server = create_mcp_server(db_path=db_path)
    proj = db.create_project("Phantom", str(tmp_path / "ws"), init_git=False)
    p_uuid = proj["project_uuid"]

    for _ in range(2):
        res = await server.call_tool("create_kanban_task", {"project_uuid": p_uuid, "title": "x"})
        out = json.loads(res.content[0].text)
        assert out["success"] is False
        assert "card_id" in out["error"]
        assert "UNIQUE" not in out["error"]

    assert db.get_card_by_id(p_uuid, "") is None
    with db.get_connection() as conn:
        cnt = conn.execute(
            "SELECT COUNT(*) as c FROM kanban_cards WHERE card_id = '' AND project_uuid = ?;", (p_uuid,)
        ).fetchone()["c"]
    assert cnt == 0


def test_reconcile_skips_card_yaml_with_traversal_id(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    proj = db.create_project("ReconcileTraversal", str(tmp_path / "ws"), init_git=False)
    p_uuid = proj["project_uuid"]
    gov_path = Path(proj["gov_repo_path"])
    docs_okf = gov_path / "docs" / "okf"
    cards_dir = docs_okf / "kanban" / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)

    (cards_dir / "evil.yaml").write_text(
        'id: "../../escape"\ntitle: "x"\nstatus: "backlog"\n', encoding="utf-8"
    )
    (cards_dir / "good.yaml").write_text(
        'id: "CARD-GOOD"\ntitle: "x"\nstatus: "backlog"\n', encoding="utf-8"
    )

    res = OKFAdapter().reconcile_kanban_from_okf(gov_path, p_uuid, db)
    assert res["cards_loaded"] == 1
    assert db.get_card_by_id(p_uuid, "CARD-GOOD") is not None
    assert db.get_card_by_id(p_uuid, "../../escape") is None


def test_get_task_full_context_never_reads_outside_cards_dir(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    proj = db.create_project("ContextTraversal", str(tmp_path / "ws"), init_git=False)
    p_uuid = proj["project_uuid"]
    gov_path = Path(proj["gov_repo_path"])
    cards_dir = gov_path / "docs" / "okf" / "kanban" / "cards"

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.yaml").write_text("secret: TOPSECRET\n", encoding="utf-8")

    legacy_id = os.path.relpath(outside_dir / "secret", cards_dir)
    now = "2024-01-01T00:00:00+00:00"
    with db.get_connection() as conn:
        with conn:
            conn.execute(
                "INSERT INTO kanban_cards (card_id, project_uuid, title, status, created_at, updated_at) "
                "VALUES (?, ?, 'legacy', 'backlog', ?, ?);",
                (legacy_id, p_uuid, now, now),
            )

    handler = MCPToolsHandler(db_manager=db)
    out = handler.get_task_full_context(p_uuid, legacy_id)
    assert "TOPSECRET" not in out
    assert json.loads(out)["success"] is False


def test_okf_sinks_reject_traversal_ids(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    proj = db.create_project("SinksTraversal", str(tmp_path / "ws"), init_git=True)
    p_uuid = proj["project_uuid"]
    app_path = proj["code_repo_path"]

    handler = MCPToolsHandler(db_manager=db)
    res = handler.manage_task_worktree(p_uuid, "../../escape_wt", "node_x", "create")
    res_data = json.loads(res) if isinstance(res, str) else res
    assert res_data["success"] is False
    assert not (Path(app_path).parent.parent / "escape_wt").exists()

    out2 = json.loads(handler.run_okf_micro_script(p_uuid, "scaffold_decision", json.dumps({
        "artifact_type": "decision", "item_id": "../../../escape_dec", "title": "t", "content": "c"
    })))
    assert out2["success"] is False
    assert not list(tmp_path.rglob("*escape_dec*"))


def test_join_p2p_project_rejects_signed_traversal_project_uuid(tmp_path):
    from src.p2p.crypto import NodeCryptoManager

    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    db_dir = Path(db_path).parent
    bridge = ApiBridge(db_manager=db)

    peer = NodeCryptoManager(key_path=tmp_path / "peer.key")
    token = peer.create_signed_invite_token({
        "project_uuid": "proj_../../../../escape_join",
        "name": "Evil",
        "code_repo_url": "",
        "gov_repo_url": "",
    })

    before = set(os.listdir(db_dir)) if db_dir.exists() else set()
    res = bridge.join_p2p_project(token)
    assert res["success"] is False
    assert "project_uuid" in res["error"]
    assert db.get_project_by_uuid("proj_../../../../escape_join") is None
    after = set(os.listdir(db_dir)) if db_dir.exists() else set()
    assert after - before <= {"workspace"}
