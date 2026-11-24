"""Guardián de recursos de la ventana web (v2.0.2): umbral, enfriamiento, informe y contador de llamadas del puente."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from src.app.api_bridge import _make_public_rpc
from src.app.webview_guard import (
    DEFAULT_LIMIT_MB,
    RPC_STATS,
    RpcStats,
    WebviewResourceGuard,
    build_window_guard,
    limit_from_env,
    mode_from_env,
    result_text_bytes,
    write_report,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _guard(mb_values: List[float], trips: List[Dict[str, Any]], clock: _Clock, **kw: Any) -> WebviewResourceGuard:
    values = iter(mb_values)
    stats = kw.pop("stats", RpcStats(clock=clock))
    return WebviewResourceGuard(
        probe_mb=lambda: next(values),
        on_trip=trips.append,
        stats=stats,
        limit_mb=kw.pop("limit_mb", 1000.0),
        cooldown_s=kw.pop("cooldown_s", 120.0),
        clock=clock,
        **kw,
    )


def test_normal_usage_never_trips() -> None:
    trips: List[Dict[str, Any]] = []
    g = _guard([200, 250, 900, 1000], trips, _Clock())
    assert [g.sample_once() for _ in range(4)] == [None, None, None, None]
    assert trips == []


def test_trips_once_over_limit_then_waits_for_cooldown() -> None:
    clock = _Clock()
    trips: List[Dict[str, Any]] = []
    g = _guard([500, 1500, 2500, 2600, 2700], trips, clock)
    assert g.sample_once() is None
    first = g.sample_once()
    assert first is not None and first["rss_mb"] == 1500.0 and first["action"] == "reload"
    clock.now += 10
    assert g.sample_once() is None  # dentro del enfriamiento: no recarga en bucle
    clock.now += 200
    assert g.sample_once() is not None
    assert len(trips) == 2


def test_report_has_history_and_rpc_breakdown() -> None:
    clock = _Clock()
    stats = RpcStats(clock=clock)
    for _ in range(30):
        stats.record("pty_poll_output", 4096)
    stats.record("relay_audit_status", 0)
    trips: List[Dict[str, Any]] = []
    g = _guard([300, 3000], trips, clock, stats=stats)
    g.sample_once()
    report = g.sample_once()
    assert report is not None
    assert report["history_mb"] == [300.0, 3000.0]
    top = report["rpc_top_30s"][0]
    assert top["method"] == "pty_poll_output" and top["calls"] == 30 and top["text_kb"] == 120.0
    assert trips == [report]


def test_failing_probe_or_action_does_not_break_the_loop() -> None:
    def boom() -> float:
        raise RuntimeError("sin psutil")

    g = WebviewResourceGuard(probe_mb=boom, on_trip=lambda r: None)
    assert g.sample_once() is None

    def bad_trip(_report: Dict[str, Any]) -> None:
        raise OSError("ventana cerrada")

    g2 = WebviewResourceGuard(probe_mb=lambda: 5000.0, on_trip=bad_trip, limit_mb=1000.0)
    assert g2.sample_once() is not None  # el fallo de la acción se registra, no se propaga


def test_off_mode_does_not_start_a_thread() -> None:
    g = WebviewResourceGuard(probe_mb=lambda: 0.0, on_trip=lambda r: None, mode="off")
    g.start()
    assert g._thread is None
    g.stop()


def test_env_parsing_is_defensive() -> None:
    assert mode_from_env({}) == "reload"
    assert mode_from_env({"MCP2P_WEBVIEW_GUARD": "LOG"}) == "log"
    assert mode_from_env({"MCP2P_WEBVIEW_GUARD": "nonsense"}) == "reload"
    assert limit_from_env({}) == DEFAULT_LIMIT_MB
    assert limit_from_env({"MCP2P_WEBVIEW_LIMIT_MB": "abc"}) == DEFAULT_LIMIT_MB
    assert limit_from_env({"MCP2P_WEBVIEW_LIMIT_MB": "10"}) == DEFAULT_LIMIT_MB  # un límite ridículo se ignora
    assert limit_from_env({"MCP2P_WEBVIEW_LIMIT_MB": "4096"}) == 4096.0


def test_write_report_creates_json_and_tolerates_unwritable_dir(tmp_path: Path) -> None:
    path = write_report({"rss_mb": 3000}, tmp_path / "diagnostics")
    assert path is not None and json.loads(path.read_text(encoding="utf-8"))["rss_mb"] == 3000
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert write_report({"a": 1}, blocker / "sub") is None


class _FakeWindow:
    def __init__(self, current: Any) -> None:
        self.current = current
        self.loaded: List[str] = []

    def get_current_url(self) -> Any:
        if isinstance(self.current, Exception):
            raise self.current
        return self.current

    def load_url(self, url: str) -> None:
        self.loaded.append(url)


def test_window_guard_reloads_current_page_and_saves_report(tmp_path: Path) -> None:
    window = _FakeWindow("file:///app/index.html")
    g = build_window_guard(window, "file:///fallback.html", tmp_path, mode="reload", limit_mb=1000.0)
    g._probe_mb = lambda: 4000.0
    assert g.sample_once() is not None
    assert window.loaded == ["file:///app/index.html"]
    assert len(list(tmp_path.glob("webview_runaway_*.json"))) == 1


def test_window_guard_log_mode_does_not_reload(tmp_path: Path) -> None:
    window = _FakeWindow("file:///app/index.html")
    g = build_window_guard(window, "file:///fallback.html", tmp_path, mode="log", limit_mb=1000.0)
    g._probe_mb = lambda: 4000.0
    assert g.sample_once() is not None
    assert window.loaded == []
    assert len(list(tmp_path.glob("webview_runaway_*.json"))) == 1


def test_window_guard_falls_back_when_current_url_is_unavailable(tmp_path: Path) -> None:
    window = _FakeWindow(RuntimeError("sin ventana"))
    g = build_window_guard(window, "file:///fallback.html", tmp_path, mode="reload", limit_mb=1000.0)
    g._probe_mb = lambda: 4000.0
    g.sample_once()
    assert window.loaded == ["file:///fallback.html"]


def test_rpc_wrapper_counts_calls_and_output_text_even_on_error() -> None:
    before = {r["method"]: r["calls"] for r in RPC_STATS.top(30.0, limit=50)}

    def pty_poll_output_fake(pane_id: str, since: int = 0) -> Dict[str, Any]:
        return {"text": "x" * 2048}

    def failing(pane_id: str) -> Dict[str, Any]:
        raise ValueError("fallo")

    ok = _make_public_rpc("zz_fake_poll", pty_poll_output_fake)
    bad = _make_public_rpc("zz_fake_fail", failing)
    assert ok("pane_1")["text"].startswith("x")
    try:
        bad("pane_1")
    except ValueError:
        pass
    after = {r["method"]: r for r in RPC_STATS.top(30.0, limit=50)}
    assert after["zz_fake_poll"]["calls"] == before.get("zz_fake_poll", 0) + 1
    assert after["zz_fake_poll"]["text_kb"] >= 2.0
    assert after["zz_fake_fail"]["calls"] == before.get("zz_fake_fail", 0) + 1


def test_result_text_bytes_ignores_non_text_results() -> None:
    assert result_text_bytes({"text": "abc"}) == 3
    assert result_text_bytes({"success": True}) == 0
    assert result_text_bytes(None) == 0
    assert result_text_bytes("abc") == 0
