"""
Catalogo de CLIs de agente (datos, no codigo) y resolucion segura de binarios.

* El catalogo del repo (`cli_catalog.json`) es la unica fuente de comandos de instalacion
  instalables. Cada comando pasa por una gramatica CERRADA por metodo (npm, brew, winget,
  curl|sh, irm|iex) con lista blanca de hosts; nada se interpola con texto externo.
* El catalogo del usuario (`cli_catalog.user.json` en `get_user_data_dir()`) solo puede AGREGAR
  CLIs nuevos: nunca pisa los del repo, siempre queda `verified: false` y nunca es instalable.
* La resolucion de binarios NO ejecuta nada para decidir (ni `--version`): PATH, `~/.local/bin`,
  rutas candidatas (con glob por version semantica) y la ruta configurada por el usuario, que
  tiene prioridad. Nunca se usa `shell=True`.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("mcp2p.adapters.terminal.cli_catalog")

CATALOG_SCHEMA_VERSION = 1
SUPPORTED_PLATFORMS: Tuple[str, ...] = ("linux", "darwin", "win32")
SETTINGS_NAMESPACE = "terminal_cli"
USER_CATALOG_FILENAME = "cli_catalog.user.json"
MAX_CATALOG_BYTES = 256 * 1024
MAX_USER_ENTRIES = 32
MAX_GLOB_MATCHES = 200
MAX_PATH_LEN = 4096

# Hosts desde los que se acepta descargar un instalador (`curl | sh` / `irm | iex`).
# Cambiarla exige tocar codigo (y por tanto revision), no un archivo de datos.
TRUSTED_INSTALL_HOSTS = frozenset({"claude.ai", "chatgpt.com", "antigravity.google", "opencode.ai"})

_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_BINARY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
_ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,31}")
_ENV_VAR_IN_PATH_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]{0,31})%")
_VERSION_SEGMENT_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?")
_WIN_ABS_RE = re.compile(r"[A-Za-z]:[\\/]")

_NPM_PACKAGE = r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*(?:@[A-Za-z0-9._-]+)?"
_URL = r"https://[a-z0-9][a-z0-9.-]*/[A-Za-z0-9._/-]*"
_INSTALL_PATTERNS: Dict[str, "re.Pattern[str]"] = {
    "npm": re.compile(rf"npm install -g {_NPM_PACKAGE}"),
    "brew": re.compile(r"brew install (?:--cask )?[a-z0-9][a-z0-9._+@-]*(?:/[a-z0-9][a-z0-9._+-]*){0,2}"),
    "winget": re.compile(r"winget install [A-Za-z0-9]+(?:\.[A-Za-z0-9][A-Za-z0-9-]*)+"),
    "curl_sh": re.compile(rf"curl -fsSL ({_URL}) \| (?:sh|bash)"),
    "powershell_irm": re.compile(
        rf"(?:irm ({_URL}) \| iex|powershell -ExecutionPolicy ByPass -c \"irm ({_URL}) \| iex\")"
    ),
}
_SCRIPT_METHODS = frozenset({"curl_sh", "powershell_irm"})
INSTALL_METHODS = tuple(_INSTALL_PATTERNS)
# `flag` y `env` son los unicos metodos que el host inyecta al lanzar un panel (EGM-70). `subcommand` y
# `config_file` se conservan como metadato para catalogos de usuario; `unsupported` marca un CLI verificado
# que no admite configuracion MCP de sesion; `stdio_env_proxy` usa config stdio estatica + proxy por entorno.
_MCP_METHODS = frozenset({"flag", "subcommand", "config_file", "env", "none", "unsupported", "stdio_env_proxy"})
# Formatos del archivo de configuracion MCP por panel (los renderiza `mcp_injection.py`).
MCP_CONFIG_FORMATS = frozenset({"none", "claude_mcp_json", "opencode_json", "gemini_settings_json"})
# Marcadores permitidos en `mcp.argv` / `mcp.env`. NO existe un marcador de token: el token del panel no
# puede llegar al argv ni a las variables inyectadas desde el catalogo; solo viaja dentro del archivo 0600.
MCP_PLACEHOLDERS = ("{mcp_config_file}", "{hub_url}")
_PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")
_MCP_ENV_DENY = frozenset({"PATH", "HOME", "SHELL", "PYTHONPATH", "PYTHONHOME", "IFS", "TERM", "USER"})
_KINDS = frozenset({"agent_cli", "shell"})


class CatalogValidationError(ValueError):
    """El catalogo (o una de sus entradas) no cumple el esquema estricto."""


class SettingsStore(Protocol):
    """Subconjunto de `ExtensionRegistryRepositoryPort` que usa el catalogo."""

    def get_extension_setting(self, namespace: str, key: str, default: Optional[str] = None) -> Optional[str]: ...

    def set_extension_setting(self, namespace: str, key: str, value: str) -> None: ...

    def delete_extension_setting(self, namespace: str, key: str) -> bool: ...


def current_platform() -> str:
    plat = sys.platform
    if plat.startswith("linux"):
        return "linux"
    if plat == "darwin":
        return "darwin"
    if plat == "win32":
        return "win32"
    return plat


# ---------------------------------------------------------------------------
# Validadores primitivos
# ---------------------------------------------------------------------------


def has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 or ch in "\u0085  " for ch in value)


def _text(value: Any, field_name: str, *, max_len: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise CatalogValidationError(f"{field_name}: se esperaba texto.")
    if not value and not allow_empty:
        raise CatalogValidationError(f"{field_name}: no puede estar vacio.")
    if len(value) > max_len:
        raise CatalogValidationError(f"{field_name}: excede {max_len} caracteres.")
    if has_control_chars(value):
        raise CatalogValidationError(f"{field_name}: contiene caracteres de control.")
    return value


def _only_keys(obj: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    extra = sorted(set(obj) - set(allowed))
    if extra:
        raise CatalogValidationError(f"{where}: campos no permitidos {extra}.")


def _https_url(value: Any, field_name: str) -> str:
    text = _text(value, field_name, max_len=300)
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CatalogValidationError(f"{field_name}: debe ser una URL https sin credenciales.")
    return text


def validate_install_command(method: str, command: str) -> str:
    """Valida un comando contra la gramatica cerrada de su metodo; devuelve el comando."""
    pattern = _INSTALL_PATTERNS.get(method)
    if pattern is None:
        raise CatalogValidationError(f"metodo de instalacion desconocido: {method!r}.")
    text = _text(command, "install.command", max_len=300)
    match = pattern.fullmatch(text)
    if match is None:
        raise CatalogValidationError(f"comando de instalacion fuera de la lista cerrada para {method!r}.")
    if method in _SCRIPT_METHODS:
        urls = [g for g in match.groups() if g]
        for url in urls:
            parsed = urlparse(url)
            if parsed.hostname not in TRUSTED_INSTALL_HOSTS or parsed.port is not None:
                raise CatalogValidationError(f"host de instalador no confiable: {parsed.hostname!r}.")
    return text


def install_requirements(method: str, command: str) -> Tuple[str, ...]:
    """Herramientas previas que exige el metodo (derivadas en codigo, no del archivo de datos)."""
    if method == "npm":
        return ("npm", "node")
    if method == "brew":
        return ("brew",)
    if method == "winget":
        return ("winget",)
    if method == "curl_sh":
        return ("curl", "bash" if command.endswith("| bash") else "sh")
    if method == "powershell_irm":
        return ("powershell",)
    return ()


def validate_path_pattern(pattern: str, field_name: str = "candidate_paths") -> str:
    """Patron de ruta candidata: absoluto o `~/`, solo `*` como comodin, sin `..`."""
    text = _text(pattern, field_name, max_len=512)
    segments = re.split(r"[\\/]", text)
    if ".." in segments:
        raise CatalogValidationError(f"{field_name}: no se admite '..'.")
    if any(ch in text for ch in "?[]{}"):
        raise CatalogValidationError(f"{field_name}: solo se admite '*' como comodin.")
    anchored = (
        text.startswith("~/")
        or text.startswith("/")
        or text.startswith("%")
        or _WIN_ABS_RE.match(text) is not None
    )
    if not anchored:
        raise CatalogValidationError(f"{field_name}: debe ser absoluta, '~/' o %VAR%.")
    return text


def validate_user_binary_path(path: Any) -> str:
    """Ruta de binario elegida por el usuario: absoluta, sin `..`, sin control, ejecutable."""
    if not isinstance(path, str) or not path.strip():
        raise CatalogValidationError("la ruta debe ser un texto no vacio.")
    if len(path) > MAX_PATH_LEN:
        raise CatalogValidationError("la ruta es demasiado larga.")
    if has_control_chars(path):
        raise CatalogValidationError("la ruta contiene caracteres de control.")
    if not (os.path.isabs(path) or _WIN_ABS_RE.match(path)):
        raise CatalogValidationError("la ruta debe ser absoluta.")
    if ".." in re.split(r"[\\/]", path):
        raise CatalogValidationError("la ruta no puede contener '..'.")
    return path


_WINDOWS_EXE_SUFFIXES = (".exe", ".cmd", ".bat", ".com")


def _executable_variants(name: str, is_windows: bool) -> Tuple[str, ...]:
    """Nombres a probar para un binario: en Windows `claude` -> `claude.exe`, `claude.cmd`... (como PATHEXT)."""
    if not is_windows or name.lower().endswith(_WINDOWS_EXE_SUFFIXES):
        return (name,)
    return (*(name + ext for ext in _WINDOWS_EXE_SUFFIXES), name)


def _is_cwd_hit(path: str) -> bool:
    try:
        return os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(os.getcwd())
    except OSError:
        return False


def is_executable_file(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.access(path, os.X_OK)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstallOption:
    platform: str
    method: str
    command: str
    verified: bool
    source_url: str
    note: str = ""

    @property
    def downloads_script(self) -> bool:
        return self.method in _SCRIPT_METHODS

    @property
    def requires(self) -> Tuple[str, ...]:
        return install_requirements(self.method, self.command)


def _default_osc7501() -> Dict[str, str]:
    return {
        "status": "unverified",
        "probe_reply_payload": "",
        "verified_with_cli_version": "",
        "verified_on": "",
        "note": "",
    }


@dataclass(frozen=True)
class CliSpec:
    id: str
    display_name: str
    icon: str
    kind: str
    binary: str
    binary_aliases: Tuple[str, ...]
    candidate_paths: Mapping[str, Tuple[str, ...]]
    launch_flags: Tuple[str, ...]
    mcp: Mapping[str, Any]
    resume: Mapping[str, Any]
    install: Mapping[str, Tuple[InstallOption, ...]]
    source: str = "repo"
    verified: bool = True
    shell_env: Optional[str] = None
    osc7501: Mapping[str, str] = field(default_factory=_default_osc7501)

    def mcp_verified(self, frozen: Optional[bool] = None) -> bool:
        """True si la integracion MCP de este CLI esta verificada para el modo de ejecucion pedido.

        `verified_dev` rige al correr desde fuentes y `verified_frozen` en el binario empaquetado (R1.5): una
        verificacion medida en desarrollo no vale para el binario congelado. Solo cuentan las del catalogo del repo.
        """
        if self.source != "repo":
            return False
        is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
        key = "verified_frozen" if is_frozen else "verified_dev"
        return bool(self.mcp.get(key, self.mcp.get("verified", False)))

    def osc7501_probe_reply_bytes(self) -> Optional[bytes]:
        """Devuelve la respuesta a la sonda OSC 7501 solo para CLIs verificados del catalogo del repo."""
        if self.source != "repo" or self.kind != "agent_cli":
            return None
        if self.osc7501.get("status") != "verified":
            return None
        payload = self.osc7501.get("probe_reply_payload", "")
        if not isinstance(payload, str) or not payload:
            return None
        if _OSC7501_PROBE_PAYLOAD_RE.fullmatch(payload) is None:
            return None
        return f"\x1b]7501;{payload}\x07".encode("ascii")

    @property
    def binary_names(self) -> Tuple[str, ...]:
        return (self.binary, *self.binary_aliases)


@dataclass(frozen=True)
class CliCatalog:
    catalog_version: str
    profiles: Mapping[str, CliSpec]
    warnings: Tuple[str, ...] = ()

    def get(self, cli_id: Any) -> Optional[CliSpec]:
        if not isinstance(cli_id, str):
            return None
        return self.profiles.get(cli_id)

    def ids(self) -> List[str]:
        return list(self.profiles)


# ---------------------------------------------------------------------------
# Carga y validacion estricta
# ---------------------------------------------------------------------------

_ENTRY_KEYS = (
    "id", "display_name", "icon", "kind", "binary", "binary_aliases", "candidate_paths",
    "launch_flags", "mcp", "resume", "install", "shell_env", "osc7501",
)
_INSTALL_KEYS = ("method", "command", "verified", "source_url", "note")
_MCP_KEYS = (
    "method", "format", "argv", "env", "path", "verified", "note",
    "verified_dev", "verified_frozen", "verified_with_cli_version", "verified_on",
)
_OSC7501_KEYS = ("status", "probe_reply_payload", "verified_with_cli_version", "verified_on", "note")
_OSC7501_PROBE_PAYLOAD_RE = re.compile(r"\?[0-9A-Za-z:;_=-]{0,31}")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CLI_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,31}")
_RESUME_KEYS = ("argv", "verified", "note")


def _argv_list(value: Any, field_name: str, *, max_items: int = 16) -> Tuple[str, ...]:
    if not isinstance(value, list) or len(value) > max_items:
        raise CatalogValidationError(f"{field_name}: se esperaba una lista corta.")
    return tuple(_text(item, f"{field_name}[]", max_len=200) for item in value)


def _parse_entry(raw: Any, *, source: str) -> CliSpec:
    if not isinstance(raw, dict):
        raise CatalogValidationError("entrada de catalogo: se esperaba un objeto.")
    _only_keys(raw, _ENTRY_KEYS, "entrada")
    cli_id = _text(raw.get("id"), "id", max_len=32)
    if _ID_RE.fullmatch(cli_id) is None:
        raise CatalogValidationError(f"id invalido: {cli_id!r}.")
    where = f"entrada {cli_id}"
    kind = _text(raw.get("kind", "agent_cli"), f"{where}.kind", max_len=16)
    if kind not in _KINDS:
        raise CatalogValidationError(f"{where}: kind invalido.")
    if source == "user" and kind != "agent_cli":
        raise CatalogValidationError(f"{where}: el catalogo de usuario solo admite agent_cli.")
    icon = _text(raw.get("icon", cli_id), f"{where}.icon", max_len=32)
    if _ID_RE.fullmatch(icon) is None:
        raise CatalogValidationError(f"{where}: icon invalido.")
    binary = _text(raw.get("binary"), f"{where}.binary", max_len=64)
    if _BINARY_RE.fullmatch(binary) is None:
        raise CatalogValidationError(f"{where}: binary debe ser un nombre sin separadores de ruta.")
    aliases_raw = raw.get("binary_aliases", [])
    if not isinstance(aliases_raw, list) or len(aliases_raw) > 8:
        raise CatalogValidationError(f"{where}: binary_aliases invalido.")
    aliases = tuple(_text(a, f"{where}.binary_aliases[]", max_len=64) for a in aliases_raw)
    if any(_BINARY_RE.fullmatch(a) is None for a in aliases):
        raise CatalogValidationError(f"{where}: alias de binario invalido.")

    shell_env = raw.get("shell_env")
    if shell_env is not None:
        shell_env = _text(shell_env, f"{where}.shell_env", max_len=32)
        if _ENV_NAME_RE.fullmatch(shell_env) is None or kind != "shell":
            raise CatalogValidationError(f"{where}: shell_env invalido.")

    cand_raw = raw.get("candidate_paths", {})
    if not isinstance(cand_raw, dict):
        raise CatalogValidationError(f"{where}: candidate_paths invalido.")
    _only_keys(cand_raw, SUPPORTED_PLATFORMS, f"{where}.candidate_paths")
    candidates: Dict[str, Tuple[str, ...]] = {}
    for plat, items in cand_raw.items():
        if not isinstance(items, list) or len(items) > 16:
            raise CatalogValidationError(f"{where}.candidate_paths.{plat}: lista invalida.")
        candidates[plat] = tuple(validate_path_pattern(i, f"{where}.candidate_paths.{plat}[]") for i in items)

    flags = _argv_list(raw.get("launch_flags", []), f"{where}.launch_flags")

    mcp_raw = raw.get("mcp", {"method": "none", "verified": False})
    if not isinstance(mcp_raw, dict):
        raise CatalogValidationError(f"{where}.mcp: se esperaba un objeto.")
    _only_keys(mcp_raw, _MCP_KEYS, f"{where}.mcp")
    mcp_method = _text(mcp_raw.get("method", "none"), f"{where}.mcp.method", max_len=16)
    if mcp_method not in _MCP_METHODS:
        raise CatalogValidationError(f"{where}.mcp.method invalido.")
    mcp_format = _text(mcp_raw.get("format", "none"), f"{where}.mcp.format", max_len=32)
    if mcp_format not in MCP_CONFIG_FORMATS:
        raise CatalogValidationError(f"{where}.mcp.format invalido.")
    mcp_env_raw = mcp_raw.get("env", {})
    if not isinstance(mcp_env_raw, dict) or len(mcp_env_raw) > 4:
        raise CatalogValidationError(f"{where}.mcp.env: se esperaba un objeto corto.")
    mcp_env: Dict[str, str] = {}
    for env_name, env_template in mcp_env_raw.items():
        if (
            not isinstance(env_name, str)
            or _ENV_NAME_RE.fullmatch(env_name) is None
            or env_name in _MCP_ENV_DENY
            or env_name.startswith(("MCP2P_", "LD_", "DYLD_"))
        ):
            raise CatalogValidationError(f"{where}.mcp.env: nombre de variable no permitido: {env_name!r}.")
        mcp_env[env_name] = _text(env_template, f"{where}.mcp.env.{env_name}", max_len=200)
    mcp_argv = list(_argv_list(mcp_raw.get("argv", []), f"{where}.mcp.argv"))
    templates = [*mcp_argv, *mcp_env.values()]
    for template in templates:
        for marker in _PLACEHOLDER_RE.findall(template):
            if marker not in MCP_PLACEHOLDERS:
                raise CatalogValidationError(f"{where}.mcp: marcador no permitido {marker!r}.")
    uses_file = any("{mcp_config_file}" in t for t in templates)
    if uses_file != (mcp_format != "none"):
        raise CatalogValidationError(f"{where}.mcp: format y {{mcp_config_file}} deben usarse juntos.")
    if mcp_method == "flag" and (not mcp_argv or mcp_env):
        raise CatalogValidationError(f"{where}.mcp: method=flag exige argv (sin env).")
    if mcp_method == "env" and (not mcp_env or mcp_argv):
        raise CatalogValidationError(f"{where}.mcp: method=env exige env (sin argv).")
    if mcp_method in ("none", "unsupported", "stdio_env_proxy") and (mcp_argv or mcp_env):
        raise CatalogValidationError(f"{where}.mcp: method={mcp_method} no admite argv ni env.")
    split = "verified_dev" in mcp_raw or "verified_frozen" in mcp_raw
    if split and "verified" in mcp_raw:
        raise CatalogValidationError(f"{where}.mcp: usa verified o verified_dev/verified_frozen, no ambos.")
    if split:
        for key in ("verified_dev", "verified_frozen"):
            if not isinstance(mcp_raw.get(key), bool):
                raise CatalogValidationError(f"{where}.mcp.{key}: se esperaba true o false (y ambos campos juntos).")
        verified_dev = mcp_raw["verified_dev"] and source == "repo"
        verified_frozen = mcp_raw["verified_frozen"] and source == "repo"
    else:
        legacy = bool(mcp_raw.get("verified", False)) and source == "repo"
        verified_dev = verified_frozen = legacy
    verified_cli_version = ""
    verified_on = ""
    if "verified_with_cli_version" in mcp_raw or "verified_on" in mcp_raw:
        verified_cli_version = _text(mcp_raw.get("verified_with_cli_version"), f"{where}.mcp.verified_with_cli_version", max_len=32)
        if _CLI_VERSION_RE.fullmatch(verified_cli_version) is None:
            raise CatalogValidationError(f"{where}.mcp.verified_with_cli_version invalido.")
        verified_on = _text(mcp_raw.get("verified_on"), f"{where}.mcp.verified_on", max_len=10)
        if _DATE_RE.fullmatch(verified_on) is None:
            raise CatalogValidationError(f"{where}.mcp.verified_on: se esperaba AAAA-MM-DD.")
    if split and (verified_dev or verified_frozen) and not (verified_cli_version and verified_on):
        raise CatalogValidationError(
            f"{where}.mcp: una verificacion exige verified_with_cli_version y verified_on (version del CLI y fecha)."
        )
    mcp: Dict[str, Any] = {
        "method": mcp_method,
        "format": mcp_format,
        "argv": mcp_argv,
        "env": mcp_env,
        "verified_dev": verified_dev,
        "verified_frozen": verified_frozen,
        # `verified` es el valor efectivo en ESTE modo de ejecucion (fuentes o congelado).
        "verified": verified_frozen if getattr(sys, "frozen", False) else verified_dev,
        "verified_with_cli_version": verified_cli_version,
        "verified_on": verified_on,
        "note": _text(mcp_raw.get("note", ""), f"{where}.mcp.note", max_len=300, allow_empty=True),
    }
    if "path" in mcp_raw:
        mcp["path"] = _text(mcp_raw["path"], f"{where}.mcp.path", max_len=300)

    resume_raw = raw.get("resume", {"argv": []})
    if not isinstance(resume_raw, dict):
        raise CatalogValidationError(f"{where}.resume: se esperaba un objeto.")
    _only_keys(resume_raw, _RESUME_KEYS, f"{where}.resume")
    resume: Dict[str, Any] = {
        "argv": list(_argv_list(resume_raw.get("argv", []), f"{where}.resume.argv")),
        "verified": bool(resume_raw.get("verified", False)) and source == "repo",
        "note": _text(resume_raw.get("note", ""), f"{where}.resume.note", max_len=300, allow_empty=True),
    }

    install_raw = raw.get("install", {})
    if not isinstance(install_raw, dict):
        raise CatalogValidationError(f"{where}.install: se esperaba un objeto.")
    _only_keys(install_raw, SUPPORTED_PLATFORMS, f"{where}.install")
    install: Dict[str, Tuple[InstallOption, ...]] = {}
    for plat, options in install_raw.items():
        if not isinstance(options, list) or len(options) > 8:
            raise CatalogValidationError(f"{where}.install.{plat}: lista invalida.")
        parsed: List[InstallOption] = []
        for opt in options:
            if not isinstance(opt, dict):
                raise CatalogValidationError(f"{where}.install.{plat}: opcion invalida.")
            _only_keys(opt, _INSTALL_KEYS, f"{where}.install.{plat}")
            method = _text(opt.get("method"), f"{where}.install.method", max_len=24)
            command = validate_install_command(method, opt.get("command"))  # type: ignore[arg-type]
            parsed.append(
                InstallOption(
                    platform=plat,
                    method=method,
                    command=command,
                    # Un catalogo de usuario NUNCA puede declararse verificado.
                    verified=bool(opt.get("verified", False)) and source == "repo",
                    source_url=_https_url(opt.get("source_url"), f"{where}.install.source_url"),
                    note=_text(opt.get("note", ""), f"{where}.install.note", max_len=300, allow_empty=True),
                )
            )
        install[plat] = tuple(parsed)

    osc_raw = raw.get("osc7501")
    if osc_raw is None:
        osc_raw = {}
    if not isinstance(osc_raw, dict):
        raise CatalogValidationError(f"{where}.osc7501: se esperaba un objeto.")
    _only_keys(osc_raw, _OSC7501_KEYS, f"{where}.osc7501")
    osc_status = _text(osc_raw.get("status", "unverified"), f"{where}.osc7501.status", max_len=16)
    if osc_status not in ("verified", "unverified"):
        raise CatalogValidationError(f"{where}.osc7501.status: debe ser 'verified' o 'unverified'.")
    osc_payload = _text(
        osc_raw.get("probe_reply_payload", ""),
        f"{where}.osc7501.probe_reply_payload",
        max_len=32,
        allow_empty=True,
    )
    osc_cli_version = _text(
        osc_raw.get("verified_with_cli_version", ""),
        f"{where}.osc7501.verified_with_cli_version",
        max_len=32,
        allow_empty=True,
    )
    osc_verified_on = _text(
        osc_raw.get("verified_on", ""),
        f"{where}.osc7501.verified_on",
        max_len=10,
        allow_empty=True,
    )
    osc_note = _text(osc_raw.get("note", ""), f"{where}.osc7501.note", max_len=300, allow_empty=True)
    if source == "user" or kind == "shell":
        osc_status = "unverified"
        osc_payload = ""
    if osc_status == "verified":
        if not osc_payload or _OSC7501_PROBE_PAYLOAD_RE.fullmatch(osc_payload) is None:
            raise CatalogValidationError(f"{where}.osc7501.probe_reply_payload invalido.")
        if not osc_cli_version or _CLI_VERSION_RE.fullmatch(osc_cli_version) is None:
            raise CatalogValidationError(f"{where}.osc7501.verified_with_cli_version invalido.")
        if not osc_verified_on or _DATE_RE.fullmatch(osc_verified_on) is None:
            raise CatalogValidationError(f"{where}.osc7501.verified_on: se esperaba AAAA-MM-DD.")
    osc7501: Dict[str, str] = {
        "status": osc_status,
        "probe_reply_payload": osc_payload,
        "verified_with_cli_version": osc_cli_version,
        "verified_on": osc_verified_on,
        "note": osc_note,
    }

    return CliSpec(
        id=cli_id,
        display_name=_text(raw.get("display_name"), f"{where}.display_name", max_len=60),
        icon=icon,
        kind=kind,
        binary=binary,
        binary_aliases=aliases,
        candidate_paths=candidates,
        launch_flags=flags,
        mcp=mcp,
        resume=resume,
        install=install,
        source=source,
        verified=(source == "repo"),
        shell_env=shell_env,
        osc7501=osc7501,
    )


def _read_json_file(path: Path) -> Any:
    size = path.stat().st_size
    if size > MAX_CATALOG_BYTES:
        raise CatalogValidationError(f"{path.name}: excede {MAX_CATALOG_BYTES} bytes.")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def default_repo_catalog_path() -> Path:
    here = Path(__file__).resolve().with_name("cli_catalog.json")
    if here.is_file():
        return here
    try:
        from src.runtime.paths import get_bundle_dir

        return Path(get_bundle_dir()) / "src" / "adapters" / "terminal" / "cli_catalog.json"
    except Exception:  # pragma: no cover - defensivo
        return here


def default_user_catalog_path() -> Path:
    from src.runtime.paths import get_user_data_dir

    return Path(get_user_data_dir()) / USER_CATALOG_FILENAME


def load_repo_catalog(path: Optional[Path] = None) -> CliCatalog:
    """Carga el catalogo del repo; cualquier defecto es fatal (es un artefacto del build)."""
    target = Path(path) if path is not None else default_repo_catalog_path()
    data = _read_json_file(target)
    if not isinstance(data, dict):
        raise CatalogValidationError("catalogo: se esperaba un objeto.")
    _only_keys(data, ("schema_version", "catalog_version", "clis"), "catalogo")
    if data.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise CatalogValidationError("catalogo: schema_version no soportado.")
    entries = data.get("clis")
    if not isinstance(entries, list) or not entries:
        raise CatalogValidationError("catalogo: 'clis' debe ser una lista no vacia.")
    profiles: Dict[str, CliSpec] = {}
    for raw in entries:
        spec = _parse_entry(raw, source="repo")
        if spec.id in profiles:
            raise CatalogValidationError(f"id duplicado: {spec.id}.")
        profiles[spec.id] = spec
    version = _text(data.get("catalog_version"), "catalog_version", max_len=32)
    return CliCatalog(catalog_version=version, profiles=profiles)


def load_catalog(
    repo_path: Optional[Path] = None,
    user_path: Optional[Path] = None,
    *,
    include_user: bool = True,
) -> CliCatalog:
    """Catalogo del repo + CLIs nuevos del usuario (entradas invalidas se descartan con aviso)."""
    base = load_repo_catalog(repo_path)
    if not include_user:
        return base
    warnings: List[str] = []
    profiles: Dict[str, CliSpec] = dict(base.profiles)
    try:
        target = Path(user_path) if user_path is not None else default_user_catalog_path()
    except Exception as exc:  # pragma: no cover - defensivo
        return CliCatalog(base.catalog_version, profiles, (f"catalogo de usuario no disponible: {exc}",))
    try:
        if not target.is_file():
            return CliCatalog(base.catalog_version, profiles, ())
        data = _read_json_file(target)
        if not isinstance(data, dict):
            raise CatalogValidationError("se esperaba un objeto.")
        _only_keys(data, ("schema_version", "clis"), "catalogo de usuario")
        if data.get("schema_version") != CATALOG_SCHEMA_VERSION:
            raise CatalogValidationError("schema_version no soportado.")
        entries = data.get("clis")
        if not isinstance(entries, list):
            raise CatalogValidationError("'clis' debe ser una lista.")
    except (OSError, ValueError) as exc:
        msg = f"catalogo de usuario ignorado: {exc}"
        logger.warning(msg)
        return CliCatalog(base.catalog_version, profiles, (msg,))

    for index, raw in enumerate(entries[:MAX_USER_ENTRIES]):
        try:
            spec = _parse_entry(raw, source="user")
            if spec.id in profiles:
                raise CatalogValidationError(f"el id {spec.id!r} ya existe; el catalogo de usuario solo agrega CLIs nuevos.")
            profiles[spec.id] = spec
        except CatalogValidationError as exc:
            warnings.append(f"entrada de usuario #{index} descartada: {exc}")
    if len(entries) > MAX_USER_ENTRIES:
        warnings.append(f"se ignoraron entradas de usuario por encima de {MAX_USER_ENTRIES}.")
    for w in warnings:
        logger.warning(w)
    return CliCatalog(base.catalog_version, profiles, tuple(warnings))


# ---------------------------------------------------------------------------
# Resolucion de binarios
# ---------------------------------------------------------------------------


def _version_key(path: str) -> Tuple[int, int, int, int, str, str]:
    for segment in reversed(re.split(r"[\\/]", path)):
        m = _VERSION_SEGMENT_RE.fullmatch(segment)
        if m:
            pre = m.group(4) or ""
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, pre, path)
    return (-1, -1, -1, -1, "", path)


def version_from_path(path: str) -> Optional[str]:
    for segment in reversed(re.split(r"[\\/]", path)):
        m = _VERSION_SEGMENT_RE.fullmatch(segment)
        if m:
            return segment.lstrip("v")
    return None


@dataclass(frozen=True)
class Resolution:
    cli_id: str
    found: bool
    path: Optional[str] = None
    source: Optional[str] = None  # configured | path | local_bin | candidate | shell_env
    configured_path: Optional[str] = None
    configured_path_valid: Optional[bool] = None
    version: Optional[str] = None


@dataclass(frozen=True)
class InstallSelection:
    option: Optional[InstallOption]
    code: str  # OK | NO_INSTALL_METHOD | PREREQUISITE_MISSING
    missing: Tuple[str, ...] = ()
    options: Tuple[Tuple[InstallOption, Tuple[str, ...]], ...] = ()


class CliCatalogService:
    """Catalogo + resolucion de binarios + seleccion de instalador (sin efectos secundarios)."""

    def __init__(
        self,
        settings: Optional[SettingsStore] = None,
        *,
        repo_catalog_path: Optional[Path] = None,
        user_catalog_path: Optional[Path] = None,
        include_user_catalog: bool = True,
        which: Callable[[str], Optional[str]] = shutil.which,
        platform: Optional[str] = None,
        home: Optional[str] = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._settings = settings
        self._repo_path = repo_catalog_path
        self._user_path = user_catalog_path
        self._include_user = include_user_catalog
        self._which = which
        self._platform = platform
        self._home = home
        self._environ = environ

    # -- contexto ---------------------------------------------------------

    @property
    def platform(self) -> str:
        return self._platform or current_platform()

    @property
    def home(self) -> str:
        return self._home if self._home is not None else os.path.expanduser("~")

    @property
    def environ(self) -> Mapping[str, str]:
        return self._environ if self._environ is not None else os.environ

    def which(self, name: str) -> Optional[str]:
        return self._which(name)

    def catalog(self) -> CliCatalog:
        return load_catalog(self._repo_path, self._user_path, include_user=self._include_user)

    def get_spec(self, cli_id: Any) -> Optional[CliSpec]:
        return self.catalog().get(cli_id)

    # -- ajustes persistidos ---------------------------------------------

    @staticmethod
    def _setting_key(cli_id: str) -> str:
        return f"binary_path.{cli_id}"

    def get_configured_path(self, cli_id: str) -> Optional[str]:
        if self._settings is None:
            return None
        try:
            value = self._settings.get_extension_setting(SETTINGS_NAMESPACE, self._setting_key(cli_id), None)
        except Exception as exc:
            logger.debug("No se pudo leer la ruta configurada de %s: %s", cli_id, exc)
            return None
        return value if isinstance(value, str) and value else None

    def check_binary_path(self, cli_id: Any, path: Any) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Valida (sin persistir) la ruta elegida para un CLI; `(ruta_limpia, error)`."""
        spec = self.get_spec(cli_id)
        if spec is None:
            return None, {"success": False, "code": "CLI_PROFILE_UNKNOWN", "message": "CLI desconocido en el catalogo."}
        try:
            clean = validate_user_binary_path(path)
        except CatalogValidationError as exc:
            return None, {"success": False, "code": "INVALID_PATH", "message": str(exc)}
        if not os.path.isfile(clean):
            return None, {"success": False, "code": "PATH_NOT_FOUND", "message": "La ruta no es un archivo existente."}
        if not is_executable_file(clean):
            return None, {"success": False, "code": "PATH_NOT_EXECUTABLE", "message": "El archivo no es ejecutable."}
        return clean, None

    def set_binary_path(self, cli_id: Any, path: Any) -> Dict[str, Any]:
        """Valida y persiste la ruta de un CLI; `path` vacio la borra."""
        spec = self.get_spec(cli_id)
        if spec is None:
            return {"success": False, "code": "CLI_PROFILE_UNKNOWN", "message": "CLI desconocido en el catalogo."}
        if self._settings is None:
            return {"success": False, "code": "SETTINGS_UNAVAILABLE", "message": "No hay almacen de ajustes."}
        if path is None or (isinstance(path, str) and path == ""):
            try:
                self._settings.delete_extension_setting(SETTINGS_NAMESPACE, self._setting_key(spec.id))
            except Exception as exc:
                return {"success": False, "code": "SETTINGS_UNAVAILABLE", "message": str(exc)}
            return {"success": True, "code": "OK", "cli_id": spec.id, "configured_path": None,
                    "message": "Ruta configurada eliminada."}
        clean, error = self.check_binary_path(spec.id, path)
        if error is not None or clean is None:
            return error or {"success": False, "code": "INVALID_PATH", "message": "Ruta invalida."}
        try:
            self._settings.set_extension_setting(SETTINGS_NAMESPACE, self._setting_key(spec.id), clean)
        except Exception as exc:
            return {"success": False, "code": "SETTINGS_UNAVAILABLE", "message": str(exc)}
        return {"success": True, "code": "OK", "cli_id": spec.id, "configured_path": clean,
                "message": "Ruta guardada."}

    # -- resolucion -------------------------------------------------------

    def _expand(self, pattern: str) -> Optional[str]:
        text = pattern
        if text.startswith("~/"):
            text = self.home.rstrip("/\\") + "/" + text[2:]

        missing: List[str] = []

        def _sub(m: "re.Match[str]") -> str:
            val = self.environ.get(m.group(1))
            if not val:
                missing.append(m.group(1))
                return ""
            return val

        text = _ENV_VAR_IN_PATH_RE.sub(_sub, text)
        return None if missing else text

    def _candidate_matches(self, spec: CliSpec) -> List[str]:
        found: List[str] = []
        for pattern in spec.candidate_paths.get(self.platform, ()):
            expanded = self._expand(pattern)
            if expanded is None:
                continue
            if "*" in expanded:
                matches = glob.glob(expanded)[:MAX_GLOB_MATCHES]
                matches.sort(key=_version_key, reverse=True)
            else:
                matches = [expanded]
            for m in matches:
                if os.path.isabs(m) and is_executable_file(m) and m not in found:
                    found.append(m)
        # Entre patrones distintos gana la version semantica mas reciente.
        found.sort(key=_version_key, reverse=True)
        return found

    def resolve(self, cli_id: Any) -> Resolution:
        spec = self.get_spec(cli_id)
        if spec is None:
            return Resolution(cli_id=str(cli_id), found=False)
        return self._resolve_spec(spec)

    def _resolve_spec(self, spec: CliSpec) -> Resolution:
        configured = self.get_configured_path(spec.id)
        configured_valid: Optional[bool] = None
        if configured is not None:
            try:
                validate_user_binary_path(configured)
                configured_valid = is_executable_file(configured)
            except CatalogValidationError:
                configured_valid = False
            if configured_valid:
                return Resolution(spec.id, True, configured, "configured", configured, True, version_from_path(configured))

        def _done(path: str, source: str) -> Resolution:
            return Resolution(spec.id, True, path, source, configured, configured_valid, version_from_path(path))

        is_win = self.platform == "win32"
        # En Windows la shell por defecto es %COMSPEC% (cmd.exe); `$SHELL` no existe fuera de Git Bash.
        shell_env_name = "COMSPEC" if (spec.kind == "shell" and is_win) else spec.shell_env
        if spec.kind == "shell" and shell_env_name:
            env_shell = self.environ.get(shell_env_name)
            if (
                env_shell
                and not has_control_chars(env_shell)
                and os.path.isabs(env_shell)
                and ".." not in re.split(r"[\\/]", env_shell)
                and is_executable_file(env_shell)
            ):
                return _done(env_shell, "shell_env")

        # La shell de Windows no se busca en PATH por su nombre POSIX (`sh` de Git Bash no es la shell del sistema).
        if not (spec.kind == "shell" and is_win):
            for name in spec.binary_names:
                hit = self._which(name)
                if hit and is_win and _is_cwd_hit(hit):
                    continue  # shutil.which en Windows prueba antes el directorio actual: no se ejecuta lo que ahi haya
                if hit and os.path.isabs(hit) and is_executable_file(hit):
                    return _done(hit, "path")

            local_bin = self.home.rstrip("/\\") + "/.local/bin"
            for name in spec.binary_names:
                for variant in _executable_variants(name, is_win):
                    candidate = f"{local_bin}/{variant}"
                    if is_executable_file(candidate):
                        return _done(candidate, "local_bin")

        matches = self._candidate_matches(spec)
        if matches:
            return _done(matches[0], "candidate")
        return Resolution(spec.id, False, None, None, configured, configured_valid, None)

    # -- lanzamiento ------------------------------------------------------

    def build_launch(self, cli_id: Any) -> Tuple[Optional[List[str]], Resolution, Optional[CliSpec]]:
        """`(argv, resolucion, spec)`; `argv` es None si el perfil es desconocido o no hay binario."""
        spec = self.get_spec(cli_id)
        if spec is None:
            return None, Resolution(cli_id=str(cli_id), found=False), None
        res = self._resolve_spec(spec)
        if not res.found or res.path is None:
            return None, res, spec
        return [res.path, *spec.launch_flags], res, spec

    # -- instalacion ------------------------------------------------------

    def select_install(self, spec: CliSpec) -> InstallSelection:
        options = spec.install.get(self.platform, ())
        if not options:
            return InstallSelection(None, "NO_INSTALL_METHOD")
        annotated = tuple(
            (opt, tuple(tool for tool in opt.requires if not self._which(tool))) for opt in options
        )
        satisfied = [(o, m) for o, m in annotated if not m]
        # Gestor de paquetes antes que script descargado; dentro de cada grupo, orden del catalogo.
        satisfied.sort(key=lambda pair: pair[0].downloads_script)
        if satisfied:
            return InstallSelection(satisfied[0][0], "OK", (), annotated)
        first = sorted(annotated, key=lambda pair: pair[0].downloads_script)[0]
        return InstallSelection(first[0], "PREREQUISITE_MISSING", first[1], annotated)

    def install_hint(self, cli_id: Any) -> Dict[str, Any]:
        """Datos para adjuntar a `CLI_BINARY_NOT_FOUND` (`install_available`, comando propuesto)."""
        spec = self.get_spec(cli_id)
        if spec is None or spec.kind == "shell":
            return {"install_available": False, "install_command": None}
        sel = self.select_install(spec)
        opt = sel.option
        installable = bool(opt and opt.verified and spec.verified and spec.source == "repo"
                           and self.platform != "win32")
        return {
            "install_available": installable and sel.code == "OK",
            "install_command": opt.command if opt else None,
            "install_method": opt.method if opt else None,
            "install_verified": bool(opt and opt.verified),
            "install_missing_requirements": list(sel.missing),
            "install_downloads_script": bool(opt and opt.downloads_script),
        }

    # -- vista para la UI -------------------------------------------------

    def describe(self) -> Dict[str, Any]:
        cat = self.catalog()
        clis: List[Dict[str, Any]] = []
        for spec in cat.profiles.values():
            res = self._resolve_spec(spec)
            sel = self.select_install(spec) if spec.kind != "shell" else InstallSelection(None, "NO_INSTALL_METHOD")
            hint = self.install_hint(spec.id)
            clis.append(
                {
                    "id": spec.id,
                    "display_name": spec.display_name,
                    "icon": spec.icon,
                    "kind": spec.kind,
                    "source": spec.source,
                    "verified": spec.verified,
                    "installed": res.found,
                    "resolved_path": res.path,
                    "resolution_source": res.source,
                    "configured_path": res.configured_path,
                    "configured_path_valid": res.configured_path_valid,
                    "version": res.version,
                    "launchable": res.found,
                    "resume": {"argv": list(spec.resume.get("argv", [])), "verified": bool(spec.resume.get("verified"))},
                    "mcp": dict(spec.mcp),
                    "install": {
                        "available": hint["install_available"],
                        "code": sel.code,
                        "command": hint["install_command"],
                        "method": hint.get("install_method"),
                        "verified": hint.get("install_verified", False),
                        "downloads_script": hint.get("install_downloads_script", False),
                        "missing_requirements": hint.get("install_missing_requirements", []),
                        "source_url": sel.option.source_url if sel.option else None,
                        "note": sel.option.note if sel.option else "",
                        "options": [
                            {
                                "method": o.method,
                                "command": o.command,
                                "verified": o.verified,
                                "downloads_script": o.downloads_script,
                                "missing_requirements": list(m),
                                "source_url": o.source_url,
                            }
                            for o, m in sel.options
                        ],
                    },
                }
            )
        return {"catalog_version": cat.catalog_version, "platform": self.platform,
                "clis": clis, "warnings": list(cat.warnings)}


def legacy_profiles(catalog: CliCatalog, shell_default: str) -> Dict[str, List[str]]:
    """Forma historica de `CLI_PROFILES`: `{id: [binario]}` (la shell sale de `$SHELL`)."""
    out: Dict[str, List[str]] = {}
    for spec in catalog.profiles.values():
        out[spec.id] = [shell_default] if spec.kind == "shell" else [spec.binary]
    return out
