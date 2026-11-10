"""
Servicio de Red P2P Híbrido para MCP2P.

Combina:
1. Notificaciones en Red Local (LAN): Servidor HTTP liviano sin dependencias externas
   para recepción de Webhooks de sincronización en tiempo real.
2. Descubrimiento y Sondeo WAN Asíncrono: Inspección periódica no invasiva
   mediante 'git ls-remote' para detectar actualizaciones en GitHub a 0 tokens.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("mcp2p.p2p.network")

MAX_WEBHOOK_BODY_BYTES = 1_048_576  # 1 MiB


class P2PNotificationServer(HTTPServer):
    """Servidor HTTP extendido con contexto de MCP2P."""
    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        on_event_callback: Callable[[Dict[str, Any]], None],
        is_trusted_pubkey: Optional[Callable[[str], bool]] = None,
    ):
        super().__init__(server_address, RequestHandlerClass)
        self.on_event_callback = on_event_callback
        self.is_trusted_pubkey = is_trusted_pubkey


class P2PWebhookHandler(BaseHTTPRequestHandler):
    """Manejador HTTP simple para recibir eventos P2P y webhooks en LAN."""

    def log_message(self, format, *args):
        # Silenciar logs ruidosos en stdout
        return

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "online", "service": "mcp2p_network"}\n')
            return

        self.send_response(404)
        self.end_headers()

    def _reply(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ("/webhook/p2p", "/notify"):
            self.send_response(404)
            self.end_headers()
            return

        raw_len = self.headers.get("Content-Length")
        try:
            length = int(raw_len) if raw_len is not None else 0
        except (TypeError, ValueError):
            self.close_connection = True
            self._reply(400, b'{"error": "Content-Length invalido", "received": false}\n')
            return
        if length < 0:
            self.close_connection = True
            self._reply(400, b'{"error": "Content-Length invalido", "received": false}\n')
            return
        if length > MAX_WEBHOOK_BODY_BYTES:
            self.close_connection = True
            self._reply(413, b'{"error": "Cuerpo demasiado grande", "received": false}\n')
            return

        body = self.rfile.read(length) if length else b""
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            self._reply(400, b'{"error": "JSON invalido", "received": false}\n')
            return

        pubkey = data.get("sender_pubkey") if isinstance(data, dict) else None
        sig = data.get("sig") if isinstance(data, dict) else None
        payload = data.get("payload") if isinstance(data, dict) else None
        if not (isinstance(pubkey, str) and pubkey and isinstance(sig, str) and sig and isinstance(payload, dict)):
            logger.warning("Rechazado webhook P2P entrante: sobre sin firma Ed25519 (sig/payload/sender_pubkey).")
            self._reply(401, b'{"error": "Firma Ed25519 requerida", "received": false}\n')
            return

        from src.p2p.crypto import NodeCryptoManager
        if not NodeCryptoManager.verify_signature(payload, sig, pubkey):
            logger.warning("Rechazado webhook P2P entrante: firma Ed25519 invalida.")
            self._reply(401, b'{"error": "Firma Ed25519 invalida o ausente", "received": false}\n')
            return

        trust_check = getattr(self.server, "is_trusted_pubkey", None)
        try:
            trusted = bool(trust_check(pubkey)) if trust_check else False
        except Exception as exc:
            logger.warning(f"Error evaluando confianza del emisor P2P: {exc}")
            trusted = False
        if not trusted:
            logger.warning("Rechazado webhook P2P entrante: clave publica no pertenece a un colaborador aprobado.")
            self._reply(403, b'{"error": "Nodo emisor no confiable", "received": false}\n')
            return

        callback = getattr(self.server, "on_event_callback", None)
        if callback:
            try:
                callback(payload)
            except Exception as exc:
                logger.warning(f"Error procesando webhook entrante: {exc}")
        self._reply(200, b'{"received": true}\n')


class P2PNotificationService:
    """
    Gestiona la comunicación en tiempo real LAN y sondeo de pulso WAN para proyectos colaborativos.
    """

    def __init__(
        self,
        node_uuid: str,
        lan_port: int = 42424,
        on_event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        crypto_manager: Optional[Any] = None,
        is_trusted_pubkey: Optional[Callable[[str], bool]] = None,
        bind_host: Optional[str] = None
    ) -> None:
        self.node_uuid = node_uuid
        self.lan_port = lan_port
        self.on_event = on_event_callback or (lambda evt: None)
        self.crypto = crypto_manager
        self.is_trusted_pubkey = is_trusted_pubkey
        self.bind_host = bind_host or os.environ.get("MCP2P_LAN_BIND_HOST", "127.0.0.1")
        self._server: Optional[P2PNotificationServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._running = False
        self._remote_heads_cache: Dict[str, str] = {}

    def start_lan_server(self) -> bool:
        """Inicia el listener HTTP para la red local en un hilo secundario daemon."""
        if self._running:
            return True

        # Intentar enlazar en el puerto solicitado o alternativos
        ports_to_try = [self.lan_port, self.lan_port + 1, self.lan_port + 2, 0]
        for p in ports_to_try:
            try:
                self._server = P2PNotificationServer(
                    (self.bind_host, p), P2PWebhookHandler, self.on_event, self.is_trusted_pubkey
                )
                actual_port = self._server.server_address[1]
                self.lan_port = actual_port
                break
            except OSError:
                continue

        if not self._server:
            logger.error("No se pudo iniciar el listener HTTP LAN")
            return False

        self._running = True
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()
        logger.info(f"P2P LAN listener iniciado en {self.bind_host}:{self.lan_port}")
        return True

    def stop_lan_server(self) -> None:
        """Detiene el listener HTTP local."""
        self._running = False
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None

    def broadcast_lan_notification(self, peer_endpoints: List[str], event_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Envía un aviso de actualización firmado con Ed25519 a nodos conocidos en LAN.
        """
        import urllib.request
        event_payload["from_node"] = self.node_uuid
        event_payload["timestamp"] = datetime.now(timezone.utc).isoformat()

        if self.crypto:
            sig = self.crypto.sign_payload(event_payload)
            package = {
                "payload": event_payload,
                "sig": sig,
                "sender_pubkey": self.crypto.public_key_base64,
                "from_node": self.node_uuid
            }
            body = json.dumps(package).encode("utf-8")
        else:
            body = json.dumps(event_payload).encode("utf-8")

        successes = []
        failures = []

        for endpoint in peer_endpoints:
            url = endpoint if endpoint.startswith("http") else f"http://{endpoint}"
            if not url.endswith("/webhook/p2p") and not url.endswith("/notify"):
                url = f"{url.rstrip('/')}/webhook/p2p"
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": "MCP2P-LAN-Notifier"}
            )
            try:
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    if resp.status == 200:
                        successes.append(endpoint)
                    else:
                        failures.append((endpoint, f"HTTP {resp.status}"))
            except Exception as e:
                failures.append((endpoint, str(e)))

        return {
            "delivered": len(successes),
            "successes": successes,
            "failed": len(failures),
            "failures": failures
        }

    def check_wan_remote_changes(self, repo_path: str | Path, remote_url: Optional[str] = None) -> Dict[str, Any]:
        """
        Inspecciona el repositorio remoto con 'git ls-remote' (consumo 0 tokens y sin alterar el working tree local).
        Compara la referencia HEAD remota contra la última conocida para detectar nuevos commits de pares.
        """
        path = Path(repo_path)
        cmd = ["git", "ls-remote", "--heads"]
        if remote_url:
            cmd.append(remote_url)
        else:
            cmd.append("origin")

        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        try:
            res = subprocess.run(
                cmd,
                cwd=path,
                capture_output=True,
                text=True,
                timeout=12,
                creationflags=creationflags
            )
            if res.returncode != 0:
                return {"success": False, "error": res.stderr.strip() or "ls-remote falló"}

            heads = {}
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    sha, ref = parts[0], parts[1]
                    heads[ref] = sha

            repo_key = str(path.resolve())
            last_known = self._remote_heads_cache.get(repo_key, {})
            has_changes = False
            new_refs = []

            for ref, sha in heads.items():
                if ref in last_known and last_known[ref] != sha:
                    has_changes = True
                    new_refs.append(ref)
                elif ref not in last_known:
                    if last_known:
                        has_changes = True
                        new_refs.append(ref)

            self._remote_heads_cache[repo_key] = heads

            return {
                "success": True,
                "has_changes": has_changes,
                "changed_refs": new_refs,
                "current_remote_heads": heads
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}
