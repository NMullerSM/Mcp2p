"""
Inyeccion de la configuracion MCP de cada panel (EGM-70).

Al lanzar un panel con un CLI del catalogo se escribe un archivo de configuracion con la URL del Hub
embebido y `Authorization: Bearer <token del panel>` en un directorio `0700` propio de la sesion del
panel (archivo `0600`), y al CLI solo se le pasa la RUTA de ese archivo (por flag o por variable de
entorno segun el campo `mcp` del catalogo).

Garantias:
* El token solo existe dentro del archivo `0600` y en la variable `MCP2P_SESSION_TOKEN` del entorno del
  panel (heredada de EGM-22). Nunca va en `argv`: las plantillas del catalogo no tienen marcador de token.
* Solo los CLI del catalogo del repo se inyectan; el catalogo de usuario nunca (plantillas no verificadas).
* El directorio se borra al cerrar el panel o al terminar su proceso, y al arrancar se barren los
  directorios huerfanos de procesos de la GUI que ya no existen (`sweep_orphans`).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from src.adapters.terminal.cli_catalog import CliSpec

logger = logging.getLogger("mcp2p.adapters.terminal.mcp_injection")

MCP_SERVER_NAME = "mcp2p"
CONFIG_FILENAME = "mcp-config.json"
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_ORPHAN_DIR_RE = re.compile(r"(\d+)-[A-Za-z0-9_.-]+")
_SAFE_URL_RE = re.compile(r"https?://[A-Za-z0-9.\[\]:-]+(?::\d+)?(?:/[A-Za-z0-9._~/-]*)?")


class McpInjectionError(RuntimeError):
    """No se pudo preparar la configuracion MCP del panel (no se lanza el CLI a medias)."""


@dataclass(frozen=True)
class McpInjection:
    """Resultado de preparar la inyeccion; `argv`/`env` se anaden al lanzamiento del CLI."""

    status: str  # injected | unsupported | not_applicable | skipped
    method: str = "none"
    verified: bool = False
    reason: str = ""
    argv: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    session_dir: Optional[Path] = None

    def public(self) -> Dict[str, Any]:
        """Forma segura para el puente: nunca incluye token, ruta ni argv."""
        return {"status": self.status, "method": self.method, "verified": self.verified, "reason": self.reason}


def _render_config(fmt: str, hub_url: str, token: str) -> Dict[str, Any]:
    auth = {"Authorization": f"Bearer {token}"}
    if fmt == "claude_mcp_json":
        return {"mcpServers": {MCP_SERVER_NAME: {"type": "http", "url": hub_url, "headers": auth}}}
    if fmt == "opencode_json":
        return {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {MCP_SERVER_NAME: {"type": "remote", "url": hub_url, "headers": auth, "enabled": True}},
        }
    if fmt == "gemini_settings_json":
        return {"mcpServers": {MCP_SERVER_NAME: {"httpUrl": hub_url, "headers": auth}}}
    raise McpInjectionError(f"formato de configuracion MCP desconocido: {fmt!r}")


def _substitute(template: str, *, config_file: Optional[str], hub_url: str) -> str:
    out = template.replace("{hub_url}", hub_url)
    if config_file is not None:
        out = out.replace("{mcp_config_file}", config_file)
    return out


def _default_base_dir() -> Path:
    uid = os.getuid() if hasattr(os, "getuid") else 0
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return Path(runtime) / "mcp2p-panes"
    return Path(tempfile.gettempdir()) / f"mcp2p-panes-{uid}"


class PaneMcpConfigStore:
    """Directorio base `0700` con un subdirectorio `<pid>-<session_id>` por panel vivo."""

    def __init__(self, base_dir: Optional[Path] = None, *, owner_pid: Optional[int] = None) -> None:
        self._base = Path(base_dir) if base_dir is not None else None
        self._pid = int(owner_pid) if owner_pid is not None else os.getpid()

    @property
    def base_dir(self) -> Path:
        if self._base is None:
            self._base = _default_base_dir()
        return self._base

    # -- directorios -------------------------------------------------------

    @staticmethod
    def _owned_private_dir(path: Path) -> bool:
        try:
            st = os.lstat(path)
        except OSError:
            return False
        if not stat.S_ISDIR(st.st_mode):  # tambien rechaza enlaces simbolicos
            return False
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            return False
        return True

    def _ensure_base(self) -> Path:
        base = self.base_dir
        try:
            base.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise McpInjectionError(f"no se pudo crear el directorio de configuracion MCP: {exc}") from exc
        if not self._owned_private_dir(base):
            raise McpInjectionError("el directorio de configuracion MCP no es un directorio propio (¿enlace o ajeno?).")
        try:
            os.chmod(base, 0o700)
        except OSError as exc:
            raise McpInjectionError(f"no se pudo restringir {base}: {exc}") from exc
        return base

    def _session_path(self, session_id: str) -> Path:
        if _SESSION_ID_RE.fullmatch(session_id) is None:
            raise McpInjectionError("session_id invalido para el directorio de configuracion MCP.")
        return self.base_dir / f"{self._pid}-{session_id}"

    # -- API ---------------------------------------------------------------

    def prepare(self, spec: Optional[CliSpec], *, session_id: str, hub_url: Optional[str], token: Optional[str]) -> McpInjection:
        """Escribe la config del panel y devuelve lo que hay que anadir a `argv`/`env` del CLI."""
        if spec is None:
            return McpInjection("skipped", reason="comando propio: sin configuracion MCP")
        mcp = spec.mcp
        method = str(mcp.get("method", "none"))
        verified = bool(mcp.get("verified")) and spec.source == "repo"
        if method == "unsupported":
            return McpInjection("unsupported", method, verified, str(mcp.get("note", "")))
        if method not in ("flag", "env") or spec.source != "repo":
            return McpInjection("not_applicable", method, verified, "este CLI no recibe configuracion MCP del host")
        if not hub_url or not token:
            return McpInjection("skipped", method, verified, "el Hub embebido no esta disponible")
        if _SAFE_URL_RE.fullmatch(hub_url) is None:
            raise McpInjectionError("URL del Hub no valida para inyectar.")

        fmt = str(mcp.get("format", "none"))
        config_file: Optional[str] = None
        session_dir: Optional[Path] = None
        if fmt != "none":
            payload = _render_config(fmt, hub_url, token)
            self._ensure_base()
            session_dir = self._session_path(session_id)
            try:
                session_dir.mkdir(mode=0o700)
                os.chmod(session_dir, 0o700)
                target = session_dir / CONFIG_FILENAME
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
            except (OSError, FileExistsError) as exc:
                self.cleanup(session_id)
                raise McpInjectionError(f"no se pudo escribir la configuracion MCP del panel: {exc}") from exc
            config_file = str(target)

        argv = [_substitute(t, config_file=config_file, hub_url=hub_url) for t in mcp.get("argv", [])]
        env = {k: _substitute(v, config_file=config_file, hub_url=hub_url) for k, v in dict(mcp.get("env", {})).items()}
        return McpInjection("injected", method, verified, "", argv, env, session_dir)

    def cleanup(self, session_id: str) -> bool:
        """Borra el directorio de la sesion (idempotente). Devuelve True si existia."""
        try:
            path = self._session_path(session_id)
        except McpInjectionError:
            return False
        if not self._owned_private_dir(path):
            return False
        shutil.rmtree(path, ignore_errors=True)
        return True

    def sweep_orphans(self) -> int:
        """Borra directorios de procesos de la GUI que ya no existen. Devuelve cuantos."""
        base = self.base_dir
        if not self._owned_private_dir(base):
            return 0
        removed = 0
        try:
            entries = list(os.scandir(base))
        except OSError:
            return 0
        for entry in entries:
            match = _ORPHAN_DIR_RE.fullmatch(entry.name)
            if match is None:
                continue
            pid = int(match.group(1))
            if pid == self._pid or psutil.pid_exists(pid):
                continue
            path = Path(entry.path)
            if self._owned_private_dir(path):
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        if removed:
            logger.info("Se barrieron %d directorios MCP de paneles huerfanos.", removed)
        return removed

    def cleanup_all(self) -> None:
        """Borra todo lo de este proceso (cierre ordenado de la GUI)."""
        base = self.base_dir
        if not self._owned_private_dir(base):
            return
        prefix = f"{self._pid}-"
        try:
            for entry in os.scandir(base):
                if entry.name.startswith(prefix) and self._owned_private_dir(Path(entry.path)):
                    shutil.rmtree(entry.path, ignore_errors=True)
        except OSError:
            pass
