"""
Sub-adaptador UI para Laboratorio OKF (carpetas dinámicas, tags YAML, Freshness),
Multi-Seed P2P, Puerta con Llave (ExtensionSecurityGate) y Diagnóstico del Sistema.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.adapters.outbound.extension_gate import (
    ExtensionSecurityGate,
    NullAgentTerminalConnector,
    OutboundRestConnector,
    SignedConnectorManifest,
)
from src.adapters.terminal.cli_catalog import CliCatalogService, has_control_chars
from src.adapters.terminal.pty_host import (
    DEFAULT_MANUAL_COMMAND,
    HARD_MAX_PANES,
    MAX_PANES_SETTING_KEY,
    MAX_PANES_SETTING_NAMESPACE,
    PaneLimitError,
    normalize_pane_id,
)
from src.app.ui_adapters import _BaseUIAdapter
from src.core.kernel import CoreKernel
from src.mcp.pane_registry import EmbeddedMcpHub, PaneRegistry
from src.p2p.mesh_adapter import MultiPeerMeshConnector
from src.runtime.terminal_platform import is_unsupported_terminal_platform, unsupported_platform_response

logger = logging.getLogger("mcp2p.api_bridge.extensions_lab")


def _default_mcp_config(hub_url: Optional[str] = None) -> Dict[str, Any]:
    return {
        "mcpServers": {
            "mcp2p": {
                "url": hub_url or "http://127.0.0.1:8765/mcp",
            }
        }
    }


_ROLE_RE = re.compile(r"[A-Za-z0-9_.-]{1,48}")
_MAX_CWD_LEN = 4096
_MAX_TERMINAL_DIMENSION = 1000
_MAX_COMMAND_LEN = 2000


def _fail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    """Respuesta de error uniforme `{success, code, message, ...}` del puente de terminales."""
    return {"success": False, "code": code, "message": message, **extra}


def _strip_secret_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = dict(payload)
    cleaned.pop("session_token", None)
    return cleaned


class ExtensionsLabUIAdapter(_BaseUIAdapter):
    """Expone las capacidades del Laboratorio OKF, P2P Seeds, Puerta con Llave y Swarm Terminal Matrix."""

    def __init__(self, bridge: Any) -> None:
        super().__init__(bridge)
        self._rest_connectors: Dict[str, OutboundRestConnector] = {}
        self._terminal_lock = threading.RLock()
        self._pane_registry: Optional[PaneRegistry] = None
        self._embedded_hub: Optional[EmbeddedMcpHub] = None
        self._null_terminal_connector: Optional[NullAgentTerminalConnector] = None

    def _get_gate(self) -> ExtensionSecurityGate:
        gate = self.extension_gate
        if gate is None:
            gate = ExtensionSecurityGate(db_manager=self.db)
            self._bridge.extension_gate = gate
        return gate

    def _get_mesh(self) -> MultiPeerMeshConnector:
        mesh = self.mesh
        if mesh is None:
            mesh = MultiPeerMeshConnector(node_uuid=self._local_node(), crypto_manager=self.crypto)
            self._bridge.mesh = mesh
        return mesh

    def _get_rest_connector(self, connector_id: str) -> OutboundRestConnector:
        if connector_id not in self._rest_connectors:
            self._rest_connectors[connector_id] = OutboundRestConnector(
                gate=self._get_gate(),
                connector_id=connector_id,
            )
        return self._rest_connectors[connector_id]

    @staticmethod
    def _format_gate_row(raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "connector_id": raw.get("connector_id", ""),
            "unlocked": bool(raw.get("unlocked", False)),
            "allowed_domains": list(raw.get("allowed_domains", [])),
            "allow_private_ips": bool(raw.get("allow_private_ips", False)),
            "circuit_state": raw.get("circuit_state", "closed"),
            "manifest_expires_or_issued_at": raw.get("issued_at") or "",
            "issued_at": raw.get("issued_at") or "",
            "owner_node_uuid": raw.get("owner_node_uuid") or "",
            "db_scopes": list(raw.get("db_scopes", [])),
            "can_invoke_subprocess": bool(raw.get("can_invoke_subprocess", False)),
        }

    # ==========================
    # Gestión dinámica de carpetas OKF
    # ==========================

    def list_okf_folders(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}
            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}
            self.okf.ensure_okf_structure(gov_path, proj.get("name", "Proyecto"), target_uuid)
            folders = self.okf.list_okf_folders(gov_path)
            return {"success": True, "folders": folders}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def add_okf_folder(
        self,
        project_uuid: Optional[str],
        folder_path: str,
        category: str = "custom",
        description: str = "",
    ) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}
            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}
            res = self.okf.add_okf_folder(gov_path, folder_path, category=category, description=description)
            return {"success": True, "folder": res.get("folder"), "folders": res.get("folders", [])}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def remove_okf_folder(
        self,
        project_uuid: Optional[str],
        folder_path: str,
        archive_if_not_empty: bool = True,
    ) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}
            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}
            res = self.okf.remove_okf_folder(gov_path, folder_path, archive_if_not_empty=archive_if_not_empty)
            return {
                "success": True,
                "archived_to": res.get("archived_to"),
                "folders": res.get("folders", []),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def rename_okf_folder(
        self,
        project_uuid: Optional[str],
        old_path: str,
        new_path: str,
    ) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}
            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}
            res = self.okf.rename_okf_folder(gov_path, old_path, new_path)
            return {"success": True, "folders": res.get("folders", [])}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================
    # Freshness y Tags YAML OKF
    # ==========================

    def get_okf_freshness_report(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}
            gov_path = proj.get("gov_repo_path") or ""
            code_path = proj.get("code_repo_path") or ""
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}
            sla_days = max(1, int(round(float(proj.get("default_sla_hours") or 336) / 24.0)))
            report = self.okf.get_okf_freshness_report(code_path, gov_path, sla_days=sla_days)
            return {
                "success": True,
                "sla_days": report.get("sla_days", sla_days),
                "summary": report.get("summary", {}),
                "items": report.get("items", []),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_okf_tags_index(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}
            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}
            idx = self.okf.get_tags_index(gov_path, rebuild=True)
            return {
                "success": True,
                "tags": idx.get("tags", {}),
                "items": idx.get("items", []),
                "total_artifacts": idx.get("total_artifacts", 0),
                "total_tags": idx.get("total_tags", 0),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_artifact_tags(
        self,
        project_uuid: Optional[str],
        rel_path: str,
        tags: List[str],
    ) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}
            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}
            res = self.okf.update_artifact_tags(gov_path, rel_path, tags)
            return {
                "success": True,
                "path": res.get("path", rel_path),
                "tags": res.get("tags", []),
                "index": res.get("index", {}),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def filter_okf_by_tags(
        self,
        project_uuid: Optional[str],
        tags: List[str],
        match_all: bool = False,
    ) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}
            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}
            items = self.okf.filter_by_tags(gov_path, tags, match_all=match_all)
            return {"success": True, "items": items}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================
    # Multi-Seed P2P
    # ==========================

    def _merged_seeds(self, target_uuid: Optional[str]) -> List[Dict[str, Any]]:
        mesh = self._get_mesh()
        db_seeds = self.db.list_seed_nodes(target_uuid)
        for row in db_seeds:
            s_url = (row.get("seed_url") or "").strip()
            if s_url and all(s.get("seed_url") != s_url for s in mesh.list_seed_nodes()):
                try:
                    mesh.add_seed_node(s_url)
                except ValueError:
                    pass
        mesh_map = {s.get("seed_url"): s for s in mesh.list_seed_nodes()}
        merged: List[Dict[str, Any]] = []
        seen = set()
        for row in db_seeds:
            url = row.get("seed_url", "")
            seen.add(url)
            m_info = mesh_map.get(url, {})
            merged.append({
                "seed_url": url,
                "url": url,
                "label": row.get("label", ""),
                "created_at": row.get("created_at", ""),
                "healthy": m_info.get("healthy", True),
                "circuit_state": m_info.get("circuit_state", "closed"),
                "failure_count": m_info.get("failure_count", 0),
            })
        for url, m_info in mesh_map.items():
            if url not in seen:
                merged.append(m_info)
        return merged

    def list_p2p_seeds(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid(project_uuid)
            seeds = self._merged_seeds(target_uuid)
            peers = list(self._get_mesh().get_peers())
            seen_peers = {p.get("node_uuid") for p in peers}
            if target_uuid:
                for collab in self.db.get_project_collaborators(target_uuid):
                    n_id = collab.get("node_uuid")
                    if n_id and n_id not in seen_peers:
                        seen_peers.add(n_id)
                        peers.append({
                            "node_uuid": n_id,
                            "role": collab.get("role", "collaborator"),
                            "status": collab.get("status", "pending_approval"),
                            "endpoint": "",
                        })
            return {"success": True, "seeds": seeds, "peers": peers}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def add_p2p_seed(self, project_uuid: Optional[str], seed_url: str) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid(project_uuid)
            cleaned = (seed_url or "").strip()
            if not cleaned:
                return {"success": False, "error": "seed_url es obligatorio"}
            self._get_mesh().add_seed_node(cleaned)
            self.db.add_seed_node(target_uuid, cleaned)
            return {"success": True, "seeds": self._merged_seeds(target_uuid)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def remove_p2p_seed(self, project_uuid: Optional[str], seed_url: str) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid(project_uuid)
            cleaned = (seed_url or "").strip()
            self._get_mesh().remove_seed_node(cleaned)
            self.db.remove_seed_node(target_uuid, cleaned)
            return {"success": True, "seeds": self._merged_seeds(target_uuid)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================
    # Puerta con Llave (ExtensionSecurityGate) y OutboundRestConnector
    # ==========================

    def get_extension_gates_status(self) -> Dict[str, Any]:
        try:
            raw_gates = self._get_gate().get_gates_status()
            gates = [self._format_gate_row(g) for g in raw_gates]
            return {"success": True, "gates": gates}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def unlock_extension_gate(
        self,
        connector_id: str,
        allowed_domains: List[str],
        allow_private_ips: bool = False,
    ) -> Dict[str, Any]:
        try:
            cid = (connector_id or "").strip()
            if not cid:
                return {"success": False, "error": "connector_id es obligatorio"}
            domains = [str(d).strip() for d in (allowed_domains or []) if str(d).strip()]
            owner_node = self._local_node()
            manifest = SignedConnectorManifest.create_signed(
                crypto_manager=self.crypto,
                connector_id=cid,
                owner_node_uuid=owner_node,
                allowed_domains=domains,
                allow_private_ips=bool(allow_private_ips),
            )
            gate = self._get_gate()
            gate.unlock_connector(
                manifest=manifest,
                trusted_owner_pubkey=self.crypto.public_key_base64,
                actor_kind="human_ui",
            )
            matched = next(
                (self._format_gate_row(g) for g in gate.get_gates_status() if g.get("connector_id") == cid),
                {"connector_id": cid, "unlocked": True, "allowed_domains": domains, "allow_private_ips": bool(allow_private_ips)},
            )
            return {"success": True, "gate": matched}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def lock_extension_gate(self, connector_id: str) -> Dict[str, Any]:
        try:
            cid = (connector_id or "").strip()
            gate = self._get_gate()
            gate.lock_connector(cid)
            matched = next(
                (self._format_gate_row(g) for g in gate.get_gates_status() if g.get("connector_id") == cid),
                {"connector_id": cid, "unlocked": False, "allowed_domains": [], "allow_private_ips": False},
            )
            return {"success": True, "gate": matched}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def test_outbound_rest_gate(self, connector_id: str, url: str) -> Dict[str, Any]:
        cid = (connector_id or "outbound_rest").strip() or "outbound_rest"
        connector = self._get_rest_connector(cid)
        try:
            resp = connector.request(url, method="GET")
            body = resp.get("body", "")
            return {
                "success": bool(resp.get("ok", True)),
                "blocked_reason": None,
                "status_code": resp.get("status_code"),
                "preview": str(body)[:400],
            }
        except PermissionError as pe:
            return {
                "success": False,
                "blocked_reason": str(pe),
                "status_code": 403,
                "preview": "",
            }
        except ValueError as ve:
            return {
                "success": False,
                "blocked_reason": str(ve),
                "status_code": 400,
                "preview": "",
            }
        except Exception as exc:
            return {
                "success": False,
                "blocked_reason": str(exc),
                "status_code": 502,
                "preview": "",
            }

    # ==========================
    # Diagnóstico del Sistema
    # ==========================

    def run_system_diagnostics(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        checks: List[Dict[str, str]] = []

        # 1. SQLite WAL y Pool
        try:
            projects_count = len(self.db.list_projects())
            checks.append({
                "name": "SQLite Pool + WAL",
                "status": "passed",
                "detail": f"Base de datos accesible ({projects_count} proyectos) en {self.db.db_path}",
            })
        except Exception as e:
            checks.append({"name": "SQLite Pool + WAL", "status": "failed", "detail": str(e)})

        # 2. Identidad Ed25519
        try:
            pub = self.crypto.public_key_base64
            if getattr(self.crypto, "is_locked", False):
                # EGM-53: con la clave cifrada y bloqueada el diagnóstico no firma nada.
                checks.append({
                    "name": "Identidad Ed25519",
                    "status": "passed" if pub else "failed",
                    "detail": f"Llave local cifrada y bloqueada ({pub[:16]}...)",
                })
            else:
                sig = self.crypto.sign_human_approval("DIAG_CHECK", "diagnostics", self._local_node(), self._dev_name(), "OK")
                ok = bool(pub and sig.get("signature_ed25519"))
                checks.append({
                    "name": "Identidad Ed25519",
                    "status": "passed" if ok else "failed",
                    "detail": f"Llave local activa ({pub[:16]}...)" if ok else "Llave Ed25519 no disponible",
                })
        except Exception as e:
            checks.append({"name": "Identidad Ed25519", "status": "failed", "detail": str(e)})

        # 3. Puerta con Llave (Default-Deny)
        try:
            gates = self._get_gate().get_gates_status()
            term = NullAgentTerminalConnector(self._get_gate())
            checks.append({
                "name": "ExtensionSecurityGate",
                "status": "passed",
                "detail": f"{len(gates)} conectores monitoreados (terminales agente disponibles={term.is_available()})",
            })
        except Exception as e:
            checks.append({"name": "ExtensionSecurityGate", "status": "failed", "detail": str(e)})

        # 4. Integridad OKF del proyecto activo/solicitado
        target_uuid = self._resolve_project_uuid(project_uuid)
        if target_uuid:
            proj = self.db.get_project_by_uuid(target_uuid)
            gov_path = proj.get("gov_repo_path") if proj else None
            if gov_path and Path(gov_path).exists():
                try:
                    val = self.okf.validate_okf_integrity(gov_path)
                    ok = bool(val.get("valid", True))
                    checks.append({
                        "name": "Gobernanza OKF",
                        "status": "passed" if ok else "failed",
                        "detail": f"Proyecto {proj.get('name')} valid={ok}",
                    })
                except Exception as e:
                    checks.append({"name": "Gobernanza OKF", "status": "failed", "detail": str(e)})
            else:
                checks.append({
                    "name": "Gobernanza OKF",
                    "status": "passed",
                    "detail": "Sin repositorio OKF local inicializado todavía",
                })

        # 5. Malla P2P / Seeds
        try:
            seeds = self._merged_seeds(target_uuid)
            checks.append({
                "name": "Malla P2P Multi-Seed",
                "status": "passed",
                "detail": f"{len(seeds)} seeds configurados, modo={self._get_mesh().transport_mode}",
            })
        except Exception as e:
            checks.append({"name": "Malla P2P Multi-Seed", "status": "failed", "detail": str(e)})

        passed = sum(1 for c in checks if c["status"] == "passed")
        failed = sum(1 for c in checks if c["status"] != "passed")
        return {
            "success": True,
            "checks": checks,
            "summary": {"passed": passed, "failed": failed},
        }

    # ==========================
    # Swarm Terminal Matrix (PTY + EmbeddedMcpHub + PaneRegistry)
    # ==========================

    def _get_terminal_connector(self) -> Any:
        kernel = self.core_kernel
        if kernel is not None:
            conn = getattr(kernel, "terminal_connector", None)
            if conn is None and callable(getattr(kernel, "get_connector", None)):
                conn = kernel.get_connector(CoreKernel.PORT_AGENT_TERMINAL)
            if conn is not None:
                return conn
        with self._terminal_lock:
            if self._null_terminal_connector is None:
                self._null_terminal_connector = NullAgentTerminalConnector(self._get_gate())
            return self._null_terminal_connector

    def _get_pane_registry(self) -> PaneRegistry:
        with self._terminal_lock:
            if self._pane_registry is None:
                kernel = self.core_kernel
                collab_repo = getattr(kernel, "collab_repo", None) if kernel is not None else None
                self._pane_registry = PaneRegistry(collab_repo=collab_repo)
            return self._pane_registry

    def _get_embedded_hub(self) -> Optional[EmbeddedMcpHub]:
        with self._terminal_lock:
            kernel = self.core_kernel
            registry = self._get_pane_registry()
            if self._embedded_hub is None and kernel is not None:
                self._embedded_hub = EmbeddedMcpHub(
                    kernel=kernel,
                    pane_registry=registry,
                )
            return self._embedded_hub

    def _ensure_hub_started(self) -> Optional[str]:
        """Arranca `EmbeddedMcpHub` de forma segura e idempotente; retorna None si falla."""
        try:
            hub = self._get_embedded_hub()
            if hub is None:
                return None
            if hub.is_running() and hub.url:
                return hub.url
            return hub.start(timeout_s=5.0)
        except Exception as exc:
            logger.warning("No se pudo iniciar EmbeddedMcpHub (modo tolerante): %s", exc)
            return None

    def _resolve_active_code_repo(self) -> Optional[str]:
        try:
            target_uuid = self._resolve_project_uuid()
            if not target_uuid:
                return None
            proj = self.db.get_project_by_uuid(target_uuid)
            if not proj:
                return None
            code_repo = proj.get("code_repo_path")
            if code_repo and Path(str(code_repo)).is_dir():
                return str(code_repo)
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_degraded_instructions(terminal: Any, hub_url: Optional[str] = None) -> Dict[str, Any]:
        for obj in (terminal, getattr(terminal, "fallback", None)):
            if obj is not None and callable(getattr(obj, "_degraded_response", None)):
                try:
                    raw = dict(obj._degraded_response())
                    if raw.get("manual_command") and isinstance(raw.get("mcp_config"), dict):
                        return {
                            "manual_command": raw["manual_command"],
                            "mcp_config": raw["mcp_config"],
                        }
                except Exception:
                    pass
        return {
            "manual_command": DEFAULT_MANUAL_COMMAND,
            "mcp_config": _default_mcp_config(hub_url=hub_url),
        }

    def pty_get_matrix_status(self) -> Dict[str, Any]:
        try:
            terminal = self._get_terminal_connector()
            registry = self._get_pane_registry()

            try:
                connector_status = dict(terminal.get_status())
            except Exception as exc:
                connector_status = {
                    "available": False,
                    "degraded": True,
                    "mode": "error",
                    "error": str(exc),
                }

            avail_fn = getattr(terminal, "is_available", None)
            is_avail = bool(avail_fn()) if callable(avail_fn) else bool(connector_status.get("available", False))
            degraded = (
                bool(connector_status.get("degraded", False))
                or (not is_avail)
                or (not bool(connector_status.get("available", is_avail)))
            )

            sessions: List[Dict[str, Any]] = []
            list_fn = getattr(terminal, "list_sessions", None)
            if callable(list_fn):
                try:
                    sessions = [_strip_secret_fields(dict(s)) for s in list_fn()]
                except Exception:
                    sessions = []
            elif isinstance(connector_status.get("sessions"), list):
                sessions = [_strip_secret_fields(dict(s)) for s in connector_status["sessions"]]

            hub = self._embedded_hub
            hub_running = bool(hub.is_running()) if hub is not None else False
            hub_url = hub.url if (hub is not None and hub_running) else None

            manual_command: Optional[str] = None
            mcp_config: Optional[Dict[str, Any]] = None
            if degraded:
                instructions = self._extract_degraded_instructions(terminal, hub_url=hub_url)
                manual_command = instructions["manual_command"]
                mcp_config = instructions["mcp_config"]

            return {
                "success": True,
                "degraded": degraded,
                "connector_status": connector_status,
                "hub_url": hub_url,
                "hub_running": hub_running,
                "panes": registry.list_panes(),
                "sessions": sessions,
                "manual_command": manual_command,
                "mcp_config": mcp_config,
            }
        except Exception as exc:
            instructions = self._extract_degraded_instructions(None)
            return {
                "success": False,
                "degraded": True,
                "error": str(exc),
                "connector_status": {"available": False, "degraded": True, "mode": "error"},
                "hub_url": None,
                "hub_running": False,
                "panes": [],
                "sessions": [],
                "manual_command": instructions["manual_command"],
                "mcp_config": instructions["mcp_config"],
            }

    # ------------------------------------------------------------------
    # Catalogo de CLIs (T1/T2 del plan 2026-11-15, seccion 5b)
    # ------------------------------------------------------------------

    def _catalog_service(self) -> CliCatalogService:
        """Reutiliza el servicio del conector PTY (misma resolucion que el spawn) o crea uno nuevo."""
        try:
            terminal = self._get_terminal_connector()
            primary = getattr(terminal, "primary", terminal)
            svc = getattr(primary, "catalog_service", None)
            if isinstance(svc, CliCatalogService):
                return svc
        except Exception:
            pass
        kernel = self.core_kernel
        return CliCatalogService(getattr(kernel, "extension_repo", None))

    @staticmethod
    def _terminal_max_panes(terminal: Any) -> int:
        try:
            return int(getattr(terminal, "max_panes", 4) or 4)
        except (TypeError, ValueError):
            return 4

    def pty_list_cli_catalog(self) -> Dict[str, Any]:
        """Lista los CLIs del catalogo con estado de instalacion; no ejecuta ningun binario."""
        try:
            svc = self._catalog_service()
            view = svc.describe()
            terminal = self._get_terminal_connector()
            return {
                "success": True,
                "code": "OK",
                "message": "Catalogo de CLIs.",
                "max_panes": self._terminal_max_panes(terminal),
                "hard_max_panes": HARD_MAX_PANES,
                "terminal_platform_supported": not is_unsupported_terminal_platform(),
                **view,
            }
        except Exception as exc:
            logger.warning("pty_list_cli_catalog fallo: %s", exc)
            return _fail("INTERNAL_ERROR", f"No se pudo leer el catalogo de CLIs: {exc}")

    def pty_set_cli_path(self, cli_id: Any, path: Any) -> Dict[str, Any]:
        """Fija (o borra con cadena vacia) la ruta del binario de un CLI del catalogo."""
        try:
            return self._catalog_service().set_binary_path(cli_id, path)
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_set_max_panes(self, max_panes: Any) -> Dict[str, Any]:
        """Cambia el maximo de paneles (1..6) y lo persiste; no cierra paneles vivos."""
        if isinstance(max_panes, bool) or not isinstance(max_panes, int) or not (1 <= max_panes <= HARD_MAX_PANES):
            return _fail("INVALID_ARGUMENT", f"max_panes debe ser un entero entre 1 y {HARD_MAX_PANES}.")
        try:
            terminal = self._get_terminal_connector()
            setter = getattr(terminal, "set_max_panes", None)
            if not callable(setter):
                return _fail("TERMINAL_ADAPTER_DEGRADED", "El conector de terminales no admite cambiar el maximo.")
            applied = int(setter(max_panes))
            repo = getattr(self.core_kernel, "extension_repo", None)
            if repo is not None:
                repo.set_extension_setting(MAX_PANES_SETTING_NAMESPACE, MAX_PANES_SETTING_KEY, str(applied))
            return {"success": True, "code": "OK", "max_panes": applied, "hard_max_panes": HARD_MAX_PANES,
                    "message": f"Maximo de paneles: {applied}."}
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    @staticmethod
    def _validate_cwd(cwd: Any) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        if cwd is None:
            return None, None
        if not isinstance(cwd, str) or not cwd.strip():
            return None, _fail("INVALID_CWD", "La carpeta de trabajo debe ser un texto no vacio.")
        if len(cwd) > _MAX_CWD_LEN or has_control_chars(cwd):
            return None, _fail("INVALID_CWD", "La carpeta de trabajo contiene caracteres no validos.")
        if not os.path.isabs(cwd):
            return None, _fail("INVALID_CWD", "La carpeta de trabajo debe ser una ruta absoluta.")
        if not os.path.isdir(cwd):
            return None, _fail("INVALID_CWD", f"La carpeta de trabajo no existe o no es un directorio: {cwd}")
        return os.path.realpath(cwd), None

    def pty_spawn_pane(
        self,
        pane_id: Any,
        cli_profile: str = "claude",
        role: str = "implementer",
        command: Optional[Any] = None,
        cols: int = 100,
        rows: int = 28,
        start_hub: bool = True,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        registry = self._get_pane_registry()
        registered_here = False
        norm_pane_id: Optional[str] = None
        try:
            terminal = self._get_terminal_connector()
            max_panes = self._terminal_max_panes(terminal)

            if is_unsupported_terminal_platform():
                return {**unsupported_platform_response(), "pane_id": str(pane_id)}

            try:
                norm_pane_id, _ = normalize_pane_id(pane_id, max_panes=max_panes)
            except PaneLimitError as exc:
                return _fail("PANE_LIMIT_REACHED", str(exc), reason="pane_out_of_range",
                             degraded=False, pane_id=str(pane_id), max_panes=max_panes)
            except ValueError as exc:
                return _fail("INVALID_PANE_ID", str(exc), degraded=False, pane_id=str(pane_id))

            svc = self._catalog_service()
            if not isinstance(cli_profile, str) or svc.get_spec(cli_profile) is None:
                known = svc.catalog().ids()
                return _fail("CLI_PROFILE_UNKNOWN", f"Perfil de CLI desconocido. Permitidos: {known}.",
                             degraded=False, pane_id=norm_pane_id, known_profiles=known)
            if not isinstance(role, str) or _ROLE_RE.fullmatch(role) is None:
                return _fail("INVALID_ARGUMENT", "role invalido (letras, digitos, '_', '.', '-'; max 48).",
                             degraded=False, pane_id=norm_pane_id)
            for name, value in (("cols", cols), ("rows", rows)):
                if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= _MAX_TERMINAL_DIMENSION):
                    return _fail("INVALID_ARGUMENT", f"{name} debe ser un entero entre 1 y {_MAX_TERMINAL_DIMENSION}.",
                                 degraded=False, pane_id=norm_pane_id)
            if command is not None:
                # El argv de un CLI del catalogo sale SIEMPRE del catalogo; solo la shell acepta comando propio.
                if cli_profile != "custom_shell":
                    return _fail("COMMAND_NOT_ALLOWED",
                                 "Solo el perfil custom_shell admite un comando propio; los demas salen del catalogo.",
                                 degraded=False, pane_id=norm_pane_id)
                if isinstance(command, str):
                    command_ok = 0 < len(command) <= _MAX_COMMAND_LEN and not has_control_chars(command)
                elif isinstance(command, (list, tuple)):
                    command_ok = (
                        0 < len(command) <= 32
                        and all(isinstance(c, str) and len(c) <= _MAX_COMMAND_LEN and not has_control_chars(c)
                                for c in command)
                    )
                else:
                    command_ok = False
                if not command_ok:
                    return _fail("INVALID_ARGUMENT", "command invalido.", degraded=False, pane_id=norm_pane_id)
            resolved_cwd, cwd_error = self._validate_cwd(cwd)
            if cwd_error is not None:
                return {**cwd_error, "degraded": False, "pane_id": norm_pane_id}

            # Evitar pisar el token de una sesión ya viva en ese pane_id.
            list_fn = getattr(terminal, "list_sessions", None)
            if callable(list_fn):
                for existing in list_fn():
                    if existing.get("pane_id") == norm_pane_id and existing.get("alive"):
                        return {
                            "success": False,
                            "degraded": False,
                            "code": "PANE_ALREADY_ACTIVE",
                            "pane_id": norm_pane_id,
                            "session_id": existing.get("session_id"),
                            "pid": existing.get("pid"),
                            "message": f"El panel {norm_pane_id} ya tiene una sesión viva.",
                        }

            registered = registry.register_pane(
                norm_pane_id,
                cli_profile=cli_profile,
                role=role,
            )
            registered_here = True
            token = registered["session_token"]
            node_uuid = registered["node_uuid"]

            if start_hub:
                hub_url = self._ensure_hub_started()
            else:
                hub = self._embedded_hub
                hub_url = hub.url if (hub is not None and hub.is_running()) else None

            effective_cwd = resolved_cwd if resolved_cwd is not None else self._resolve_active_code_repo()

            spawn_res = dict(
                terminal.spawn_session(
                    pane_id=norm_pane_id,
                    cli_profile=cli_profile,
                    role=role,
                    command=command,
                    cwd=effective_cwd,
                    cols=cols,
                    rows=rows,
                    hub_url=hub_url,
                    session_token=token,
                    node_uuid=node_uuid,
                )
            )

            if spawn_res.get("degraded") or not spawn_res.get("success"):
                if spawn_res.get("code") != "PANE_ALREADY_ACTIVE":
                    registry.mark_pane_closed(norm_pane_id)
                degraded = bool(spawn_res.get("degraded", False))
                cleaned = _strip_secret_fields(spawn_res)
                cleaned["success"] = False
                cleaned["degraded"] = degraded
                cleaned["pane_id"] = norm_pane_id
                cleaned["hub_url"] = hub_url
                cleaned.setdefault("code", "PTY_SPAWN_FAILED")
                cleaned.setdefault("message", "No se pudo abrir el panel.")
                if degraded:
                    instructions = self._extract_degraded_instructions(terminal, hub_url=hub_url)
                    cleaned.setdefault("manual_command", instructions["manual_command"])
                    cleaned.setdefault("mcp_config", instructions["mcp_config"])
                return cleaned

            pid = spawn_res.get("pid")
            rec = registry.get_pane(norm_pane_id)
            if rec is not None:
                if pid is not None:
                    with registry._lock:
                        rec.pid = int(pid)
                pane_meta = rec.to_dict(include_token=False)
            else:
                pane_meta = {
                    "pane_id": norm_pane_id,
                    "node_uuid": node_uuid,
                    "cli_profile": cli_profile,
                    "role": role,
                    "pid": pid,
                    "status": "active",
                }

            session_meta = _strip_secret_fields(spawn_res)
            session_meta.setdefault("alive", True)
            session_meta.setdefault("paused", False)
            session_meta.setdefault("rss_mb", 0.0)

            return {
                "success": True,
                "code": "OK",
                "degraded": False,
                "pane_id": norm_pane_id,
                "node_uuid": node_uuid,
                "pid": pid,
                "hub_url": hub_url,
                "cwd": effective_cwd,
                "pane": pane_meta,
                "session": session_meta,
            }
        except Exception as exc:
            if registered_here and norm_pane_id is not None:
                try:
                    registry.mark_pane_closed(norm_pane_id)
                except Exception:
                    pass
            return {
                "success": False,
                "degraded": False,
                "code": "INTERNAL_ERROR",
                "pane_id": norm_pane_id or str(pane_id),
                "message": f"No se pudo abrir el panel: {exc}",
                "error": str(exc),
            }

    def pty_write_input(self, pane_id: Any, data: str, is_human: bool = True) -> Dict[str, Any]:
        try:
            registry = self._get_pane_registry()
            if is_human:
                registry.record_human_keystroke(pane_id)
            terminal = self._get_terminal_connector()
            return _strip_secret_fields(dict(terminal.send_input(pane_id, data)))
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def pty_resize_pane(self, pane_id: Any, cols: int, rows: int) -> Dict[str, Any]:
        try:
            terminal = self._get_terminal_connector()
            return _strip_secret_fields(dict(terminal.resize_session(pane_id, cols, rows)))
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def pty_poll_output(self, pane_id: Any, since_seq: int = 0) -> Dict[str, Any]:
        try:
            terminal = self._get_terminal_connector()
            poll_fn = getattr(terminal, "poll_output", None)
            if callable(poll_fn):
                return _strip_secret_fields(dict(poll_fn(pane_id, since_seq=since_seq)))
            instructions = self._extract_degraded_instructions(terminal)
            return {
                "success": False,
                "degraded": True,
                "code": "TERMINAL_ADAPTER_DEGRADED",
                "pane_id": str(pane_id),
                "alive": False,
                "exit_code": None,
                "paused": False,
                "rss_mb": 0.0,
                "since_seq": since_seq,
                "next_seq": since_seq,
                "truncated": False,
                "chunks": [],
                "text": "",
                "manual_command": instructions["manual_command"],
                "mcp_config": instructions["mcp_config"],
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "pane_id": str(pane_id),
                "alive": False,
                "exit_code": None,
                "paused": False,
                "rss_mb": 0.0,
                "since_seq": since_seq,
                "next_seq": since_seq,
                "truncated": False,
                "chunks": [],
                "text": "",
            }

    def pty_pause_pane(self, pane_id: Any, paused: bool = True) -> Dict[str, Any]:
        try:
            terminal = self._get_terminal_connector()
            return _strip_secret_fields(dict(terminal.pause_session(pane_id, paused=bool(paused))))
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def pty_close_pane(self, pane_id: Any) -> Dict[str, Any]:
        try:
            terminal = self._get_terminal_connector()
            res = _strip_secret_fields(dict(terminal.close_session(pane_id)))
            registry = self._get_pane_registry()
            try:
                registry.mark_pane_closed(pane_id)
            except ValueError:
                pass
            return res
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def close_terminal_matrix(self) -> None:
        """Detiene limpiamente el EmbeddedMcpHub y revoca tokens de paneles activos."""
        with self._terminal_lock:
            hub = self._embedded_hub
            reg = self._pane_registry
        if reg is not None:
            try:
                terminal = self._get_terminal_connector()
            except Exception:
                terminal = None
            for pane in list(reg.list_panes()):
                p_id = pane.get("pane_id")
                if not p_id:
                    continue
                if terminal is not None:
                    try:
                        terminal.close_session(p_id)
                    except Exception:
                        pass
                try:
                    reg.mark_pane_closed(p_id)
                except Exception:
                    pass
        if hub is not None:
            try:
                hub.stop(timeout_s=3.0)
            except Exception as exc:
                logger.debug("Error deteniendo EmbeddedMcpHub en close_terminal_matrix: %s", exc)

    def _close_terminal_matrix(self) -> None:
        self.close_terminal_matrix()
