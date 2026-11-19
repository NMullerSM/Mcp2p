"""Banco de pruebas de la entrega directa (WP-6b): SQLite real, relevo real y dobles para panel, PTY y reloj.

Ningún doble escribe en un PTY real: `FakePaneInput.writes` es el ESPÍA de escrituras (D12-ter).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.adapters.persistence.relay_sqlite import SqliteRelayStore
from src.core.ports.delivery import LocalOnlyDeliveryPolicy, WriteResult
from src.core.services.agent_activity_registry import AgentActivityRegistry
from src.core.services.direct_delivery_service import DeliverySettings, DirectDeliveryService
from src.core.services.message_relay_service import MessageRelayService
from src.storage.db import DatabaseManager

PROJECT = "11111111-1111-4111-8111-111111111111"
OTHER_PROJECT = "22222222-2222-4222-8222-222222222222"
NODE_A = "node-aaaaaaaa"
NODE_B = "node-bbbbbbbb"
NODE_C = "node-cccccccc"


@dataclass
class FakePane:
    pane_id: str
    node_uuid: str
    project_uuid: str = PROJECT
    status: str = "active"
    cli_profile: str = "claude_code"
    role: str = "implementer"
    delivery_mode: str = "direct"
    delivery_batch: bool = False
    last_human_keystroke_at: float = 0.0
    last_human_submit_at: float = 0.0


class FakePanes:
    def __init__(self) -> None:
        self.panes: Dict[str, FakePane] = {}
        self.typing: Dict[str, bool] = {}
        self.human_submits: List[str] = []

    def add(self, pane: FakePane) -> FakePane:
        self.panes[pane.pane_id] = pane
        return pane

    def find_active_pane_by_node(self, node_uuid: str, *, project_uuid: Optional[str] = None) -> Optional[FakePane]:
        for pane in self.panes.values():
            if pane.status != "closed" and pane.node_uuid == node_uuid:
                if project_uuid is None or pane.project_uuid == project_uuid:
                    return pane
        return None

    def get_pane(self, pane_id: Any) -> Optional[FakePane]:
        return self.panes.get(str(pane_id))

    def list_panes(self, *, project_uuid: Optional[str] = None, include_closed: bool = False) -> List[Dict[str, Any]]:
        return [
            {"pane_id": p.pane_id, "node_uuid": p.node_uuid, "project_uuid": p.project_uuid}
            for p in self.panes.values()
            if p.status != "closed" and (project_uuid is None or p.project_uuid == project_uuid)
        ]

    def is_human_typing_active(self, pane_id: Any, cooldown_seconds: float = 5.0, now: Optional[float] = None) -> bool:
        return bool(self.typing.get(str(pane_id), False))

    def record_human_keystroke(self, pane_id: Any, timestamp: Optional[float] = None, *, submitted: bool = False) -> bool:
        if submitted:
            self.human_submits.append(str(pane_id))
        return True


class FakePaneInput:
    """Espía de escrituras: guarda cada `write`, mide el máximo de escrituras simultáneas y permite fallos."""

    def __init__(self) -> None:
        self.incarnations: Dict[str, str] = {}
        self.writes: List[Dict[str, Any]] = []
        self.output_age: Dict[str, Optional[float]] = {}
        self.fail_next: Optional[WriteResult] = None
        self.block: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._active = 0
        self.max_concurrent = 0
        self.on_write: Optional[Callable[[str, str], None]] = None

    def current_incarnation(self, pane_id: str) -> Optional[str]:
        return self.incarnations.get(pane_id)

    def last_output_age_s(self, pane_id: str) -> Optional[float]:
        return self.output_age.get(pane_id)

    def write(self, pane_id: str, data: str, *, incarnation: str) -> WriteResult:
        with self._lock:
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            gate = self.block.get(pane_id)
            if gate is not None:
                gate.wait(timeout=10)
            with self._lock:
                if self.fail_next is not None:
                    res, self.fail_next = self.fail_next, None
                    return res
                if self.incarnations.get(pane_id) != incarnation:
                    return WriteResult(False, 0, "INCARNATION_CHANGED")
                self.writes.append({"pane_id": pane_id, "data": data, "incarnation": incarnation})
            if self.on_write is not None:
                self.on_write(pane_id, data)
            return WriteResult(True, len(data.encode("utf-8")), "OK")
        finally:
            with self._lock:
                self._active -= 1

    def texts(self, pane_id: str) -> List[str]:
        return [w["data"] for w in self.writes if w["pane_id"] == pane_id]


class Clock:
    def __init__(self, start_ms: int = 1_800_000_000_000) -> None:
        self.ms = start_ms
        self._lock = threading.Lock()

    def __call__(self) -> int:
        with self._lock:
            return self.ms

    def advance(self, ms: int) -> None:
        with self._lock:
            self.ms += ms


VERIFIED_CAPS: Dict[str, Any] = {
    "delivery_verified": True,
    "activity_verified": True,
    "paste_mode": "bracketed",
    "submit_delay_ms": 300,
    "max_inject_bytes": 2048,
    "confirm_event": "turn_started",
}


class Lab:
    def __init__(self, tmp_path: Path, *, caps: Optional[Dict[str, Any]] = None, async_mode: bool = False) -> None:
        self.db = DatabaseManager(db_path=str(tmp_path / "relay.db"))
        self.clock = Clock()
        self.store = SqliteRelayStore(self.db)
        self.panes = FakePanes()
        self.input = FakePaneInput()
        self.activity = AgentActivityRegistry(clock_ms=self.clock)
        self.relay = MessageRelayService(store=self.store, identity=object(), pane_directory=self.panes,
                                         clock_ms=self.clock)
        self.caps: Dict[str, Any] = dict(caps or VERIFIED_CAPS)
        self.privilege = "standard"
        self.sleeps: List[float] = []
        self.on_sleep: Optional[Callable[[], None]] = None
        self.settings = DeliverySettings()
        self.policy = LocalOnlyDeliveryPolicy()
        self.service = DirectDeliveryService(
            relay=self.relay,
            store=self.store,
            panes=self.panes,
            pane_input=self.input,
            activity=self.activity,
            capabilities=lambda rec: dict(self.caps),
            privilege_of=lambda rec: self.privilege,
            remote_policy=_PolicyProxy(self),
            settings_reader=lambda: self.settings,
            clock_ms=self.clock,
            sleep=self._sleep,
            max_workers=6,
        )
        self.activity.subscribe(self.service.on_activity)
        if not async_mode:
            self.service._stop.set()  # pruebas deterministas: solo `pump()` entrega; los despertares no lanzan hilos
        self._seq = 0

    def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.on_sleep is not None:
            self.on_sleep()

    def add_pane(self, node: str = NODE_B, pane_id: str = "pane_2", *, project: str = PROJECT, **kw: Any) -> FakePane:
        pane = self.panes.add(FakePane(pane_id=pane_id, node_uuid=node, project_uuid=project, **kw))
        self.input.incarnations[pane_id] = f"sess-{pane_id}-1"
        return pane

    def make_idle(self, node: str = NODE_B, *, advance_ms: int = 10) -> None:
        """El agente termina un turno (hook `Stop`): queda libre con fuente `hook`."""
        self.clock.advance(advance_ms)
        pane = self.panes.find_active_pane_by_node(node)
        inc = self.input.incarnations.get(pane.pane_id) if pane else None
        self.activity.ingest(node, inc, "turn_ended", self.clock(), "hook")

    def hook(self, node: str, event: str) -> None:
        self.clock.advance(5)
        pane = self.panes.find_active_pane_by_node(node)
        inc = self.input.incarnations.get(pane.pane_id) if pane else None
        self.activity.ingest(node, inc, event, self.clock(), "hook")

    def insert(
        self,
        to_node: str = NODE_B,
        text: str = "hola",
        *,
        from_principal: str = "pane:" + NODE_A,
        kind: str = "note",
        project: str = PROJECT,
        trust: str = "pane_authenticated",
        chain_id: str = "chain-1",
        hop: int = 0,
        state: str = "queued",
        ttl_ms: int = 3_600_000,
    ) -> str:
        self._seq += 1
        msg_id = f"00000000-0000-4000-8000-{self._seq:012d}"
        now = self.clock()
        row = {
            "msg_id": msg_id, "project_uuid": project, "from_principal": from_principal,
            "to_principal": f"pane:{to_node}", "kind": kind, "chain_id": chain_id, "hop": hop,
            "parent_msg_id": None, "trust": trust, "content_hash": "h" * 64, "content": text,
            "envelope_json": json.dumps({}), "state": state, "state_reason": None, "card_id": None,
            "created_at_ms": now, "expires_at_ms": now + ttl_ms, "updated_at_ms": now,
        }
        assert self.store.insert_message(row, [{"event": "submitted", "msg_id": msg_id, "chain_id": chain_id,
                                                  "hop": hop, "project_uuid": project,
                                                  "from_principal": from_principal,
                                                  "to_principal": f"pane:{to_node}", "trust": trust,
                                                  "content_hash": "h" * 64, "actor": "test"}])
        self.clock.advance(1)
        return msg_id

    def state_of(self, msg_id: str) -> str:
        return str(self.store.get_message(msg_id)["state"])

    def delivery_of(self, msg_id: str) -> Optional[str]:
        row = self.store.delivery_row(msg_id)
        return None if row is None else str(row["state"])

    def events(self, msg_id: str) -> List[str]:
        return [str(r["event"]) for r in self.store.audit_for(msg_id)]


class _PolicyProxy:
    def __init__(self, lab: "Lab") -> None:
        self._lab = lab

    def allows_injection(self, msg_row: Any) -> bool:
        return self._lab.policy.allows_injection(msg_row)


def wait_until(predicate: Callable[[], bool], timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
