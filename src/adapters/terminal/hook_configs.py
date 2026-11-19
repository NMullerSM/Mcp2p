"""Generadores puros de configuracion de *hooks* por sesion para CLIs de agente (v2.0.2 WP-5a).

Funciones 100% puras y sin I/O ni estado global:
- `hook_command_argv`: construye el comando del cliente mínimo (`--hook-post` congelado o `hook_client.py` en fuentes).
- `command_line`: cita argumentos con `shlex.join` (POSIX) o `subprocess.list2cmdline` (`win32`).
- `claude_settings`: estructura `--settings` de Claude Code con `Notification` y sus *matchers*.
- `agy_settings`: estructura tentativa para `agy --settings` (forma POR VERIFICAR en WP-5b M1).
- `codex_notify_args`: argumentos `-c notify=[...]` con cadenas literales TOML (POR VERIFICAR en WP-5b M1).
- `opencode_plugin_source` / `opencode_config_with_plugin`: plugin JS para OpenCode y fusión no destructiva con `mcp`.
"""

from __future__ import annotations

import copy
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

_EVENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,47}$")
_REPO_ROOT = Path(__file__).resolve().parents[3]

CLAUDE_SIMPLE_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "PermissionRequest",
    "PostToolUse",
)
CLAUDE_NOTIFICATION_MATCHERS = ("permission_prompt", "idle_prompt")
AGY_HOOK_EVENTS = ("PreInvocation", "PostInvocation", "PreToolUse", "PostToolUse", "Stop")


def _validate_event(event: str) -> str:
    if not isinstance(event, str) or _EVENT_RE.fullmatch(event) is None:
        raise ValueError(f"Nombre de evento de hook inválido: {event!r}")
    return event


def hook_command_argv(
    event: str,
    *,
    frozen: bool,
    executable: str,
    repo_root: Optional[Path] = None,
) -> List[str]:
    """Devuelve `argv` para invocar el cliente de *hooks* con `event`.

    - Congelado (`frozen=True`): `[executable, "--hook-post", event]`
    - Fuentes (`frozen=False`): `[executable, str(repo_root / "src/app/hook_client.py"), event]`
    """
    clean_event = _validate_event(event)
    if not isinstance(executable, str) or not executable.strip():
        raise ValueError("executable debe ser una cadena no vacía.")
    if frozen:
        return [executable, "--hook-post", clean_event]
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    script_path = root / "src" / "app" / "hook_client.py"
    return [executable, str(script_path), clean_event]


def command_line(argv: Sequence[str], platform: str) -> str:
    """Serializa `argv` como línea de comando segura para `platform` (`win32` o POSIX)."""
    items = [str(part) for part in argv]
    if platform == "win32":
        return subprocess.list2cmdline(items)
    return shlex.join(items)


def _command_entry(command: str, *, timeout: int = 5) -> Dict[str, Any]:
    return {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}


def claude_settings(hook_argv: Callable[[str], List[str]], platform: str) -> Dict[str, Any]:
    """Genera el JSON para `claude --settings <archivo>` sin tocar `~/.claude/settings.json`."""
    hooks: Dict[str, List[Dict[str, Any]]] = {}
    for ev in CLAUDE_SIMPLE_EVENTS:
        cmd = command_line(hook_argv(f"claude.{ev}"), platform)
        hooks[ev] = [_command_entry(cmd, timeout=5)]

    notifications: List[Dict[str, Any]] = []
    for matcher in CLAUDE_NOTIFICATION_MATCHERS:
        cmd = command_line(hook_argv(f"claude.Notification.{matcher}"), platform)
        notifications.append(
            {
                "matcher": matcher,
                "hooks": [{"type": "command", "command": cmd, "timeout": 5}],
            }
        )
    hooks["Notification"] = notifications
    return {"hooks": hooks}


def agy_settings(hook_argv: Callable[[str], List[str]], platform: str) -> Dict[str, Any]:
    """Genera la configuración tentativa para `agy --settings <archivo>` (forma POR VERIFICAR en WP-5b M1)."""
    hooks: Dict[str, List[Dict[str, Any]]] = {}
    for ev in AGY_HOOK_EVENTS:
        cmd = command_line(hook_argv(f"agy.{ev}"), platform)
        hooks[ev] = [_command_entry(cmd, timeout=5)]
    return {"hooks": hooks}


