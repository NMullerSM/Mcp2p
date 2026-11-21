"""Adaptador UI de la medición automática de CLIs (WP-5d).

Acciones del HUMANO desde Ajustes. Medir abre el CLI sin interfaz y gasta unos pocos tokens, así que pasa por
`ConfirmationGate` (`cli.measure`, no recordable). Nada de esto existe como herramienta MCP.
"""

from __future__ import annotations

import datetime
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.app.ui_adapters import _BaseUIAdapter

logger = logging.getLogger("mcp2p.app.ui_adapters.measurement")


def _fail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "code": code, "message": message, **extra}


class MeasurementUIAdapter(_BaseUIAdapter):
    def __init__(self, bridge: Any) -> None:
        super().__init__(bridge)
        self._svc: Any = None

    def _catalog(self) -> Any:
        extensions = getattr(self._bridge, "extensions_ui", None)
        return extensions._catalog_service()

    def _service(self) -> Any:
        if self._svc is not None:
            return self._svc
        from src.adapters.terminal.cli_measurement import HeadlessCliProbe
        from src.adapters.terminal.cli_measurement_store import binary_stamp, detect_cli_version, validate_record
        from src.core.services.cli_measurement_service import CliMeasurementService

        catalog_svc = self._catalog()
        store = catalog_svc.measurement_store()
        platform = catalog_svc.platform
        probe = HeadlessCliProbe(catalog_svc)

        def list_clis() -> List[Dict[str, Any]]:
            return [
                {"cli_id": s.id, "display_name": s.display_name}
                for s in catalog_svc.catalog().profiles.values()
                if s.kind != "shell"
            ]

        def resolve(cli_id: str) -> Optional[Dict[str, Any]]:
            res = catalog_svc.resolve(cli_id)
            if not res.found or not res.path:
                return None
            return {"path": res.path, "version": res.version or detect_cli_version(res.path)}

        def record_state(cli_id: str) -> Tuple[str, Optional[Dict[str, Any]]]:
            record = store.get(cli_id, platform)
            spec = catalog_svc.get_spec(cli_id)
            if record is None or spec is None:
                return "none", None
            _blocks, why = validate_record(record, spec, platform)
            state = {"ok": "measured", "stale": "stale"}.get(why, "invalid")
            return state, (record if why in ("ok", "stale") else None)

        self._svc = CliMeasurementService(
            probe_run=probe.run,
            list_clis=list_clis,
            resolve=resolve,
            stamp=binary_stamp,
            record_state=record_state,
            save=store.put,
            forget=lambda cli_id: store.forget(cli_id, platform),
            platform=platform,
            today=lambda: datetime.date.today().isoformat(),
        )
        return self._svc

    def pty_get_cli_measurements(self) -> Dict[str, Any]:
        try:
            return self._service().status()
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def _probe_cwd_for(self, cli_id: str, project_uuid: Any) -> Optional[Path]:
        if cli_id != "claude":
            return None
        if isinstance(project_uuid, str) and project_uuid.strip():
            db = getattr(getattr(self._bridge, "core_kernel", None), "db", None)
            getter = getattr(db, "get_project_by_uuid", None) if db is not None else None
            proj = getter(project_uuid.strip()) if callable(getter) else None
            if isinstance(proj, dict):
                for key in ("code_repo_path", "base_path"):
                    raw = proj.get(key)
                    if isinstance(raw, str) and raw.strip():
                        cand = Path(raw.strip())
                        if cand.is_dir():
                            return cand
        from src.runtime.paths import get_user_data_dir

        sandbox = Path(get_user_data_dir()) / "cli_probe_cwd"
        sandbox.mkdir(parents=True, exist_ok=True)
        return sandbox

    def pty_measure_cli(self, cli_id: Any, project_uuid: Any = None) -> Dict[str, Any]:
        try:
            service = self._service()
            catalog = self._catalog()
            spec = catalog.get_spec(cli_id) if isinstance(cli_id, str) else None
            if spec is None:
                return _fail("UNSUPPORTED_CLI", "Ese CLI no está en el catálogo.")
            gate = getattr(self._bridge, "confirm_gate", None)
            if gate is None:
                return _fail("CONFIRMATION_UNAVAILABLE", "No hay servicio de confirmación; la acción se rechaza.")
            probe_cwd = self._probe_cwd_for(cli_id, project_uuid)
            return gate.require(
                "cli.measure",
                f"Medir {spec.display_name} en este equipo",
                f"MCP2P abrirá {spec.display_name} en segundo plano (sin ventana), pegará un mensaje de prueba de dos "
                "líneas y esperará su respuesta de una palabra. Gasta unos pocos tokens del modelo y puede tardar un "
                "par de minutos. No toca tus terminales abiertas ni tu configuración. Si sale bien, se activa la "
                "entrega directa para este CLI.",
                lambda: service.start(cli_id, cwd=probe_cwd),
                args_digest=f"measure:{cli_id}",
            )
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_cancel_cli_measurement(self) -> Dict[str, Any]:
        try:
            return self._service().cancel()
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_forget_cli_measurement(self, cli_id: Any) -> Dict[str, Any]:
        try:
            return self._service().forget(cli_id)
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))
