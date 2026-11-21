"""Medición automática de CLIs (WP-5d): dominio puro, sin E/S.

Un intento de medición lanza el CLI sin interfaz, pega un mensaje de prueba, lo envía con Enter y registra los avisos
(`hooks`) que el CLI manda al Hub. Aquí se decide, a partir de lo observado, si la entrega directa es segura para ese
CLI en ese equipo y qué bloques `activity` y `delivery` del catálogo describen lo medido.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = 1
MEASURABLE_CLIS: Tuple[str, ...] = ("claude", "codex", "opencode", "antigravity")
HOOK_INSTALL_BY_CLI: Mapping[str, str] = {
    "claude": "claude_settings",
    "antigravity": "agy_settings",
    "codex": "codex_notify",
    "opencode": "opencode_plugin",
}
PASTE_MODES: Tuple[str, ...] = ("bracketed", "single_line")
DEFAULT_SUBMIT_DELAY_MS = 300
DEFAULT_MAX_INJECT_BYTES = 2048

EVENT_ALIASES: Mapping[str, Tuple[str, ...]] = {
    "session_started": ("session_started", "claude.SessionStart"),
    "turn_started": ("turn_started", "claude.UserPromptSubmit", "agy.PreInvocation"),
    "turn_ended": ("turn_ended", "claude.Stop", "agy.Stop", "codex.notify", "session.idle"),
    "permission_requested": (
        "permission_requested",
        "claude.PermissionRequest",
        "claude.Notification.permission_prompt",
        "permission.asked",
        "permission.updated",
    ),
    "idle_notice": ("idle_notice", "claude.Notification.idle_prompt", "session.idle"),
    "tool_used": ("tool_used", "claude.PostToolUse", "agy.PostToolUse"),
    "subagent_ended": ("subagent_ended", "claude.SubagentStop"),
}

_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,31}")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]")

REASONS_ES: Mapping[str, str] = {
    "SPAWN_FAILED": "No se pudo abrir el CLI en una terminal de prueba.",
    "NO_HOOK_EVENTS": (
        "El CLI no envió ningún aviso. Abre el CLI una vez, inicia sesión y acepta los avisos de primera vez, "
        "y vuelve a medir."
    ),
    "NO_TURN_END": (
        "El CLI no avisó del fin del turno. Puede estar pidiendo un permiso o una confirmación, o no tener sesión iniciada."
    ),
    "NEEDS_PERMISSION": "El CLI se quedó pidiendo un permiso durante la prueba; ábrelo y vuelve a medir.",
    "PASTE_NOT_SUPPORTED": "El CLI no entiende el pegado entre corchetes ni el modo de una sola línea.",
    "PREMATURE_SUBMIT": "El CLI envió el texto antes del Enter: no se puede pegar un mensaje de varias líneas.",
    "CANCELLED": "Medición cancelada.",
    "TIMEOUT": "La prueba tardó demasiado y se cortó.",
    "UNSUPPORTED_CLI": "Este CLI no admite la medición automática.",
    "CLI_NOT_INSTALLED": "El CLI no está instalado.",
}


@dataclass(frozen=True)
class HookEvent:
    """Un aviso recibido del CLI (nombre crudo de la ruta, tipo del cuerpo y campos candidatos a id de sesión)."""

    event: str
    t_ms: int
    payload_type: str = ""
    session_fields: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Attempt:
    """Todo lo observado en un intento con un modo de pegado."""

    paste_mode: str
    spawn_ok: bool
    events: Tuple[HookEvent, ...] = ()
    enter_at_ms: int = 0
    screen_after_paste: str = ""
    cancelled: bool = False
    timed_out: bool = False


@dataclass(frozen=True)
class AttemptResult:
    ok: bool
    reason_code: Optional[str]
    mapped_events: Mapping[str, str] = field(default_factory=dict)
    confirm_event: str = ""
    session_id_field: str = ""
    permission_seen: bool = False


def strip_ansi(text: str) -> str:
    """Quita secuencias de escape de una captura de pantalla para buscar texto en ella."""
    return _ANSI_RE.sub("", text or "")


def event_key(ev: HookEvent) -> str:
    """Clave cruda tal como la normaliza el endpoint de hooks (`codex.notify.<tipo>`, `opencode.event.<tipo>`)."""
    if ev.event in ("codex.notify", "opencode.event") and ev.payload_type:
        return f"{ev.event}.{ev.payload_type}"
    return ev.event


def normalize_event(ev: HookEvent) -> Optional[str]:
    for norm, aliases in EVENT_ALIASES.items():
        if ev.event in aliases or (ev.payload_type and ev.payload_type in aliases):
            return norm
    if ev.payload_type.startswith("permission."):
        return "permission_requested"
    return None


def infer_normalized_events(events: Sequence[HookEvent]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for ev in events:
        key = event_key(ev)
        norm = normalize_event(ev)
        if key and norm is not None:
            mapping.setdefault(key, norm)
    return mapping


def evaluate_attempt(attempt: Attempt) -> AttemptResult:
    """Decide si este intento prueba que el CLI avisa de sus turnos y acepta el pegado + Enter."""
    if attempt.cancelled:
        return AttemptResult(False, "CANCELLED")
    if not attempt.spawn_ok:
        return AttemptResult(False, "SPAWN_FAILED")
    events = sorted(attempt.events, key=lambda e: e.t_ms)
    if not events:
        return AttemptResult(False, "NO_HOOK_EVENTS")
    normalized = [(ev, normalize_event(ev)) for ev in events]
    mapped = infer_normalized_events(events)
    session_fields = sorted({name for ev in events for name in ev.session_fields})
    session_field = session_fields[0] if session_fields else ""

    if attempt.paste_mode == "bracketed" and "200~" in strip_ansi(attempt.screen_after_paste):
        return AttemptResult(False, "PASTE_NOT_SUPPORTED", mapped)
    if any(norm == "turn_started" and ev.t_ms < attempt.enter_at_ms for ev, norm in normalized):
        return AttemptResult(False, "PREMATURE_SUBMIT", mapped)

    after_enter = [(ev, norm) for ev, norm in normalized if ev.t_ms >= attempt.enter_at_ms]
    permission = any(norm == "permission_requested" for _ev, norm in after_enter)
    ended = any(norm == "turn_ended" for _ev, norm in after_enter)
    if not ended:
        if permission:
            return AttemptResult(False, "NEEDS_PERMISSION", mapped, permission_seen=True)
        return AttemptResult(False, "TIMEOUT" if attempt.timed_out else "NO_TURN_END", mapped)
    started = any(norm == "turn_started" for _ev, norm in after_enter)
    return AttemptResult(
        True,
        None,
        mapped_events=mapped,
        confirm_event="turn_started" if started else "",
        session_id_field=session_field,
        permission_seen=permission,
    )


def clean_version(raw: Optional[str]) -> str:
    value = (raw or "").strip().lstrip("v")
    return value if _VERSION_RE.fullmatch(value) else ""


def build_record(
    *,
    cli_id: str,
    platform: str,
    today: str,
    cli_version: Optional[str],
    binary_path: str,
    binary_mtime_ns: int,
    binary_size: int,
    result: AttemptResult,
    paste_mode: str,
) -> Dict[str, Any]:
    """Registro persistible de una medición correcta: bloques de catálogo + sello del programa medido."""
    version = clean_version(cli_version)
    return {
        "schema": SCHEMA_VERSION,
        "cli_id": cli_id,
        "platform": platform,
        "measured_on": today,
        "cli_version": version,
        "binary": {"path": binary_path, "mtime_ns": int(binary_mtime_ns), "size": int(binary_size)},
        "activity": {
            "status": "verified",
            "platforms": [platform],
            "hook_install": HOOK_INSTALL_BY_CLI.get(cli_id, "none"),
            "events": dict(result.mapped_events),
            "session_id_field": result.session_id_field,
            "verified_with_cli_version": version,
            "verified_on": today,
            "note": "Medido automáticamente en este equipo.",
        },
        "delivery": {
            "status": "verified",
            "platforms": [platform],
            "paste_mode": paste_mode,
            "submit_delay_ms": DEFAULT_SUBMIT_DELAY_MS,
            "max_inject_bytes": DEFAULT_MAX_INJECT_BYTES,
            "confirm_event": result.confirm_event,
            "verified_with_cli_version": version,
            "verified_on": today,
            "note": "Medido automáticamente en este equipo.",
        },
    }


def reason_text(code: Optional[str]) -> str:
    return REASONS_ES.get(code or "", "La medición no pudo completarse.")


def choose_best(results: Sequence[Tuple[str, AttemptResult]]) -> Tuple[Optional[str], Optional[AttemptResult]]:
    """De los intentos hechos (en orden), el primero válido; si ninguno, el último fallo (el más informativo)."""
    for mode, res in results:
        if res.ok:
            return mode, res
    if results:
        return results[-1][0], results[-1][1]
    return None, None


def prompt_lines(token: str) -> List[str]:
    """Mensaje de prueba: dos líneas cortas (comprueba el pegado de varias líneas) y una respuesta de una palabra."""
    return [f"Prueba de MCP2P {token}.", "Responde solo con la palabra LISTO."]
