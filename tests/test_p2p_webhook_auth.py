"""F3: el webhook P2P LAN aceptaba como confiable cualquier payload sin firma (si faltaban
las claves sig/payload), y aunque llegara firmado, verificaba con la clave pública que
manda el PROPIO EMISOR (cualquiera puede generarse un par Ed25519 y pasar la verificación).
Además escuchaba en 0.0.0.0 por defecto y sin límite de tamaño de cuerpo."""
import json
import socket
import time
import urllib.error
import urllib.request

import pytest

from src.p2p.crypto import NodeCryptoManager
from src.p2p.network import P2PNotificationService


def _start_service(tmp_path, is_trusted_pubkey=None, crypto_manager=None, bind_host=None, on_event=None):
    try:
        service = P2PNotificationService(
            node_uuid="node_test",
            lan_port=0,
            on_event_callback=on_event,
            crypto_manager=crypto_manager,
            is_trusted_pubkey=is_trusted_pubkey,
            bind_host=bind_host,
        )
    except TypeError:
        # Compatibilidad con la firma previa a F3, para que el RED falle por assert, no por TypeError.
        service = P2PNotificationService(
            node_uuid="node_test",
            lan_port=0,
            on_event_callback=on_event,
            crypto_manager=crypto_manager,
        )
    assert service.start_lan_server()
    return service


def _post(port, body_bytes, path="/webhook/p2p"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body_bytes,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.mark.parametrize("name,body", [
    ("plain_dict", json.dumps({"type": "CARD_UPDATE", "card_id": "TASK-999", "status": "merged"}).encode()),
    ("payload_list", json.dumps({"sig": "x", "payload": ["a"], "sender_pubkey": "y"}).encode()),
])
def test_webhook_rejects_unsigned_or_malformed_envelope(tmp_path, name, body):
    events_received = []
    service = _start_service(
        tmp_path, is_trusted_pubkey=lambda pk: True,
        on_event=lambda evt: events_received.append(evt),
    )
    try:
        status, _ = _post(service.lan_port, body)
        assert status == 401
        assert events_received == []
    finally:
        service.stop_lan_server()


def test_webhook_rejects_signed_envelope_missing_sender_pubkey(tmp_path):
    crypto = NodeCryptoManager(key_path=tmp_path / "signer.key")
    payload = {"type": "CARD_UPDATE", "card_id": "TASK-1"}
    sig = crypto.sign_payload(payload)
    body = json.dumps({"sig": sig, "payload": payload}).encode()

    service = _start_service(tmp_path, is_trusted_pubkey=lambda pk: True)
    try:
        status, _ = _post(service.lan_port, body)
        assert status == 401
    finally:
        service.stop_lan_server()


def test_webhook_rejects_valid_signature_from_untrusted_key(tmp_path):
    trusted = NodeCryptoManager(key_path=tmp_path / "trusted.key")
    attacker = NodeCryptoManager(key_path=tmp_path / "attacker.key")

    events_received = []
    service = _start_service(
        tmp_path, is_trusted_pubkey=lambda pk: pk == trusted.public_key_base64,
        on_event=lambda evt: events_received.append(evt),
    )
    try:
        # Atacante: firma válida, clave propia -> no es de confianza.
        atk_payload = {"type": "CARD_UPDATE", "card_id": "TASK-ATK"}
        atk_body = json.dumps({
            "sig": attacker.sign_payload(atk_payload), "payload": atk_payload,
            "sender_pubkey": attacker.public_key_base64,
        }).encode()
        status, _ = _post(service.lan_port, atk_body)
        assert status == 403
        assert events_received == []

        # Control positivo: la clave de confianza sí pasa.
        good_payload = {"type": "CARD_UPDATE", "card_id": "TASK-101"}
        good_body = json.dumps({
            "sig": trusted.sign_payload(good_payload), "payload": good_payload,
            "sender_pubkey": trusted.public_key_base64,
        }).encode()
        status2, body2 = _post(service.lan_port, good_body)
        assert status2 == 200
        assert events_received[-1]["card_id"] == "TASK-101"
    finally:
        service.stop_lan_server()


def test_webhook_fails_closed_without_trust_function(tmp_path):
    trusted = NodeCryptoManager(key_path=tmp_path / "trusted2.key")
    service = _start_service(tmp_path, is_trusted_pubkey=None)
    try:
        payload = {"type": "CARD_UPDATE", "card_id": "TASK-2"}
        body = json.dumps({
            "sig": trusted.sign_payload(payload), "payload": payload,
            "sender_pubkey": trusted.public_key_base64,
        }).encode()
        status, _ = _post(service.lan_port, body)
        assert status == 403
    finally:
        service.stop_lan_server()


@pytest.mark.parametrize("case", ["notanumber", "negative", "oversized"])
def test_webhook_bad_content_length_does_not_kill_server(tmp_path, case):
    events_received = []
    service = _start_service(
        tmp_path, is_trusted_pubkey=lambda pk: True,
        on_event=lambda evt: events_received.append(evt),
    )
    try:
        with socket.create_connection(("127.0.0.1", service.lan_port), timeout=3) as s:
            s.settimeout(3)
            if case == "notanumber":
                header_val = "nope"
            elif case == "negative":
                header_val = "-1"
            else:
                header_val = "2097152"
            req_bytes = (
                f"POST /webhook/p2p HTTP/1.1\r\nHost: x\r\nContent-Length: {header_val}\r\n\r\n"
            ).encode()
            s.sendall(req_bytes)
            if case == "oversized":
                pass  # no se envía cuerpo: el servidor no debe intentar leer 2 MiB.
            resp = s.recv(200).decode(errors="ignore")

        first_line = resp.splitlines()[0] if resp else ""
        if case == "oversized":
            assert "413" in first_line
        else:
            assert "400" in first_line

        # El servidor sigue vivo.
        req = urllib.request.Request(f"http://127.0.0.1:{service.lan_port}/health")
        with urllib.request.urlopen(req, timeout=5) as health_resp:
            assert health_resp.status == 200
        assert events_received == []
    finally:
        service.stop_lan_server()


def test_lan_server_bind_host_defaults_to_loopback(tmp_path, monkeypatch):
    service = _start_service(tmp_path, is_trusted_pubkey=lambda pk: True)
    try:
        assert service._server.server_address[0] == "127.0.0.1"
    finally:
        service.stop_lan_server()

    monkeypatch.setenv("MCP2P_LAN_BIND_HOST", "0.0.0.0")
    service2 = _start_service(tmp_path, is_trusted_pubkey=lambda pk: True)
    try:
        assert service2._server.server_address[0] == "0.0.0.0"
    finally:
        service2.stop_lan_server()
