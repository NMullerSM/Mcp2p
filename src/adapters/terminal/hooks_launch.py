"""Aumentador de lanzamiento para configuración por sesión de hooks del CLI (v2.0.2, WP-6a)."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any, Dict

from src.adapters.terminal.hook_configs import (
    claude_settings,
    codex_notify_args,
    hook_command_argv,
    opencode_config_with_plugin,
    opencode_plugin_source,
)
from src.adapters.terminal.mcp_injection import (
    CONFIG_FILENAME,
    agy_hooks_registered,
    ensure_agy_hooks_registered,
    has_global_registration_consent,
)

if TYPE_CHECKING:
    from src.adapters.terminal.launch_augment import LaunchAddition, LaunchContext


def augment(ctx: "LaunchContext") -> "LaunchAddition":
    """Devuelve flags, entorno y archivos de hooks cuando `activity` está verificada para la plataforma."""
    from src.adapters.terminal.launch_augment import LaunchAddition

    verified = bool(ctx.spec is not None and ctx.spec.capability_verified("activity", ctx.platform))
    services = ctx.services
    hook_argv = getattr(services, "hook_argv", None) if services is not None else None
    if not verified:
        if hook_argv is None:
            return LaunchAddition()
        return LaunchAddition(meta={"hooks": "none"})

    if not callable(hook_argv):
        frozen = bool(getattr(sys, "frozen", False))
        hook_argv = lambda ev: hook_command_argv(ev, frozen=frozen, executable=sys.executable)  # noqa: E731

    activity_cfg = getattr(ctx.spec, "activity", None) or {}
    hook_install = str(activity_cfg.get("hook_install") or "none")

    if hook_install == "claude_settings":
        cfg = claude_settings(hook_argv, ctx.platform)
        target = ctx.session_file("hooks.json")
        raw = (json.dumps(cfg, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        return LaunchAddition(
            argv=("--settings", str(target)),
            files={"hooks.json": raw},
            meta={"hooks": "installed"},
        )

    if hook_install == "agy_settings":
        if has_global_registration_consent():
            res = ensure_agy_hooks_registered(hook_argv, ctx.platform)
            return LaunchAddition(meta={"hooks": "installed" if res.get("registered") else "none"})
        if agy_hooks_registered(hook_argv, ctx.platform):
            return LaunchAddition(meta={"hooks": "installed"})
        return LaunchAddition(meta={"hooks": "needs_consent"})

    if hook_install == "codex_notify":
        argv = tuple(codex_notify_args(hook_argv))
        return LaunchAddition(
            argv=argv,
            meta={"hooks": "installed"},
        )

    if hook_install == "opencode_plugin":
        mcp_cfg: Dict[str, Any] = {}
        mcp_file = ctx.session_file(CONFIG_FILENAME)
        if mcp_file.is_file() and not mcp_file.is_symlink():
            try:
                loaded = json.loads(mcp_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    mcp_cfg = loaded
            except (OSError, ValueError):
                mcp_cfg = {}
        plugin_path = ctx.session_file("mcp2p_plugin.js")
        opencode_path = ctx.session_file("opencode.json")
        merged = opencode_config_with_plugin(mcp_cfg, plugin_path)
        files = {
            "mcp2p_plugin.js": opencode_plugin_source().encode("utf-8"),
            "opencode.json": (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        }
        return LaunchAddition(
            env={"OPENCODE_CONFIG": str(opencode_path)},
            files=files,
            meta={"hooks": "installed"},
        )

    return LaunchAddition(meta={"hooks": "none"})
