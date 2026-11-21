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
    status_dot,
)

logger = logging.getLogger(__name__)

STEPS_TOTAL = 10  # 5 pasos por intento y hasta 2 intentos
RETRY_WITH_SINGLE_LINE = frozenset({"PASTE_NOT_SUPPORTED", "PREMATURE_SUBMIT"})

ProbeFn = Callable[..., Attempt]
CwdFor = Callable[[str], Optional[Any]]


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
        auto_enabled: Optional[Callable[[], bool]] = None,
        attempt_get: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        attempt_put: Optional[Callable[[str, Dict[str, Any]], None]] = None,
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
        self._auto_enabled = auto_enabled or (lambda: False)
        self._attempt_get = attempt_get or (lambda cli_id: None)
        self._attempt_put = attempt_put or (lambda cli_id, attempt: None)
        self._auto_pending: List[Tuple[str, Optional[CwdFor]]] = []

    # -- consulta --------------------------------------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        with self._lock:
            running = dict(self._running) if self._running else None
            last = {k: dict(v) for k, v in self._last.items()}
            fresh = set(self._measured_this_run)
            queued = {cli for cli, _cwd in self._auto_pending}
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
                "auto_pending": cli_id in queued,
            }
            if cli_id not in MEASURABLE_CLIS:
                item["state"] = "unsupported"
                _paint_dot(item, running)
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
            _paint_dot(item, running)
            items.append(item)
        return {
            "success": True,
            "code": "OK",
            "platform": self._platform,
            "running": running,
            "auto_enabled": bool(self._auto_enabled()),
            "items": items,
        }

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
            self._thread.start()  # dentro del cerrojo: `wait()` nunca ve un hilo todavía sin arrancar
        return {"success": True, "code": "OK", "started": True, "cli_id": cli_id}

    def cancel(self) -> Dict[str, Any]:
        with self._lock:
            self._auto_pending.clear()
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
        """Espera a que termine la medición en curso y las encadenadas (pruebas); True si ya no hay ninguna."""
        deadline = time.monotonic() + timeout_s
        while True:
            with self._lock:
                thread = self._thread
            if thread is None:
                return True
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                return False
            with self._lock:
                if self._thread is thread:
                    return True

    # -- medición automática ----------------------------------------------------------------------------------------

    def auto_candidates(self) -> List[str]:
        """CLIs instalados sin medición válida y que aún no se intentaron con ESTE programa (ruta, tamaño y fecha)."""
        out: List[str] = []
        for cli_id in MEASURABLE_CLIS:
            resolved = self._resolve(cli_id)
            if resolved is None:
                continue
            state, _record = self._record_state(cli_id)
            if state == "measured":
                continue
            path = str(resolved.get("path") or "")
            stamp = self._stamp(path) if path else None
            if stamp is None:
                continue
            last = self._attempt_get(cli_id)
            if last and last.get("path") == path and list(last.get("stamp") or []) == [stamp[0], stamp[1]]:
                continue
            out.append(cli_id)
        return out

    def auto_tick(self, *, cwd_for: Optional[CwdFor] = None) -> Dict[str, Any]:
        """Si el interruptor está activo, mide en segundo plano los CLIs pendientes, de uno en uno."""
        if not self._auto_enabled():
            return {"success": True, "code": "OK", "started": None, "queued": 0, "reason": "AUTO_DISABLED"}
        with self._lock:
            if self._running is not None:
                return {"success": True, "code": "OK", "started": None, "queued": len(self._auto_pending),
                        "reason": "BUSY"}
        candidates = self.auto_candidates()
        if not candidates:
            return {"success": True, "code": "OK", "started": None, "queued": 0, "reason": "NOTHING_TO_DO"}
        with self._lock:
            self._auto_pending = [(cli_id, cwd_for) for cli_id in candidates[1:]]
        first = candidates[0]
        res = self.start(first, cwd=cwd_for(first) if cwd_for else None)
        if not res.get("success"):
            with self._lock:
                self._auto_pending.clear()
            return {"success": True, "code": "OK", "started": None, "queued": 0, "reason": res.get("code")}
        return {"success": True, "code": "OK", "started": first, "queued": len(candidates) - 1, "reason": None}

    def _start_next_auto(self) -> None:
        while True:
            with self._lock:
                if self._cancel.is_set() or not self._auto_pending or self._running is not None:
                    if self._cancel.is_set():
                        self._auto_pending.clear()
                    return
                cli_id, cwd_for = self._auto_pending.pop(0)
            if not self._auto_enabled():
                with self._lock:
                    self._auto_pending.clear()
                return
            if self.start(cli_id, cwd=cwd_for(cli_id) if cwd_for else None).get("success"):
                return

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
            self._fail_run(cli_id, "SPAWN_FAILED", f"La medición falló: {exc}", resolved=resolved)
        finally:
            with self._lock:
                self._running = None
        self._start_next_auto()

    def _fail_run(
        self, cli_id: str, code: str, text: Optional[str] = None, resolved: Optional[Dict[str, Any]] = None
    ) -> None:
        with self._lock:
            self._last[cli_id] = {"reason_code": code, "reason_text": text or reason_text(code)}
        if resolved is not None and code != "CANCELLED":
            self._note_attempt(cli_id, resolved, code)

    def _note_attempt(self, cli_id: str, resolved: Dict[str, Any], code: str) -> None:
        """Guarda con qué programa falló, para que la medición automática no lo repita con el mismo."""
        path = str(resolved.get("path") or "")
        stamp = self._stamp(path) if path else None
        if stamp is None:
            return
        try:
            self._attempt_put(
                cli_id,
                {"path": path, "stamp": [stamp[0], stamp[1]], "reason_code": code, "at": self._today()},
            )
        except Exception:
            logger.warning("No se pudo guardar el intento de %s", cli_id, exc_info=True)

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
            self._fail_run(cli_id, "SPAWN_FAILED", resolved=resolved)
            return
        if not best.ok:
            code = best.reason_code or "TIMEOUT"
            detail = (
                f"El CLI pidió confirmar que confía en «{best.detail}». Ábrelo ahí una vez, "
                "acepta «Yes, I trust this folder» y vuelve a medir."
                if code == "NEEDS_WORKSPACE_TRUST" and best.detail
                else None
            )
            self._fail_run(cli_id, code, detail, resolved=resolved)
            return
        path = str(resolved.get("path") or "")
        if not clean_version(resolved.get("version")):
            self._fail_run(cli_id, "VERSION_UNKNOWN", resolved=resolved)
            return
        stamp = self._stamp(path)
        if not path or stamp is None:
            self._fail_run(cli_id, "CLI_NOT_INSTALLED", resolved=resolved)
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


def _paint_dot(item: Dict[str, Any], running: Optional[Dict[str, Any]]) -> None:
    measuring = running is not None and running.get("cli_id") == item["cli_id"]
    color, label = status_dot(item["state"], installed=bool(item["installed"]), measuring=measuring)
    item["dot"] = color
    item["dot_label"] = label


def _fail(code: str, message: str) -> Dict[str, Any]:
    return {"success": False, "code": code, "message": message}
