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

import contextlib
import contextvars
import json
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import psutil

from src.adapters.terminal.cli_catalog import CliSpec, current_platform
from src.adapters.terminal.hook_configs import (
    AGY_HOOK_SECTION_NAME,
    agy_hooks_config,
    hook_command_argv,
)
from src.core.services.message_relay_service import TOOL_TIMEOUT_MS

logger = logging.getLogger("mcp2p.adapters.terminal.mcp_injection")

MCP_SERVER_NAME = "mcp2p"
CONFIG_FILENAME = "mcp-config.json"
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_SESSION_FILE_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
MAX_SESSION_FILE_BYTES = 256 * 1024
_ORPHAN_DIR_RE = re.compile(r"(\d+)-[A-Za-z0-9_.-]+")
_SAFE_URL_RE = re.compile(r"https?://[A-Za-z0-9.\[\]:-]+(?::\d+)?(?:/[A-Za-z0-9._~/-]*)?")


# Consentimiento del usuario para escribir el archivo GLOBAL de Antigravity durante el spawn en curso. Lo fija la capa
# de UI (tras el dialogo nativo o el consentimiento recordado) con `global_registration_consent(...)`; sin el, el
# valor es siempre False: ningun hilo ni prueba hereda un consentimiento ajeno.
_GLOBAL_CONSENT: contextvars.ContextVar[bool] = contextvars.ContextVar("mcp2p_agy_global_consent", default=False)


@contextlib.contextmanager
def global_registration_consent(granted: bool):
    """Dentro del bloque, `PaneMcpConfigStore.prepare` puede registrar `mcp2p` en la config global de `agy`."""
    token = _GLOBAL_CONSENT.set(bool(granted))
    try:
        yield
    finally:
        _GLOBAL_CONSENT.reset(token)


class McpInjectionError(RuntimeError):
    """No se pudo preparar la configuracion MCP del panel (no se lanza el CLI a medias)."""


@dataclass(frozen=True)
class McpInjection:
    """Resultado de preparar la inyeccion; `argv`/`env` se anaden al lanzamiento del CLI."""

    status: str  # injected | unsupported | not_applicable | skipped | needs_consent
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
        return {
            "mcpServers": {
                MCP_SERVER_NAME: {"type": "http", "url": hub_url, "headers": auth, "timeout": TOOL_TIMEOUT_MS}
            }
        }
    if fmt == "opencode_json":
        return {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {MCP_SERVER_NAME: {"type": "remote", "url": hub_url, "headers": auth, "enabled": True}},
        }
    raise McpInjectionError(f"formato de configuracion MCP desconocido: {fmt!r}")


def _substitute(template: str, *, config_file: Optional[str], hub_url: str) -> str:
    out = template.replace("{hub_url}", hub_url)
    if config_file is not None:
        out = out.replace("{mcp_config_file}", config_file)
    return out


def _restrict_to_owner(fd: int, path: Path) -> None:
    """Deja el archivo en 0600. Windows no tiene `os.fchmod`: ahi el archivo hereda la ACL del perfil del usuario."""
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


AGY_MANAGED_KEY = "x-mcp2p-managed"
AGY_MANAGED_BY = "mcp2p"
AGY_BACKUP_SUFFIX = ".mcp2p.bak"
HUB_PROXY_ONLY_FLAG = "--hub-proxy-only"


def has_global_registration_consent() -> bool:
    """True solo dentro de un bloque `global_registration_consent(True)` de la UI."""
    return bool(_GLOBAL_CONSENT.get())


def default_agy_config_path() -> Path:
    """Archivo GLOBAL de Antigravity donde se registra el servidor `mcp2p` (fuera del perfil del panel)."""
    return Path.home() / ".gemini" / "config" / "mcp_config.json"


def default_agy_hooks_path() -> Path:
    """Archivo GLOBAL de Antigravity donde se registran los *hooks* cuando el usuario lo consiente."""
    return Path.home() / ".gemini" / "config" / "hooks.json"


def _app_version() -> str:
    try:
        from src.app.version import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - la version es informativa
        return "unknown"


