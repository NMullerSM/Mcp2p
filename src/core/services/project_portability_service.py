"""
Exportar e importar proyectos entre maquinas (plan 2026-11-15, seccion 8.3).

Paquete `.zip`:

    manifest.json                    version, raiz de workspace de origen, proyectos y sha256 de cada archivo
    db/mcp2p_local.db                copia CONSISTENTE (API de backup de sqlite3, nunca copia en caliente)
    workspace/<ruta relativa>/...    repositorios de gobernanza/codigo bajo la raiz de workspace elegida
    secrets/node_identity.key[.enc]  SOLO con include_secrets

Reglas de seguridad:
* Los secretos (PAT en `app_settings`, clave de identidad, archivos `.env`/claves privadas del workspace y
  credenciales en URLs de `.git/config`) se EXCLUYEN por defecto; solo `include_secrets=True` los lleva.
  La base exportada se compacta (`secure_delete` + `VACUUM`) para que no queden restos en paginas libres.
* La importacion trata el zip como hostil: rutas absolutas, `..`, backslashes, unidades de Windows,
  enlaces simbolicos y archivos especiales se rechazan; solo se aceptan prefijos conocidos; cada archivo
  debe figurar en el manifiesto y coincidir en sha256; se limitan entradas, tamanos y razon de compresion.
* Nunca se sobrescribe una base existente sin `merge` o `force`; en `merge` los proyectos con el mismo
  `project_uuid` se omiten y se informan (salvo `force`). Nunca se sobrescriben carpetas del workspace.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

FORMAT_NAME = "mcp2p-portable-project"
FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
DB_ARCHIVE_PATH = "db/mcp2p_local.db"
SECRET_ARCHIVE_PATHS = ("secrets/node_identity.key", "secrets/node_identity.key.enc")
_ALLOWED_PREFIXES = ("workspace/",)
_UUID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_SQL_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_URL_CREDENTIALS_RE = re.compile(r"(://)[^/@\s:]+:[^/@\s]+@")
_SECRET_KEY_HINTS = ("pat", "token", "secret", "passphrase", "password", "private", "credential", "api_key", "apikey")

# Carpetas pesadas y regenerables que no viajan en el paquete.
EXCLUDED_DIR_NAMES = frozenset({"node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".webcache"})
# Archivos con pinta de secreto: excluidos salvo include_secrets.
_SECRET_FILE_PATTERNS = (
    re.compile(r"^\.env(\..*)?$"), re.compile(r".*\.pem$"), re.compile(r".*\.key$"), re.compile(r".*\.p12$"),
    re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$"), re.compile(r"^\.netrc$"), re.compile(r"^\.git-credentials$"),
)

# Claves de .git/config que ejecutan programas externos: se eliminan al importar un paquete ajeno.
_DANGEROUS_GIT_CONFIG_RE = re.compile(
    r"^\s*(fsmonitor|sshcommand|hookspath|helper|textconv|clean|smudge|process|editor|pager|askpass|program|"
    r"command|cmd|proxycommand|gitproxy)\s*=|^\s*[A-Za-z0-9_.-]+\s*=\s*!",
    re.IGNORECASE,
)

_PROJECT_TABLES = ("kanban_cards", "a2a_messages", "dispute_tickets", "outbox_events",
                   "project_collaborators", "project_arbiters", "p2p_seeds")
_TICKET_TABLES = ("jury_borda_scores", "human_jury_votes", "dispute_vote_delivery")


class PortabilityError(Exception):
    """Error de negocio con codigo estable (`code`) y mensaje legible."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        return {"success": False, "code": self.code, "error": self.message, "message": self.message, **self.details}


@dataclass(frozen=True)
class PortabilityLimits:
    max_entries: int = 200_000
    max_total_bytes: int = 8 * 1024**3
    max_file_bytes: int = 2 * 1024**3
    max_manifest_bytes: int = 4 * 1024**2
    max_ratio: int = 1000  # razon de compresion maxima para archivos > 1 MiB
    max_name_len: int = 1024


# ---------------------------------------------------------------------------
# Rutas entre sistemas operativos
# ---------------------------------------------------------------------------


def _looks_windows(path: str) -> bool:
    return bool(_WIN_DRIVE_RE.match(path)) or path.startswith("\\\\") or ("\\" in path and "/" not in path)


def _has_control(text: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in text)


def relative_under_root(path: str, root: str) -> Optional[Tuple[str, ...]]:
    """Partes de `path` relativas a `root` (misma familia de rutas), o None si no esta debajo."""
    if not isinstance(path, str) or not isinstance(root, str) or not path or not root:
        return None
    if _has_control(path) or _has_control(root):
        return None
    win_root, win_path = _looks_windows(root), _looks_windows(path)
    if win_root != win_path:
        return None
    pure_cls = PureWindowsPath if win_root else PurePosixPath
    try:
        rel = pure_cls(path).relative_to(pure_cls(root))
    except ValueError:
        return None
    parts = tuple(rel.parts)
    if any(p in ("", ".", "..") or _has_control(p) or "/" in p or "\\" in p for p in parts):
        return None
    return parts


def rewrite_path(path: str, source_root: str, dest_root: Path) -> Optional[str]:
    """Reescribe una ruta absoluta del origen a la raiz de destino; None si no esta bajo el origen."""
    parts = relative_under_root(path, source_root)
    if parts is None:
        return None
    return str(Path(dest_root).joinpath(*parts))


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _is_secret_setting(key: str) -> bool:
    low = key.lower()
    return any(h in low for h in _SECRET_KEY_HINTS)


