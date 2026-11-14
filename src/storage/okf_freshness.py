"""
Motor determinista de cálculo de Freshness y verificación de firmas Ed25519 para OKF.

Evalúa cada artefacto .md/.yaml en docs/okf/ calculando un puntaje 0..100 y estado
('fresh' >= 80, 'aging' 50..79, 'stale' < 50) a partir de:
1. Días transcurridos desde la última modificación/revisión respecto al SLA.
2. Commits Git posteriores en el código que tocan su `files_scope` / `source_ref`.
3. Verificación criptográfica Ed25519 de bloques `signatures:` contra `trusted_nodes.yaml`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from src.p2p.crypto import NodeCryptoManager
from src.proc_utils import run_hidden
from src.storage.okf_tags import OkfTagEngine
from src.storage.validators import InvalidIdentifierError, ensure_within


_IGNORED_FRESHNESS_REL_PATHS = frozenset({
    "tags_index.yaml",
    "okf_schema.json",
    "architecture/precedents_index.yaml",
    "conventions/trusted_nodes.yaml",
    "skills/registry.yaml",
    "mcps/mcp_manifest.yaml",
})


class OkfFreshnessEngine:
    """Calcula frescura y autenticidad Ed25519 de los artefactos OKF."""

    def __init__(self) -> None:
        self.tag_engine = OkfTagEngine()

    @staticmethod
    def _parse_iso_datetime(value: Any) -> Optional[datetime]:
        if not value or not isinstance(value, str):
            return None
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _load_trusted_nodes(docs_okf: Path) -> Dict[str, Dict[str, Any]]:
        trusted_path = docs_okf / "conventions" / "trusted_nodes.yaml"
        if not trusted_path.is_file():
            return {}
        try:
            loaded = yaml.safe_load(trusted_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("nodes"), list):
                return {
                    str(n["node_uuid"]): n
                    for n in loaded["nodes"]
                    if isinstance(n, dict) and n.get("node_uuid")
                }
        except Exception:
            pass
        return {}

    @staticmethod
    def _extract_doc_metadata(path: Path, rel_posix: str) -> Tuple[Dict[str, Any], str, str]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        suffix = path.suffix.lower()
        if suffix in (".yaml", ".yml"):
            try:
                loaded = yaml.safe_load(text) or {}
            except yaml.YAMLError:
                loaded = {}
            meta = loaded if isinstance(loaded, dict) else {}
            return meta, text, text

        fm, body, has_fm = OkfTagEngine._split_markdown_frontmatter(text)
        meta = dict(fm) if (has_fm and isinstance(fm, dict)) else {}
        return meta, text, body

    @staticmethod
    def _classify_artifact(rel_posix: str, meta: Dict[str, Any]) -> str:
        doc_id = str(meta.get("id") or meta.get("ruling_id") or "")
        if rel_posix.startswith("architecture/adrs/") or doc_id.upper().startswith("ADR-"):
            return "adr"
        if rel_posix.startswith("architecture/rulings/") or "ruling_id" in meta:
            return "ruling"
        if rel_posix.startswith("architecture/decisions/") or doc_id.upper().startswith("DEC-"):
            return "decision"
        if rel_posix.startswith("kanban/"):
            return "kanban_card"
        if rel_posix.startswith("requirements/"):
            return "requirement"
        return "document"

    @staticmethod
    def _candidate_signed_texts(path: Path, full_text: str, body: str, meta: Dict[str, Any]) -> List[str]:
        candidates = [full_text]
        if body and body != full_text:
            candidates.append(body)

        # ADRs promovidos mediante OKFAdapter.move_decision_lifecycle incrustan la decisión original
        m = re.search(
            r"##\s+Decisión Original\s*\([^)]+\)\n(.*?)\n\n##\s+Consecuencias y Resolución\n",
            body,
            re.DOTALL,
        )
        if m:
            candidates.append(m.group(1))

        if path.suffix.lower() in (".yaml", ".yml") and isinstance(meta, dict):
            without_sigs = {k: v for k, v in meta.items() if k != "signatures"}
            candidates.append(yaml.safe_dump(without_sigs, sort_keys=False, allow_unicode=True))
            candidates.append(json.dumps(without_sigs, sort_keys=True, ensure_ascii=False))

        return candidates

    def verify_artifact_signatures(
        self,
        path: Path,
        rel_posix: str,
        trusted_nodes: Dict[str, Dict[str, Any]],
        meta: Optional[Dict[str, Any]] = None,
        full_text: Optional[str] = None,
        body: Optional[str] = None,
    ) -> Tuple[Optional[bool], List[str]]:
        """
        Verifica el bloque `signatures:` de un artefacto contra `trusted_nodes.yaml`.
        Retorna (signature_valid, reasons):
        - True si existe al menos una firma Ed25519 válida de un nodo confiable.
        - False si es ADR/ruling sin firma válida o si existe bloque `signatures:` inválido.
        - None si es un documento general sin bloque `signatures:`.
        """
        if meta is None or full_text is None or body is None:
            meta, full_text, body = self._extract_doc_metadata(path, rel_posix)

        doc_type = self._classify_artifact(rel_posix, meta)
        requires_signature = doc_type in ("adr", "ruling")
        signatures = meta.get("signatures")

        if not signatures or not isinstance(signatures, list):
            if requires_signature:
                return False, ["Bloque 'signatures' ausente en ADR/dictamen."]
            return None, []

        candidates = self._candidate_signed_texts(path, full_text, body, meta)
        reasons: List[str] = []

        for idx, sig_block in enumerate(signatures):
            if not isinstance(sig_block, dict):
                reasons.append(f"Firma #{idx + 1} con formato inválido.")
                continue
            node_uuid = str(sig_block.get("node_uuid") or "")
            if not node_uuid or node_uuid not in trusted_nodes:
                reasons.append(f"Firma #{idx + 1}: nodo '{node_uuid}' no está en trusted_nodes.yaml.")
                continue
            trusted_pubkey = trusted_nodes[node_uuid].get("public_key")
            if not trusted_pubkey:
                reasons.append(f"Firma #{idx + 1}: nodo '{node_uuid}' carece de public_key confiable.")
                continue
            if sig_block.get("public_key") != trusted_pubkey:
                reasons.append(f"Firma #{idx + 1}: public_key no coincide con trusted_nodes.yaml.")
                continue

            if any(NodeCryptoManager.verify_artifact_signature(text_cand, sig_block) for text_cand in candidates):
                return True, []
            reasons.append(f"Firma #{idx + 1}: firma Ed25519 o content_sha256 inválido.")

        return False, reasons or ["Ninguna firma válida encontrada."]

    @staticmethod
    def _extract_scope_paths(meta: Dict[str, Any]) -> List[str]:
        raw_scopes: List[str] = []
        for key in ("files_scope", "source_ref"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                raw_scopes.append(val.strip())
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str) and item.strip():
                        raw_scopes.append(item.strip())
        return raw_scopes

    @staticmethod
    def _count_commits_after(
        code_repo_path: Optional[Path],
        scope_paths: List[str],
        since_dt: datetime,
    ) -> int:
        if code_repo_path is None or not scope_paths:
            return 0
        if not (code_repo_path / ".git").exists():
            return 0

        safe_rel_paths: List[str] = []
        for raw in scope_paths:
            if raw.startswith("-") or "\x00" in raw:
                continue
            try:
                resolved = ensure_within(code_repo_path, code_repo_path / raw)
                rel = resolved.relative_to(code_repo_path).as_posix()
                if rel and rel != ".":
                    safe_rel_paths.append(rel)
            except InvalidIdentifierError:
                continue

        if not safe_rel_paths:
            return 0

        try:
            res = run_hidden(
                ["git", "log", f"--since={since_dt.isoformat()}", "--oneline", "--", *safe_rel_paths],
                cwd=str(code_repo_path),
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode != 0:
                return 0
            lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
            return len(lines)
        except Exception:
            return 0

    def compute_project_freshness(
        self,
        code_repo_path: Optional[str | Path],
        gov_base_path: str | Path,
        sla_days: int = 14,
    ) -> Dict[str, Any]:
        """
        Calcula el reporte de frescura determinista y verificación criptográfica para docs/okf/.
        """
        docs_okf = OkfTagEngine.resolve_docs_okf(gov_base_path)
        code_path = Path(code_repo_path).resolve() if code_repo_path else None
        sla = max(1, int(sla_days))
        now_dt = datetime.now(timezone.utc)
        trusted_nodes = self._load_trusted_nodes(docs_okf)

        items: List[Dict[str, Any]] = []
        if docs_okf.is_dir():
            all_files = sorted(
                (p for p in docs_okf.rglob("*") if p.is_file()),
                key=lambda p: p.relative_to(docs_okf).as_posix(),
            )
            for path in all_files:
                rel_posix = path.relative_to(docs_okf).as_posix()
                parts = rel_posix.split("/")
                if any(part.startswith(".") for part in parts):
                    continue
                if parts[0] == "history" or rel_posix in _IGNORED_FRESHNESS_REL_PATHS:
                    continue
                if path.suffix.lower() not in (".md", ".yaml", ".yml"):
                    continue

                meta, full_text, body = self._extract_doc_metadata(path, rel_posix)
                doc_type = self._classify_artifact(rel_posix, meta)
                doc_id = str(meta.get("id") or meta.get("ruling_id") or path.stem)
                doc_title = str(meta.get("title") or "").strip()
                if not doc_title and path.suffix.lower() == ".md":
                    m_title = re.search(r"^#\s+(.+)$", body or full_text, re.MULTILINE)
                    if m_title:
                        doc_title = m_title.group(1).strip()
                if not doc_title:
                    doc_title = path.stem

                doc_dt = (
                    self._parse_iso_datetime(meta.get("updated_at"))
                    or self._parse_iso_datetime(meta.get("approved_at"))
                    or self._parse_iso_datetime(meta.get("timestamp"))
                    or self._parse_iso_datetime(meta.get("created_at"))
                    or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                )
                days_old = max(0, int((now_dt - doc_dt).total_seconds() // 86400))

                scope_paths = self._extract_scope_paths(meta)
                commits_after = self._count_commits_after(code_path, scope_paths, doc_dt)

                sig_valid, _ = self.verify_artifact_signatures(
                    path,
                    rel_posix,
                    trusted_nodes,
                    meta=meta,
                    full_text=full_text,
                    body=body,
                )

                age_penalty = 0
                if days_old > sla:
                    age_penalty = min(55, int(((days_old - sla) / sla) * 30) + 5)
                commit_penalty = min(40, commits_after * 10)
                sig_penalty = 35 if sig_valid is False else 0

                score = max(0, min(100, 100 - age_penalty - commit_penalty - sig_penalty))
                if score >= 80:
                    status = "fresh"
                elif score >= 50:
                    status = "aging"
                else:
                    status = "stale"

                tags = self.tag_engine.normalize_tags(meta.get("tags"))
                items.append({
                    "path": rel_posix,
                    "id": doc_id,
                    "title": doc_title,
                    "type": doc_type,
                    "score": score,
                    "status": status,
                    "signature_valid": sig_valid,
                    "commits_after": commits_after,
                    "days_old": days_old,
                    "tags": tags,
                })

        total = len(items)
        fresh_count = sum(1 for item in items if item["status"] == "fresh")
        aging_count = sum(1 for item in items if item["status"] == "aging")
        stale_count = sum(1 for item in items if item["status"] == "stale")
        avg_score = round(sum(item["score"] for item in items) / total, 1) if total else 100.0
        verified_signatures = sum(1 for item in items if item["signature_valid"] is True)
        unverified_adrs = sum(
            1 for item in items
            if item["type"] in ("adr", "ruling") and item["signature_valid"] is False
        )

        return {
            "success": True,
            "sla_days": sla,
            "summary": {
                "total": total,
                "fresh": fresh_count,
                "aging": aging_count,
                "stale": stale_count,
                "avg_score": avg_score,
                "verified_signatures": verified_signatures,
                "unverified_adrs": unverified_adrs,
            },
            "items": items,
        }
