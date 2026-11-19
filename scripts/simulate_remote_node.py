"""Herramienta de desarrollo para la prueba manual N12; en 2.0.2 no hay transporte real."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.adapters.identity.peer_verifier import PeerEnvelopeVerifier
from src.adapters.persistence.node_policy_sqlite import SqliteNodePolicyStore
from src.adapters.persistence.relay_sqlite import SqliteRelayStore
from src.core.services.message_relay_service import MessageRelayService
from src.core.services.remote_intake_service import RemoteIntakeService
from src.p2p.crypto import NodeCryptoManager
from src.storage.db import DatabaseManager
from tests.helpers.simulated_device import SimulatedDevice


def _normalize_pane_slot(raw: str) -> str:
    val = str(raw or "").strip()
    if val.isdigit():
        return f"pane_{val}"
    return val


class _DbPaneDirectory:
    def __init__(self, *, pane_slot: str, node_uuid: str, project_uuid: str) -> None:
        self._rec = SimpleNamespace(
            pane_id=pane_slot,
            node_uuid=node_uuid,
            project_uuid=project_uuid,
            status="active",
        )

    def find_active_pane_by_node(self, node_uuid: str, project_uuid: Optional[str] = None) -> Optional[Any]:
        if node_uuid == self._rec.node_uuid and (project_uuid is None or project_uuid == self._rec.project_uuid):
            return self._rec
        return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Simula mensajes entrantes de otro dispositivo sobre una BD local de MCP2P (N12)."
    )
    parser.add_argument("--db", required=True, help="Ruta a la base de datos SQLite local.")
    parser.add_argument("--project", required=True, help="UUID del proyecto destino.")
    parser.add_argument("--target-pane", required=True, help="Ranura del panel destino (p. ej. pane_2).")
    parser.add_argument("--name", required=True, help="Nombre declarado por el nodo remoto.")
    parser.add_argument("--same-owner", action="store_true", help="Firma el device_cert con la clave humana local.")
    parser.add_argument("--text", default="Mensaje simulado desde otro equipo", help="Texto del mensaje.")
    parser.add_argument("--kind", default="note", help="Tipo de sobre (p. ej. note, task.request).")
    parser.add_argument("--count", type=int, default=1, help="Cantidad de mensajes a enviar.")
    parser.add_argument("--new-key", action="store_true", help="Genera una clave nueva conservando el nombre.")
    parser.add_argument(
        "--human-key",
        default=None,
        help="Ruta opcional a node_identity.key local (por defecto ~/.mcp2p/node_identity.key).",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        print(json.dumps({"success": False, "code": "DB_NOT_FOUND", "error": f"No existe {db_path}"}))
        return 1

    pane_slot = _normalize_pane_slot(args.target_pane)
    db = DatabaseManager(db_path=str(db_path))
    try:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT node_uuid FROM pane_identities WHERE project_uuid = ? AND pane_slot = ?;",
                (args.project, pane_slot),
            ).fetchone()
        if row is None:
            print(
                json.dumps(
                    {
                        "success": False,
                        "code": "TARGET_NOT_FOUND",
                        "error": f"No existe pane_identities para ({args.project}, {pane_slot}).",
                    }
                )
            )
            return 1

        target_node_uuid = str(row["node_uuid"])
        pane_dir = _DbPaneDirectory(
            pane_slot=pane_slot,
            node_uuid=target_node_uuid,
            project_uuid=args.project,
        )
        relay_store = SqliteRelayStore(db)
        relay = MessageRelayService(store=relay_store, pane_directory=pane_dir)
        node_store = SqliteNodePolicyStore(db)
        verifier = PeerEnvelopeVerifier(node_store=node_store)

        human_key_path = (
            Path(args.human_key).expanduser()
            if args.human_key
            else Path.home() / ".mcp2p" / "node_identity.key"
        )
        local_crypto = NodeCryptoManager(key_path=human_key_path) if human_key_path.exists() else None
        local_human_pub = local_crypto.public_key_base64 if local_crypto is not None else None

        intake = RemoteIntakeService(
            relay=relay,
            node_store=node_store,
            verifier=verifier,
            local_human_pub=local_human_pub,
        )

        name_tag = hashlib.sha256(args.name.encode("utf-8")).hexdigest()[:12]
        sim_root = db_path.parent / ".sim_devices"
        if args.new_key:
            device_dir = sim_root / f"{name_tag}-{uuid.uuid4().hex[:8]}"
            device_id = f"sim-{name_tag}-{uuid.uuid4().hex[:6]}"
        else:
            device_dir = sim_root / name_tag
            device_id = f"sim-{name_tag}"

        human_signer = None
        if args.same_owner:
            if local_crypto is None or local_crypto.is_locked:
                print(
                    json.dumps(
                        {
                            "success": False,
                            "code": "HUMAN_KEY_UNAVAILABLE",
                            "error": "No hay clave humana local desbloqueada para --same-owner.",
                        }
                    )
                )
                return 1
            human_signer = local_crypto

        device = SimulatedDevice(
            device_dir,
            args.name,
            human_signer=human_signer,
            device_id=device_id,
        )

        results: List[Dict[str, Any]] = []
        count = max(1, int(args.count))
        for i in range(count):
            msg_text = args.text if count == 1 else f"{args.text} (#{i + 1})"
            env, content = device.envelope_to(
                target_node_uuid,
                args.project,
                msg_text,
                kind=args.kind,
            )
            res = intake.accept_remote_envelope(
                env,
                content,
                transport="simulated",
                declared_node_name=args.name,
            )
            results.append(res)

        ok = any(r.get("success") for r in results)
        print(
            json.dumps(
                {
                    "success": ok,
                    "device_fingerprint": device.fingerprint(),
                    "target_pane": pane_slot,
                    "target_node_uuid": target_node_uuid,
                    "results": results,
                },
                ensure_ascii=False,
            )
        )
        return 0 if ok else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
