"""Medición automática de CLIs en segundo plano (WP-5d).

Orquesta una medición a la vez: comprueba que el CLI existe, lanza la sonda sin interfaz (primero con pegado entre
corchetes y, si el CLI no lo admite, con una sola línea), decide con el dominio si es seguro y, si lo es, guarda la
medición, que activa la entrega directa para ese CLI al volver a cargar el catálogo. No importa nada de `src/adapters`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.domain.cli_measurement import (
    MEASURABLE_CLIS,
    Attempt,
    AttemptResult,
    build_record,
    choose_best,
    clean_version,
    evaluate_attempt,
    reason_text,
)

logger = logging.getLogger(__name__)

STEPS_TOTAL = 10  # 5 pasos por intento y hasta 2 intentos
RETRY_WITH_SINGLE_LINE = frozenset({"PASTE_NOT_SUPPORTED", "PREMATURE_SUBMIT"})

ProbeFn = Callable[..., Attempt]


class CliMeasurementService:
    def __init__(
        self,
        *,
        probe_run: ProbeFn,
        list_clis: Callable[[], List[Dict[str, Any]]],
        resolve: Callable[[str], Optional[Dict[str, Any]]],
        stamp: Callable[[str], Optional[Tuple[int, int]]],
        record_state: Callable[[str], Tuple[str, Optional[Dict[str, Any]]]],
        save: Callable[[Dict[str, Any]], None],
        forget: Callable[[str], bool],
        platform: str,
        today: Callable[[], str],
        clock_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        self._probe_run = probe_run
        self._list_clis = list_clis
        self._resolve = resolve
        self._stamp = stamp
        self._record_state = record_state
        self._save = save
        self._forget = forget
        self._platform = platform
        self._today = today
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.Lock()
        self._running: Optional[Dict[str, Any]] = None
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last: Dict[str, Dict[str, Any]] = {}
        self._measured_this_run: set = set()

    # -- consulta --------------------------------------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        with self._lock:
            running = dict(self._running) if self._running else None
            last = {k: dict(v) for k, v in self._last.items()}
            fresh = set(self._measured_this_run)
        items: List[Dict[str, Any]] = []
        for info in self._list_clis():
            cli_id = str(info["cli_id"])
            installed = self._resolve(cli_id) is not None
            item: Dict[str, Any] = {
                "cli_id": cli_id,
                "display_name": info.get("display_name") or cli_id,
                "installed": installed,
                "state": "not_measured",
                "measured_at": None,
                "cli_version": None,
                "paste_mode": None,
                "confirm_event": None,
                "reason_code": None,
                "reason_text": None,
                "restart_needed": False,
            }
            if cli_id not in MEASURABLE_CLIS:
                item["state"] = "unsupported"
                items.append(item)
                continue
            state, record = self._record_state(cli_id)
            if state == "measured" and record is not None:
                item.update(
                    state="measured",
                    measured_at=record.get("measured_on"),
                    cli_version=record.get("cli_version") or None,
                    paste_mode=record["delivery"]["paste_mode"],
                    confirm_event=record["delivery"]["confirm_event"] or None,
                    restart_needed=cli_id in fresh,
                )
            elif state == "stale":
                item["state"] = "stale"
                if record is not None:
                    item["measured_at"] = record.get("measured_on")
                    item["cli_version"] = record.get("cli_version") or None
            elif state == "invalid":
                item.update(state="failed", reason_code="RECORD_INVALID", reason_text=reason_text("RECORD_INVALID"))
            failure = last.get(cli_id)
            if failure is not None and item["state"] != "measured":
                item.update(state="failed", reason_code=failure["reason_code"], reason_text=failure["reason_text"])
            items.append(item)
        return {"success": True, "code": "OK", "platform": self._platform, "running": running, "items": items}

    # -- acciones --------------------------------------------------------------------------------------------------

    def start(self, cli_id: Any, *, cwd: Optional[Any] = None) -> Dict[str, Any]:
        if not isinstance(cli_id, str) or cli_id not in MEASURABLE_CLIS:
            return _fail("UNSUPPORTED_CLI", reason_text("UNSUPPORTED_CLI"))
        resolved = self._resolve(cli_id)
        if resolved is None:
            return _fail("CLI_NOT_INSTALLED", reason_text("CLI_NOT_INSTALLED"))
        with self._lock:
            if self._running is not None:
                return _fail("MEASUREMENT_BUSY", "Ya hay una medición en curso.")
            self._cancel.clear()
            self._running = {
                "cli_id": cli_id,
                "step": "preparando",
                "steps_done": 0,
                "steps_total": STEPS_TOTAL,
                "message": "Preparando la prueba",
                "started_ms": int(self._clock_ms()),
            }
            self._last.pop(cli_id, None)
            self._thread = threading.Thread(
                target=self._run, args=(cli_id, resolved, cwd), name=f"mcp2p-measure-{cli_id}", daemon=True
            )
            thread = self._thread
        thread.start()
        return {"success": True, "code": "OK", "started": True, "cli_id": cli_id}

    def cancel(self) -> Dict[str, Any]:
        self._cancel.set()
        return {"success": True, "code": "OK"}

    def forget(self, cli_id: Any) -> Dict[str, Any]:
        if not isinstance(cli_id, str) or cli_id not in MEASURABLE_CLIS:
            return _fail("UNSUPPORTED_CLI", reason_text("UNSUPPORTED_CLI"))
        removed = bool(self._forget(cli_id))
        with self._lock:
            self._last.pop(cli_id, None)
            self._measured_this_run.discard(cli_id)
        return {"success": True, "code": "OK", "removed": removed}

    def wait(self, timeout_s: float = 30.0) -> bool:
        """Espera a que termine la medición en curso (pruebas); True si ya no hay ninguna."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout_s)
        return not (thread is not None and thread.is_alive())

    # -- trabajo ---------------------------------------------------------------------------------------------------

    def _progress(self, step: str, done: int, total: int, message: str) -> None:
        with self._lock:
            if self._running is not None:
                self._running.update(step=step, steps_done=min(done, total), steps_total=total, message=message)

    def _run(self, cli_id: str, resolved: Dict[str, Any], cwd: Optional[Any] = None) -> None:
        try:
            self._measure(cli_id, resolved, cwd=cwd)
        except Exception as exc:  # nunca deja la medición «en curso» para siempre
            logger.warning("Fallo la medición de %s", cli_id, exc_info=True)
            self._fail_run(cli_id, "SPAWN_FAILED", f"La medición falló: {exc}")
        finally:
            with self._lock:
                self._running = None

    def _fail_run(self, cli_id: str, code: str, text: Optional[str] = None) -> None:
        with self._lock:
            self._last[cli_id] = {"reason_code": code, "reason_text": text or reason_text(code)}

    def _measure(self, cli_id: str, resolved: Dict[str, Any], cwd: Optional[Any] = None) -> None:
        results: List[Tuple[str, AttemptResult]] = []
        modes = ["bracketed", "single_line"]
        for index, mode in enumerate(modes):
            probe_kw: Dict[str, Any] = {
                "paste_mode": mode,
                "progress": self._progress,
                "cancel": self._cancel,
                "step_offset": index * 5,
                "steps_total": STEPS_TOTAL,
            }
            if cwd is not None:
                probe_kw["cwd"] = cwd
            attempt = self._probe_run(cli_id, **probe_kw)
            result = evaluate_attempt(attempt)
            results.append((mode, result))
            if result.ok or result.reason_code not in RETRY_WITH_SINGLE_LINE:
                break
        mode, best = choose_best(results)
        if best is None or mode is None:
            self._fail_run(cli_id, "SPAWN_FAILED")
            return
        if not best.ok:
            code = best.reason_code or "TIMEOUT"
            detail = (
                f"El CLI pidió confirmar que confía en «{best.detail}». Ábrelo ahí una vez, "
                "acepta «Yes, I trust this folder» y vuelve a medir."
                if code == "NEEDS_WORKSPACE_TRUST" and best.detail
                else None
            )
            self._fail_run(cli_id, code, detail)
            return
        path = str(resolved.get("path") or "")
        if not clean_version(resolved.get("version")):
            self._fail_run(cli_id, "VERSION_UNKNOWN")
            return
        stamp = self._stamp(path)
        if not path or stamp is None:
            self._fail_run(cli_id, "CLI_NOT_INSTALLED")
            return
        record = build_record(
            cli_id=cli_id,
            platform=self._platform,
            today=self._today(),
            cli_version=resolved.get("version"),
            binary_path=path,
            binary_mtime_ns=stamp[0],
            binary_size=stamp[1],
            result=best,
            paste_mode=mode,
        )
        self._save(record)
        with self._lock:
            self._measured_this_run.add(cli_id)
            self._last.pop(cli_id, None)


def _fail(code: str, message: str) -> Dict[str, Any]:
    return {"success": False, "code": code, "message": message}