def build_agy_entry(
    *,
    python_executable: Optional[str] = None,
    repo_root: Optional[Path] = None,
    frozen: Optional[bool] = None,
    executable: Optional[str] = None,
    version: Optional[str] = None,
) -> Dict[str, Any]:
    """Entrada exacta que MCP2P escribe para `agy`: solo proxy al Hub (`--hub-proxy-only`), nunca la BD.

    * Empaquetado (congelado): `<ejecutable> --mcp-server --hub-proxy-only`, sin `cwd` ni `PYTHONPATH`.
    * Desde fuentes: `<python> -m src.mcp.server --hub-proxy-only` con `cwd`/`PYTHONPATH` en el repo.
    """
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    marker = {"installed_by": AGY_MANAGED_BY, "version": version or _app_version()}
    if is_frozen:
        return {
            "command": executable or sys.executable,
            "args": ["--mcp-server", HUB_PROXY_ONLY_FLAG],
            AGY_MANAGED_KEY: marker,
        }
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[3]
    entry: Dict[str, Any] = {
        "command": python_executable or sys.executable,
        "args": ["-m", "src.mcp.server", HUB_PROXY_ONLY_FLAG],
    }
    if root.exists():
        entry["cwd"] = str(root)
        entry["env"] = {"PYTHONPATH": str(root)}
    entry[AGY_MANAGED_KEY] = marker
    return entry


def _is_managed(entry: Any) -> bool:
    marker = entry.get(AGY_MANAGED_KEY) if isinstance(entry, dict) else None
    return isinstance(marker, dict) and marker.get("installed_by") == AGY_MANAGED_BY


def _same_launch(current: Dict[str, Any], desired: Dict[str, Any]) -> bool:
    """True si la entrada propia ya lanza exactamente lo deseado (la version del marcador no cuenta)."""
    keys = ("command", "args", "cwd", "env")
    return all(current.get(k) == desired.get(k) for k in keys)


def _indent_of(text: str) -> Any:
    match = re.search(r'^([ \t]+)"', text, re.MULTILINE)
    return match.group(1) if match else 2


def _read_config_file(target: Path) -> tuple[Optional[bytes], Optional[str]]:
    """Bytes del archivo (None si no existe) o la razon del rechazo. Nunca sigue enlaces simbolicos."""
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, "unreadable_config"
    if stat.S_ISLNK(st.st_mode):
        return None, "symlink_rejected"
    if not stat.S_ISREG(st.st_mode):
        return None, "invalid_existing_config"
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None, "symlink_rejected" if target.is_symlink() else "unreadable_config"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, "invalid_existing_config"
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read(), None
    finally:
        os.close(fd)


def _atomic_write_text(target: Path, text: str) -> None:
    """Escribe en un temporal del MISMO directorio y lo renombra encima: el original nunca queda truncado."""
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        _restrict_to_owner(fd, tmp)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _backup_once(target: Path, original: bytes) -> None:
    """Copia exacta `<archivo>.mcp2p.bak` la PRIMERA vez que se toca el archivo; luego no se vuelve a escribir."""
    backup = target.with_name(target.name + AGY_BACKUP_SUFFIX)
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        return
    _restrict_to_owner(fd, backup)
    with os.fdopen(fd, "wb") as fh:
        fh.write(original)
        fh.flush()
        os.fsync(fh.fileno())


def _load_agy_config(target: Path) -> tuple[Dict[str, Any], Optional[bytes], Optional[Dict[str, Any]]]:
    """`(datos, bytes_originales, error)`; `error` es la respuesta de fallo ya armada."""
    raw, reason = _read_config_file(target)
    if reason is not None:
        return {}, None, {"registered": False, "reason": reason, "path": str(target)}
    if raw is None:
        return {}, None, None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}, raw, {"registered": False, "reason": "invalid_existing_config", "path": str(target)}
    if not isinstance(parsed, dict):
        return {}, raw, {"registered": False, "reason": "invalid_existing_config", "path": str(target)}
    return parsed, raw, None


def _save_agy_config(target: Path, data: Dict[str, Any], original: Optional[bytes]) -> None:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if original is not None:
        _backup_once(target, original)
    indent = _indent_of(original.decode("utf-8", errors="replace")) if original is not None else 2
    _atomic_write_text(target, json.dumps(data, indent=indent, ensure_ascii=False) + "\n")


