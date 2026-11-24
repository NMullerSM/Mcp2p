"""Guardián de recursos de la ventana web (v2.0.2).

El proceso que dibuja la interfaz (WebKitWebProcess, WebView2…) normalmente ocupa unos cientos de MB. Si crece sin
control deja a todo el equipo sin memoria y la propia aplicación no puede evitarlo desde dentro de la página.
Este módulo vigila ese proceso desde Python y, al pasar un umbral claramente anormal, deja un informe con la
evolución de la memoria y las llamadas del puente de los últimos segundos, y recarga la vista para soltar la
memoria (las terminales siguen vivas: viven en el backend).

Sin dependencias de la ventana: la sonda de memoria y la acción son funciones inyectadas.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("mcp2p.app.webview_guard")

ENV_MODE = "MCP2P_WEBVIEW_GUARD"  # reload (por defecto) | log | off
ENV_LIMIT_MB = "MCP2P_WEBVIEW_LIMIT_MB"
DEFAULT_LIMIT_MB = 2048.0
DEFAULT_INTERVAL_S = 3.0
DEFAULT_COOLDOWN_S = 120.0
HISTORY_SAMPLES = 40
_MODES = ("reload", "log", "off")
_WEB_PROCESS_MARKERS = ("webkitwebproc", "msedgewebview2", "webcontent")  # `comm` de Linux se trunca a 15 letras


class RpcStats:
    """Cuenta las llamadas del puente por método (y el texto de salida que devuelven) en una ventana móvil."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, horizon_s: float = 120.0, max_events: int = 50_000):
        self._clock = clock
        self._horizon_s = float(horizon_s)
        self._events: Deque[Tuple[float, str, int]] = collections.deque(maxlen=max_events)
        self._lock = threading.Lock()

    def record(self, name: str, text_bytes: int = 0) -> None:
        now = self._clock()
        with self._lock:
            self._events.append((now, name, int(text_bytes)))
            cutoff = now - self._horizon_s
            while self._events and self._events[0][0] < cutoff:
                self._events.popleft()

    def top(self, window_s: float = 30.0, limit: int = 8) -> List[Dict[str, Any]]:
        cutoff = self._clock() - float(window_s)
        agg: Dict[str, List[int]] = {}
        with self._lock:
            for ts, name, nbytes in self._events:
                if ts >= cutoff:
                    row = agg.setdefault(name, [0, 0])
                    row[0] += 1
                    row[1] += nbytes
        rows = [
            {"method": n, "calls": c, "calls_per_s": round(c / window_s, 2), "text_kb": round(b / 1024, 1)}
            for n, (c, b) in agg.items()
        ]
        rows.sort(key=lambda r: (r["calls"], r["text_kb"]), reverse=True)
        return rows[:limit]


RPC_STATS = RpcStats()


def result_text_bytes(result: Any) -> int:
    """Tamaño del texto de salida de una respuesta del puente (solo `text`); barato y sin serializar."""
    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str):
            return len(text)
    return 0


def default_webview_rss_mb() -> float:
    """RSS (MB) del proceso de ventana web más grande entre los descendientes de esta aplicación (0 si no hay)."""
    import psutil

    best = 0
    for child in psutil.Process().children(recursive=True):
        try:
            if any(marker in child.name().lower() for marker in _WEB_PROCESS_MARKERS):
                best = max(best, child.memory_info().rss)
        except (psutil.Error, OSError):
            continue
    return best / float(2**20)


def mode_from_env(environ: Optional[Dict[str, str]] = None) -> str:
    raw = ((environ if environ is not None else os.environ).get(ENV_MODE) or "reload").strip().lower()
    return raw if raw in _MODES else "reload"


def limit_from_env(environ: Optional[Dict[str, str]] = None) -> float:
    try:
        value = float((environ if environ is not None else os.environ).get(ENV_LIMIT_MB) or DEFAULT_LIMIT_MB)
    except ValueError:
        return DEFAULT_LIMIT_MB
    return value if value >= 256 else DEFAULT_LIMIT_MB


def write_report(report: Dict[str, Any], directory: Path) -> Optional[Path]:
    """Guarda el informe como JSON legible en `directory`; None si no se pudo escribir."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"webview_runaway_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path
    except OSError as exc:
        logger.warning("No se pudo guardar el informe del guardián de la ventana: %s", exc)
        return None


class WebviewResourceGuard:
    def __init__(
        self,
        *,
        probe_mb: Callable[[], float],
        on_trip: Callable[[Dict[str, Any]], None],
        stats: Optional[RpcStats] = None,
        mode: str = "reload",
        limit_mb: float = DEFAULT_LIMIT_MB,
        interval_s: float = DEFAULT_INTERVAL_S,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._probe_mb = probe_mb
        self._on_trip = on_trip
        self._stats = stats or RPC_STATS
        self._mode = mode if mode in _MODES else "reload"
        self._limit_mb = float(limit_mb)
        self._interval_s = float(interval_s)
        self._cooldown_s = float(cooldown_s)
        self._clock = clock
        self._history: Deque[float] = collections.deque(maxlen=HISTORY_SAMPLES)
        self._last_trip: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def mode(self) -> str:
        return self._mode

    def sample_once(self) -> Optional[Dict[str, Any]]:
        """Toma una muestra; devuelve el informe si esta muestra disparó el guardián."""
        try:
            mb = float(self._probe_mb())
        except Exception:
            return None
        self._history.append(round(mb, 1))
        now = self._clock()
        if mb <= self._limit_mb:
            return None
        if self._last_trip is not None and now - self._last_trip < self._cooldown_s:
            return None
        self._last_trip = now
        report: Dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "rss_mb": round(mb, 1),
            "limit_mb": self._limit_mb,
            "action": self._mode,
            "history_mb": list(self._history),
            "interval_s": self._interval_s,
            "rpc_top_30s": self._stats.top(30.0),
        }
        logger.warning(
            "La ventana web ocupa %.0f MB (límite %.0f MB); acción: %s. Llamadas del puente más frecuentes: %s",
            mb, self._limit_mb, self._mode, report["rpc_top_30s"][:3],
        )
        try:
            self._on_trip(report)
        except Exception as exc:
            logger.warning("La acción del guardián de la ventana falló: %s", exc)
        return report

    def start(self) -> None:
        if self._mode == "off" or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="webview-guard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_s):
            self.sample_once()


def build_window_guard(
    window: Any,
    fallback_url: str,
    report_dir: Path,
    *,
    mode: Optional[str] = None,
    limit_mb: Optional[float] = None,
) -> WebviewResourceGuard:
    """Guardián para una ventana PyWebView: al dispararse guarda el informe y (modo `reload`) recarga la vista."""
    chosen_mode = mode or mode_from_env()

    def on_trip(report: Dict[str, Any]) -> None:
        path = write_report(report, report_dir)
        if path is not None:
            logger.warning("Informe de la ventana web guardado en %s", path)
        if chosen_mode != "reload":
            return
        url: Optional[str] = None
        try:
            url = window.get_current_url()
        except Exception:
            url = None
        window.load_url(url or fallback_url)

    return WebviewResourceGuard(
        probe_mb=default_webview_rss_mb,
        on_trip=on_trip,
        mode=chosen_mode,
        limit_mb=limit_mb if limit_mb is not None else limit_from_env(),
    )
