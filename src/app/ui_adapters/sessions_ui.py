"""Adaptador UI del puente para persistencia, restauración y relanzamiento de sesiones de terminal (v2.0.2, WP-4a)."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.adapters.terminal.cli_catalog import current_platform
from src.app.ui_adapters import _BaseUIAdapter
from src.app.ui_adapters.pane_lifecycle import PaneSpawnedEvent
from src.core.services.terminal_session_service import (
    VALID_RESTORE_POLICIES,
    RestorePlan,
    TerminalSessionService,
    format_warning,
)

logger = logging.getLogger("mcp2p.app.ui_adapters.sessions")

_RESTORE_STAGGER_S = 0.75
_EARLY_EXIT_WAIT_S = 5.0
_EARLY_EXIT_POLL_S = 0.25


def _fail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "code": code, "message": message, **extra}


def _extract_event_role(bridge: Any, ev: PaneSpawnedEvent) -> str:
    role = (ev.request or {}).get("role") or (ev.launch_meta or {}).get("role") or ""
    if role:
        return str(role).strip().lower()
    try:
        reg = bridge.extensions_ui._get_pane_registry()
        rec = reg.get_pane(ev.pane_id)
        if rec is not None and getattr(rec, "role", None):
            return str(rec.role).strip().lower()
    except Exception:
        pass
    return ""


def wire_sessions(bridge: Any) -> None:
    """Inicializa `TerminalSessionService` en el kernel y suscribe los eventos de ciclo de vida de paneles."""
    kernel = getattr(bridge, "core_kernel", None)
    extensions = getattr(bridge, "extensions_ui", None)
    if kernel is None or extensions is None:
        return

    from src.core.factory import ApplicationFactory

    service = ApplicationFactory.terminal_session_service(
        kernel,
        catalog_service=extensions._catalog_service,
    )
    lifecycle = getattr(extensions, "pane_lifecycle", None)
    if lifecycle is None:
        return

    def _on_spawned(ev: PaneSpawnedEvent) -> None:
        if _extract_event_role(bridge, ev) == "installer":
            return
        meta = ev.launch_meta or {}
        cli_session_id = meta.get("cli_session_id")
        cli_session_source = meta.get("cli_session_source", "none")
        service.record_spawn(
            project_uuid=ev.project_uuid,
            pane_slot=ev.pane_id,
            cli_id=ev.cli_profile,
            cwd=ev.cwd,
            cli_session_id=cli_session_id,
            cli_session_source=cli_session_source,
        )

    def _on_closed_by_user(project_uuid: str, pane_id: str) -> None:
        service.record_closed_by_user(project_uuid, pane_id)

    def _on_app_closing(alive_slots: Mapping[str, Sequence[str]]) -> None:
        service.mark_app_closing(alive_slots)

    lifecycle.on_spawned(_on_spawned)
    lifecycle.on_closed_by_user(_on_closed_by_user)
    lifecycle.on_app_closing(_on_app_closing)


class SessionsUIAdapter(_BaseUIAdapter):
    """Expone a la GUI la consulta/ejecución de restauración, política y relanzamiento de paneles."""

    def _service(self) -> Optional[TerminalSessionService]:
        kernel = self.core_kernel
        if kernel is None:
            return None
        from src.core.factory import ApplicationFactory

        extensions = getattr(self._bridge, "extensions_ui", None)
        cat_getter = getattr(extensions, "_catalog_service", None)
        return ApplicationFactory.terminal_session_service(
            kernel,
            catalog_service=cat_getter if callable(cat_getter) else None,
        )

    def _resolve_target_project(self, project_uuid: Any) -> Tuple[str, Optional[Dict[str, Any]]]:
        if project_uuid is not None and not isinstance(project_uuid, str):
            return "", _fail("INVALID_ARGUMENT", "project_uuid debe ser texto.")
        requested = (project_uuid or "").strip() if isinstance(project_uuid, str) else ""
        if requested:
            return requested, None
        resolved = self._resolve_project_uuid() or ""
        return resolved, None

    def _busy_slots(self, terminal: Any) -> List[str]:
        list_fn = getattr(terminal, "list_sessions", None) if terminal is not None else None
        if not callable(list_fn):
            return []
        try:
            return [
                str(s["pane_id"])
                for s in list_fn()
                if isinstance(s, Mapping) and s.get("pane_id") and s.get("alive")
            ]
        except Exception:
            return []

    def _cli_display_name(self, cli_id: str) -> str:
        try:
            spec = self._bridge.extensions_ui._catalog_service().get_spec(cli_id)
            if spec is not None and getattr(spec, "display_name", None):
                return str(spec.display_name)
        except Exception:
            pass
        return cli_id

    def _serialize_plan(self, plan: RestorePlan) -> Dict[str, Any]:
        return {
            "project_uuid": plan.project_uuid,
            "closed_cleanly": plan.closed_cleanly,
            "saved_at": plan.saved_at,
            "warnings": list(plan.warnings),
            "warning_details": [format_warning(w) for w in plan.warnings],
            "items": [
                {
                    "pane_slot": item.pane_slot,
                    "cli_id": item.cli_id,
                    "cli_display_name": self._cli_display_name(item.cli_id),
                    "cwd": item.cwd,
                    "position": item.position,
                    "role": item.role,
                    "mandate": item.mandate,
                    "auto_relay": item.auto_relay,
                    "worker_mode": item.worker_mode,
                    "delivery_mode": item.delivery_mode,
                    "resume_mode": item.resume_mode,
                    "cli_session_id": item.cli_session_id,
                    "warnings": list(item.warnings),
                    "warning_details": [
                        format_warning(
                            w,
                            cli_name=self._cli_display_name(item.cli_id),
                            cwd=item.cwd or "",
                            pane_slot=item.pane_slot,
                        )
                        for w in item.warnings
                    ],
                    "skip": item.skip,
                }
                for item in plan.items
            ],
        }

    @staticmethod
    def _session_exit_status(terminal: Any, slot: str) -> Tuple[bool, Optional[int]]:
        """Devuelve `(exited, exit_code)` consultando `list_sessions` y `poll_output`."""
        list_fn = getattr(terminal, "list_sessions", None)
        if callable(list_fn):
            try:
                for sess in list_fn():
                    if isinstance(sess, Mapping) and str(sess.get("pane_id")) == slot:
                        if not sess.get("alive", True):
                            return True, sess.get("exit_code")
                        return False, None
            except Exception:
                pass
        poll_fn = getattr(terminal, "poll_output", None)
        if callable(poll_fn):
            try:
                polled = poll_fn(slot, since_seq=0)
                if isinstance(polled, Mapping) and not polled.get("alive", True):
                    return True, polled.get("exit_code")
            except Exception:
                pass
        return False, None

    def _watch_resumed_early_exit(
        self,
        terminal: Any,
        proj: str,
        resumed_entries: List[Tuple[str, Dict[str, Any]]],
        service: TerminalSessionService,
    ) -> None:
        if not resumed_entries:
            return
        poll_s = _EARLY_EXIT_POLL_S
        wait_s = _EARLY_EXIT_WAIT_S
        max_polls = max(1, int(round(wait_s / poll_s)))
        deadline = time.monotonic() + wait_s

        def _check_once() -> bool:
            all_exited = True
            for slot, entry in resumed_entries:
                if not entry.get("ok"):
                    continue
                exited, exit_code = self._session_exit_status(terminal, slot)
                if exited:
                    entry["ok"] = False
                    entry["code"] = "RESUME_FAILED_EARLY_EXIT"
                    entry["exit_code"] = exit_code
                    code_txt = f"código {exit_code}" if exit_code is not None else "sin código"
                    entry["message"] = (
                        f"La sesión reanudada en {slot} se cerró enseguida ({code_txt})."
                    )
                    service.record_exited(proj, slot, exit_code)
                else:
                    all_exited = False
            return all_exited

        for _ in range(max_polls):
            if _check_once():
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(poll_s)
        _check_once()

    def pty_get_restorable_session(
        self, project_uuid: Optional[str] = None, include_handled: bool = False
    ) -> Dict[str, Any]:
        """Devuelve `{policy, plan}` con las sesiones restaurables de `project_uuid`.

        `include_handled=True` incluye la última sesión guardada aunque se haya descartado o restaurado ya."""
        try:
            proj, err = self._resolve_target_project(project_uuid)
            if err is not None:
                return err
            service = self._service()
            if service is None:
                return _fail("SERVICE_UNAVAILABLE", "El servicio de sesiones no está disponible.")
            terminal = self._bridge.extensions_ui._get_terminal_connector()
            max_panes = self._bridge.extensions_ui._terminal_max_panes(terminal)
            busy = self._busy_slots(terminal)
            plan = service.restorable(
                proj, max_panes=max_panes, busy_slots=busy, include_handled=include_handled is True
            )
            return {
                "success": True,
                "code": "OK",
                "policy": service.restore_policy(),
                "plan": self._serialize_plan(plan),
            }
        except Exception as exc:
            logger.warning("pty_get_restorable_session falló: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_restore_session(
        self,
        project_uuid: Any,
        pane_slots: Any = None,
        include_handled: bool = False,
    ) -> Dict[str, Any]:
        """Restaura en orden por posición las ranuras pedidas con escalonado de 750 ms."""
        try:
            proj, err = self._resolve_target_project(project_uuid)
            if err is not None:
                return err
            wanted: Optional[set[str]] = None
            if pane_slots is not None:
                if isinstance(pane_slots, (str, bytes)) or not isinstance(pane_slots, Sequence):
                    return _fail("INVALID_ARGUMENT", "pane_slots debe ser una lista de ranuras.")
                wanted = {str(s) for s in pane_slots}

            service = self._service()
            if service is None:
                return _fail("SERVICE_UNAVAILABLE", "El servicio de sesiones no está disponible.")
            extensions = self._bridge.extensions_ui
            terminal = extensions._get_terminal_connector()
            max_panes = extensions._terminal_max_panes(terminal)
            busy = self._busy_slots(terminal)
            plan = service.restorable(
                proj, max_panes=max_panes, busy_slots=busy, include_handled=include_handled is True
            )

            results: List[Dict[str, Any]] = []
            resumed_entries: List[Tuple[str, Dict[str, Any]]] = []
            spawned_count = 0

            for item in plan.items:
                if wanted is not None and item.pane_slot not in wanted:
                    continue
                if item.skip:
                    warn_code = item.warnings[0] if item.warnings else "SKIPPED"
                    detail = format_warning(
                        warn_code,
                        cli_name=self._cli_display_name(item.cli_id),
                        cwd=item.cwd or "",
                        pane_slot=item.pane_slot,
                    )
                    results.append(
                        {
                            "pane_slot": item.pane_slot,
                            "ok": False,
                            "code": warn_code,
                            "message": detail["message"],
                            "resume_mode": item.resume_mode,
                        }
                    )
                    continue

                if spawned_count > 0:
                    time.sleep(_RESTORE_STAGGER_S)

                spawn_mode = "none" if item.resume_mode == "fresh" else item.resume_mode
                spawn_sid = item.cli_session_id if item.resume_mode == "by_id" else None
                spawn_res = extensions.pty_spawn_pane(
                    pane_id=item.pane_slot,
                    cli_profile=item.cli_id,
                    role="peer",
                    cwd=item.cwd,
                    project_uuid=proj or None,
                    resume_mode=spawn_mode,
                    cli_session_id=spawn_sid,
                )
                spawned_count += 1
                ok = bool(spawn_res.get("success"))
                entry: Dict[str, Any] = {
                    "pane_slot": item.pane_slot,
                    "ok": ok,
                    "code": str(spawn_res.get("code") or ("OK" if ok else "PTY_SPAWN_FAILED")),
                    "message": str(spawn_res.get("message") or ("Panel restaurado." if ok else "No se pudo restaurar el panel.")),
                    "resume_mode": item.resume_mode,
                }
                results.append(entry)
                if ok and item.resume_mode in ("by_id", "continue"):
                    resumed_entries.append((item.pane_slot, entry))

            if resumed_entries:
                self._watch_resumed_early_exit(terminal, proj, resumed_entries, service)

            service.mark_restored(proj)
            return {
                "success": True,
                "code": "OK",
                "project_uuid": proj,
                "results": results,
            }
        except Exception as exc:
            logger.warning("pty_restore_session falló: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_dismiss_restore(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """Persiste «Ahora no» sobre la instantánea actual de `project_uuid`."""
        try:
            proj, err = self._resolve_target_project(project_uuid)
            if err is not None:
                return err
            service = self._service()
            if service is None:
                return _fail("SERVICE_UNAVAILABLE", "El servicio de sesiones no está disponible.")
            service.dismiss(proj)
            return {"success": True, "code": "OK", "project_uuid": proj}
        except Exception as exc:
            logger.warning("pty_dismiss_restore falló: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_get_restore_policy(self) -> Dict[str, Any]:
        """Devuelve la política global de restauración (`ask` | `auto` | `never`)."""
        try:
            service = self._service()
            if service is None:
                return _fail("SERVICE_UNAVAILABLE", "El servicio de sesiones no está disponible.")
            return {"success": True, "code": "OK", "policy": service.restore_policy()}
        except Exception as exc:
            logger.warning("pty_get_restore_policy falló: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_set_restore_policy(self, policy: Any) -> Dict[str, Any]:
        """Valida y persiste la política global de restauración (`ask` | `auto` | `never`)."""
        try:
            if not isinstance(policy, str) or policy not in VALID_RESTORE_POLICIES:
                return _fail(
                    "INVALID_ARGUMENT",
                    "Política inválida; permitidas: ask, auto, never.",
                )
            service = self._service()
            if service is None:
                return _fail("SERVICE_UNAVAILABLE", "El servicio de sesiones no está disponible.")
            saved = service.set_restore_policy(policy)
            return {"success": True, "code": "OK", "policy": saved}
        except Exception as exc:
            logger.warning("pty_set_restore_policy falló: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def _best_relaunch_mode(
        self,
        *,
        registry: Any,
        service: Optional[TerminalSessionService],
        project_uuid: str,
        pane_slot: str,
        cli_profile: str,
        cwd: Optional[str],
        cli_session_id: Optional[str],
    ) -> str:
        cat_svc = self._bridge.extensions_ui._catalog_service()
        spec = cat_svc.get_spec(cli_profile)
        platform = getattr(cat_svc, "platform", None) or current_platform()
        if spec is None:
            return "none"
        session_cfg = getattr(spec, "session", {}) or {}
        if (
            cli_session_id
            and spec.capability_verified("session", platform)
            and bool(session_cfg.get("resume_by_id_argv"))
        ):
            return "by_id"

        resume_cfg = getattr(spec, "resume", {}) or {}
        has_verified_resume = (
            getattr(spec, "source", "repo") == "repo"
            and bool(resume_cfg.get("verified"))
            and bool(resume_cfg.get("argv"))
        )
        if not has_verified_resume:
            return "none"

        my_cwd_key = os.path.realpath(cwd) if cwd else ""
        group_without_id = 0
        for pane in registry.list_panes(project_uuid=project_uuid):
            if pane.get("cli_profile") != cli_profile:
                continue
            p_cwd = pane.get("cwd")
            p_cwd_key = os.path.realpath(p_cwd) if p_cwd else ""
            if p_cwd_key != my_cwd_key:
                continue
            row = service.get_session(project_uuid, str(pane.get("pane_id") or "")) if service is not None else None
            if not (row and row.get("cli_session_id")):
                group_without_id += 1
        return "continue" if group_without_id <= 1 else "none"

    def pty_relaunch_pane(self, pane_id: Any) -> Dict[str, Any]:
        """Cierra un panel activo y lo reabre en la misma ranura con el mejor modo de reanudación."""
        try:
            if pane_id is None or isinstance(pane_id, bool) or not str(pane_id).strip():
                return _fail("INVALID_ARGUMENT", "Indique el identificador del panel.")
            extensions = self._bridge.extensions_ui
            registry = extensions._get_pane_registry()
            try:
                rec = registry.get_pane(pane_id)
            except (TypeError, ValueError):
                rec = None
            if rec is None or rec.status == "closed":
                return _fail("PANE_NOT_FOUND", "No hay un panel activo con ese identificador.")

            slot = rec.pane_id
            proj = rec.project_uuid or ""
            cli_profile = rec.cli_profile
            cwd = rec.cwd
            role = rec.role

            service = self._service()
            row = service.get_session(proj, slot) if service is not None else None
            cli_session_id = row.get("cli_session_id") if row else None

            best_mode = self._best_relaunch_mode(
                registry=registry,
                service=service,
                project_uuid=proj,
                pane_slot=slot,
                cli_profile=cli_profile,
                cwd=cwd,
                cli_session_id=cli_session_id,
            )

            close_res = extensions.pty_close_pane(slot)
            if not close_res.get("success"):
                return _fail(
                    str(close_res.get("code") or "CLOSE_FAILED"),
                    str(close_res.get("message") or close_res.get("error") or "No se pudo cerrar el panel."),
                    pane_id=slot,
                )

            spawn_res = extensions.pty_spawn_pane(
                pane_id=slot,
                cli_profile=cli_profile,
                role=role or "peer",
                cwd=cwd,
                project_uuid=proj or None,
                resume_mode=best_mode,
                cli_session_id=cli_session_id if best_mode == "by_id" else None,
            )
            return {
                **spawn_res,
                "pane_id": slot,
                "resume_mode": "fresh" if best_mode == "none" else best_mode,
            }
        except Exception as exc:
            logger.warning("pty_relaunch_pane falló: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))
