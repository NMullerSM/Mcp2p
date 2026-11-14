"""
Sub-adaptador UI para Laboratorio OKF (carpetas dinámicas, tags YAML, Freshness),
Multi-Seed P2P, Puerta con Llave (ExtensionSecurityGate) y Diagnóstico del Sistema.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.adapters.outbound.extension_gate import (
    ExtensionSecurityGate,
    NullAgentTerminalConnector,
    OutboundRestConnector,
    SignedConnectorManifest,
)
from src.app.ui_adapters import _BaseUIAdapter
from src.p2p.mesh_adapter import MultiPeerMeshConnector

logger = logging.getLogger("mcp2p.api_bridge.extensions_lab")


class ExtensionsLabUIAdapter(_BaseUIAdapter):
    """Expone las 16 capacidades nuevas del Laboratorio OKF, P2P Seeds y Puerta con Llave."""

    def __init__(self, bridge: Any) -> None:
        super().__init__(bridge)
        self._rest_connectors: Dict[str, OutboundRestConnector] = {}

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
