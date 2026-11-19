"""Pruebas de ingestión remota, verificación criptográfica, cuarentena y puente GUI (WP-7)."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from src.adapters.identity.envelope_crypto import b64url
from src.adapters.identity.peer_verifier import PeerEnvelopeVerifier
from src.adapters.persistence.node_policy_sqlite import SqliteNodePolicyStore
from src.app.api_bridge import ApiBridge
from src.core.domain.node_policy import (
    CHAIN_APPROVAL_TTL_MS,
    PREVIOUS_NAME_VISIBLE_MS,
    QUARANTINE_TTL_MS,
)
from src.core.factory import ApplicationFactory
from src.core.kernel import CoreKernel
from src.mcp.pane_registry import PaneRegistry
from src.p2p.crypto import NodeCryptoManager
from src.storage.db import DatabaseManager
from tests.helpers.legacy_db import build_legacy_db
from tests.helpers.simulated_device import SimulatedDevice

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_system_pty_writes(monkeypatch: pytest.MonkeyPatch):
    """Doble espía de D12: asegura cero escrituras a PTY durante todas las pruebas de WP-7."""
    from src.adapters.terminal.pty_host import PosixPtyHostConnector
    from src.adapters.terminal.winpty_host import WinPtyHostConnector

    writes: List[tuple] = []

    def _spy_posix(self, pane_id: Any, data: Any) -> Dict[str, Any]:
        writes.append(("PosixPtyHostConnector", pane_id, data))
        return {"success": False, "error": "bloqueado por espía"}

    def _spy_win(self, pane_id: Any, data: Any) -> Dict[str, Any]:
        writes.append(("WinPtyHostConnector", pane_id, data))
        return {"success": False, "error": "bloqueado por espía"}

    monkeypatch.setattr(PosixPtyHostConnector, "send_input", _spy_posix)
    monkeypatch.setattr(WinPtyHostConnector, "send_input", _spy_win)
    yield writes
    assert writes == [], f"Invariante D12 roto: {writes}"


class _FakeClock:
    def __init__(self, now_ms: int = 1_760_000_000_000) -> None:
        self.now_ms = int(now_ms)

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, delta_ms: int) -> int:
        self.now_ms += int(delta_ms)
        return self.now_ms


class _GuiTerminal:
    max_panes = 6

    def __init__(self) -> None:
        import os

        self.pid = os.getpid()
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def is_available(self) -> bool:
        return True

    def get_status(self) -> Dict[str, Any]:
        return {"available": True, "degraded": False, "mode": "fake"}

    def list_sessions(self) -> list:
        return list(self.sessions.values())

    def spawn_session(self, **kwargs: Any) -> Dict[str, Any]:
        sess = {
            "pane_id": kwargs["pane_id"],
            "alive": True,
            "session_id": f"s-{kwargs['pane_id']}",
            "pid": self.pid,
        }
        self.sessions[kwargs["pane_id"]] = sess
        return {"success": True, **sess}

    def send_input(self, pane_id: Any, data: Any) -> Dict[str, Any]:
        return {"success": True}

    def close_session(self, pane_id: Any) -> Dict[str, Any]:
        self.sessions.pop(str(pane_id), None)
        return {"success": True}

    def close(self) -> None:
        self.sessions.clear()


class _Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.app.ui_adapters.extensions_lab_ui as lab_mod

        monkeypatch.setattr(lab_mod, "is_unsupported_terminal_platform", lambda: False)
        monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)
        self.tmp_path = tmp_path
        self.clock = _FakeClock()
        self.db = DatabaseManager(db_path=str(tmp_path / "wp7.db"))
        self.human_crypto = NodeCryptoManager(key_path=tmp_path / "local_human" / "node_identity.key")
        self.kernel = ApplicationFactory.create_desktop_kernel(
            db_manager=self.db,
            crypto=self.human_crypto,
        )
        self.bridge = ApiBridge(db_manager=self.db, core_kernel=self.kernel)
        self.term = _GuiTerminal()
        self.kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, self.term)
        self.proj = self.db.create_project("WP7", str(tmp_path / "repo"))["project_uuid"]
        assert self.bridge.pty_spawn_pane("pane_1", "custom_shell", start_hub=False, project_uuid=self.proj)["success"]
        assert self.bridge.pty_spawn_pane("pane_2", "custom_shell", start_hub=False, project_uuid=self.proj)["success"]
        self.reg: PaneRegistry = self.bridge.extensions_ui._get_pane_registry()
        self.target_rec = self.reg.get_pane("pane_2")
        self.relay = ApplicationFactory.message_relay_service(self.kernel, self.reg)
        self.intake = ApplicationFactory.remote_intake_service(self.kernel, self.reg)
        self.relay._clock_ms = self.clock
        self.intake._clock_ms = self.clock
        self.dialogs: List[tuple] = []
        self.dialog_answer = True

        def _dialog(title: str, msg: str) -> bool:
            self.dialogs.append((title, msg))
            return self.dialog_answer

        self.bridge.confirmations.dialog = _dialog

    def close(self) -> None:
        self.bridge._close_terminal_matrix()
        self.kernel.close()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ctx = _Env(tmp_path, monkeypatch)
    try:
        yield ctx
    finally:
        ctx.close()


def test_bit_flip_wrong_key_expired_and_unknown_key_are_rejected_and_audited(env: _Env) -> None:
    foreign = SimulatedDevice(env.tmp_path / "dev_a", "Portátil", clock_ms=env.clock)
    env_ok, text = foreign.envelope_to(env.target_rec.node_uuid, env.proj, "hola mundo")

    # 1. Bit-flip en contenido
    bad_content = env.intake.accept_remote_envelope(
        env_ok, "hola manipulado", transport="simulated", declared_node_name="Portátil"
    )
    assert bad_content["success"] is False and bad_content["reason"] == "CONTENT_HASH_MISMATCH"

    # 2. Bit-flip en la firma
    sig_val = env_ok["sig"]["value"]
    flipped_char = "A" if sig_val[0] != "A" else "B"
    bad_sig = {**env_ok, "sig": {**env_ok["sig"], "value": flipped_char + sig_val[1:]}}
    res_sig = env.intake.accept_remote_envelope(
        bad_sig, text, transport="simulated", declared_node_name="Portátil"
    )
    assert res_sig["success"] is False and res_sig["reason"] == "BAD_SIGNATURE"

    # 3. Clave desconocida sin certificado de dispositivo
    no_cert = SimulatedDevice(env.tmp_path / "dev_nocert", "SinCert", include_cert=False, clock_ms=env.clock)
    env_nc, txt_nc = no_cert.envelope_to(env.target_rec.node_uuid, env.proj, "sin cert")
    res_nc = env.intake.accept_remote_envelope(
        env_nc, txt_nc, transport="simulated", declared_node_name="SinCert"
    )
    assert res_nc["success"] is False and res_nc["reason"] == "UNKNOWN_KEY"

    # 4. Sobre caducado
    env_exp, txt_exp = foreign.envelope_to(
        env.target_rec.node_uuid, env.proj, "caduco", ttl_ms=1000
    )
    env.clock.advance(2000)
    res_exp = env.intake.accept_remote_envelope(
        env_exp, txt_exp, transport="simulated", declared_node_name="Portátil"
    )
    assert res_exp["success"] is False and res_exp["reason"] == "EXPIRED"

    # 5. Clave equivocada afirmando huella conocida
    # Registramos primero dev_a como conocido
    env_valid, txt_valid = foreign.envelope_to(env.target_rec.node_uuid, env.proj, "legitimo")
    assert env.intake.accept_remote_envelope(
        env_valid, txt_valid, transport="simulated", declared_node_name="Portátil"
    )["success"] is True

    attacker = SimulatedDevice(env.tmp_path / "dev_atk", "Atacante", clock_ms=env.clock)
    env_spoof, txt_spoof = attacker.envelope_to(env.target_rec.node_uuid, env.proj, "suplantado")
    env_spoof = {**env_spoof, "sig": {**env_spoof["sig"], "key": foreign.fingerprint()}}
    res_spoof = env.intake.accept_remote_envelope(
        env_spoof, txt_spoof, transport="simulated", declared_node_name="Portátil"
    )
    assert res_spoof["success"] is False and res_spoof["reason"] == "BAD_SIGNATURE"

    # Auditoría íntegra y registros `rejected` presentes
    chain = env.relay.store.verify_chain()
    assert chain["ok"] is True
    counts = env.relay.store.audit_counts()
    assert counts["rejected"] >= 5

    # 6. Certificado de dispositivo falsificado (firma humana inválida) -> BAD_DEVICE_CERT
    forged_dev = SimulatedDevice(env.tmp_path / "dev_forged", "Forjado", clock_ms=env.clock)
    env_fg, txt_fg = forged_dev.envelope_to(env.target_rec.node_uuid, env.proj, "cert falso")
    env_fg["device_cert"]["human_pub"] = env.human_crypto.public_key_base64
    # Re-firmar el sobre con la clave del dispositivo para que la firma del sobre sí sea válida, solo el cert es falso
    env_fg = forged_dev.identity.sign_header({k: v for k, v in env_fg.items() if k != "sig"})
    res_fg = env.intake.accept_remote_envelope(
        env_fg, txt_fg, transport="simulated", declared_node_name="Forjado"
    )
    assert res_fg["success"] is False and res_fg["reason"] == "BAD_DEVICE_CERT"

    # 7. Destino de otro proyecto o inexistente -> TARGET_NOT_FOUND
    other_proj = env.db.create_project("Otro", str(env.tmp_path / "otro_repo"))["project_uuid"]
    env_op, txt_op = foreign.envelope_to(env.target_rec.node_uuid, other_proj, "otro proyecto")
    res_op = env.intake.accept_remote_envelope(
        env_op, txt_op, transport="simulated", declared_node_name="Portátil"
    )
    assert res_op["success"] is False and res_op["code"] == "TARGET_NOT_FOUND"

    # 8. Nodo ya conocido puede enviar sin device_cert (clave ya guardada)
    env_known_nocert, txt_kn = foreign.envelope_to(env.target_rec.node_uuid, env.proj, "sin cert conocido")
    env_known_nocert = foreign.identity.sign_header(
        {k: v for k, v in env_known_nocert.items() if k != "sig"} | {"device_cert": None}
    )
    res_kn = env.intake.accept_remote_envelope(
        env_known_nocert, txt_kn, transport="simulated", declared_node_name="Portátil"
    )
    assert res_kn["success"] is True and res_kn["state"] == "parked"


def test_first_contact_defaults_same_owner_direct_foreign_ask_each(env: _Env) -> None:
    same_dev = SimulatedDevice(
        env.tmp_path / "same",
        "Mi Portátil",
        human_signer=env.human_crypto,
        clock_ms=env.clock,
    )
    foreign_dev = SimulatedDevice(
        env.tmp_path / "foreign",
        "Equipo Externo",
        clock_ms=env.clock,
    )

    e1, t1 = same_dev.envelope_to(env.target_rec.node_uuid, env.proj, "hola mio")
    r1 = env.intake.accept_remote_envelope(e1, t1, transport="simulated", declared_node_name="Mi Portátil")
    assert r1["success"] is True
    assert r1["relation"] == "same_owner"
    assert r1["policy"] == "direct"
    assert r1["state"] == "queued"

    e2, t2 = foreign_dev.envelope_to(env.target_rec.node_uuid, env.proj, "hola externo")
    r2 = env.intake.accept_remote_envelope(e2, t2, transport="simulated", declared_node_name="Equipo Externo")
    assert r2["success"] is True
    assert r2["relation"] == "foreign"
    assert r2["policy"] == "ask_each"
    assert r2["state"] == "parked"
    assert r2["state_reason"] == "UNTRUSTED_NODE"


def test_rename_keeps_policy_and_records_previous_name(env: _Env) -> None:
    dev = SimulatedDevice(env.tmp_path / "dev_ren", "Portátil Viejo", clock_ms=env.clock)
    e1, t1 = dev.envelope_to(env.target_rec.node_uuid, env.proj, "uno")
    env.intake.accept_remote_envelope(e1, t1, transport="simulated", declared_node_name=dev.display_name)
    fp = dev.fingerprint()
    assert env.bridge.relay_set_node_policy(fp, "direct")["success"] is True

    dev.rename("Portátil Nuevo")
    e2, t2 = dev.envelope_to(env.target_rec.node_uuid, env.proj, "dos")
    r2 = env.intake.accept_remote_envelope(e2, t2, transport="simulated", declared_node_name=dev.display_name)
    assert r2["policy"] == "direct" and r2["state"] == "queued"

    nodes = env.bridge.relay_list_nodes()["nodes"]
    node = next(n for n in nodes if n["device_fingerprint"] == fp)
    assert node["display_name"] == "Portátil Nuevo"
    assert node["previous_name"] == "Portátil Viejo"
    assert "Portátil Viejo" in node["previous_names"]
    assert node["policy"] == "direct"

    # Tras más de 7 días deja de mostrarse el nombre anterior reciente
    env.clock.advance(PREVIOUS_NAME_VISIBLE_MS + 1000)
    node_later = env.intake.get_node(fp)
    assert node_later is not None and node_later["previous_name"] is None


def test_same_name_new_key_is_new_node_with_collision_flag(env: _Env) -> None:
    dev1 = SimulatedDevice(env.tmp_path / "d1", "Portátil", clock_ms=env.clock)
    e1, t1 = dev1.envelope_to(env.target_rec.node_uuid, env.proj, "del nodo 1")
    r1 = env.intake.accept_remote_envelope(e1, t1, transport="simulated", declared_node_name="Portátil")
    assert env.bridge.relay_resolve_parked(r1["msg_id"], "release")["success"] is True
    assert env.bridge.relay_set_node_policy(dev1.fingerprint(), "direct")["success"] is True

    dev2 = SimulatedDevice(env.tmp_path / "d2", "Portátil", clock_ms=env.clock)
    e2, t2 = dev2.envelope_to(env.target_rec.node_uuid, env.proj, "del impostor")
    r2 = env.intake.accept_remote_envelope(e2, t2, transport="simulated", declared_node_name="Portátil")
    assert r2["device_fingerprint"] != dev1.fingerprint()
    assert r2["policy"] == "ask_each"
    assert r2["state"] == "parked"
    assert r2["name_collision"] is True

    inbox = env.bridge.relay_list_human_inbox(env.proj)["items"]
    assert len(inbox) == 1
    assert inbox[0]["msg_id"] == r2["msg_id"]
    assert inbox[0]["remote"]["name_collision"] is True


def test_ask_each_approves_the_conversation_until_expiry(env: _Env) -> None:
    dev = SimulatedDevice(env.tmp_path / "dev_chain", "Externo", clock_ms=env.clock)
    chain_id = "chain-conv-1"
    e1, t1 = dev.envelope_to(env.target_rec.node_uuid, env.proj, "msg 1", chain_id=chain_id, hop=0)
    r1 = env.intake.accept_remote_envelope(e1, t1, transport="simulated", declared_node_name="Externo")
    assert r1["state"] == "parked"

    # Liberar el mensaje aprueba la conversación por 2h
    rel = env.bridge.relay_resolve_parked(r1["msg_id"], "release")
    assert rel["success"] is True and rel["state"] == "queued"

    e2, t2 = dev.envelope_to(env.target_rec.node_uuid, env.proj, "msg 2", chain_id=chain_id, hop=1)
    r2 = env.intake.accept_remote_envelope(e2, t2, transport="simulated", declared_node_name="Externo")
    assert r2["state"] == "queued"

    # Otra conversación sigue pidiendo permiso
    e_other, t_other = dev.envelope_to(env.target_rec.node_uuid, env.proj, "otra", chain_id="chain-conv-2")
    r_other = env.intake.accept_remote_envelope(e_other, t_other, transport="simulated", declared_node_name="Externo")
    assert r_other["state"] == "parked"

    # Al vencer las 2 horas, la conversación vuelve a requerir aprobación
    env.clock.advance(CHAIN_APPROVAL_TTL_MS + 1)
    e3, t3 = dev.envelope_to(env.target_rec.node_uuid, env.proj, "msg 3", chain_id=chain_id, hop=2)
    r3 = env.intake.accept_remote_envelope(e3, t3, transport="simulated", declared_node_name="Externo")
    assert r3["state"] == "parked"


def test_ask_once_shows_one_request_at_a_time(env: _Env) -> None:
    dev = SimulatedDevice(env.tmp_path / "dev_once", "UnaVez", clock_ms=env.clock)
    e1, t1 = dev.envelope_to(env.target_rec.node_uuid, env.proj, "primero")
    r1 = env.intake.accept_remote_envelope(e1, t1, transport="simulated", declared_node_name="UnaVez")
    assert env.bridge.relay_set_node_policy(dev.fingerprint(), "ask_once")["success"] is True

    e2, t2 = dev.envelope_to(env.target_rec.node_uuid, env.proj, "segundo")
    r2 = env.intake.accept_remote_envelope(e2, t2, transport="simulated", declared_node_name="UnaVez")
    e3, t3 = dev.envelope_to(env.target_rec.node_uuid, env.proj, "tercero")
    r3 = env.intake.accept_remote_envelope(e3, t3, transport="simulated", declared_node_name="UnaVez")

    assert r1["state_reason"] == "UNTRUSTED_NODE"
    assert r2["state_reason"] == "UNTRUSTED_NODE_QUEUED"
    assert r3["state_reason"] == "UNTRUSTED_NODE_QUEUED"

    visible_1 = env.bridge.relay_list_human_inbox(env.proj)["items"]
    assert [i["msg_id"] for i in visible_1] == [r1["msg_id"]]

    # Al liberar el primero se revela únicamente el segundo
    assert env.bridge.relay_resolve_parked(r1["msg_id"], "release")["success"] is True
    visible_2 = env.bridge.relay_list_human_inbox(env.proj)["items"]
    assert [i["msg_id"] for i in visible_2] == [r2["msg_id"]]

    # Al liberar el segundo se revela el tercero
    assert env.bridge.relay_resolve_parked(r2["msg_id"], "release")["success"] is True
    visible_3 = env.bridge.relay_list_human_inbox(env.proj)["items"]
    assert [i["msg_id"] for i in visible_3] == [r3["msg_id"]]


def test_blocked_drops_with_audit_and_no_row(env: _Env) -> None:
    dev = SimulatedDevice(env.tmp_path / "dev_blk", "Bloqueado", clock_ms=env.clock)
    e1, t1 = dev.envelope_to(env.target_rec.node_uuid, env.proj, "pendiente")
    r1 = env.intake.accept_remote_envelope(e1, t1, transport="simulated", declared_node_name="Bloqueado")
    assert r1["state"] == "parked"

    n_dialogs = len(env.dialogs)
    res = env.bridge.relay_set_node_policy(dev.fingerprint(), "blocked")
    assert res["success"] is True
    assert len(env.dialogs) == n_dialogs  # sin diálogo nativo al bloquear
    assert res["discarded_count"] == 1
    assert env.relay.store.get_message(r1["msg_id"])["state"] == "discarded"

    e2, t2 = dev.envelope_to(env.target_rec.node_uuid, env.proj, "nuevo de bloqueado")
    r2 = env.intake.accept_remote_envelope(e2, t2, transport="simulated", declared_node_name="Bloqueado")
    assert r2["success"] is False and r2["code"] == "NODE_BLOCKED"
    assert env.relay.store.get_message(e2["msg_id"]) is None

    events = [a["event"] for a in env.relay.store.audit_for(e2["msg_id"])]
    assert events == ["remote_blocked"]


def test_direct_queues_with_device_certified_for_same_owner_and_unverified_for_foreign(env: _Env) -> None:
    same_dev = SimulatedDevice(
        env.tmp_path / "same2", "Mio", human_signer=env.human_crypto, clock_ms=env.clock
    )
    e1, t1 = same_dev.envelope_to(
        env.target_rec.node_uuid, env.proj, "mismo dueño", trust="human_confirmed"
    )
    r1 = env.intake.accept_remote_envelope(e1, t1, transport="simulated", declared_node_name="Mio")
    assert r1["state"] == "queued"
    assert r1["trust"] == "device_certified"
    assert env.relay.store.get_message(r1["msg_id"])["trust"] == "device_certified"

    foreign_dev = SimulatedDevice(env.tmp_path / "for2", "Ajeno", clock_ms=env.clock)
    e0, t0 = foreign_dev.envelope_to(env.target_rec.node_uuid, env.proj, "hola")
    env.intake.accept_remote_envelope(e0, t0, transport="simulated", declared_node_name="Ajeno")
    assert env.bridge.relay_set_node_policy(foreign_dev.fingerprint(), "direct")["success"] is True

    # Incluso si el sobre remoto declara trust="human_confirmed", el Hub local fija "unverified"
    e2, t2 = foreign_dev.envelope_to(
        env.target_rec.node_uuid, env.proj, "ajeno directo", trust="human_confirmed"
    )
    r2 = env.intake.accept_remote_envelope(e2, t2, transport="simulated", declared_node_name="Ajeno")
    assert r2["state"] == "queued"
    assert r2["trust"] == "unverified"
    assert env.relay.store.get_message(r2["msg_id"])["trust"] == "unverified"


def test_quarantine_expires_after_24h(env: _Env) -> None:
    dev = SimulatedDevice(env.tmp_path / "dev_q", "Cuarentena", clock_ms=env.clock)
    e1, t1 = dev.envelope_to(
        env.target_rec.node_uuid, env.proj, "espera 24h", ttl_ms=7 * 24 * 60 * 60 * 1000
    )
    r1 = env.intake.accept_remote_envelope(e1, t1, transport="simulated", declared_node_name="Cuarentena")
    assert r1["state"] == "parked"

    env.clock.advance(QUARANTINE_TTL_MS - 1000)
    assert len(env.bridge.relay_list_human_inbox(env.proj)["items"]) == 1

    env.clock.advance(2000)
    # Expiración perezosa al listar bandeja del humano
    assert env.bridge.relay_list_human_inbox(env.proj)["items"] == []
    row = env.relay.store.get_message(r1["msg_id"])
    assert row is not None and row["state"] == "expired"
    assert row["state_reason"] == "QUARANTINE_EXPIRED"
    assert any(a["event"] == "expired" for a in env.relay.store.audit_for(r1["msg_id"]))


def test_trust_direct_requires_native_confirmation(env: _Env) -> None:
    dev = SimulatedDevice(env.tmp_path / "dev_conf", "Portátil Oficina", clock_ms=env.clock)
    e1, t1 = dev.envelope_to(env.target_rec.node_uuid, env.proj, "hola")
    env.intake.accept_remote_envelope(e1, t1, transport="simulated", declared_node_name="Portátil Oficina")
    fp = dev.fingerprint()

    env.dialog_answer = False
    declined = env.bridge.relay_set_node_policy(fp, "direct")
    assert declined["success"] is False and declined["code"] == "CONFIRMATION_DECLINED"
    assert len(env.dialogs) == 1
    dialog_msg = env.dialogs[-1][1]
    assert fp in dialog_msg
    assert "Portátil Oficina" in dialog_msg
    assert "ajeno" in dialog_msg
    assert env.intake.get_node(fp)["policy"] == "ask_each"

    env.dialog_answer = True
    accepted = env.bridge.relay_set_node_policy(fp, "direct")
    assert accepted["success"] is True and accepted["node"]["policy"] == "direct"


def test_allows_injection_only_for_direct_policy(env: _Env) -> None:
    # Mensaje local: siempre permitido
    local_sent = env.relay.submit(
        env.reg.get_pane("pane_1"), target_pane_id="pane_2", text="local"
    )
    local_row = env.relay.store.get_message(local_sent["msg_id"])
    assert env.intake.allows_injection(local_row) is True

    # Mensaje remoto liberado con política ask_each: no inyectable
    dev = SimulatedDevice(env.tmp_path / "dev_inj", "Remoto", clock_ms=env.clock)
    e1, t1 = dev.envelope_to(env.target_rec.node_uuid, env.proj, "remoto")
    r1 = env.intake.accept_remote_envelope(e1, t1, transport="simulated", declared_node_name="Remoto")
    env.bridge.relay_resolve_parked(r1["msg_id"], "release")
    rem_row = env.relay.store.get_message(r1["msg_id"])
    assert env.intake.allows_injection(rem_row) is False

    # Cambiar a direct -> ahora sí permite inyección
    assert env.bridge.relay_set_node_policy(dev.fingerprint(), "direct")["success"] is True
    assert env.intake.allows_injection(rem_row) is True


def test_reply_to_remote_is_unavailable(env: _Env) -> None:
    same_dev = SimulatedDevice(
        env.tmp_path / "dev_rep", "Mi Otro Equipo", human_signer=env.human_crypto, clock_ms=env.clock
    )
    e1, t1 = same_dev.envelope_to(env.target_rec.node_uuid, env.proj, "pregunta remota")
    r1 = env.intake.accept_remote_envelope(e1, t1, transport="simulated", declared_node_name="Mi Otro Equipo")
    assert r1["state"] == "queued"

    got = env.relay.next_for(env.target_rec, limit=5)
    assert len(got["messages"]) == 1
    msg_id = got["messages"][0]["mcp2p_envelope"]["msg_id"]

    rep = env.relay.reply(env.target_rec, msg_id=msg_id, text="mi respuesta")
    assert rep["success"] is False
    assert rep["code"] == "REMOTE_REPLY_UNAVAILABLE"
    assert "2.0.3" in rep["error"]


def test_no_src_path_calls_accept_remote_envelope() -> None:
    calls: List[str] = []
    definitions: List[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "accept_remote_envelope":
                definitions.append(rel)
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute) and fn.attr == "accept_remote_envelope":
                    calls.append(f"{rel}:{node.lineno}")
                elif isinstance(fn, ast.Name) and fn.id == "accept_remote_envelope":
                    calls.append(f"{rel}:{node.lineno}")
    assert definitions == ["src/core/services/remote_intake_service.py"]
    assert calls == [], f"Ninguna ruta en src/ puede llamar a accept_remote_envelope: {calls}"


def test_local_node_name_get_and_set(env: _Env) -> None:
    initial = env.bridge.relay_get_local_node()
    assert initial["success"] is True
    assert isinstance(initial["display_name"], str) and initial["display_name"]
    assert initial["human_key_available"] is True

    bad = env.bridge.relay_set_local_node_name("   \x00\x1b  ")
    assert bad["success"] is False and bad["code"] == "INVALID_ARGUMENT"

    ok = env.bridge.relay_set_local_node_name("  Estación Principal\x07  ")
    assert ok["success"] is True and ok["display_name"] == "Estación Principal"
    again = env.bridge.relay_get_local_node()
    assert again["display_name"] == "Estación Principal"


def test_simulate_remote_node_script_against_tmp_db(tmp_path: Path) -> None:
    db_path = tmp_path / "sim.db"
    db = DatabaseManager(db_path=str(db_path))
    proj = db.create_project("SimProj", str(tmp_path / "repo"))["project_uuid"]
    reg = PaneRegistry(identity_repo=None)
    # Guardamos identidad real de pane_2 en BD
    from src.adapters.persistence.sqlite_repositories import SqlitePaneIdentityRepository

    ident_repo = SqlitePaneIdentityRepository(db)
    ident = ident_repo.get_or_create_pane_identity(proj, "pane_2")
    assert ident["node_uuid"]
    human_key_path = tmp_path / "human" / "node_identity.key"
    NodeCryptoManager(key_path=human_key_path)
    db.close()

    script = ROOT / "scripts" / "simulate_remote_node.py"
    proc1 = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db_path),
            "--project",
            proj,
            "--target-pane",
            "pane_2",
            "--name",
            "Portátil",
            "--text",
            "Hola desde fuera",
            "--count",
            "2",
            "--human-key",
            str(human_key_path),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc1.returncode == 0, proc1.stderr or proc1.stdout
    out1 = json.loads(proc1.stdout)
    assert out1["success"] is True and len(out1["results"]) == 2
    assert all(r["state"] == "parked" for r in out1["results"])

    # Segunda llamada con --new-key y mismo nombre debe marcar colisión
    proc2 = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db_path),
            "--project",
            proj,
            "--target-pane",
            "pane_2",
            "--name",
            "Portátil",
            "--new-key",
            "--human-key",
            str(human_key_path),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc2.returncode == 0, proc2.stderr or proc2.stdout
    out2 = json.loads(proc2.stdout)
    assert out2["device_fingerprint"] != out1["device_fingerprint"]
    assert out2["results"][0]["name_collision"] is True

    # --same-owner debe entrar directo
    proc3 = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db_path),
            "--project",
            proj,
            "--target-pane",
            "pane_2",
            "--name",
            "Mi Tablet",
            "--same-owner",
            "--human-key",
            str(human_key_path),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc3.returncode == 0, proc3.stderr or proc3.stdout
    out3 = json.loads(proc3.stdout)
    assert out3["results"][0]["relation"] == "same_owner"
    assert out3["results"][0]["state"] == "queued"


@pytest.mark.parametrize("version", ["v2_0_1", "bebdcca"])
def test_legacy_db_upgrades_with_node_policy_tables(tmp_path: Path, version: str) -> None:
    db_path = tmp_path / f"legacy_{version}.db"
    build_legacy_db(db_path, version, rows=True)
    db = DatabaseManager(db_path=str(db_path))
    try:
        with db.get_connection() as conn:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            }
        assert {"trusted_nodes", "node_chain_approvals", "remote_message_origin"} <= tables
    finally:
        db.close()
    # Idempotente en segundo arranque
    db2 = DatabaseManager(db_path=str(db_path))
    db2.close()