def ensure_agy_mcp_registered(
    config_path: Optional[Path] = None,
    *,
    python_executable: Optional[str] = None,
    repo_root: Optional[Path] = None,
    frozen: Optional[bool] = None,
    executable: Optional[str] = None,
    replace_unmanaged: bool = False,
) -> Dict[str, Any]:
    """Registra la entrada stdio de `mcp2p` en el archivo GLOBAL de Antigravity (`agy`). Llamar solo con consentimiento.

    * Escritura atomica (temporal + `os.replace`), `0600`, y copia `<archivo>.mcp2p.bak` la primera vez.
    * Rechaza enlaces simbolicos; no toca el resto de servidores del usuario.
    * La entrada lleva el marcador `x-mcp2p-managed`: si es nuestra y esta vieja o apunta a una ruta muerta se
      actualiza; una entrada `mcp2p` SIN marcador (manual) se respeta, salvo `replace_unmanaged=True`.
    """
    target = Path(config_path) if config_path is not None else default_agy_config_path()
    data, original, error = _load_agy_config(target)
    if error is not None:
        return error

    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
    elif not isinstance(servers, dict):
        return {"registered": False, "reason": "invalid_existing_config", "path": str(target)}
    else:
        servers = dict(servers)

    desired = build_agy_entry(python_executable=python_executable, repo_root=repo_root, frozen=frozen, executable=executable)
    existing = servers.get(MCP_SERVER_NAME)
    updated = False
    if isinstance(existing, dict) and existing:
        if _is_managed(existing):
            if _same_launch(existing, desired):
                return {"registered": True, "already_present": True, "managed": True, "path": str(target)}
            updated = True  # nuestra, pero vieja o con una ruta muerta
        elif not replace_unmanaged:
            args = existing.get("args")
            return {
                "registered": True,
                "already_present": True,
                "managed": False,
                "hub_proxy_only": isinstance(args, list) and HUB_PROXY_ONLY_FLAG in args,
                "path": str(target),
            }

    servers[MCP_SERVER_NAME] = desired
    data["mcpServers"] = servers
    try:
        _save_agy_config(target, data, original)
    except OSError as exc:
        return {"registered": False, "reason": "write_failed", "error": str(exc), "path": str(target)}
    result: Dict[str, Any] = {"registered": True, "already_present": False, "managed": True, "path": str(target)}
    if updated:
        result["updated"] = True
    return result