def _is_secret_like_file(name: str) -> bool:
    return any(p.match(name) for p in _SECRET_FILE_PATTERNS)


def _sql_ident(name: str) -> str:
    if not _SQL_IDENT_RE.fullmatch(name):
        raise PortabilityError("ARCHIVE_INVALID", f"Identificador SQL no valido en la base: {name!r}.")
    return f'"{name}"'


def _table_columns(conn: sqlite3.Connection, schema: str, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA {schema}.table_info({_sql_ident(table)});").fetchall()
    return [str(r[1]) for r in rows]


def _table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    row = conn.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?;", (table,)).fetchone()
    return row is not None


def _open_untrusted_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA trusted_schema=OFF;")
    except sqlite3.DatabaseError:
        pass
    return conn


class ProjectPortabilityService:
    """Caso de uso de exportacion/importacion de proyectos (sin dependencias de UI ni de red)."""

    def __init__(self, *, limits: Optional[PortabilityLimits] = None) -> None:
        self.limits = limits or PortabilityLimits()

    # ==================================================================
    # EXPORT
    # ==================================================================

    def export_projects(
        self,
        db_path: str | Path,
        archive_path: str | Path,
        *,
        workspace_root: Optional[str | Path] = None,
        include_repos: bool = False,
        project_uuids: Optional[Sequence[str]] = None,
        include_secrets: bool = False,
        identity_key_paths: Optional[Sequence[str | Path]] = None,
        overwrite: bool = False,
        app_version: str = "",
    ) -> Dict[str, Any]:
        db_file = Path(db_path)
        archive = Path(archive_path)
        if not db_file.is_file():
            raise PortabilityError("DB_NOT_FOUND", f"No existe la base de datos: {db_file}")
        if archive.exists() and not overwrite:
            raise PortabilityError("ARCHIVE_EXISTS", f"El paquete ya existe: {archive} (use --overwrite).")
        root = Path(os.path.abspath(workspace_root)) if workspace_root else db_file.resolve().parent / "workspace"
        archive.parent.mkdir(parents=True, exist_ok=True)

        warnings: List[str] = []
        with tempfile.TemporaryDirectory(prefix="mcp2p-export-") as tmp:
            staged = Path(tmp) / "mcp2p_local.db"
            self._consistent_copy(db_file, staged)
            selected = self._prepare_staged_db(staged, project_uuids, include_secrets)
            projects = self._read_projects(staged)
            if not projects:
                raise PortabilityError("NO_PROJECTS", "No hay proyectos que exportar.")

            entries: List[Dict[str, Any]] = []  # {arc, src, kind, project_uuid}
            skipped: List[str] = []
            project_roots: Dict[str, List[str]] = {}
            if include_repos:
                claimed: Set[str] = set()
                for proj in projects:
                    rels = self._project_roots(proj, str(root))
                    project_roots[proj["project_uuid"]] = ["/".join(r) for r in rels]
                    for rel in rels:
                        base = root.joinpath(*rel)
                        if not base.is_dir() or base.is_symlink():
                            warnings.append(f"Ruta de repositorio no incluida (no existe o es enlace): {base}")
                            continue
                        self._collect_files(base, root, proj["project_uuid"], entries, skipped, claimed, include_secrets)
                    outside = [
                        p for p in (proj.get("base_path"), proj.get("code_repo_path"), proj.get("gov_repo_path"))
                        if p and relative_under_root(str(p), str(root)) is None
                    ]
                    if outside:
                        warnings.append(
                            f"Proyecto {proj['project_uuid']}: rutas fuera de la raiz de workspace, no incluidas: {outside}"
                        )
            if skipped:
                warnings.append(f"{len(skipped)} elementos omitidos (enlaces simbolicos, archivos especiales o con pinta de secreto).")

            secrets_added: List[str] = []
            if include_secrets:
                for candidate in identity_key_paths or ():
                    cand = Path(candidate)
                    if cand.is_file() and not cand.is_symlink() and cand.name in {Path(p).name for p in SECRET_ARCHIVE_PATHS}:
                        entries.append({"arc": f"secrets/{cand.name}", "src": str(cand), "kind": "secret", "project_uuid": None})
                        secrets_added.append(cand.name)
                if not secrets_added:
                    warnings.append("include_secrets: no se encontro clave de identidad que incluir.")

            tmp_zip = archive.with_name(archive.name + ".partial")
            files_meta: List[Dict[str, Any]] = []
            try:
                with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                    files_meta.append(self._add_file(zf, DB_ARCHIVE_PATH, str(staged), "db", None, False))
                    for ent in sorted(entries, key=lambda e: e["arc"]):
                        scrub = ent["arc"].endswith("/.git/config") and not include_secrets
                        files_meta.append(self._add_file(zf, ent["arc"], ent["src"], ent["kind"], ent["project_uuid"], scrub))
                    manifest = {
                        "format": FORMAT_NAME,
                        "format_version": FORMAT_VERSION,
                        "app_version": app_version,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "source_platform": sys.platform,
                        "source_workspace_root": str(root),
                        "includes_secrets": bool(include_secrets),
                        "includes_repos": bool(include_repos),
                        "projects": [
                            {
                                "project_uuid": p["project_uuid"],
                                "name": p.get("name", ""),
                                "base_path": p.get("base_path", ""),
                                "code_repo_path": p.get("code_repo_path", "") or "",
                                "gov_repo_path": p.get("gov_repo_path", "") or "",
                                "workspace_roots": project_roots.get(p["project_uuid"], []),
                            }
                            for p in projects
                        ],
                        "files": files_meta,
                        "warnings": warnings,
                    }
                    zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))
                os.replace(tmp_zip, archive)
            finally:
                if tmp_zip.exists():
                    tmp_zip.unlink()

        result: Dict[str, Any] = {
            "success": True,
            "code": "OK",
            "archive": str(archive),
            "projects": [p["project_uuid"] for p in projects],
            "files": len(files_meta),
            "includes_secrets": bool(include_secrets),
            "includes_repos": bool(include_repos),
            "source_workspace_root": str(root),
            "warnings": warnings,
        }
        if include_secrets:
            result["secrets_warning"] = (
                "ATENCION: el paquete contiene secretos (PAT de GitHub y/o clave de identidad). "
                "Guardelo cifrado y no lo comparta."
            )
        return result

    @staticmethod
    def _consistent_copy(db_file: Path, dest: Path) -> None:
        src = sqlite3.connect(str(db_file), timeout=30)
        try:
            dst = sqlite3.connect(str(dest))
            try:
                src.backup(dst)  # copia consistente aunque haya escrituras/WAL en curso
            finally:
                dst.close()
        finally:
            src.close()

    def _prepare_staged_db(self, staged: Path, project_uuids: Optional[Sequence[str]], include_secrets: bool) -> List[str]:
        conn = sqlite3.connect(str(staged))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA secure_delete=ON;")
            conn.execute("PRAGMA foreign_keys=ON;")
            existing = [r["project_uuid"] for r in conn.execute("SELECT project_uuid FROM projects;")]
            if project_uuids:
                wanted = list(dict.fromkeys(str(u) for u in project_uuids))
                missing = [u for u in wanted if u not in existing]
                if missing:
                    raise PortabilityError("PROJECT_NOT_FOUND", f"Proyecto(s) inexistente(s): {missing}")
                selected = wanted
                marks = ",".join("?" for _ in selected)
                with conn:
                    conn.execute(f"DELETE FROM projects WHERE project_uuid NOT IN ({marks});", selected)
                    for table in _PROJECT_TABLES:
                        if _table_exists(conn, "main", table) and "project_uuid" in _table_columns(conn, "main", table):
                            conn.execute(
                                f"DELETE FROM {_sql_ident(table)} WHERE project_uuid NOT IN ({marks});", selected
                            )
                    self._delete_orphan_ticket_rows(conn)
            else:
                selected = existing
            with conn:
                self._scrub_settings(conn, include_secrets)
            conn.commit()
            conn.execute("VACUUM;")  # sin restos de PAT/otros proyectos en paginas libres
        finally:
            conn.close()
        return selected

    @staticmethod
    def _delete_orphan_ticket_rows(conn: sqlite3.Connection) -> None:
        if not _table_exists(conn, "main", "dispute_tickets"):
            return
        for table in _TICKET_TABLES:
            if _table_exists(conn, "main", table):
                conn.execute(
                    f"DELETE FROM {_sql_ident(table)} WHERE ticket_id NOT IN (SELECT ticket_id FROM dispute_tickets);"
                )

    @staticmethod
    def _scrub_settings(conn: sqlite3.Connection, include_secrets: bool) -> None:
        if _table_exists(conn, "main", "extension_settings"):
            # Rutas de binarios por CLI: propias de cada maquina, nunca viajan.
            conn.execute("DELETE FROM extension_settings WHERE namespace = 'terminal_cli';")
        if include_secrets:
            return
        if _table_exists(conn, "main", "app_settings"):
            for (key,) in conn.execute("SELECT key FROM app_settings;").fetchall():
                if _is_secret_setting(str(key)):
                    conn.execute("DELETE FROM app_settings WHERE key = ?;", (key,))
        if _table_exists(conn, "main", "extension_settings"):
            for ns, key in conn.execute("SELECT namespace, key FROM extension_settings;").fetchall():
                if _is_secret_setting(str(key)) or _is_secret_setting(str(ns)):
                    conn.execute("DELETE FROM extension_settings WHERE namespace = ? AND key = ?;", (ns, key))

    @staticmethod
    def _read_projects(staged: Path) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(str(staged))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY project_uuid;")]
        finally:
            conn.close()

    @staticmethod
    def _project_roots(proj: Dict[str, Any], root: str) -> List[Tuple[str, ...]]:
        found: List[Tuple[str, ...]] = []
        for field in ("base_path", "code_repo_path", "gov_repo_path"):
            value = proj.get(field)
            if value:
                parts = relative_under_root(str(value), root)
                if parts:
                    found.append(parts)
        unique = sorted(set(found), key=len)
        minimal: List[Tuple[str, ...]] = []
        for cand in unique:  # descarta las anidadas en otra ya incluida
            if not any(cand[: len(m)] == m for m in minimal):
                minimal.append(cand)
        return minimal

    def _collect_files(
        self,
        base: Path,
        root: Path,
        project_uuid: str,
        entries: List[Dict[str, Any]],
        skipped: List[str],
        claimed: Set[str],
        include_secrets: bool,
    ) -> None:
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            keep: List[str] = []
            for d in dirnames:
                full = os.path.join(dirpath, d)
                if d in EXCLUDED_DIR_NAMES:
                    continue
                if os.path.islink(full):
                    skipped.append(full)
                    continue
                keep.append(d)
            dirnames[:] = keep
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    skipped.append(full)
                    continue
                if not include_secrets and _is_secret_like_file(fname):
                    skipped.append(full)
                    continue
                rel = Path(full).relative_to(root).as_posix()
                if any(ord(c) < 32 or c == "\\" for c in rel):
                    skipped.append(full)
                    continue
                if rel in claimed:
                    continue
                claimed.add(rel)
                entries.append({"arc": f"workspace/{rel}", "src": full, "kind": "workspace", "project_uuid": project_uuid})

    @staticmethod
    def _add_file(
        zf: zipfile.ZipFile, arc: str, src: str, kind: str, project_uuid: Optional[str], scrub_git_config: bool
    ) -> Dict[str, Any]:
        st = os.stat(src)
        mode = 0o755 if (st.st_mode & stat.S_IXUSR) else 0o644
        ts = datetime.fromtimestamp(max(st.st_mtime, 315532800)).timetuple()[:6]
        info = zipfile.ZipInfo(arc, date_time=ts)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (stat.S_IFREG | mode) << 16
        h = hashlib.sha256()
        size = 0
        if scrub_git_config:
            text = Path(src).read_text(encoding="utf-8", errors="replace")
            data = _URL_CREDENTIALS_RE.sub(r"\1", text).encode("utf-8")
            zf.writestr(info, data)
            h.update(data)
            size = len(data)
        else:
            with open(src, "rb") as fin, zf.open(info, "w", force_zip64=True) as fout:
                for block in iter(lambda: fin.read(1024 * 1024), b""):
                    h.update(block)
                    size += len(block)
                    fout.write(block)
        meta: Dict[str, Any] = {"path": arc, "size": size, "sha256": h.hexdigest(), "kind": kind}
        if project_uuid:
            meta["project_uuid"] = project_uuid
        return meta

    # ==================================================================
    # IMPORT
    # ==================================================================

    def import_projects(
        self,
        archive_path: str | Path,
        db_path: str | Path,
        workspace_root: str | Path,
        *,
        merge: bool = False,
        force: bool = False,
        include_secrets: bool = False,
        identity_dir: Optional[str | Path] = None,
    ) -> Dict[str, Any]:
        archive = Path(archive_path)
        if not archive.is_file():
            raise PortabilityError("ARCHIVE_NOT_FOUND", f"No existe el paquete: {archive}")
        if not workspace_root or not str(workspace_root).strip():
            raise PortabilityError("WORKSPACE_ROOT_REQUIRED", "Indique la nueva raiz de workspace (--workspace-root).")
        dest_root = Path(workspace_root).expanduser()
        if not dest_root.is_absolute():
            raise PortabilityError("WORKSPACE_ROOT_INVALID", "La raiz de workspace debe ser una ruta absoluta.")
        dest_root = Path(os.path.abspath(dest_root))
        db_file = Path(db_path)

        db_exists = db_file.exists()
        if db_exists and not (merge or force):
            raise PortabilityError(
                "DB_EXISTS",
                f"Ya existe una base en {db_file}; use --merge (agrega proyectos) o --force (reemplaza) explicitamente.",
            )

        try:
            zf_ctx = zipfile.ZipFile(archive)
        except (zipfile.BadZipFile, OSError) as exc:
            raise PortabilityError("ARCHIVE_INVALID", f"Paquete zip invalido: {exc}") from exc
        with zf_ctx as zf:
            infos = self._validate_members(zf)
            manifest = self._load_manifest(zf, infos)
            declared = self._validate_manifest(manifest, infos)

            created_root = not dest_root.exists()
            dest_root.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=".mcp2p-import-", dir=str(dest_root)))
            moved: List[Tuple[Path, Path]] = []
            hardening_notes: List[str] = []
            try:
                self._extract_verified(zf, infos, declared, stage)
                hardening_notes = self._harden_git_dirs(stage, declared)
                staged_db = stage / "db" / "mcp2p_local.db"
                self._check_staged_db(staged_db, manifest)

                source_root = manifest["source_workspace_root"]
                projects_manifest = {p["project_uuid"]: p for p in manifest["projects"]}
                rewrites = self._rewrite_staged_paths(staged_db, source_root, dest_root, projects_manifest)

                secrets_in_pkg = [p for p in declared if p.startswith("secrets/")]
                if not include_secrets:
                    self._scrub_staged_secrets(staged_db)
                identity_notes: List[str] = []

                existing_ids: Set[str] = set()
                if db_exists:
                    try:
                        existing_ids = self._existing_project_ids(db_file)
                    except sqlite3.DatabaseError as exc:
                        raise PortabilityError("DB_INVALID", f"La base de destino no es una base SQLite valida: {exc}") from exc
                conflicts = sorted(u for u in projects_manifest if u in existing_ids)
                if merge and not force:
                    import_ids = [u for u in projects_manifest if u not in existing_ids]
                    skipped_ids = conflicts
                else:
                    import_ids = list(projects_manifest)
                    skipped_ids = []

                # Los archivos de proyectos omitidos no se instalan.
                wanted_ids = set(import_ids)
                for path, meta in declared.items():
                    if path.startswith("workspace/") and meta.get("project_uuid") not in wanted_ids:
                        try:
                            (stage / Path(*path.split("/"))).unlink()
                        except OSError:
                            pass

                # Carpetas de workspace: nunca se pisan.
                plan_moves, ws_conflicts = self._plan_workspace_moves(stage, declared, import_ids, dest_root)
                if ws_conflicts:
                    raise PortabilityError(
                        "WORKSPACE_CONFLICT",
                        "Ya existen rutas del workspace de destino; muevalas o elija otra raiz: " + ", ".join(ws_conflicts[:5]),
                        conflicts=ws_conflicts,
                    )

                ident_target = self._plan_identity(secrets_in_pkg, include_secrets, identity_dir, force, identity_notes)

                # --- aplicar ---
                for src_dir, dst_dir in plan_moves:
                    dst_dir.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(src_dir, dst_dir)
                    moved.append((src_dir, dst_dir))
                try:
                    db_mode, db_backup = self._apply_db(
                        staged_db, db_file, import_ids, merge=merge, force=force, db_exists=db_exists
                    )
                except Exception:
                    for src_dir, dst_dir in reversed(moved):
                        try:
                            os.replace(dst_dir, src_dir)
                        except OSError:
                            pass
                    raise
                installed_secrets = self._install_identity(stage, ident_target)
            finally:
                shutil.rmtree(stage, ignore_errors=True)
                if created_root and not moved:
                    try:
                        dest_root.rmdir()  # no dejar una carpeta vacia si la importacion fallo
                    except OSError:
                        pass

        return {
            "success": True,
            "code": "OK",
            "db_path": str(db_file),
            "db_mode": db_mode,
            "db_backup": db_backup,
            "workspace_root": str(dest_root),
            "projects_imported": import_ids,
            "projects_skipped_existing": skipped_ids,
            "projects_replaced": conflicts if (force and db_exists) else [],
            "rewritten_paths": rewrites,
            "secrets_included": bool(installed_secrets or (include_secrets and any(
                p.startswith("secrets/") for p in declared))),
            "identity_files_installed": installed_secrets,
            "warnings": identity_notes
            + hardening_notes
            + (
                [f"Se reemplazo la base completa; copia previa en {db_backup}."]
                if db_mode == "replaced" and db_backup
                else []
            )
            + list(manifest.get("warnings", [])),
            "source_workspace_root": source_root,
        }

    # -- validacion del zip ------------------------------------------------

    def _validate_members(self, zf: zipfile.ZipFile) -> Dict[str, zipfile.ZipInfo]:
        lim = self.limits
        infos = zf.infolist()
        if len(infos) > lim.max_entries:
            raise PortabilityError("ARCHIVE_INVALID", "El paquete tiene demasiadas entradas.")
        seen: Set[str] = set()
        by_name: Dict[str, zipfile.ZipInfo] = {}
        total = 0
        for info in infos:
            name = info.filename
            self._validate_member_name(name)
            mode = (info.external_attr >> 16) & 0xFFFF
            fmt = stat.S_IFMT(mode)
            if fmt and fmt not in (stat.S_IFREG, stat.S_IFDIR):
                raise PortabilityError("ARCHIVE_UNSAFE", f"Entrada no permitida (enlace simbolico o especial): {name!r}")
            if info.flag_bits & 0x1:
                raise PortabilityError("ARCHIVE_UNSAFE", f"Entrada cifrada no soportada: {name!r}")
            if name.endswith("/"):
                continue
            folded = name.casefold()
            if folded in seen:
                raise PortabilityError("ARCHIVE_UNSAFE", f"Entrada duplicada (o colision de mayusculas): {name!r}")
            seen.add(folded)
            if info.file_size > lim.max_file_bytes:
                raise PortabilityError("ARCHIVE_TOO_LARGE", f"Archivo demasiado grande: {name!r}")
            if info.file_size > 1024 * 1024 and info.compress_size > 0 and info.file_size / info.compress_size > lim.max_ratio:
                raise PortabilityError("ARCHIVE_UNSAFE", f"Razon de compresion sospechosa en {name!r}")
            total += info.file_size
            if total > lim.max_total_bytes:
                raise PortabilityError("ARCHIVE_TOO_LARGE", "El contenido descomprimido excede el limite.")
            by_name[name] = info
        return by_name

    def _validate_member_name(self, name: str) -> None:
        if not name or len(name) > self.limits.max_name_len:
            raise PortabilityError("ARCHIVE_UNSAFE", "Nombre de entrada vacio o demasiado largo.")
        if _has_control(name) or "\\" in name:
            raise PortabilityError("ARCHIVE_UNSAFE", f"Nombre de entrada no valido (control o backslash): {name!r}")
        if name.startswith("/") or _WIN_DRIVE_RE.match(name):
            raise PortabilityError("ARCHIVE_UNSAFE", f"Ruta absoluta no permitida: {name!r}")
        segments = name.rstrip("/").split("/")
        if any(seg in ("", ".", "..") or len(seg) > 255 for seg in segments):
            raise PortabilityError("ARCHIVE_UNSAFE", f"Ruta con segmentos no permitidos: {name!r}")
        if name == MANIFEST_NAME or name == DB_ARCHIVE_PATH or name in SECRET_ARCHIVE_PATHS:
            return
        if name in ("db/", "secrets/", "workspace/"):
            return
        if name.startswith(_ALLOWED_PREFIXES):
            return
        raise PortabilityError("ARCHIVE_UNSAFE", f"Entrada fuera de los prefijos permitidos: {name!r}")

    def _load_manifest(self, zf: zipfile.ZipFile, infos: Dict[str, zipfile.ZipInfo]) -> Dict[str, Any]:
        info = infos.get(MANIFEST_NAME)
        if info is None:
            raise PortabilityError("ARCHIVE_INVALID", "El paquete no tiene manifest.json.")
        if info.file_size > self.limits.max_manifest_bytes:
            raise PortabilityError("ARCHIVE_TOO_LARGE", "manifest.json demasiado grande.")
        with zf.open(info) as fh:
            raw = fh.read(self.limits.max_manifest_bytes + 1)
        if len(raw) > self.limits.max_manifest_bytes:
            raise PortabilityError("ARCHIVE_TOO_LARGE", "manifest.json demasiado grande.")
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PortabilityError("ARCHIVE_INVALID", f"manifest.json ilegible: {exc}") from exc
        if not isinstance(manifest, dict):
            raise PortabilityError("ARCHIVE_INVALID", "manifest.json debe ser un objeto.")
        return manifest

    def _validate_manifest(self, manifest: Dict[str, Any], infos: Dict[str, zipfile.ZipInfo]) -> Dict[str, Dict[str, Any]]:
        if manifest.get("format") != FORMAT_NAME:
            raise PortabilityError("ARCHIVE_INVALID", "Formato de paquete desconocido.")
        if manifest.get("format_version") != FORMAT_VERSION:
            raise PortabilityError(
                "ARCHIVE_VERSION_UNSUPPORTED",
                f"Version de paquete no soportada: {manifest.get('format_version')!r} (se espera {FORMAT_VERSION}).",
            )
        root = manifest.get("source_workspace_root")
        if not isinstance(root, str) or not root or _has_control(root):
            raise PortabilityError("ARCHIVE_INVALID", "source_workspace_root invalido.")
        projects = manifest.get("projects")
        if not isinstance(projects, list) or not projects:
            raise PortabilityError("ARCHIVE_INVALID", "El manifiesto no lista proyectos.")
        for p in projects:
            if not isinstance(p, dict) or not isinstance(p.get("project_uuid"), str) or not _UUID_RE.fullmatch(p["project_uuid"]):
                raise PortabilityError("ARCHIVE_INVALID", "project_uuid invalido en el manifiesto.")
            for fld in ("base_path", "code_repo_path", "gov_repo_path"):
                v = p.get(fld, "")
                if not isinstance(v, str) or _has_control(v):
                    raise PortabilityError("ARCHIVE_INVALID", f"{fld} invalido en el manifiesto.")
        uuids = [p["project_uuid"] for p in projects]
        if len(set(uuids)) != len(uuids):
            raise PortabilityError("ARCHIVE_INVALID", "project_uuid duplicado en el manifiesto.")
        files = manifest.get("files")
        if not isinstance(files, list):
            raise PortabilityError("ARCHIVE_INVALID", "El manifiesto no lista archivos.")
        declared: Dict[str, Dict[str, Any]] = {}
        for f in files:
            if (
                not isinstance(f, dict) or not isinstance(f.get("path"), str)
                or not isinstance(f.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", f["sha256"])
                or not isinstance(f.get("size"), int) or isinstance(f.get("size"), bool) or f["size"] < 0
                or f.get("kind") not in ("db", "workspace", "secret")
            ):
                raise PortabilityError("ARCHIVE_INVALID", "Entrada de archivo invalida en el manifiesto.")
            if f["path"] in declared:
                raise PortabilityError("ARCHIVE_INVALID", "Archivo duplicado en el manifiesto.")
            expected_kind = (
                "db" if f["path"] == DB_ARCHIVE_PATH
                else "secret" if f["path"] in SECRET_ARCHIVE_PATHS
                else "workspace" if f["path"].startswith("workspace/") else None
            )
            if expected_kind != f["kind"]:
                raise PortabilityError("ARCHIVE_INVALID", f"kind incoherente con la ruta {f['path']!r}.")
            if f.get("project_uuid") is not None and f["project_uuid"] not in uuids:
                raise PortabilityError("ARCHIVE_INVALID", "Archivo asociado a un proyecto desconocido.")
            declared[f["path"]] = f
        if DB_ARCHIVE_PATH not in declared:
            raise PortabilityError("ARCHIVE_INVALID", "El manifiesto no declara la base de datos.")
        actual = {n for n in infos if n != MANIFEST_NAME}
        if actual != set(declared):
            extra = sorted(actual - set(declared))[:3]
            missing = sorted(set(declared) - actual)[:3]
            raise PortabilityError("ARCHIVE_INVALID", f"El zip y el manifiesto no coinciden (extra={extra}, faltan={missing}).")
        return declared

    # -- extraccion verificada ---------------------------------------------

    def _extract_verified(
        self, zf: zipfile.ZipFile, infos: Dict[str, zipfile.ZipInfo], declared: Dict[str, Dict[str, Any]], stage: Path
    ) -> None:
        stage_real = os.path.realpath(stage)
        total = 0
        for name, meta in declared.items():
            info = infos[name]
            target = stage.joinpath(*name.split("/"))
            if os.path.commonpath([stage_real, os.path.realpath(target.parent)]) != stage_real:
                raise PortabilityError("ARCHIVE_UNSAFE", f"Ruta fuera del destino: {name!r}")
            os.makedirs(target.parent, exist_ok=True)
            if os.path.commonpath([stage_real, os.path.realpath(target.parent)]) != stage_real:
                raise PortabilityError("ARCHIVE_UNSAFE", f"Ruta fuera del destino: {name!r}")
            h = hashlib.sha256()
            written = 0
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                with os.fdopen(fd, "wb") as out, zf.open(info) as src:
                    while True:
                        block = src.read(1024 * 1024)
                        if not block:
                            break
                        written += len(block)
                        total += len(block)
                        if written > meta["size"] or written > self.limits.max_file_bytes or total > self.limits.max_total_bytes:
                            raise PortabilityError("ARCHIVE_UNSAFE", f"El contenido real excede lo declarado en {name!r}.")
                        h.update(block)
                        out.write(block)
            except zipfile.BadZipFile as exc:
                raise PortabilityError("ARCHIVE_INVALID", f"Entrada corrupta {name!r}: {exc}") from exc
            if written != meta["size"] or h.hexdigest() != meta["sha256"]:
                raise PortabilityError("ARCHIVE_INTEGRITY", f"El sha256/tamano de {name!r} no coincide con el manifiesto.")
            if name.startswith("workspace/"):
                mode = (info.external_attr >> 16) & 0o777
                os.chmod(target, 0o755 if mode & 0o100 else 0o644)
            else:
                os.chmod(target, 0o600)

    @staticmethod
    def _harden_git_dirs(stage: Path, declared: Dict[str, Dict[str, Any]]) -> List[str]:
        """Neutraliza lo que ejecutaria codigo del paquete al usar git: hooks y comandos en .git/config."""
        notes: List[str] = []
        removed_hooks: List[str] = []
        sanitized: List[str] = []
        for path in declared:
            if not path.startswith("workspace/"):
                continue
            segments = path.split("/")
            target = stage.joinpath(*segments)
            if ".git" in segments[:-1]:
                git_idx = segments.index(".git")
                tail = segments[git_idx + 1:]
                if tail and tail[0] == "hooks" and len(tail) >= 2 and not segments[-1].endswith(".sample"):
                    try:
                        target.unlink()
                        removed_hooks.append(path)
                    except OSError:
                        pass
                    continue
            if segments[-1] == "config" and len(segments) >= 2 and segments[-2] == ".git" and target.is_file():
                text = target.read_text(encoding="utf-8", errors="replace")
                kept = [ln for ln in text.splitlines() if not _DANGEROUS_GIT_CONFIG_RE.match(ln)]
                if len(kept) != len(text.splitlines()):
                    target.write_text("\n".join(kept) + "\n", encoding="utf-8")
                    sanitized.append(path)
        if removed_hooks:
            notes.append(f"Se omitieron {len(removed_hooks)} hooks de git del paquete (no se ejecuta codigo ajeno).")
        if sanitized:
            notes.append(f"Se quitaron comandos externos de {len(sanitized)} archivo(s) .git/config del paquete.")
        return notes

    # -- base de datos -------------------------------------------------------

    def _check_staged_db(self, staged_db: Path, manifest: Dict[str, Any]) -> None:
        try:
            conn = _open_untrusted_db(staged_db)
        except sqlite3.DatabaseError as exc:
            raise PortabilityError("ARCHIVE_INVALID", f"La base del paquete no se puede abrir: {exc}") from exc
        try:
            try:
                if conn.execute("PRAGMA quick_check;").fetchone()[0] != "ok":
                    raise PortabilityError("ARCHIVE_INVALID", "La base del paquete esta corrupta.")
                objs = conn.execute("SELECT type, name FROM sqlite_master WHERE type IN ('trigger','view');").fetchall()
                if objs:
                    raise PortabilityError(
                        "ARCHIVE_UNSAFE", f"La base del paquete trae triggers/vistas no permitidos: {[o[1] for o in objs]}"
                    )
                if not _table_exists(conn, "main", "projects"):
                    raise PortabilityError("ARCHIVE_INVALID", "La base del paquete no tiene la tabla projects.")
                ids = {r[0] for r in conn.execute("SELECT project_uuid FROM projects;")}
            except sqlite3.DatabaseError as exc:
                raise PortabilityError("ARCHIVE_INVALID", f"La base del paquete es invalida: {exc}") from exc
            manifest_ids = {p["project_uuid"] for p in manifest["projects"]}
            if ids != manifest_ids:
                raise PortabilityError("ARCHIVE_INVALID", "Los proyectos de la base no coinciden con el manifiesto.")
        finally:
            conn.close()

    def _rewrite_staged_paths(
        self, staged_db: Path, source_root: str, dest_root: Path, projects_manifest: Dict[str, Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        changes: List[Dict[str, str]] = []
        conn = _open_untrusted_db(staged_db)
        try:
            with conn:
                for row in conn.execute("SELECT project_uuid, base_path, code_repo_path, gov_repo_path FROM projects;").fetchall():
                    new_vals: Dict[str, str] = {}
                    for fld in ("base_path", "code_repo_path", "gov_repo_path"):
                        old = row[fld] or ""
                        if not old:
                            new_vals[fld] = old
                            continue
                        new = rewrite_path(old, source_root, dest_root)
                        if new is None:
                            # Fuera de la raiz de origen: no hay a donde reubicarla.
                            new = (
                                str(dest_root / "imported" / row["project_uuid"]) if fld == "base_path" else ""
                            )
                        new_vals[fld] = new
                        if new != old:
                            changes.append({"project_uuid": row["project_uuid"], "field": fld, "old": old, "new": new})
                    conn.execute(
                        "UPDATE projects SET base_path = ?, code_repo_path = ?, gov_repo_path = ? WHERE project_uuid = ?;",
                        (new_vals["base_path"], new_vals["code_repo_path"], new_vals["gov_repo_path"], row["project_uuid"]),
                    )
        finally:
            conn.close()
        return changes

    def _scrub_staged_secrets(self, staged_db: Path) -> None:
        conn = _open_untrusted_db(staged_db)
        try:
            conn.execute("PRAGMA secure_delete=ON;")
            with conn:
                self._scrub_settings(conn, include_secrets=False)
            conn.commit()
            conn.execute("VACUUM;")
        finally:
            conn.close()

    @staticmethod
    def _existing_project_ids(db_file: Path) -> Set[str]:
        conn = sqlite3.connect(str(db_file))
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='projects';").fetchone():
                return set()
            return {r[0] for r in conn.execute("SELECT project_uuid FROM projects;")}
        finally:
            conn.close()

    def _apply_db(
        self, staged_db: Path, db_file: Path, import_ids: List[str], *, merge: bool, force: bool, db_exists: bool
    ) -> Tuple[str, Optional[str]]:
        db_file.parent.mkdir(parents=True, exist_ok=True)
        backup_path: Optional[Path] = None
        if not db_exists or (force and not merge):
            if db_exists:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                backup_path = db_file.with_name(f"{db_file.name}.bak-{stamp}")
                src = sqlite3.connect(str(db_file))
                try:
                    bak = sqlite3.connect(str(backup_path))
                    try:
                        src.backup(bak)
                    finally:
                        bak.close()
                finally:
                    src.close()
            staged = sqlite3.connect(str(staged_db))
            try:
                dest = sqlite3.connect(str(db_file))
                try:
                    staged.backup(dest)
                finally:
                    dest.close()
            finally:
                staged.close()
            return ("replaced" if db_exists else "created"), (str(backup_path) if backup_path else None)

        conn = sqlite3.connect(str(db_file), timeout=30)
        try:
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("ATTACH DATABASE ? AS src;", (str(staged_db),))
            marks = ",".join("?" for _ in import_ids) or "''"
            with conn:
                if import_ids:
                    if force:
                        for pid in import_ids:
                            conn.execute("DELETE FROM main.projects WHERE project_uuid = ?;", (pid,))
                            if _table_exists(conn, "main", "p2p_seeds"):
                                conn.execute("DELETE FROM main.p2p_seeds WHERE project_uuid = ?;", (pid,))
                    self._copy_rows(conn, "swarm_nodes", "1=1", [])
                    self._copy_rows(conn, "projects", f"project_uuid IN ({marks})", import_ids)
                    for table in _PROJECT_TABLES:
                        self._copy_rows(conn, table, f"project_uuid IN ({marks})", import_ids)
                    for table in _TICKET_TABLES:
                        self._copy_rows(
                            conn, table,
                            f"ticket_id IN (SELECT ticket_id FROM src.dispute_tickets WHERE project_uuid IN ({marks}))",
                            import_ids,
                        )
            conn.commit()
            conn.execute("DETACH DATABASE src;")
        finally:
            conn.close()
        return "merged", None

    @staticmethod
    def _copy_rows(conn: sqlite3.Connection, table: str, where: str, params: List[Any]) -> None:
        if not _table_exists(conn, "main", table) or not _table_exists(conn, "src", table):
            return
        main_cols = _table_columns(conn, "main", table)
        src_cols = set(_table_columns(conn, "src", table))
        common = [c for c in main_cols if c in src_cols]
        if not common:
            return
        cols = ",".join(_sql_ident(c) for c in common)
        conn.execute(
            f"INSERT OR IGNORE INTO main.{_sql_ident(table)} ({cols}) SELECT {cols} FROM src.{_sql_ident(table)} WHERE {where};",
            params,
        )

    # -- workspace e identidad -----------------------------------------------

    def _plan_workspace_moves(
        self, stage: Path, declared: Dict[str, Dict[str, Any]], import_ids: Iterable[str], dest_root: Path
    ) -> Tuple[List[Tuple[Path, Path]], List[str]]:
        """Mueve por carpeta de primer nivel minima: `workspace/<a>/<b>` -> `dest_root/<a>/<b>` por archivo-raiz."""
        wanted = set(import_ids)
        top_dirs: Dict[Tuple[str, ...], None] = {}
        for path, meta in declared.items():
            if not path.startswith("workspace/") or meta.get("project_uuid") not in wanted:
                continue
            rel = tuple(path.split("/")[1:])
            top_dirs[rel[:1] if len(rel) > 1 else rel] = None
        moves: List[Tuple[Path, Path]] = []
        conflicts: List[str] = []
        for rel in top_dirs:
            src = stage / "workspace" / Path(*rel)
            dst = dest_root / Path(*rel)
            if os.path.lexists(dst):
                conflicts.append(str(dst))
            else:
                moves.append((src, dst))
        # Si dos proyectos comparten la misma carpeta de primer nivel, el movimiento es unico.
        return moves, conflicts

    @staticmethod
    def _plan_identity(
        secrets_in_pkg: List[str], include_secrets: bool, identity_dir: Optional[str | Path], force: bool, notes: List[str]
    ) -> Optional[Path]:
        if not secrets_in_pkg:
            return None
        if not include_secrets:
            notes.append("El paquete trae secretos; se omitieron (use --include-secrets para importarlos).")
            return None
        target = Path(identity_dir) if identity_dir else Path.home() / ".mcp2p"
        for arc in secrets_in_pkg:
            dst = target / Path(arc).name
            if dst.exists() and not force:
                raise PortabilityError(
                    "IDENTITY_EXISTS", f"Ya existe {dst}; no se sobrescribe una identidad sin --force."
                )
        return target

    @staticmethod
    def _install_identity(stage: Path, target: Optional[Path]) -> List[str]:
        if target is None:
            return []
        installed: List[str] = []
        secrets_dir = stage / "secrets"
        if not secrets_dir.is_dir():
            return installed
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(target, 0o700)
        except OSError:
            pass
        for item in sorted(secrets_dir.iterdir()):
            dst = target / item.name
            tmp = dst.with_name(dst.name + ".tmp")
            shutil.copyfile(item, tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, dst)
            installed.append(str(dst))
        return installed
