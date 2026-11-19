"""Aumentador de lanzamiento para rol y mandato del panel (v2.0.2, WP-3a)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Mapping, Optional

from src.core.domain.roles import ROLE_BRIEFS_ES, PaneRole, normalize_role, sanitize_mandate

if TYPE_CHECKING:
    from src.adapters.terminal.cli_catalog import CliSpec
    from src.adapters.terminal.launch_augment import LaunchAddition, LaunchContext


def role_launch_verified(spec: Optional["CliSpec"], platform: str) -> bool:
    """True si `spec` tiene `role_launch` verificado en el catálogo del repo para `platform`."""
    return bool(spec is not None and spec.capability_verified("role_launch", platform))


def readonly_mode_for_role(role: PaneRole, spec: Optional["CliSpec"], platform: str) -> str:
    """Devuelve `'enforced' | 'advisory' | 'none'` para el rol dado y el CLI del panel."""
    if role != PaneRole.AUDITOR:
        return "none"
    if role_launch_verified(spec, platform) and bool(spec.role_launch.get("readonly_argv")):  # type: ignore[union-attr]
        return "enforced"
    return "advisory"


def augment(ctx: "LaunchContext") -> "LaunchAddition":
    """Devuelve flags y `role.md` de rol/mandato cuando `role_launch` está verificado para la plataforma."""
    from src.adapters.terminal.launch_augment import LaunchAddition

    verified = role_launch_verified(ctx.spec, ctx.platform)
    services = ctx.services
    identity_repo = getattr(services, "identity_repo", None) if services is not None else None
    if identity_repo is None and not verified:
        return LaunchAddition()

    project_uuid = str((ctx.request or {}).get("project_uuid") or "")
    identity: Optional[Mapping[str, object]] = None
    if identity_repo is not None:
        get_fn = getattr(identity_repo, "get_pane_identity", None)
        if callable(get_fn):
            raw = get_fn(project_uuid, ctx.pane_slot)
            if isinstance(raw, Mapping):
                identity = raw

    role = normalize_role(identity.get("role") if identity is not None else None)
    mandate = sanitize_mandate(identity.get("mandate") if identity is not None else "")
    readonly = readonly_mode_for_role(role, ctx.spec, ctx.platform)

    if not verified:
        return LaunchAddition(meta={"role": role.value, "readonly": readonly})

    argv: List[str] = []
    files: Dict[str, bytes] = {}
    sys_argv = tuple(ctx.spec.role_launch.get("system_prompt_file_argv") or ())
    if sys_argv:
        role_path = ctx.session_file("role.md")
        brief = ROLE_BRIEFS_ES[role]
        if mandate:
            content = f"{brief}\n\nMandato del humano (texto literal):\n<<<\n{mandate}\n>>>\n"
        else:
            content = f"{brief}\n"
        files["role.md"] = content.encode("utf-8")
        argv.extend(str(arg).replace("{role_file}", str(role_path)) for arg in sys_argv)

    if role == PaneRole.AUDITOR:
        ro_argv = tuple(ctx.spec.role_launch.get("readonly_argv") or ())
        if ro_argv:
            argv.extend(str(arg) for arg in ro_argv)

    return LaunchAddition(
        argv=tuple(argv),
        files=files,
        meta={"role": role.value, "readonly": readonly},
    )
