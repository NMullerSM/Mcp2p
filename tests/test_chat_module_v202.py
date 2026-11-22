"""Módulo de chat (WP-8): dominio, servicio con puntos de conexión, adaptador local, vista incremental del relevo,
puente y reglas de arquitectura (el módulo es autónomo y no importa infraestructura)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pytest

from src.chat.adapters.local_agents import LocalAgentsAdapter
from src.chat.domain import (
    ChatEntry,
    Participant,
    SendResult,
    bubble_color,
    group_participants,
    parse_mentions,
    tick_for,
)
from src.chat.service import MAX_FEED_LIMIT, ChatService

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT = "proj-1"


def agent(n: int, *, cli: str = "claude", role: str = "peer", aliases=()) -> Participant:
    return Participant(f"pane:node-{n}", f"Terminal {n} · {cli}", kind="agent", mention=f"@terminal{n}", role=role,
                       role_label=role.capitalize(), cli_profile=cli, aliases=tuple(aliases))


def entry(i: int, *, rev: int = 0, text: str = "hola", sender: Participant | None = None) -> ChatEntry:
    return ChatEntry(id=f"m{i}", ts_ms=i * 10, rev_ms=rev or i * 10, project_uuid=PROJECT, source="t",
                     sender=sender or agent(1), text=text)


# ---------------------------------------------------------------- dominio

def test_mentions_resolve_by_handle_role_alias_and_all_without_repeating_targets():
    team = [agent(1, role="orchestrator"), agent(2, aliases=("claude",)), agent(3, cli="codex", role="auditor")]
    p = parse_mentions("@terminal2 hazlo y @Orquestador revisa; @claude confirma", team)
    assert [t.principal for t in p.targets] == ["pane:node-2", "pane:node-1"]
    assert p.unknown == () and p.ambiguous == () and p.broadcast is False
    allp = parse_mentions("@todos a trabajar @terminal1", team)
    assert allp.broadcast and len(allp.targets) == 3
    assert [t.principal for t in parse_mentions("@AUDITOR mira", team).targets] == ["pane:node-3"]


def test_unknown_and_ambiguous_mentions_are_reported_and_emails_are_not_mentions():
    team = [agent(1, aliases=("claude",)), agent(2, aliases=("claude",))]
    p = parse_mentions("@nadie y @claude y escribe a juan@correo.com", team)
    assert p.unknown == ("nadie",) and p.ambiguous == ("claude",)
    assert parse_mentions("sin menciones", team).targets == ()
    assert parse_mentions("@orquestador", team).unknown == ("orquestador",), "no hay nadie con ese rol"


def test_bubble_color_is_stable_and_in_the_palette():
    colors = {bubble_color(f"pane:n{i}") for i in range(40)}
    assert colors <= {f"c{i}" for i in range(1, 9)} and len(colors) > 3
    assert bubble_color("pane:x") == bubble_color("pane:x")


@pytest.mark.parametrize(
    "state, delivery, replies, tick",
    [
        ("queued", None, 0, "queued"),
        ("parked", None, 0, "held"),
        ("expired", None, 0, "expired"),
        ("discarded", None, 0, "discarded"),
        ("delivered", "injected", 0, "delivered"),
        ("delivered", "submitted", 0, "delivering"),
        ("delivered", "unverifiable", 0, "doubtful"),
        ("delivered", None, 0, "read"),
        ("acked", "injected", 2, "replied"),
        ("queued", "injected", 3, "queued"),
    ],
)
def test_tick_for_every_state_has_text(state, delivery, replies, tick):
    got, label = tick_for(state, delivery, replies)
    assert got == tick and label


def test_group_participants_offer_all_and_one_entry_per_present_role():
    team = [agent(1, role="orchestrator"), agent(2, role="orchestrator"), agent(3, role="auditor")]
    groups = group_participants(team)
    assert [g.mention for g in groups] == ["@todos", "@orquestador", "@auditor"]
    assert all(g.kind == "group" for g in groups)
    assert group_participants([]) == []


# ---------------------------------------------------------------- servicio y puntos de conexión

class FakeSource:
    def __init__(self, sid: str, entries: List[ChatEntry], fail: bool = False) -> None:
        self.id, self.entries, self.fail = sid, entries, fail

    def list_since(self, project_uuid: str, since_rev_ms: int, limit: int) -> List[ChatEntry]:
        if self.fail:
            raise RuntimeError("fuente caída")
        return [e for e in self.entries if (e.rev_ms or e.ts_ms) >= since_rev_ms and e.project_uuid == project_uuid]


class FakeRoster:
    def __init__(self, rid: str, people: List[Participant]) -> None:
        self.id, self.people = rid, people

    def participants(self, project_uuid: str) -> List[Participant]:
        return list(self.people)


class FakeTransport:
    def __init__(self, ok: bool = True) -> None:
        self.ok, self.calls = ok, []

    def send(self, project_uuid: str, targets: Sequence[Participant], text: str, kind: str) -> List[SendResult]:
        self.calls.append((project_uuid, [t.principal for t in targets], text, kind))
        return [SendResult(t.principal, self.ok, "OK" if self.ok else "SEND_FAILED", "", f"id-{i}" if self.ok else None)
                for i, t in enumerate(targets)]


def test_feed_merges_sources_sorted_dedups_by_id_and_reports_a_failing_source():
    svc = ChatService()
    svc.register_source(FakeSource("a", [entry(1), entry(3)]))
    svc.register_source(FakeSource("b", [entry(2), entry(3, rev=99, text="nuevo")]))
    svc.register_source(FakeSource("rota", [], fail=True))
    out = svc.feed(PROJECT)
    assert [e["id"] for e in out["entries"]] == ["m1", "m2", "m3"]
    assert out["entries"][2]["text"] == "nuevo", "la revisión nueva sustituye a la vieja"
    assert out["cursor_ms"] == 99
    status = {s["id"]: s for s in out["sources"]}
    assert status["a"]["ok"] and not status["rota"]["ok"] and "caída" in status["rota"]["reason"]


def test_feed_is_incremental_keeps_the_newest_on_limit_and_never_moves_the_cursor_back():
    svc = ChatService()
    svc.register_source(FakeSource("a", [entry(i) for i in range(1, 8)]))
    assert [e["id"] for e in svc.feed(PROJECT, 0, 3)["entries"]] == ["m5", "m6", "m7"]
    assert svc.feed(PROJECT, 70)["entries"][0]["id"] == "m7"
    assert svc.feed(PROJECT, 500)["cursor_ms"] == 500 and svc.feed(PROJECT, 500)["entries"] == []
    assert len(svc.feed(PROJECT, 0, 10 ** 6)["entries"]) <= MAX_FEED_LIMIT


def test_push_entries_from_a_hook_show_up_in_the_feed_of_their_project_only():
    svc = ChatService()
    svc.publish(entry(5))
    svc.publish(ChatEntry(id="otro", ts_ms=1, project_uuid="otro-proyecto", sender=agent(1), text="x"))
    assert [e["id"] for e in svc.feed(PROJECT)["entries"]] == ["m5"]


def test_channels_depend_on_the_registered_transports():
    svc = ChatService()
    assert {c.kind: c.available for c in svc.channels()} == {
        "agent": False, "agents": False, "node_devs": False, "group": False}
    svc.register_transport("agent", FakeTransport())
    by = {c.kind: c for c in svc.channels()}
    assert by["agent"].available and by["agents"].available
    assert not by["node_devs"].available and by["node_devs"].reason
    svc.register_transport("node_dev", FakeTransport())
    assert all(c.available for c in svc.channels())


def test_send_validates_before_touching_any_transport():
    svc = ChatService()
    transport = FakeTransport()
    svc.register_transport("agent", transport)
    svc.register_roster(FakeRoster("r", [agent(1, aliases=("claude",)), agent(2, aliases=("claude",))]))
    cases = {
        "": "EMPTY_MESSAGE", "   ": "EMPTY_MESSAGE", "x" * 9000 + " @terminal1": "MESSAGE_TOO_LONG",
        "hola sin mención": "NO_RECIPIENT", "@nadie hola": "UNKNOWN_MENTION", "@claude hola": "AMBIGUOUS_MENTION",
    }
    for text, code in cases.items():
        out = svc.send(PROJECT, text)
        assert out["success"] is False and out["code"] == code and out["message"], (text, out)
    assert svc.send(PROJECT, None)["code"] == "EMPTY_MESSAGE"
    assert transport.calls == []


def test_send_groups_targets_by_kind_and_reports_per_target_results():
    svc = ChatService()
    local, dev = FakeTransport(), FakeTransport()
    svc.register_transport("agent", local)
    svc.register_roster(FakeRoster("loc", [agent(1, role="orchestrator"), agent(2)]))
    out = svc.send(PROJECT, "@orquestador haz esto y planea así")
    assert out["success"] is True and [r["target"] for r in out["results"]] == ["pane:node-1"]
    assert local.calls == [(PROJECT, ["pane:node-1"], "@orquestador haz esto y planea así", "note")]
    assert svc.send(PROJECT, "@todos listos")["results"].__len__() == 2
    # un tipo de participante sin transporte no rompe el resto
    ghost = Participant("dev:n/x", "Ana (nodo B)", kind="node_dev", mention="@ana")
    svc.register_roster(FakeRoster("remoto", [ghost]))
    mixed = svc.send(PROJECT, "@terminal2 y @ana")
    codes = {r["target"]: r["code"] for r in mixed["results"]}
    assert mixed["success"] is True and codes == {"pane:node-2": "OK", "dev:n/x": "CHANNEL_UNAVAILABLE"}
    only_ghost = svc.send(PROJECT, "@ana hola")
    assert only_ghost["success"] is False and only_ghost["code"] == "SEND_FAILED"
    svc.register_transport("node_dev", dev)
    assert svc.send(PROJECT, "@ana hola")["success"] and dev.calls[0][1] == ["dev:n/x"]


def test_a_broken_transport_or_roster_does_not_take_the_chat_down():
    svc = ChatService()

    class Boom:
        def send(self, *a: Any, **k: Any):
            raise RuntimeError("red caída")

    class BoomRoster:
        id = "rota"

        def participants(self, project_uuid: str):
            raise RuntimeError("sin roster")

    svc.register_roster(BoomRoster())
    svc.register_roster(FakeRoster("ok", [agent(1)]))
    svc.register_transport("agent", Boom())
    out = svc.send(PROJECT, "@terminal1 hola")
    assert out["success"] is False and out["results"][0]["code"] == "INTERNAL_ERROR"


def test_a_p2p_adapter_plugs_in_with_the_three_ports_and_touches_nothing_else():
    """Los puntos de conexión: un adaptador futuro (P2P/servidor) solo registra su fuente, su roster y su transporte."""
    svc = ChatService()
    svc.register_roster(FakeRoster("loc", [agent(1)]))
    svc.register_transport("agent", FakeTransport())
    remote_dev = Participant("dev:nodo-b/ana", "Ana (nodo B)", kind="node_dev", mention="@ana")
    p2p_transport = FakeTransport()
    svc.register_roster(FakeRoster("p2p", [remote_dev]))
    svc.register_source(FakeSource("p2p", [ChatEntry(id="r1", ts_ms=5, project_uuid=PROJECT, sender=remote_dev,
                                                      text="hola equipo", origin="remote:nodo-b", source="p2p")]))
    svc.register_transport("node_dev", p2p_transport)
    feed = svc.feed(PROJECT)["entries"]
    assert feed[0]["origin"] == "remote:nodo-b" and feed[0]["sender"]["kind"] == "node_dev"
    assert svc.send(PROJECT, "@ana y @terminal1 reunión")["success"] is True
    assert p2p_transport.calls[0][1] == ["dev:nodo-b/ana"]
    assert all(c.available for c in svc.channels())


# ---------------------------------------------------------------- adaptador local

def make_adapter(rows: List[Dict[str, Any]], sent: List[Any]) -> LocalAgentsAdapter:
    roster = [
        {"pane_id": "pane_1", "node_uuid": "n1", "cli_profile": "claude", "role": "orchestrator",
         "role_label": "Orquestador"},
        {"pane_id": "pane_2", "node_uuid": "n2", "cli_profile": "claude", "role": "peer", "role_label": "Par"},
        {"pane_id": "pane_3", "node_uuid": "n3", "cli_profile": "codex", "role": "peer", "role_label": "Par"},
    ]

    def send(project: str, pane: str, text: str, kind: str) -> Dict[str, Any]:
        sent.append((project, pane, text, kind))
        if pane == "pane_3":
            return {"success": False, "code": "TARGET_NOT_FOUND", "message": "cerrada"}
        return {"success": True, "msg_id": f"id-{pane}"}

    return LocalAgentsAdapter(chat_rows=lambda p, s, l: rows, roster=lambda p: roster, send=send)


def test_local_roster_builds_handles_and_only_unique_cli_aliases():
    people = {p.mention: p for p in make_adapter([], []).participants(PROJECT)}
    assert set(people) == {"@terminal1", "@terminal2", "@terminal3"}
    assert people["@terminal3"].aliases == ("codex",) and people["@terminal1"].aliases == ()
    assert people["@terminal1"].principal == "pane:n1" and people["@terminal1"].role == "orchestrator"


def test_local_rows_become_entries_with_sender_labels_ticks_and_remote_origin():
    rows = [
        {"msg_id": "a", "from": {"principal": "human:local", "kind": "human"},
         "to": {"principal": "pane:n1", "kind": "pane", "pane_id": "pane_1"}, "text": "haz esto", "kind": "note",
         "chain_id": "c", "hop": 0, "parent_msg_id": None, "state": "delivered", "delivery_state": "injected",
         "reply_count": 0, "created_at": 100, "rev_ms": 150, "remote": None},
        {"msg_id": "b", "from": {"principal": "pane:zzz", "kind": "pane", "pane_id": "pane_9", "role": "peer",
                                  "role_label": "Par"},
         "to": {"principal": "pane:n1", "kind": "pane"}, "text": "hola", "kind": "task.reply", "chain_id": "c",
         "hop": 1, "parent_msg_id": "a", "state": "parked", "delivery_state": None, "reply_count": 0,
         "created_at": 200, "remote": {"display_name": "Nodo B"}},
    ]
    a, b = make_adapter(rows, []).list_since(PROJECT, 0, 50)
    assert a.sender.label == "Tú" and a.sender.kind == "human" and a.recipients[0].label.startswith("Terminal 1")
    assert (a.tick, a.rev_ms, a.ts_ms, a.source) == ("delivered", 150, 100, "local_agents")
    assert b.sender.label == "Terminal 9" and b.tick == "held" and b.origin == "remote:Nodo B" and b.parent_id == "a"
    assert b.recipients[0].principal == "pane:n1"
    d = a.to_dict()
    assert d["sender"]["color"] == bubble_color("human:local") and d["tick_label"]


def test_local_send_maps_results_and_missing_terminals():
    sent: List[Any] = []
    adapter = make_adapter([], sent)
    people = {p.mention: p for p in adapter.participants(PROJECT)}
    ghost = Participant("pane:cerrada", "x", kind="agent")
    results = adapter.send(PROJECT, [people["@terminal1"], people["@terminal3"], ghost], "texto", "note")
    assert [(r.ok, r.code) for r in results] == [(True, "OK"), (False, "TARGET_NOT_FOUND"), (False, "TARGET_NOT_FOUND")]
    assert results[0].msg_id == "id-pane_1" and results[1].message == "cerrada"
    assert sent == [(PROJECT, "pane_1", "texto", "note"), (PROJECT, "pane_3", "texto", "note")]


# ---------------------------------------------------------------- vista incremental del relevo

def test_relay_chat_view_returns_changed_old_messages_so_ticks_update(tmp_path):
    from tests.helpers.direct_delivery_lab import NODE_A, NODE_B, PROJECT as LAB_PROJECT, Lab

    lab = Lab(tmp_path)
    lab.add_pane(NODE_B, "pane_2")
    lab.add_pane(NODE_A, "pane_1", role="orchestrator")
    msg = lab.insert(NODE_B, "hola")
    first = lab.relay.chat_view(LAB_PROJECT, since_rev_ms=0)
    assert [m["msg_id"] for m in first] == [msg] and first[0]["rev_ms"] >= first[0]["created_at"]
    cursor = first[0]["rev_ms"]
    assert [m["msg_id"] for m in lab.relay.chat_view(LAB_PROJECT, since_rev_ms=cursor)] == [msg], "el cursor es >= "
    lab.clock.advance(50)
    lab.make_idle(NODE_B)
    lab.service.pump(NODE_B)  # el motor de entrega toma el mensaje: aparece su fila de entrega
    again = lab.relay.chat_view(LAB_PROJECT, since_rev_ms=cursor)
    assert again[0]["delivery_state"] is not None and again[0]["rev_ms"] > cursor
    assert lab.relay.chat_view(LAB_PROJECT, since_rev_ms=again[0]["rev_ms"] + 1) == []
    assert lab.relay.chat_view("otro-proyecto") == []
    # el contrato anterior sigue igual: thread_view no trae rev_ms
    assert "rev_ms" not in lab.relay.thread_view(LAB_PROJECT)[0]


# ---------------------------------------------------------------- puente

@pytest.fixture
def bridge(tmp_path, monkeypatch):
    from src.app.api_bridge import ApiBridge
    from src.storage.db import DatabaseManager

    monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)
    monkeypatch.setenv("MCP2P_MEASUREMENTS_FILE", str(tmp_path / "m.json"))
    b = ApiBridge(db_manager=DatabaseManager(db_path=str(tmp_path / "chat.db")))
    try:
        yield b
    finally:
        b._close_terminal_matrix()
        b.core_kernel.close()


def test_bridge_exposes_channels_feed_and_send_with_clear_errors(bridge):
    channels = bridge.chat_get_channels("proj_a")
    assert channels["success"] is True
    kinds = {c["kind"]: c for c in channels["channels"]}
    assert set(kinds) == {"agent", "agents", "node_devs", "group"}
    assert kinds["node_devs"]["available"] is False and kinds["node_devs"]["reason"]
    assert channels["participants"] == []
    feed = bridge.chat_get_feed("proj_a", 0, 50)
    assert feed["success"] is True and feed["entries"] == [] and feed["cursor_ms"] == 0
    assert bridge.chat_send("", "@todos hola")["code"] == "INVALID_ARGUMENT"
    assert bridge.chat_send("proj_a", "")["code"] == "EMPTY_MESSAGE"
    assert bridge.chat_send("proj_a", "@nadie hola")["code"] == "UNKNOWN_MENTION"


def test_bridge_end_to_end_human_message_reaches_the_inbox_and_shows_in_the_feed(bridge):
    reg = bridge.extensions_ui._get_pane_registry()
    reg.register_pane("pane_1", project_uuid="proj_a", cli_profile="claude")
    reg.register_pane("pane_2", project_uuid="proj_a", cli_profile="codex")
    reg.set_role("pane_1", "orchestrator")
    people = {p["mention"]: p for p in bridge.chat_get_channels("proj_a")["participants"]}
    assert {"@terminal1", "@terminal2", "@todos", "@orquestador"} <= set(people)
    assert people["@terminal2"]["role"] == "peer"
    out = bridge.chat_send("proj_a", "@orquestador planifica con calma")
    assert out["success"] is True and [r["ok"] for r in out["results"]] == [True]
    feed = bridge.chat_get_feed("proj_a", 0, 50)
    (item,) = feed["entries"]
    assert item["text"] == "@orquestador planifica con calma" and item["sender"]["kind"] == "human"
    assert item["recipients"][0]["principal"] == people["@terminal1"]["principal"]
    assert item["tick"] in ("queued", "read", "delivering", "delivered") and item["tick_label"]
    assert bridge.chat_get_feed("otro_proyecto")["entries"] == []
    assert bridge.chat_send("proj_a", "@todos descansad")["success"] is True
    assert len(bridge.chat_get_feed("proj_a", feed["cursor_ms"])["entries"]) >= 2


# ---------------------------------------------------------------- arquitectura

PURE_FILES = ("domain.py", "ports.py", "service.py", "__init__.py")
FORBIDDEN = ("src.adapters", "src.app", "src.storage", "src.mcp", "src.p2p", "src.core", "src.a2a", "src.runtime")


def _imports(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append(node.module)
    return out


def test_chat_module_never_imports_infrastructure():
    root = REPO_ROOT / "src" / "chat"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        for mod in _imports(path):
            if any(mod == f or mod.startswith(f + ".") for f in FORBIDDEN):
                offenders.append((str(path.relative_to(REPO_ROOT)), mod))
    assert offenders == [], "src/chat debe ser autónomo: se cablea desde src/app/ui_adapters/chat_ui.py"


def test_only_the_chat_ui_adapter_wires_the_module_and_nothing_else_imports_it():
    importers = set()
    for path in (REPO_ROOT / "src").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("src/chat/"):
            continue
        if any(m == "src.chat" or m.startswith("src.chat.") for m in _imports(path)):
            importers.add(rel)
    assert importers == {"src/app/ui_adapters/chat_ui.py"}


def test_chat_module_never_writes_to_a_pty_directly():
    for path in (REPO_ROOT / "src" / "chat").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "send_input" not in text and "pty_write_input" not in text and "os.write" not in text, path