def unregister_agy_mcp(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Quita la entrada `mcp2p` SOLO si lleva nuestro marcador `x-mcp2p-managed`; una manual no se toca."""
    target = Path(config_path) if config_path is not None else default_agy_config_path()
    data, original, error = _load_agy_config(target)
    if error is not None:
        return {"removed": False, "reason": error["reason"], "path": str(target)}
    servers = data.get("mcpServers")
    entry = servers.get(MCP_SERVER_NAME) if isinstance(servers, dict) else None
    if entry is None:
        return {"removed": False, "reason": "not_registered", "path": str(target)}
    if not _is_managed(entry):
        return {"removed": False, "reason": "not_managed", "path": str(target)}
    remaining = {k: v for k, v in servers.items() if k != MCP_SERVER_NAME}
    data["mcpServers"] = remaining
    try:
        _save_agy_config(target, data, original)
    except OSError as exc:
        return {"removed": False, "reason": "write_failed", "error": str(exc), "path": str(target)}
    return {"removed": True, "path": str(target)}


def agy_registration_state(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Estado de solo lectura del registro: `not_registered` | `managed` | `manual` | error (`reason`)."""
    target = Path(config_path) if config_path is not None else default_agy_config_path()
    data, _original, error = _load_agy_config(target)
    if error is not None:
        return {"state": "error", "reason": error["reason"], "path": str(target)}
    servers = data.get("mcpServers")
    entry = servers.get(MCP_SERVER_NAME) if isinstance(servers, dict) else None
    if not isinstance(entry, dict) or not entry:
        return {"state": "not_registered", "path": str(target)}
    if _is_managed(entry):
        return {"state": "managed", "path": str(target)}
    args = entry.get("args")
    return {
        "state": "manual",
        "hub_proxy_only": isinstance(args, list) and HUB_PROXY_ONLY_FLAG in args,
        "path": str(target),
    }


def _default_hook_argv() -> Callable[[str], List[str]]:
    frozen = bool(getattr(sys, "frozen", False))
    return lambda ev: hook_command_argv(ev, frozen=frozen, executable=sys.executable)


def agy_hooks_registered(
    hook_argv: Optional[Callable[[str], List[str]]] = None,
    platform: Optional[str] = None,
    hooks_path: Optional[Path] = None,
) -> bool:
    """True si la sección `mcp2p` está registrada en `hooks.json` de Antigravity con los comandos actuales."""
    target = Path(hooks_path) if hooks_path is not None else default_agy_hooks_path()
    data, _original, error = _load_agy_config(target)
    if error is not None or not data:
        return False
    existing = data.get(AGY_HOOK_SECTION_NAME)
    if not isinstance(existing, dict) or not existing:
        return False
    argv_fn = hook_argv if callable(hook_argv) else _default_hook_argv()
    plat = platform or current_platform()
    desired = agy_hooks_config(argv_fn, plat)[AGY_HOOK_SECTION_NAME]
    return existing == desired


def ensure_agy_hooks_registered(
    hook_argv: Optional[Callable[[str], List[str]]] = None,
    platform: Optional[str] = None,
    hooks_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Registra la sección `mcp2p` en `~/.gemini/config/hooks.json` conservando el resto de *hooks*. Llamar solo con consentimiento."""
    target = Path(hooks_path) if hooks_path is not None else default_agy_hooks_path()
    data, original, error = _load_agy_config(target)
    if error is not None:
        return error
    argv_fn = hook_argv if callable(hook_argv) else _default_hook_argv()
    plat = platform or current_platform()
    desired = agy_hooks_config(argv_fn, plat)[AGY_HOOK_SECTION_NAME]
    existing = data.get(AGY_HOOK_SECTION_NAME)
    if isinstance(existing, dict) and existing == desired:
        return {"registered": True, "already_present": True, "path": str(target)}
    data[AGY_HOOK_SECTION_NAME] = desired
    try:
        _save_agy_config(target, data, original)
    except OSError as exc:
        return {"registered": False, "reason": "write_failed", "error": str(exc), "path": str(target)}
    return {"registered": True, "already_present": False, "path": str(target)}


def unregister_agy_hooks(hooks_path: Optional[Path] = None) -> Dict[str, Any]:
    """Quita únicamente la sección `mcp2p` de `~/.gemini/config/hooks.json` (no toca otros *hooks*)."""
    target = Path(hooks_path) if hooks_path is not None else default_agy_hooks_path()
    data, original, error = _load_agy_config(target)
    if error is not None:
        return {"removed": False, "reason": error["reason"], "path": str(target)}
    if AGY_HOOK_SECTION_NAME not in data:
        return {"removed": False, "reason": "not_registered", "path": str(target)}
    data.pop(AGY_HOOK_SECTION_NAME, None)
    try:
        _save_agy_config(target, data, original)
    except OSError as exc:
        return {"removed": False, "reason": "write_failed", "error": str(exc), "path": str(target)}
    return {"removed": True, "path": str(target)}


def _default_base_dir() -> Path:
    if sys.platform == "win32":
        # Perfil del usuario (hereda la ACL privada del perfil), no `%TEMP%` compartido por herramientas de terceros.
        local = os.environ.get("LOCALAPPDATA")
        if local and os.path.isdir(local):
            return Path(local) / "mcp2p" / "panes"
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

    @staticmethod
    def _validate_session_filename(name: Any) -> str:
        if not isinstance(name, str) or name in (".", "..") or _SESSION_FILE_NAME_RE.fullmatch(name) is None:
            raise McpInjectionError(f"nombre de archivo de sesion invalido: {name!r}")
        return name

    def session_file_path(self, session_id: str, name: str) -> Path:
        """Ruta esperada (aun no escrita) dentro del directorio privado `0700` de `session_id`."""
        clean = self._validate_session_filename(name)
        return self._session_path(session_id) / clean

    def ensure_session_dir(self, session_id: str) -> Path:
        """Crea (si falta) y verifica el directorio privado `0700` del panel."""
        self._ensure_base()
        session_dir = self._session_path(session_id)
        try:
            session_dir.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise McpInjectionError(f"no se pudo crear el directorio de sesion del panel: {exc}") from exc
        if not self._owned_private_dir(session_dir):
            raise McpInjectionError("el directorio de sesion del panel no es un directorio propio.")
        try:
            os.chmod(session_dir, 0o700)
        except OSError as exc:
            raise McpInjectionError(f"no se pudo restringir {session_dir}: {exc}") from exc
        return session_dir

    def write_session_file(self, session_id: str, name: str, data: bytes) -> Path:
        """Escribe un archivo de sesion (`0600`, `O_EXCL|O_NOFOLLOW`, <= 256 KiB)."""
        clean = self._validate_session_filename(name)
        if not isinstance(data, (bytes, bytearray)):
            raise McpInjectionError("el contenido del archivo de sesion debe ser bytes.")
        raw = bytes(data)
        if len(raw) > MAX_SESSION_FILE_BYTES:
            raise McpInjectionError(f"el archivo de sesion {clean!r} excede {MAX_SESSION_FILE_BYTES} bytes.")
        session_dir = self.ensure_session_dir(session_id)
        target = session_dir / clean
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(target, flags, 0o600)
            _restrict_to_owner(fd, target)
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
        except (OSError, FileExistsError) as exc:
            raise McpInjectionError(f"no se pudo escribir el archivo de sesion {clean!r}: {exc}") from exc
        return target

    # -- API ---------------------------------------------------------------

    def prepare(
        self,
        spec: Optional[CliSpec],
        *,
        session_id: str,
        hub_url: Optional[str],
        token: Optional[str],
        global_registration_consent: Optional[bool] = None,
    ) -> McpInjection:
        """Escribe la config del panel y devuelve lo que hay que anadir a `argv`/`env` del CLI.

        Los CLI de `stdio_env_proxy` (Antigravity) solo se configuran con un archivo GLOBAL del usuario: sin
        consentimiento (argumento o `global_registration_consent(...)` de la UI) NO se escribe nada y el estado es
        `needs_consent` (D3 del dossier 36).
        """
        if spec is None:
            return McpInjection("skipped", reason="comando propio: sin configuracion MCP")
        mcp = spec.mcp
        method = str(mcp.get("method", "none"))
        verified = spec.mcp_verified() and spec.source == "repo"
        if method == "unsupported":
            return McpInjection("unsupported", method, verified, str(mcp.get("note", "")))
        if method == "stdio_env_proxy":
            if spec.source != "repo":
                return McpInjection("not_applicable", method, verified, "este CLI no recibe configuracion MCP del host")
            consent = _GLOBAL_CONSENT.get() if global_registration_consent is None else bool(global_registration_consent)
            if not consent:
                return McpInjection("needs_consent", "stdio_env_proxy", verified, "needs_consent")
            try:
                registered = ensure_agy_mcp_registered()
            except Exception as exc:
                logger.warning("No se pudo registrar mcp2p en mcp_config.json de Antigravity: %s", exc)
                return McpInjection("skipped", "stdio_env_proxy", verified, "registration_failed")
            if not registered.get("registered"):
                reason = str(registered.get("reason", "registration_failed"))
                logger.warning("No se registro mcp2p en la config de Antigravity: %s", reason)
                return McpInjection("skipped", "stdio_env_proxy", verified, reason)
            if registered.get("managed") is False and not registered.get("hub_proxy_only"):
                # Entrada manual antigua (sin --hub-proxy-only): fuera de un panel abre la BD real. La UI ofrece migrarla.
                return McpInjection("injected", "stdio_env_proxy", verified, "manual_entry")
            return McpInjection("injected", "stdio_env_proxy", verified, "stdio_hub_proxy")
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
                _restrict_to_owner(fd, target)
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
