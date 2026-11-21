"""Servicio del núcleo para persistencia, captura de sesión y plan de restauración de terminales (v2.0.2, WP-4a)."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

_CLI_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")

VALID_RESTORE_POLICIES = frozenset({"ask", "auto", "never"})
DEFAULT_RESTORE_POLICY = "ask"
RESTORE_POLICY_SETTING_KEY = "terminal_session.restore_policy"

VALID_SESSION_SOURCES = frozenset({"assigned", "hook", "none"})


def validate_cli_session_id(value: Any) -> str:
    """Valida que `cli_session_id` cumpla `^[A-Za-z0-9_-]{1,128}$` y no empiece por `-`."""
    if not isinstance(value, str) or value.startswith("-") or _CLI_SESSION_ID_RE.fullmatch(value) is None:
        raise ValueError("cli_session_id inválido: debe cumplir ^[A-Za-z0-9_-]{1,128}$ sin '-' inicial.")
    return value


def format_warning(
    code: str,
    *,
    cli_name: str = "",
    cwd: str = "",
    pane_slot: str = "",
) -> Dict[str, str]:
    """Devuelve texto legible en español y acción sugerida para un código de aviso de restauración."""
    cli_label = cli_name or "Este CLI"
    if code == "CWD_MISSING":
        return {
            "code": code,
            "message": f"La carpeta {cwd} ya no existe: elige otra o abre la terminal sin restaurar.",
            "action": "choose_cwd",
        }
    if code == "CLI_NOT_INSTALLED":
        return {
            "code": code,
            "message": f"{cli_label} no está instalado.",
            "action": "install",
        }
    if code == "CLI_REMOVED":
        return {
            "code": code,
            "message": f"{cli_label} ya no está en el catálogo.",
            "action": "",
        }
    if code == "AMBIGUOUS_CONTINUE":
        return {
            "code": code,
            "message": (
                f"Hay varias terminales de {cli_label} en la misma carpeta y no se sabe cuál era cuál: "
                "se abrirán sin reanudar."
            ),
            "action": "",
        }
    if code == "RESUME_NOT_VERIFIED":
        return {
            "code": code,
            "message": "Este CLI no tiene una reanudación verificada: se abrirá una conversación nueva.",
            "action": "",
        }
    if code == "PANE_LIMIT":
        return {
            "code": code,
            "message": "Se alcanzó el límite de terminales abiertas: esta terminal no se abrirá.",
            "action": "increase_limit",
        }
    if code == "SLOT_BUSY":
        slot_label = pane_slot or "indicada"
        return {
            "code": code,
            "message": f"La ranura {slot_label} ya está ocupada por una terminal activa.",
            "action": "",
        }
    if code == "UNCLEAN_SHUTDOWN":
        return {
            "code": code,
            "message": "MCP2P no se cerró bien la última vez.",
            "action": "",
        }
    return {
        "code": str(code),
        "message": str(code),
        "action": "",
    }


@dataclass(frozen=True)
class RestoreItem:
    """Elemento individual del plan de restauración de un proyecto."""

    pane_slot: str
    cli_id: str
    cwd: Optional[str]
    position: int
    role: str
    mandate: str
    auto_relay: bool
    delivery_mode: str
    resume_mode: str
    cli_session_id: Optional[str]
    warnings: Tuple[str, ...]
    skip: bool


@dataclass(frozen=True)
class RestorePlan:
    """Plan completo de restauración de sesiones de un proyecto."""

    project_uuid: str
    items: Tuple[RestoreItem, ...]
    closed_cleanly: bool
    saved_at: Optional[str]
    warnings: Tuple[str, ...]


class TerminalSessionService:
    """Orquesta el ciclo de vida persistente de sesiones y calcula el `RestorePlan`."""

    def __init__(
        self,
        *,
        repo: Any,
        catalog: Any,
        identity_repo: Any = None,
        settings: Any = None,
        app_version: str = "2.0.2",
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._repo = repo
        self._catalog = catalog
        self._identity_repo = identity_repo
        self._settings = settings
        self._app_version = str(app_version or "2.0.2")
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now_iso(self) -> str:
        return self._clock().isoformat()

    def _resolve_catalog(self) -> Any:
        return self._catalog() if callable(self._catalog) else self._catalog

    def get_session(self, project_uuid: str, pane_slot: str) -> Optional[Dict[str, Any]]:
        """Devuelve la fila persistida para `(project_uuid, pane_slot)` o `None`."""
        return self._repo.get(project_uuid or "", pane_slot)

    def record_spawn(
        self,
        *,
        project_uuid: str,
        pane_slot: str,
        cli_id: str,
        cwd: Optional[str],
        cli_session_id: Optional[str],
        cli_session_source: str,
        position: Optional[int] = None,
    ) -> None:
        """Registra la apertura de un panel y marca la instantánea actual como sesión viva (`closed_cleanly=0`)."""
        if cli_session_source not in VALID_SESSION_SOURCES:
            raise ValueError(f"cli_session_source inválido: {cli_session_source!r}.")
        validated_sid: Optional[str] = None
        if cli_session_id is not None:
            validated_sid = validate_cli_session_id(cli_session_id)

        proj = project_uuid or ""
        now_iso = self._now_iso()
        self._repo.upsert(
            project_uuid=proj,
            pane_slot=pane_slot,
            cli_id=cli_id,
            cwd=cwd,
            position=position,
            cli_session_id=validated_sid,
            cli_session_source=cli_session_source if validated_sid is not None else "none",
            state="open",
            last_exit_code=None,
            now_iso=now_iso,
        )
        self._repo.snapshot_set(
            proj,
            closed_cleanly=False,
            saved_at=now_iso,
            restored_at=None,
            dismissed_at=None,
            app_version=self._app_version,
        )

    def record_cli_session_id(
        self,
        *,
        project_uuid: str,
        pane_slot: str,
        cli_session_id: str,
        source: str = "hook",
    ) -> bool:
        """Asocia un `cli_session_id` validado al panel abierto; devuelve `True` si se actualizó."""
        validated_sid = validate_cli_session_id(cli_session_id)
        if source not in VALID_SESSION_SOURCES:
            raise ValueError(f"source inválido: {source!r}.")
        return bool(
            self._repo.set_cli_session_id(
                project_uuid or "",
                pane_slot,
                validated_sid,
                source=source,
                only_open=True,
                now_iso=self._now_iso(),
            )
        )

    def record_closed_by_user(self, project_uuid: str, pane_slot: str) -> None:
        """Marca un panel cerrado por el humano (`closed_by_user`) para no restaurarlo."""
        self._repo.set_state(
            project_uuid or "",
            pane_slot,
            "closed_by_user",
            now_iso=self._now_iso(),
        )

    def record_exited(self, project_uuid: str, pane_slot: str, exit_code: Optional[int] = None) -> None:
        """Marca un panel terminado por salida de su proceso (`exited`)."""
        self._repo.set_state(
            project_uuid or "",
            pane_slot,
            "exited",
            last_exit_code=exit_code,
            now_iso=self._now_iso(),
        )

    def mark_app_closing(self, alive_slots: Mapping[str, Sequence[str]]) -> int:
        """Cierra limpiamente las sesiones abiertas (`closed_by_app` / `exited`) y sella la instantánea."""
        return int(
            self._repo.mark_app_closing(
                alive_slots,
                app_version=self._app_version,
                now_iso=self._now_iso(),
            )
        )

    def restorable(
        self,
        project_uuid: str,
        *,
        max_panes: int,
        busy_slots: Sequence[str] = (),
        include_handled: bool = False,
    ) -> RestorePlan:
        """Calcula el plan de restauración ordenado de `project_uuid` sin efectos laterales.

        `include_handled=True` ignora las marcas «Ahora no» y «ya restaurada» para poder volver a restaurar a mano la
        última sesión guardada."""
        proj = project_uuid or ""
        snapshot = self._repo.snapshot_get(proj)
        if (
            snapshot is not None
            and not include_handled
            and (snapshot.get("dismissed_at") or snapshot.get("restored_at"))
        ):
            return RestorePlan(
                project_uuid=proj,
                items=(),
                closed_cleanly=bool(snapshot.get("closed_cleanly")),
                saved_at=snapshot.get("saved_at"),
                warnings=(),
            )

        rows = self._repo.list(proj, states=("open", "closed_by_app"))
        if not rows:
            if snapshot is not None:
                closed_cleanly = bool(snapshot.get("closed_cleanly"))
                saved_at = snapshot.get("saved_at")
                plan_warnings: Tuple[str, ...] = () if closed_cleanly else ("UNCLEAN_SHUTDOWN",)
            else:
                closed_cleanly = True
                saved_at = None
                plan_warnings = ()
            return RestorePlan(
                project_uuid=proj,
                items=(),
                closed_cleanly=closed_cleanly,
                saved_at=saved_at,
                warnings=plan_warnings,
            )

        grupo: Dict[Tuple[str, str], int] = {}
        for row in rows:
            if not row.get("cli_session_id"):
                cwd_val = row.get("cwd")
                cwd_key = os.path.realpath(cwd_val) if cwd_val else ""
                key = (str(row["cli_id"]), cwd_key)
                grupo[key] = grupo.get(key, 0) + 1

        catalog = self._resolve_catalog()
        platform = getattr(catalog, "platform", None) or ("win32" if sys.platform == "win32" else sys.platform)

        busy_set = {str(s) for s in busy_slots}
        vivos = len(busy_set)
        items: List[RestoreItem] = []

        for row in rows:
            slot = str(row["pane_slot"])
            cli_id = str(row["cli_id"])
            cwd_val = row.get("cwd")
            position = int(row.get("position", 0))
            sid = row.get("cli_session_id")
            avisos: List[str] = []
            saltar = False

            spec = catalog.get_spec(cli_id) if catalog is not None else None
            if spec is None:
                avisos.append("CLI_REMOVED")
                saltar = True
            else:
                res = catalog.resolve(cli_id)
                if not getattr(res, "found", False):
                    avisos.append("CLI_NOT_INSTALLED")
                    saltar = True

            if cwd_val and not os.path.isdir(cwd_val):
                avisos.append("CWD_MISSING")
                saltar = True

            if slot in busy_set:
                avisos.append("SLOT_BUSY")
                saltar = True

            if not saltar and vivos >= int(max_panes):
                avisos.append("PANE_LIMIT")
                saltar = True

            if spec is None:
                modo = "fresh"
            else:
                session_cfg = getattr(spec, "session", {}) or {}
                resume_cfg = getattr(spec, "resume", {}) or {}
                cwd_key = os.path.realpath(cwd_val) if cwd_val else ""
                group_count = grupo.get((cli_id, cwd_key), 0)
                has_verified_resume = (
                    getattr(spec, "source", "repo") == "repo"
                    and bool(resume_cfg.get("verified"))
                    and bool(resume_cfg.get("argv"))
                )
                if (
                    sid
                    and spec.capability_verified("session", platform)
                    and bool(session_cfg.get("resume_by_id_argv"))
                ):
                    modo = "by_id"
                elif has_verified_resume and group_count == 1:
                    modo = "continue"
                else:
                    modo = "fresh"
                    if not saltar:
                        if has_verified_resume and group_count > 1:
                            avisos.append("AMBIGUOUS_CONTINUE")
                        else:
                            avisos.append("RESUME_NOT_VERIFIED")

            ident = self._identity_repo.get_pane_identity(proj, slot) if self._identity_repo is not None else None
            role = str((ident or {}).get("role") or "peer")
            mandate = str((ident or {}).get("mandate") or "")
            auto_relay = bool((ident or {}).get("auto_relay_enabled", False))
            delivery_mode = str((ident or {}).get("delivery_mode") or "auto")

            items.append(
                RestoreItem(
                    pane_slot=slot,
                    cli_id=cli_id,
                    cwd=cwd_val,
                    position=position,
                    role=role,
                    mandate=mandate,
                    auto_relay=auto_relay,
                    delivery_mode=delivery_mode,
                    resume_mode=modo,
                    cli_session_id=sid,
                    warnings=tuple(avisos),
                    skip=saltar,
                )
            )
            if not saltar:
                vivos += 1

        if snapshot is not None:
            closed_cleanly = bool(snapshot.get("closed_cleanly"))
            saved_at = snapshot.get("saved_at")
        else:
            closed_cleanly = not any(r.get("state") == "open" for r in rows)
            saved_at = None

        plan_warnings = () if closed_cleanly else ("UNCLEAN_SHUTDOWN",)
        return RestorePlan(
            project_uuid=proj,
            items=tuple(items),
            closed_cleanly=closed_cleanly,
            saved_at=saved_at,
            warnings=plan_warnings,
        )

    def mark_restored(self, project_uuid: str) -> None:
        """Marca la instantánea del proyecto como restaurada para que no vuelva a ofrecerse."""
        proj = project_uuid or ""
        existing = self._repo.snapshot_get(proj)
        now_iso = self._now_iso()
        self._repo.snapshot_set(
            proj,
            closed_cleanly=bool(existing.get("closed_cleanly")) if existing is not None else False,
            saved_at=existing.get("saved_at") if existing is not None else now_iso,
            restored_at=now_iso,
            app_version=(existing.get("app_version") if existing is not None else self._app_version),
        )

    def dismiss(self, project_uuid: str) -> None:
        """Marca «Ahora no» persistente sobre la instantánea actual hasta la siguiente instantánea."""
        proj = project_uuid or ""
        existing = self._repo.snapshot_get(proj)
        now_iso = self._now_iso()
        self._repo.snapshot_set(
            proj,
            closed_cleanly=bool(existing.get("closed_cleanly")) if existing is not None else False,
            saved_at=existing.get("saved_at") if existing is not None else now_iso,
            dismissed_at=now_iso,
            app_version=(existing.get("app_version") if existing is not None else self._app_version),
        )

    def restore_policy(self) -> str:
        """Devuelve la política de restauración (`ask` | `auto` | `never`), por defecto `ask`."""
        if self._settings is None:
            return DEFAULT_RESTORE_POLICY
        getter = getattr(self._settings, "get_setting", None)
        if not callable(getter):
            return DEFAULT_RESTORE_POLICY
        raw = getter(RESTORE_POLICY_SETTING_KEY, DEFAULT_RESTORE_POLICY)
        if isinstance(raw, str) and raw in VALID_RESTORE_POLICIES:
            return raw
        return DEFAULT_RESTORE_POLICY

    def set_restore_policy(self, value: str) -> str:
        """Valida y persiste la política de restauración (`ask` | `auto` | `never`)."""
        if not isinstance(value, str) or value not in VALID_RESTORE_POLICIES:
            raise ValueError("Política de restauración inválida; permitidas: ask, auto, never.")
        if self._settings is not None:
            setter = getattr(self._settings, "set_setting", None)
            if callable(setter):
                setter(RESTORE_POLICY_SETTING_KEY, value)
        return value