def _toml_literal_string(value: str) -> str:
    if any(ch in value for ch in ("\r", "\n", "\x00")):
        raise ValueError(f"El argumento TOML contiene caracteres de control no permitidos: {value!r}")
    if "'" not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def codex_notify_args(hook_argv: Callable[[str], List[str]]) -> List[str]:
    """Genera `["-c", "notify=[...]"]` con cadenas literales TOML (comillas simples) — POR VERIFICAR (M1 codex)."""
    argv = [str(part) for part in hook_argv("codex.notify")]
    rendered = ", ".join(_toml_literal_string(part) for part in argv)
    return ["-c", f"notify=[{rendered}]"]


def opencode_plugin_source() -> str:
    """Plugin JS para OpenCode que reenvía `session.status`, `session.idle` y `permission.*` al Hub por loopback."""
    return (
        "// Plugin de hooks por sesion generado por MCP2P (v2.0.2 WP-5a).\n"
        "// Lee MCP2P_HUB_URL y MCP2P_SESSION_TOKEN del entorno en tiempo de ejecucion; sin secretos en disco.\n"
        "const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1', '[::1]']);\n"
        "\n"
        "function resolveHooksUrl(rawHubUrl) {\n"
        "  if (!rawHubUrl) return null;\n"
        "  try {\n"
        "    const parsed = new URL(rawHubUrl);\n"
        "    if (parsed.protocol !== 'http:') return null;\n"
        "    if (!LOOPBACK_HOSTS.has(parsed.hostname)) return null;\n"
        "    if (!parsed.port) return null;\n"
        "    const host = parsed.hostname.includes(':') && !parsed.hostname.startsWith('[')\n"
        "      ? `[${parsed.hostname}]`\n"
        "      : parsed.hostname;\n"
        "    return `http://${host}:${parsed.port}/hooks/v1/opencode.event`;\n"
        "  } catch {\n"
        "    return null;\n"
        "  }\n"
        "}\n"
        "\n"
        "function shouldForward(eventType) {\n"
        "  if (typeof eventType !== 'string' || !eventType) return false;\n"
        "  return (\n"
        "    eventType === 'session.status'\n"
        "    || eventType === 'session.idle'\n"
        "    || eventType === 'session.error'\n"
        "    || eventType.startsWith('permission.')\n"
        "  );\n"
        "}\n"
        "\n"
        "export const Mcp2pHooksPlugin = async () => {\n"
        "  return {\n"
        "    event: async ({ event }) => {\n"
        "      try {\n"
        "        const eventType = event && typeof event.type === 'string' ? event.type : '';\n"
        "        if (!shouldForward(eventType)) return;\n"
        "        const token = (process.env.MCP2P_SESSION_TOKEN || '').trim();\n"
        "        if (!token) return;\n"
        "        const url = resolveHooksUrl((process.env.MCP2P_HUB_URL || '').trim());\n"
        "        if (!url) return;\n"
        "        const signal = typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'\n"
        "          ? AbortSignal.timeout(2000)\n"
        "          : undefined;\n"
        "        await fetch(url, {\n"
        "          method: 'POST',\n"
        "          headers: {\n"
        "            'Content-Type': 'application/json',\n"
        "            'Authorization': `Bearer ${token}`,\n"
        "          },\n"
        "          body: JSON.stringify({ type: eventType, event }),\n"
        "          signal,\n"
        "        });\n"
        "      } catch {\n"
        "        // Nunca interrumpir al CLI ni escribir en stdout.\n"
        "      }\n"
        "    },\n"
        "  };\n"
        "};\n"
        "\n"
        "export default Mcp2pHooksPlugin;\n"
    )


def opencode_config_with_plugin(mcp_config: Mapping[str, Any], plugin_path: Path) -> Dict[str, Any]:
    """Combina la configuracion MCP existente de OpenCode con `plugin_path` sin mutar `mcp_config`."""
    merged: Dict[str, Any] = copy.deepcopy(dict(mcp_config))
    existing = merged.get("plugin")
    plugins: List[str] = list(existing) if isinstance(existing, list) else []
    plugin_entry = str(plugin_path)
    if plugin_entry not in plugins:
        plugins.append(plugin_entry)
    merged["plugin"] = plugins
    return merged
