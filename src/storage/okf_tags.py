"""
Motor determinista de Tags YAML para artefactos OKF (.md con frontmatter y .yaml).

Permite leer, actualizar sin corrupción, normalizar, indexar (docs/okf/tags_index.yaml)
y filtrar artefactos de gobernanza por etiquetas a 0 tokens.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from src.storage.fs_utils import atomic_write_text
from src.storage.validators import InvalidIdentifierError, ensure_within


_IGNORED_INDEX_REL_PATHS = frozenset({
    "tags_index.yaml",
    "okf_schema.json",
    "architecture/precedents_index.yaml",
    "conventions/trusted_nodes.yaml",
    "skills/registry.yaml",
    "mcps/mcp_manifest.yaml",
})


class OkfTagEngine:
    """Motor de lectura, escritura e indexación determinista de tags YAML en docs/okf."""

    @staticmethod
    def resolve_docs_okf(docs_okf: str | Path) -> Path:
        base = Path(docs_okf)
        candidate = base / "docs" / "okf"
        if candidate.is_dir():
            return candidate.resolve()
        return base.resolve()

    @staticmethod
    def normalize_tags(tags: Optional[Iterable[str] | str]) -> List[str]:
        """Normaliza una lista o cadena de tags a identificadores minúsculos únicos y seguros."""
        if tags is None:
            return []
        if isinstance(tags, str):
            raw_items: Iterable[Any] = tags.split(",")
        else:
            raw_items = tags

        normalized: List[str] = []
        seen = set()
        for item in raw_items:
            if item is None:
                continue
            text = str(item).strip().lstrip("#").lower()
            text = re.sub(r"[\r\n\t\x00-\x1f]+", "", text)
            text = re.sub(r"\s+", "-", text).strip("-")
            if not text:
                continue
            if text not in seen:
                seen.add(text)
                normalized.append(text)
        return normalized

    @staticmethod
    def _resolve_artifact_path(docs_okf_path: Path, rel_path: str | Path) -> Tuple[Path, str]:
        raw = str(rel_path or "").strip()
        if not raw or "\x00" in raw:
            raise InvalidIdentifierError("Ruta de artefacto vacía o inválida.")
        p_rel = Path(raw)
        candidate = p_rel if p_rel.is_absolute() else (docs_okf_path / p_rel)
        resolved = ensure_within(docs_okf_path, candidate)
        if resolved == docs_okf_path:
            raise InvalidIdentifierError("La ruta debe apuntar a un archivo dentro de docs/okf.")
        rel_posix = resolved.relative_to(docs_okf_path).as_posix()
        return resolved, rel_posix

    @staticmethod
    def _split_markdown_frontmatter(text: str) -> Tuple[Optional[Dict[str, Any]], str, bool]:
        """
        Extrae frontmatter YAML inicial (`--- ... ---`) sin alterar el cuerpo del Markdown.
        Retorna (frontmatter_dict_o_None, cuerpo_restante, tiene_frontmatter).
        """
        normalized = text.replace("\r\n", "\n")
        if not (normalized.startswith("---\n") or normalized == "---"):
            return None, text, False

        lines = normalized.split("\n")
        end_idx: Optional[int] = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end_idx = idx
                break

        if end_idx is None:
            return None, text, False

        fm_raw = "\n".join(lines[1:end_idx])
        body = "\n".join(lines[end_idx + 1 :])
        try:
            loaded = yaml.safe_load(fm_raw) if fm_raw.strip() else {}
        except yaml.YAMLError:
            return None, text, False

        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            return None, text, False

        return loaded, body, True

    def get_artifact_tags(self, docs_okf: str | Path, rel_path: str | Path) -> List[str]:
        """Obtiene los tags normalizados de un artefacto .md o .yaml dentro de docs/okf."""
        docs_root = self.resolve_docs_okf(docs_okf)
        target, _ = self._resolve_artifact_path(docs_root, rel_path)
        if not target.is_file():
            raise FileNotFoundError(f"Artefacto no encontrado: {rel_path}")

        text = target.read_text(encoding="utf-8", errors="ignore")
        suffix = target.suffix.lower()
        if suffix in (".yaml", ".yml"):
            try:
                loaded = yaml.safe_load(text) or {}
            except yaml.YAMLError:
                return []
            if isinstance(loaded, dict):
                return self.normalize_tags(loaded.get("tags"))
            return []

        fm, _, has_fm = self._split_markdown_frontmatter(text)
        if has_fm and isinstance(fm, dict):
            return self.normalize_tags(fm.get("tags"))
        return []

    def set_artifact_tags(
        self,
        docs_okf: str | Path,
        rel_path: str | Path,
        tags: Iterable[str] | str,
    ) -> List[str]:
        """
        Actualiza el campo `tags` en el frontmatter YAML (.md) o raíz YAML (.yaml/.yml)
        preservando intacto el contenido restante del documento.
        """
        docs_root = self.resolve_docs_okf(docs_okf)
        target, _ = self._resolve_artifact_path(docs_root, rel_path)
        if not target.is_file():
            raise FileNotFoundError(f"Artefacto no encontrado: {rel_path}")

        norm_tags = self.normalize_tags(tags)
        text = target.read_text(encoding="utf-8")
        suffix = target.suffix.lower()

        if suffix in (".yaml", ".yml"):
            loaded = yaml.safe_load(text) if text.strip() else {}
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise ValueError(f"El archivo YAML '{rel_path}' no contiene un mapeo raíz válido.")
            data = dict(loaded)
            data["tags"] = norm_tags
            rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
            atomic_write_text(target, rendered)
            return norm_tags

        fm, body, has_fm = self._split_markdown_frontmatter(text)
        if has_fm and fm is not None:
            fm_data = dict(fm)
            fm_data["tags"] = norm_tags
            fm_yaml = yaml.safe_dump(fm_data, sort_keys=False, allow_unicode=True).rstrip("\n")
            new_text = f"---\n{fm_yaml}\n---\n{body}"
        else:
            fm_yaml = yaml.safe_dump({"tags": norm_tags}, sort_keys=False, allow_unicode=True).rstrip("\n")
            if body:
                new_text = f"---\n{fm_yaml}\n---\n{body}"
            else:
                new_text = f"---\n{fm_yaml}\n---\n"

        atomic_write_text(target, new_text)
        return norm_tags

    def _scan_artifacts(self, docs_root: Path) -> List[Dict[str, Any]]:
        if not docs_root.is_dir():
            return []

        items: List[Dict[str, Any]] = []
        all_files = sorted(
            (p for p in docs_root.rglob("*") if p.is_file()),
            key=lambda p: p.relative_to(docs_root).as_posix(),
        )
        for path in all_files:
            rel_posix = path.relative_to(docs_root).as_posix()
            parts = rel_posix.split("/")
            if any(part.startswith(".") for part in parts):
                continue
            if parts[0] == "history" or rel_posix in _IGNORED_INDEX_REL_PATHS:
                continue
            if path.suffix.lower() not in (".md", ".yaml", ".yml"):
                continue

            try:
                tags = self.get_artifact_tags(docs_root, rel_posix)
            except Exception:
                tags = []
            items.append({"path": rel_posix, "tags": tags})

        return items

    def compile_tags_index(self, docs_okf: str | Path) -> Dict[str, Any]:
        """
        Compila un índice determinista de tags en docs/okf/tags_index.yaml.
        """
        docs_root = self.resolve_docs_okf(docs_okf)
        docs_root.mkdir(parents=True, exist_ok=True)

        items = self._scan_artifacts(docs_root)
        tags_map: Dict[str, List[str]] = {}
        for item in items:
            for tag in item["tags"]:
                tags_map.setdefault(tag, []).append(item["path"])

        sorted_tags_map = {
            tag: sorted(paths)
            for tag, paths in sorted(tags_map.items(), key=lambda kv: kv[0])
        }

        payload: Dict[str, Any] = {
            "schema_version": "okf.tags.v1",
            "total_artifacts": len(items),
            "total_tags": len(sorted_tags_map),
            "tags": sorted_tags_map,
            "items": items,
        }
        index_path = docs_root / "tags_index.yaml"
        atomic_write_text(index_path, yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
        return {**payload, "index_path": str(index_path)}

    def filter_artifacts_by_tags(
        self,
        docs_okf: str | Path,
        tags: Iterable[str] | str,
        match_all: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Filtra artefactos OKF por uno o más tags (match_all=True exige intersección completa).
        """
        query = self.normalize_tags(tags)
        if not query:
            return []

        docs_root = self.resolve_docs_okf(docs_okf)
        items = self._scan_artifacts(docs_root)
        query_set = set(query)

        results: List[Dict[str, Any]] = []
        for item in items:
            item_tags = set(item.get("tags", []))
            if match_all:
                if query_set.issubset(item_tags):
                    results.append(item)
            else:
                if query_set & item_tags:
                    results.append(item)
        return results
