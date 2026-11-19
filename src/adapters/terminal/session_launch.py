"""Aumentador de lanzamiento para asignación o reanudación por identificador de sesión del CLI (v2.0.2, WP-4a)."""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.adapters.terminal.launch_augment import LaunchAddition, LaunchContext

_CLI_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _validate_cli_session_id(value: Any) -> str:
    if not isinstance(value, str) or value.startswith("-") or _CLI_SESSION_ID_RE.fullmatch(value) is None:
        raise ValueError("cli_session_id inválido: debe cumplir ^[A-Za-z0-9_-]{1,128}$ sin '-' inicial.")
    return value


def augment(ctx: "LaunchContext") -> "LaunchAddition":
    """Devuelve flags de asignación (`assign_argv`) o reanudación por id (`resume_by_id_argv`) cuando `session` está verificado."""
    from src.adapters.terminal.launch_augment import LaunchAddition

    req = ctx.request or {}
    resume_mode = req.get("resume_mode")
    raw_sid = req.get("cli_session_id")

    if resume_mode == "by_id" or raw_sid is not None:
        validated_sid = _validate_cli_session_id(raw_sid)
    else:
        validated_sid = None

    if ctx.spec is None or not ctx.spec.capability_verified("session", ctx.platform):
        return LaunchAddition()

    session_cfg = ctx.spec.session or {}

    if resume_mode == "by_id":
        assert validated_sid is not None
        resume_by_id_argv = tuple(session_cfg.get("resume_by_id_argv") or ())
        if not resume_by_id_argv:
            return LaunchAddition()
        argv = tuple(str(arg).replace("{cli_session_id}", validated_sid) for arg in resume_by_id_argv)
        return LaunchAddition(argv=argv, meta={"cli_session_id": validated_sid})

    if resume_mode in (None, "none", "fresh") and not bool(req.get("resume")):
        assign_argv = tuple(session_cfg.get("assign_argv") or ())
        if not assign_argv:
            return LaunchAddition()
        uid = str(uuid.uuid4())
        argv = tuple(str(arg).replace("{cli_session_id}", uid) for arg in assign_argv)
        return LaunchAddition(
            argv=argv,
            meta={"cli_session_id": uid, "cli_session_source": "assigned"},
        )

    return LaunchAddition()
