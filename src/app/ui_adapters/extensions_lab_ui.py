"""
Sub-adaptador UI para Laboratorio OKF (carpetas dinámicas, tags YAML, Freshness),
Multi-Seed P2P, Puerta con Llave (ExtensionSecurityGate) y Diagnóstico del Sistema.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from src.adapters.outbound.extension_gate import (
    ExtensionSecurityGate,
    NullAgentTerminalConnector,
    OutboundRestConnector,
    SignedConnectorManifest,
)
from src.adapters.terminal.cli_catalog import (
    CatalogValidationError,
    CliCatalogService,
    has_control_chars,
    validate_install_command,
)
from src.adapters.terminal.cli_install import InstallConfirmationBroker, build_install_command
from src.adapters.terminal.launch_augment import LaunchServices
from src.adapters.terminal import mcp_injection as _mcp_injection
from src.adapters.terminal.pty_host import (
    DEFAULT_MANUAL_COMMAND,
    HARD_MAX_PANES,
    MAX_PANES_SETTING_KEY,
    MAX_PANES_SETTING_NAMESPACE,
    PaneLimitError,
    normalize_pane_id,
)
from src.app.ui_adapters import _BaseUIAdapter
from src.app.ui_adapters.pane_lifecycle import PaneLifecycle, PaneSpawnedEvent
from src.core.kernel import CoreKernel
from src.core.services.kanban_swarm_adapter import KanbanSwarmAdapter
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
_CLI_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_VALID_RESUME_MODES = frozenset({"none", "continue", "by_id"})
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
        self._install_broker: Optional[InstallConfirmationBroker] = None
        # Un cerrojo por panel: serializa comprobar -> registrar token -> lanzar (EGM-75).
        self._pane_locks: Dict[str, threading.Lock] = {}
        kernel = getattr(bridge, "core_kernel", None)
        identity_repo = getattr(kernel, "pane_identity_repo", None) if kernel is not None else None
        self.launch_services = LaunchServices(identity_repo=identity_repo)
        self.pane_lifecycle = PaneLifecycle()

    def _pane_lock(self, pane_id: str) -> threading.Lock:
        with self._terminal_lock:
            return self._pane_locks.setdefault(pane_id, threading.Lock())

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

    def _bind_launch_services(self, connector: Any) -> Any:
        if self.launch_services.identity_repo is None and self.core_kernel is not None:
            self.launch_services.identity_repo = getattr(self.core_kernel, "pane_identity_repo", None)
        for target in (connector, getattr(connector, "primary", None)):
            if target is not None and hasattr(target, "launch_services") and getattr(target, "launch_services", None) is None:
                target.launch_services = self.launch_services
        return connector

    def _get_terminal_connector(self) -> Any:
        kernel = self.core_kernel
        if kernel is not None:
            conn = getattr(kernel, "terminal_connector", None)
            if conn is None and callable(getattr(kernel, "get_connector", None)):
                conn = kernel.get_connector(CoreKernel.PORT_AGENT_TERMINAL)
            if conn is not None:
                return self._bind_launch_services(conn)
        with self._terminal_lock:
            if self._null_terminal_connector is None:
                self._null_terminal_connector = NullAgentTerminalConnector(self._get_gate())
            return self._bind_launch_services(self._null_terminal_connector)

    def _get_pane_registry(self) -> PaneRegistry:
        with self._terminal_lock:
            if self._pane_registry is None:
                kernel = self.core_kernel
                collab_repo = getattr(kernel, "collab_repo", None) if kernel is not None else None
                identity_repo = getattr(kernel, "pane_identity_repo", None) if kernel is not None else None
                self._pane_registry = PaneRegistry(collab_repo=collab_repo, identity_repo=identity_repo)
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

    def _resolve_active_code_repo(self, project_uuid: Optional[str] = None) -> Optional[str]:
        try:
            target_uuid = project_uuid or self._resolve_project_uuid()
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

            activity_fn = getattr(terminal, "get_pane_activity", None)
            session_by_pane: Dict[str, Dict[str, Any]] = {}
            for s in sessions:
                pid_key = s.get("pane_id")
                if callable(activity_fn) and pid_key is not None and "activity_state" not in s:
                    try:
                        act = activity_fn(pid_key)
                        if isinstance(act, dict):
                            state = act.get("activity_state", act.get("state", "unknown"))
                            s["activity_state"] = state
                            s["activity_source"] = act.get("activity_source", act.get("source", ""))
                            s["idle"] = bool(act.get("idle", state == "idle"))
                    except Exception:
                        pass
                if "activity_state" in s and "idle" not in s:
                    s["idle"] = s.get("activity_state") == "idle"
                if pid_key is not None:
                    session_by_pane[str(pid_key)] = s

            panes: List[Dict[str, Any]] = []
            for raw_pane in registry.list_panes():
                p = dict(raw_pane)
                match = session_by_pane.get(str(p.get("pane_id") or ""))
                if match is not None:
                    for key in ("activity_state", "activity_source", "idle", "mcp_injection"):
                        if key in match and key not in p:
                            p[key] = match[key]
                panes.append(p)

            hub = self._embedded_hub
            hub_running = bool(hub.is_running()) if hub is not None else False
            hub_url = hub.url if (hub is not None and hub_running) else None

            default_relay_status: Dict[str, Any] = {
                "auto_relay_enabled": False,
                "tripped_cases": [],
            }
            relay_status: Dict[str, Any] = dict(default_relay_status)
            if hub is not None and hub_running:
                try:
                    relay = hub.relay_orchestrator()
                    if relay is not None:
                        relay_status = dict(relay.get_auto_relay_status())
                except Exception:
                    relay_status = dict(default_relay_status)
            # D2: el Auto-Relay es por panel (cada `panes[i].auto_relay_enabled`); la raiz resume si hay alguno.
            auto_relay_enabled = any(bool(p.get("auto_relay_enabled")) for p in panes)
            relay_status["auto_relay_enabled"] = auto_relay_enabled
            relay_status["auto_relay_panes"] = sorted(
                str(p.get("pane_id")) for p in panes if bool(p.get("auto_relay_enabled"))
            )

            manual_command: Optional[str] = None
            mcp_config: Optional[Dict[str, Any]] = None
            if degraded:
                instructions = self._extract_degraded_instructions(terminal, hub_url=hub_url)
                manual_command = instructions["manual_command"]
                mcp_config = instructions["mcp_config"]

            unsupported = is_unsupported_terminal_platform()
            return {
                "success": True,
                "degraded": degraded,
                "platform_unsupported": unsupported,
                "code": "TERMINAL_PLATFORM_UNSUPPORTED" if unsupported else "OK",
                "message": unsupported_platform_response()["message"] if unsupported else "",
                "connector_status": connector_status,
                "hub_url": hub_url,
                "hub_running": hub_running,
                "auto_relay_enabled": auto_relay_enabled,
                "relay_status": relay_status,
                "panes": panes,
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
                "auto_relay_enabled": False,
                "relay_status": {"auto_relay_enabled": False, "tripped_cases": []},
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
            settings_store = getattr(svc, "_settings", None) or getattr(self.core_kernel, "extension_repo", None) or self.db
            cleaned = svc.cleanup_removed_profiles(settings_store)
            removed_profiles: List[Dict[str, str]] = []
            if cleaned:
                notice_key = "catalog.removed_notice.gemini"
                getter = getattr(self.db, "get_setting", None)
                setter = getattr(self.db, "set_setting", None)
                already_shown = False
                if callable(getter):
                    try:
                        already_shown = getter(notice_key) == "shown"
                    except Exception:
                        already_shown = False
                if not already_shown:
                    removed_profiles = cleaned
                    if callable(setter):
                        try:
                            setter(notice_key, "shown")
                        except Exception as exc:
                            logger.warning("No se pudo guardar %s: %s", notice_key, exc)
            view = svc.describe()
            terminal = self._get_terminal_connector()
            return {
                "success": True,
                "code": "OK",
                "message": "Catalogo de CLIs.",
                "max_panes": self._terminal_max_panes(terminal),
                "hard_max_panes": HARD_MAX_PANES,
                "terminal_platform_supported": not is_unsupported_terminal_platform(),
                "removed_profiles": removed_profiles,
                **view,
            }
        except Exception as exc:
            logger.warning("pty_list_cli_catalog fallo: %s", exc)
            return _fail("INTERNAL_ERROR", f"No se pudo leer el catalogo de CLIs: {exc}")

    def _confirm_native(self, title: str, message: str) -> Optional[Dict[str, Any]]:
        """Dialogo nativo (EGM-74); `None` si se acepto, si no la respuesta de error lista para devolver."""
        confirmations = getattr(self._bridge, "confirmations", None)
        if confirmations is None:
            return _fail("CONFIRMATION_UNAVAILABLE", "No hay servicio de confirmacion nativa; la accion se rechaza.")
        return confirmations.confirm(title, message)

    # ------------------------------------------------------------------
    # Registro GLOBAL de MCP2P en Antigravity (agy): opt-in con dialogo nativo (D3 del dossier 36, R1.4)
    # ------------------------------------------------------------------

    _AGY_CONSENT_KEY = "agy_global_mcp_consent"  # en el espacio MAX_PANES_SETTING_NAMESPACE: "granted" | "declined"

    def _agy_consent_state(self) -> Optional[str]:
        repo = getattr(self.core_kernel, "extension_repo", None)
        getter = getattr(repo, "get_extension_setting", None)
        if not callable(getter):
            return None
        try:
            value = getter(MAX_PANES_SETTING_NAMESPACE, self._AGY_CONSENT_KEY, None)
        except Exception:
            return None
        return value if value in ("granted", "declined") else None

    def _set_agy_consent_state(self, value: str) -> None:
        repo = getattr(self.core_kernel, "extension_repo", None)
        setter = getattr(repo, "set_extension_setting", None)
        if callable(setter):
            try:
                setter(MAX_PANES_SETTING_NAMESPACE, self._AGY_CONSENT_KEY, value)
            except Exception as exc:
                logger.warning("No se pudo recordar el consentimiento del registro de agy: %s", exc)

    def _confirm_agy_registration(self) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """Dialogo nativo con el archivo global y la entrada EXACTA. Devuelve `(error|None, estado)`."""
        state = _mcp_injection.agy_registration_state()
        if state.get("state") == "error":
            return _fail("AGY_REGISTRATION_FAILED", f"No se puede usar la configuracion de Antigravity: {state.get('reason')}.",
                         reason=state.get("reason"), path=state.get("path")), state
        replace_manual = state.get("state") == "manual" and not state.get("hub_proxy_only")
        entry = _mcp_injection.build_agy_entry()
        shown = json.dumps({"mcpServers": {_mcp_injection.MCP_SERVER_NAME: entry}}, indent=2, ensure_ascii=False)
        message = (
            "MCP2P quiere registrarse en el archivo GLOBAL de Antigravity, que afecta a TODAS sus sesiones de agy:\n\n"
            f"{state.get('path')}\n\nEntrada que se escribira (no lleva token):\n\n{shown}\n\n"
            "Se guarda una copia del original en mcp_config.json.mcp2p.bak la primera vez y puede quitarlo cuando quiera."
        )
        if replace_manual:
            message += (
                "\n\nATENCION: ya existe una entrada 'mcp2p' manual que NO usa --hub-proxy-only (puede abrir su base "
                "de datos real). Se reemplazara por la de arriba; la anterior queda en la copia .mcp2p.bak."
            )
        message += "\n\nAcepte solo si reconoce este archivo y esta entrada."
        return self._confirm_native("Registrar MCP2P en Antigravity", message), state

    def _spawn_session_with_agy_consent(self, terminal: Any, **kwargs: Any) -> Any:
        """`terminal.spawn_session(**kwargs)`; para `antigravity` resuelve antes el consentimiento del registro global.

        Sin consentimiento el panel abre igualmente (`mcp_injection.status = needs_consent`) y no se escribe nada
        fuera del perfil del panel. Un rechazo se recuerda para no preguntar en cada spawn.
        """
        consent = False
        if kwargs.get("cli_profile") == "antigravity":
            remembered = self._agy_consent_state()
            if remembered == "granted":
                consent = True
            elif remembered is None:
                declined, _state = self._confirm_agy_registration()
                if declined is None:
                    self._set_agy_consent_state("granted")
                    consent = True
                elif declined.get("code") == "CONFIRMATION_DECLINED":
                    self._set_agy_consent_state("declined")
        with _mcp_injection.global_registration_consent(consent):
            return terminal.spawn_session(**kwargs)

    def pty_register_agy_mcp(self) -> Dict[str, Any]:
        """Boton «Registrar MCP2P en Antigravity»: pide el dialogo nativo y, si se acepta, registra y lo recuerda."""
        try:
            declined, state = self._confirm_agy_registration()
            if declined is not None:
                return declined
            replace_manual = state.get("state") == "manual" and not state.get("hub_proxy_only")
            res = _mcp_injection.ensure_agy_mcp_registered(replace_unmanaged=replace_manual)
            if not res.get("registered"):
                return _fail("AGY_REGISTRATION_FAILED", "No se pudo registrar MCP2P en Antigravity.",
                             reason=res.get("reason"), path=res.get("path"))
            self._set_agy_consent_state("granted")
            return {"success": True, "code": "OK", "message": "MCP2P registrado en Antigravity. Abra el panel de nuevo.",
                    "path": res.get("path"), "managed": bool(res.get("managed")),
                    "already_present": bool(res.get("already_present"))}
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_unregister_agy_mcp(self) -> Dict[str, Any]:
        """Revoca el consentimiento y quita la entrada de MCP2P (solo si lleva nuestro marcador)."""
        try:
            self._set_agy_consent_state("declined")
            res = _mcp_injection.unregister_agy_mcp()
            if res.get("removed"):
                return {"success": True, "code": "OK", "removed": True, "path": res.get("path"),
                        "message": "MCP2P quitado de la configuracion de Antigravity."}
            if res.get("reason") in ("not_registered", "not_managed"):
                note = ("La entrada 'mcp2p' no la creo MCP2P; no se toco." if res["reason"] == "not_managed"
                        else "MCP2P no estaba registrado en Antigravity.")
                return {"success": True, "code": "OK", "removed": False, "reason": res["reason"],
                        "path": res.get("path"), "message": note}
            return _fail("AGY_REGISTRATION_FAILED", "No se pudo quitar MCP2P de Antigravity.",
                         reason=res.get("reason"), path=res.get("path"))
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_get_agy_mcp_status(self) -> Dict[str, Any]:
        """Estado de solo lectura del registro global de agy y del consentimiento recordado."""
        try:
            state = _mcp_injection.agy_registration_state()
            return {"success": True, "code": "OK", "consent": self._agy_consent_state() or "unset",
                    "registration": state.get("state"), "path": state.get("path"),
                    "hub_proxy_only": state.get("hub_proxy_only")}
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_set_cli_path(self, cli_id: Any, path: Any) -> Dict[str, Any]:
        """Fija (o borra con cadena vacia) la ruta del binario de un CLI del catalogo.

        Fijar una ruta persiste un ejecutable que el panel lanzara: exige confirmacion NATIVA con la ruta exacta.
        """
        try:
            svc = self._catalog_service()
            if isinstance(path, str) and path != "":
                clean, error = svc.check_binary_path(cli_id, path)
                if error is not None or clean is None:
                    return error or _fail("INVALID_PATH", "Ruta invalida.")
                declined = self._confirm_native(
                    "Configurar el ejecutable de un CLI",
                    f"El CLI '{cli_id}' se lanzara desde ahora con este ejecutable:\n\n{clean}\n\n"
                    "Acepte solo si reconoce esa ruta.",
                )
                if declined is not None:
                    return {**declined, "cli_id": cli_id}
            return svc.set_binary_path(cli_id, path)
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

    # ------------------------------------------------------------------
    # Instalar un CLI si falta (T3): dos pasos, lista cerrada, token de un solo uso
    # ------------------------------------------------------------------

    def _get_install_broker(self) -> InstallConfirmationBroker:
        with self._terminal_lock:
            if self._install_broker is None:
                self._install_broker = InstallConfirmationBroker()
            return self._install_broker

    def _plan_install(self, cli_id: Any) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Resuelve el instalador del catalogo del repo; `(plan, error)`. Nunca toma un comando del JS."""
        svc = self._catalog_service()
        spec = svc.get_spec(cli_id) if isinstance(cli_id, str) else None
        if spec is None:
            known = svc.catalog().ids()
            return None, _fail("CLI_PROFILE_UNKNOWN", f"CLI desconocido en el catalogo. Permitidos: {known}.",
                               known_profiles=known)
        if spec.kind == "shell":
            return None, _fail("INSTALL_NOT_AVAILABLE", "La shell no se instala desde el catalogo.", cli_id=spec.id)
        if spec.source != "repo" or not spec.verified:
            return None, _fail(
                "INSTALL_NOT_ALLOWED",
                "Este CLI viene del catalogo del usuario (no verificado): no es instalable desde la interfaz.",
                cli_id=spec.id,
            )
        resolution = svc.resolve(spec.id)
        if resolution.found:
            return None, _fail("ALREADY_INSTALLED", f"{spec.display_name} ya esta instalado.",
                               cli_id=spec.id, resolved_path=resolution.path)
        selection = svc.select_install(spec)
        if selection.code == "NO_INSTALL_METHOD" or selection.option is None:
            return None, _fail(
                "INSTALL_NOT_AVAILABLE",
                f"No hay un metodo de instalacion verificado de {spec.display_name} para este sistema; instalelo manualmente.",
                cli_id=spec.id,
            )
        option = selection.option
        if selection.code == "PREREQUISITE_MISSING":
            missing = list(selection.missing)
            return None, _fail(
                "PREREQUISITE_MISSING",
                f"Faltan herramientas previas para instalar {spec.display_name}: {', '.join(missing)}. "
                "Instalelas primero; esta interfaz no las instala.",
                cli_id=spec.id,
                missing=missing,
                command=option.command,
                method=option.method,
                source_url=option.source_url,
            )
        if not option.verified:
            return None, _fail("INSTALL_NOT_ALLOWED", "El comando de instalacion no esta verificado.", cli_id=spec.id)
        try:
            validate_install_command(option.method, option.command)
        except CatalogValidationError as exc:  # defensa en profundidad: ya se valido al cargar
            return None, _fail("INSTALL_NOT_ALLOWED", f"Comando de instalacion rechazado: {exc}", cli_id=spec.id)
        return {"spec": spec, "option": option, "svc": svc}, None

    def pty_prepare_install(self, cli_id: Any) -> Dict[str, Any]:
        """Paso 1: devuelve el comando exacto del catalogo y un token de confirmacion de un solo uso."""
        try:
            if is_unsupported_terminal_platform():
                svc = self._catalog_service()
                spec = svc.get_spec(cli_id) if isinstance(cli_id, str) else None
                manual = None
                if spec is not None and spec.source == "repo" and spec.kind != "shell":
                    options = spec.install.get(svc.platform, ())
                    manual = options[0].command if options and options[0].verified else None
                return {**unsupported_platform_response(), "cli_id": cli_id if spec else None,
                        "install_command": manual}
            plan, error = self._plan_install(cli_id)
            if error is not None or plan is None:
                return error or _fail("INTERNAL_ERROR", "Plan de instalacion vacio.")
            spec, option, svc = plan["spec"], plan["option"], plan["svc"]
            token, ttl = self._get_install_broker().issue(spec.id, option.command, svc.platform)
            warning = (
                "El instalador oficial es un script descargado de Internet y ejecutado en su shell "
                f"({option.command}). Revise el comando antes de confirmar."
                if option.downloads_script
                else ""
            )
            return {
                "success": True,
                "code": "OK",
                "message": f"Confirme para instalar {spec.display_name} en una terminal visible.",
                "requires_confirmation": True,
                "cli_id": spec.id,
                "display_name": spec.display_name,
                "command": option.command,
                "method": option.method,
                "downloads_script": option.downloads_script,
                "warning": warning,
                "requires": list(option.requires),
                "platform": svc.platform,
                "source_url": option.source_url,
                "note": option.note,
                "confirmation_token": token,
                "expires_in_s": ttl,
            }
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def _pick_install_pane(self, terminal: Any, requested: Any) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        max_panes = self._terminal_max_panes(terminal)
        alive: set[str] = set()
        list_fn = getattr(terminal, "list_sessions", None)
        if callable(list_fn):
            alive = {str(sess.get("pane_id")) for sess in list_fn() if sess.get("alive")}
        if requested is not None:
            try:
                norm, _ = normalize_pane_id(requested, max_panes=max_panes)
            except PaneLimitError as exc:
                return None, _fail("PANE_LIMIT_REACHED", str(exc), reason="pane_out_of_range", max_panes=max_panes)
            except ValueError as exc:
                return None, _fail("INVALID_PANE_ID", str(exc))
            if norm in alive:
                return None, _fail("PANE_ALREADY_ACTIVE", f"El panel {norm} ya tiene una sesion viva.", pane_id=norm)
            return norm, None
        for index in range(1, max_panes + 1):
            if f"pane_{index}" not in alive:
                return f"pane_{index}", None
        return None, _fail("PANE_LIMIT_REACHED", f"Los {max_panes} paneles estan ocupados; cierre uno primero.",
                           reason="max_panes", max_panes=max_panes)

    def pty_install_cli(self, cli_id: Any, confirmation_token: Any, pane_id: Any = None) -> Dict[str, Any]:
        """Paso 2: con un token valido abre un panel real que ejecuta el comando del catalogo."""
        pane_lock: Optional[threading.Lock] = None
        try:
            if is_unsupported_terminal_platform():
                return {**unsupported_platform_response(), "cli_id": cli_id if isinstance(cli_id, str) else None}
            plan, error = self._plan_install(cli_id)
            if error is not None or plan is None:
                return error or _fail("INTERNAL_ERROR", "Plan de instalacion vacio.")
            spec, option, svc = plan["spec"], plan["option"], plan["svc"]

            terminal = self._get_terminal_connector()
            target_pane, pane_error = self._pick_install_pane(terminal, pane_id)
            if pane_error is not None or target_pane is None:
                return {**(pane_error or _fail("PANE_LIMIT_REACHED", "Sin paneles libres.")), "cli_id": spec.id}
            pane_lock = self._pane_lock(target_pane)
            pane_lock.acquire()

            verdict = self._get_install_broker().consume(spec.id, confirmation_token, option.command, svc.platform)
            if verdict == "INSTALL_TOKEN_EXPIRED":
                return _fail("INSTALL_TOKEN_EXPIRED", "La confirmacion vencio; vuelva a preparar la instalacion.",
                             cli_id=spec.id)
            if verdict != "OK":
                return _fail("INSTALL_TOKEN_INVALID",
                             "Token de confirmacion invalido o ya usado; prepare la instalacion de nuevo.",
                             cli_id=spec.id)
            # El token es anti-accidente (lo valida el mismo puente al que llama el JS); la barrera real es el
            # dialogo NATIVO con el comando exacto (EGM-74). Con el token ya gastado, rechazar obliga a preparar de nuevo.
            declined = self._confirm_native(
                f"Instalar {spec.display_name}",
                f"Se ejecutara en una terminal visible ({target_pane}):\n\n{option.command}\n\n"
                + ("ATENCION: es un script descargado de Internet y ejecutado en su shell.\n\n"
                   if option.downloads_script else "")
                + "Acepte solo si reconoce este comando.",
            )
            if declined is not None:
                return {**declined, "cli_id": spec.id}

            install_argv = build_install_command(option.command, svc.platform, svc.which)
            spawn_res = dict(
                terminal.spawn_session(
                    pane_id=target_pane,
                    cli_profile="custom_shell",
                    role="installer",
                    command=install_argv,
                    cwd=None,
                    cols=100,
                    rows=28,
                    hub_url=None,
                    session_token=None,
                    node_uuid=None,
                )
            )
            if not spawn_res.get("success"):
                cleaned = _strip_secret_fields(spawn_res)
                cleaned.setdefault("code", "PTY_SPAWN_FAILED")
                cleaned["success"] = False
                cleaned["cli_id"] = spec.id
                return cleaned
            if spec.id == "antigravity" and self._agy_consent_state() == "granted":
                # Solo con el consentimiento ya concedido en esta maquina; si no, el primer panel de agy lo pedira.
                try:
                    _mcp_injection.ensure_agy_mcp_registered()
                except Exception:
                    pass
            return {
                "success": True,
                "code": "INSTALL_STARTED",
                "message": f"Instalando {spec.display_name} en {target_pane}; revise la salida en la terminal.",
                "cli_id": spec.id,
                "pane_id": target_pane,
                "session_id": spawn_res.get("session_id"),
                "pid": spawn_res.get("pid"),
                "command": option.command,
                "method": option.method,
                "downloads_script": option.downloads_script,
            }
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))
        finally:
            if pane_lock is not None:
                pane_lock.release()

    def _resolve_pane_project(self, project_uuid: Any) -> tuple[str, Optional[Dict[str, Any]]]:
        """Proyecto del panel: el que envia la GUI (debe existir) o el activo; '' si no hay ninguno."""
        if project_uuid is not None and not isinstance(project_uuid, str):
            return "", _fail("INVALID_ARGUMENT", "project_uuid debe ser texto.")
        requested = (project_uuid or "").strip()
        if requested:
            if len(requested) > 200 or has_control_chars(requested):
                return "", _fail("INVALID_ARGUMENT", "project_uuid invalido.")
            try:
                exists = self.db.get_project_by_uuid(requested) is not None
            except Exception:
                exists = False
            if not exists:
                return "", _fail("PROJECT_NOT_FOUND", "El proyecto del panel no existe.")
            return requested, None
        try:
            return (self._resolve_project_uuid() or ""), None
        except Exception:
            return "", None

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
        resume: bool = False,
        project_uuid: Optional[str] = None,
        resume_mode: Optional[str] = None,
        cli_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Abre un panel ligado a un proyecto (Ola 2): `project_uuid` lo envia la GUI (el proyecto visible); si
        falta, se usa el proyecto activo del puente y, sin ninguno, el panel queda sin proyecto ('')."""
        registry = self._get_pane_registry()
        registered_here = False
        norm_pane_id: Optional[str] = None
        pane_lock: Optional[threading.Lock] = None
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

            if command is not None:
                # Un comando propio ejecuta CUALQUIER cosa: confirmacion NATIVA con el argv exacto (EGM-74).
                try:
                    shown = shlex.join(shlex.split(command)) if isinstance(command, str) else shlex.join(list(command))
                except ValueError:
                    return _fail("INVALID_ARGUMENT", "command invalido (comillas sin cerrar).",
                                 degraded=False, pane_id=norm_pane_id)
                declined = self._confirm_native(
                    "Abrir un panel con un comando propio",
                    f"Se ejecutara en {norm_pane_id}"
                    + (f" (carpeta {resolved_cwd})" if resolved_cwd else "")
                    + f":\n\n{shown}\n\nAcepte solo si reconoce este comando.",
                )
                if declined is not None:
                    return {**declined, "degraded": False, "pane_id": norm_pane_id}

            if not isinstance(resume, bool):
                return _fail("INVALID_ARGUMENT", "resume debe ser booleano.", degraded=False, pane_id=norm_pane_id)
            if resume_mode is not None:
                if not isinstance(resume_mode, str) or resume_mode not in _VALID_RESUME_MODES:
                    return _fail(
                        "INVALID_ARGUMENT",
                        "resume_mode invalido (permitidos: none, continue, by_id).",
                        degraded=False,
                        pane_id=norm_pane_id,
                    )
            if cli_session_id is not None:
                if resume_mode != "by_id":
                    return _fail(
                        "INVALID_ARGUMENT",
                        "cli_session_id solo se admite cuando resume_mode='by_id'.",
                        degraded=False,
                        pane_id=norm_pane_id,
                    )
                if not isinstance(cli_session_id, str) or _CLI_SESSION_ID_RE.fullmatch(cli_session_id) is None:
                    return _fail(
                        "INVALID_ARGUMENT",
                        "cli_session_id invalido.",
                        degraded=False,
                        pane_id=norm_pane_id,
                    )
            elif resume_mode == "by_id":
                return _fail(
                    "INVALID_ARGUMENT",
                    "resume_mode='by_id' requiere cli_session_id.",
                    degraded=False,
                    pane_id=norm_pane_id,
                )
            effective_resume = (resume_mode == "continue") or (resume_mode is None and resume)
            pane_project, project_error = self._resolve_pane_project(project_uuid)
            if project_error is not None:
                return {**project_error, "degraded": False, "pane_id": norm_pane_id}

            # EGM-75: dos spawns simultaneos del mismo panel no deben pisarse el token del registro.
            pane_lock = self._pane_lock(norm_pane_id)
            pane_lock.acquire()

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

            effective_cwd = resolved_cwd if resolved_cwd is not None else self._resolve_active_code_repo(pane_project)

            # Identidad ESTABLE por (proyecto, ranura); la sesion (token, pid, session_id) es nueva en cada spawn.
            registered = registry.register_pane(
                norm_pane_id,
                cli_profile=cli_profile,
                role=role,
                project_uuid=pane_project,
                cwd=effective_cwd,
            )
            registered_here = True
            token = registered["session_token"]
            node_uuid = registered["node_uuid"]

            if start_hub:
                hub_url = self._ensure_hub_started()
            else:
                hub = self._embedded_hub
                hub_url = hub.url if (hub is not None and hub.is_running()) else None

            launch_request = {
                "project_uuid": pane_project,
                "node_uuid": node_uuid,
                "resume_mode": resume_mode or ("continue" if resume else "none"),
                "cli_session_id": cli_session_id,
            }
            spawn_res = dict(
                self._spawn_session_with_agy_consent(
                    terminal,
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
                    resume=effective_resume,
                    launch_request=launch_request,
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
            if pid is not None:
                # Ola 3 (EGM-105): el token del panel solo vale desde el arbol de procesos de este PTY.
                registry.bind_process(norm_pane_id, int(pid))
            rec = registry.get_pane(norm_pane_id)
            if rec is not None:
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

            self.pane_lifecycle.emit_spawned(
                PaneSpawnedEvent(
                    pane_id=norm_pane_id,
                    project_uuid=pane_project,
                    node_uuid=node_uuid,
                    cli_profile=cli_profile,
                    cwd=effective_cwd,
                    session_id=spawn_res.get("session_id"),
                    launch_meta=dict(spawn_res.get("launch_meta") or {}),
                    request=launch_request,
                )
            )

            return {
                "success": True,
                "code": "OK",
                "degraded": False,
                "pane_id": norm_pane_id,
                "node_uuid": node_uuid,
                "project_uuid": pane_project,
                "pid": pid,
                "hub_url": hub_url,
                "cwd": effective_cwd,
                "mcp_injection": spawn_res.get("mcp_injection"),
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
        finally:
            if pane_lock is not None:
                pane_lock.release()

    def pty_write_input(self, pane_id: Any, data: str, is_human: bool = True) -> Dict[str, Any]:
        try:
            registry = self._get_pane_registry()
            if is_human:
                # Enter del humano = turno humano: cierra la cadena causal del relevo en ese panel (Ola 3).
                submitted = isinstance(data, str) and ("\r" in data or "\n" in data)
                registry.record_human_keystroke(pane_id, submitted=submitted)
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
            registry = self._get_pane_registry()
            try:
                pane_rec = registry.get_pane(pane_id)
            except ValueError:
                pane_rec = None
            terminal = self._get_terminal_connector()
            res = _strip_secret_fields(dict(terminal.close_session(pane_id)))
            try:
                registry.mark_pane_closed(pane_id)
            except ValueError:
                pass
            if res.get("success") and (res.get("closed") or pane_rec is not None):
                closed_project = pane_rec.project_uuid if pane_rec is not None else ""
                closed_pane_id = pane_rec.pane_id if pane_rec is not None else str(res.get("pane_id") or pane_id)
                self.pane_lifecycle.emit_closed_by_user(closed_project, closed_pane_id)
            return res
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def reset_loop_breaker(self, project_uuid: Any = "", card_id: Any = "", session_id: Any = "") -> Dict[str, Any]:
        """Reinicia el Loop Breaker de una tarjeta/sesion tras intervencion humana (EGM-72), con dialogo nativo."""
        try:
            values = {"project_uuid": project_uuid, "card_id": card_id, "session_id": session_id}
            for name, value in values.items():
                if not isinstance(value, str) or len(value) > 200 or has_control_chars(value):
                    return _fail("INVALID_ARGUMENT", f"{name} debe ser texto corto.")
            if not (card_id.strip() or session_id.strip()):
                return _fail("INVALID_ARGUMENT", "Indique card_id o session_id.")
            hub = self._embedded_hub
            relay = hub.relay_orchestrator() if hub is not None else None
            if relay is None:
                return _fail("RELAY_UNAVAILABLE", "El Hub embebido no esta en marcha; no hay Loop Breaker que reiniciar.")
            declined = self._confirm_native(
                "Reiniciar el Loop Breaker",
                f"Se reanudara el relevo automatico entre agentes para:\n\n"
                f"proyecto: {project_uuid or '(cualquiera)'}\ntarjeta: {card_id or '-'}\nsesion: {session_id or '-'}\n\n"
                "El freno se activo porque los agentes se devolvian el trabajo sin converger. Acepte solo si reviso el caso.",
            )
            if declined is not None:
                return declined
            ok = relay.reset_loop_breaker(project_uuid=project_uuid, card_id=card_id, session_id=session_id)
            return {"success": bool(ok), "code": "OK" if ok else "INVALID_ARGUMENT",
                    "message": "Loop Breaker reiniciado." if ok else "No se pudo derivar la clave del caso."}
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_set_auto_relay(self, pane_id: Any, enabled: Any = None) -> Dict[str, Any]:
        """Activa o desactiva el Auto-Relay de UN panel (D2) y lo persiste por (proyecto, ranura).

        Activarlo solo significa «dejar avisos de asignacion en la bandeja de ESE panel»; nunca escribe en un PTY.
        Activarlo exige confirmacion NATIVA (EGM-74); apagarlo no pregunta (es la direccion segura).
        """
        try:
            if not isinstance(enabled, bool):
                return _fail("INVALID_ARGUMENT", "Indique el panel y enabled (booleano): el Auto-Relay es por panel.")
            registry = self._get_pane_registry()
            try:
                rec = registry.get_pane(pane_id)
            except (TypeError, ValueError):
                rec = None
            if rec is None or rec.status == "closed":
                return _fail("PANE_NOT_FOUND", "No hay un panel activo con ese identificador.")
            if enabled:
                project_label = rec.project_uuid or "(sin proyecto)"
                try:
                    proj = self.db.get_project_by_uuid(rec.project_uuid) if rec.project_uuid else None
                    if proj and proj.get("name"):
                        project_label = f"{proj['name']} ({rec.project_uuid})"
                except Exception:
                    pass
                declined = self._confirm_native(
                    "Activar Auto-Relay en un panel",
                    f"Panel: {rec.pane_id} ({rec.cli_profile})\nProyecto: {project_label}\n\n"
                    "Con Auto-Relay activo, MCP2P dejara avisos de asignacion en la bandeja del agente de este "
                    "panel, ademas de los mensajes directos que ya recibe.\n\n"
                    "No escribe nada en ninguna terminal: el agente decide cuando recoger los avisos. "
                    "Acepte solo si quiere ese comportamiento.",
                )
                if declined is not None:
                    return {**declined, "pane_id": rec.pane_id}
            updated = registry.set_auto_relay(rec.pane_id, enabled)
            if updated is None:
                return _fail("PANE_NOT_FOUND", "El panel se cerro antes de aplicar el cambio.")
            return {
                "success": True,
                "code": "OK",
                "pane_id": updated.pane_id,
                "project_uuid": updated.project_uuid,
                "auto_relay_enabled": bool(updated.auto_relay_enabled),
            }
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_set_worker_mode(self, pane_id: Any, enabled: Any = None) -> Dict[str, Any]:
        """Activa o desactiva el MODO TRABAJADOR de UN panel (F2.4) y lo persiste por (proyecto, ranura).

        Con el modo activo, las respuestas de `inbox_next`/`inbox_wait` de ese panel traen `worker_mode: true` y una
        instruccion para que el agente se quede en `inbox_wait` y responda con `inbox_reply`. Nunca escribe en un PTY.
        Activarlo exige confirmacion NATIVA (EGM-74): el agente pasa a esperar trabajo sin que nadie le escriba;
        apagarlo no pregunta (es la direccion segura).
        """
        try:
            if not isinstance(enabled, bool):
                return _fail("INVALID_ARGUMENT", "Indique el panel y enabled (booleano): el modo trabajador es por panel.")
            registry = self._get_pane_registry()
            try:
                rec = registry.get_pane(pane_id)
            except (TypeError, ValueError):
                rec = None
            if rec is None or rec.status == "closed":
                return _fail("PANE_NOT_FOUND", "No hay un panel activo con ese identificador.")
            if enabled:
                project_label = rec.project_uuid or "(sin proyecto)"
                try:
                    proj = self.db.get_project_by_uuid(rec.project_uuid) if rec.project_uuid else None
                    if proj and proj.get("name"):
                        project_label = f"{proj['name']} ({rec.project_uuid})"
                except Exception:
                    pass
                declined = self._confirm_native(
                    "Activar el modo trabajador en un panel",
                    f"Panel: {rec.pane_id} ({rec.cli_profile})\nProyecto: {project_label}\n\n"
                    "Con el modo trabajador, el agente de este panel se quedara esperando mensajes de otros agentes "
                    "(inbox_wait) y respondera con inbox_reply, sin que usted escriba en su terminal.\n\n"
                    "MCP2P no escribe nada en ninguna terminal. Mientras espera, el agente consume turnos del modelo "
                    "cada pocos minutos. Acepte solo si quiere ese comportamiento.",
                )
                if declined is not None:
                    return {**declined, "pane_id": rec.pane_id}
            updated = registry.set_worker_mode(rec.pane_id, enabled)
            if updated is None:
                return _fail("PANE_NOT_FOUND", "El panel se cerro antes de aplicar el cambio.")
            return {
                "success": True,
                "code": "OK",
                "pane_id": updated.pane_id,
                "project_uuid": updated.project_uuid,
                "worker_mode": bool(updated.worker_mode),
            }
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_get_kanban_swarm_snapshot(
        self, project_uuid: Optional[str] = None, all_projects: bool = False
    ) -> Dict[str, Any]:
        """Proyección Kanban <-> Swarm de los paneles del proyecto (o de todos con `all_projects`, solo GUI)."""
        try:
            target_uuid = None if all_projects is True else (self._resolve_project_uuid(project_uuid) or "")
            kernel = self.core_kernel
            kanban_repo = getattr(kernel, "kanban_repo", None) if kernel is not None else None
            adapter = KanbanSwarmAdapter(
                kanban_repo=kanban_repo,
                pane_registry=self._get_pane_registry(),
                terminal_connector=self._get_terminal_connector(),
                allow_all_projects=all_projects is True,
            )
            return adapter.list_swarm_panes(project_uuid=target_uuid)
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def close_terminal_matrix(self) -> None:
        """Detiene limpiamente el EmbeddedMcpHub y revoca tokens de paneles activos."""
        with self._terminal_lock:
            hub = self._embedded_hub
            reg = self._pane_registry
        terminal: Any = None
        if reg is not None:
            try:
                terminal = self._get_terminal_connector()
            except Exception:
                terminal = None
        alive_by_project: Dict[str, List[str]] = {}
        if reg is not None:
            alive_set: Optional[set[str]] = None
            list_fn = getattr(terminal, "list_sessions", None) if terminal is not None else None
            if callable(list_fn):
                try:
                    alive_set = {
                        str(s["pane_id"])
                        for s in list_fn()
                        if isinstance(s, Mapping) and s.get("pane_id") and s.get("alive")
                    }
                except Exception:
                    alive_set = None
            for pane in list(reg.list_panes()):
                p_id = pane.get("pane_id")
                if not p_id:
                    continue
                if alive_set is None or p_id in alive_set:
                    proj = str(pane.get("project_uuid") or "")
                    alive_by_project.setdefault(proj, []).append(str(p_id))
        self.pane_lifecycle.emit_app_closing(alive_by_project)
        if reg is not None:
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
