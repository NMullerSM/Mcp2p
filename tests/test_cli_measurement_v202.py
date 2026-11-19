"""Medición automática de CLIs (WP-5d): dominio, almacén con validación, servicio, sonda con PTY real y puente."""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.adapters.terminal.cli_catalog import CliCatalogService
from src.adapters.terminal.cli_measurement_store import MeasurementStore, apply_measurements, binary_stamp, validate_record
from src.core.domain.cli_measurement import (
    Attempt,
    HookEvent,
    build_record,
    choose_best,
    evaluate_attempt,
    infer_normalized_events,
    prompt_lines,
    strip_ansi,
)
from src.core.services.cli_measurement_service import CliMeasurementService

PLATFORM = "linux"


def ev(name: str, t: int, ptype: str = "", sess=()) -> HookEvent:
    return HookEvent(event=name, t_ms=t, payload_type=ptype, session_fields=tuple(sess))


def good_attempt(mode="bracketed", **kw) -> Attempt:
    events = (ev("claude.SessionStart", 100, sess=("session_id",)), ev("claude.UserPromptSubmit", 1100),
              ev("claude.Stop", 2000))
    return Attempt(paste_mode=mode, spawn_ok=True, events=kw.pop("events", events), enter_at_ms=1000,
                   screen_after_paste=kw.pop("screen", "Prueba de MCP2P abc"), **kw)


# ---------------------------------------------------------------- dominio

def test_good_attempt_is_ok_with_turn_started_confirmation_and_event_map():
    res = evaluate_attempt(good_attempt())
    assert res.ok and res.reason_code is None
    assert res.confirm_event == "turn_started"
    assert res.mapped_events == {"claude.SessionStart": "session_started", "claude.UserPromptSubmit": "turn_started",
                                 "claude.Stop": "turn_ended"}
    assert res.session_id_field == "session_id"


def test_cli_without_turn_start_event_is_ok_with_empty_confirm_event():
    events = (ev("codex.notify", 1500, "agent-turn-complete"),)
    res = evaluate_attempt(good_attempt(events=events))
    assert res.ok and res.confirm_event == ""
    assert res.mapped_events == {"codex.notify.agent-turn-complete": "turn_ended"}


@pytest.mark.parametrize(
    "attempt, code",
    [
        (Attempt("bracketed", spawn_ok=False), "SPAWN_FAILED"),
        (Attempt("bracketed", spawn_ok=True, cancelled=True), "CANCELLED"),
        (Attempt("bracketed", spawn_ok=True, events=(), enter_at_ms=1), "NO_HOOK_EVENTS"),
        (Attempt("bracketed", spawn_ok=True, events=(), enter_at_ms=1, timed_out=True), "NO_HOOK_EVENTS"),
        (good_attempt(screen="^[[200~Prueba"), "PASTE_NOT_SUPPORTED"),
        (good_attempt(events=(ev("claude.UserPromptSubmit", 500), ev("claude.Stop", 2000))), "PREMATURE_SUBMIT"),
        (good_attempt(events=(ev("claude.SessionStart", 100),)), "NO_TURN_END"),
        (good_attempt(events=(ev("claude.SessionStart", 100), ev("claude.PermissionRequest", 1500))), "NEEDS_PERMISSION"),
    ],
)
def test_failures_have_a_specific_reason(attempt, code):
    res = evaluate_attempt(attempt)
    assert not res.ok and res.reason_code == code


def test_single_line_mode_ignores_marker_text_on_screen():
    assert evaluate_attempt(good_attempt(mode="single_line", screen="200~")).ok


def test_event_inference_keys_and_ansi():
    events = [ev("opencode.event", 1, "session.idle"), ev("opencode.event", 2, "permission.asked"), ev("x.unknown", 3)]
    assert infer_normalized_events(events) == {"opencode.event.session.idle": "turn_ended",
                                               "opencode.event.permission.asked": "permission_requested"}
    assert strip_ansi("\x1b[31mhola\x1b[0m \x1b]0;t\x07fin") == "hola fin"


def test_choose_best_prefers_first_valid_else_last_failure():
    ok = evaluate_attempt(good_attempt("single_line"))
    bad = evaluate_attempt(Attempt("bracketed", spawn_ok=False))
    assert choose_best([("bracketed", bad), ("single_line", ok)])[0] == "single_line"
    assert choose_best([("bracketed", bad)])[1].reason_code == "SPAWN_FAILED"


def test_prompt_is_two_short_lines():
    lines = prompt_lines("ab12")
    assert len(lines) == 2 and all(len(x) < 80 for x in lines) and "ab12" in lines[0]


# ---------------------------------------------------------------- almacén y catálogo

@pytest.fixture
def fake_binary(tmp_path):
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


def make_record(binary: Path, cli="claude", platform=PLATFORM, mode="bracketed") -> Dict[str, Any]:
    st = os.stat(binary)
    return build_record(cli_id=cli, platform=platform, today="2026-11-19", cli_version="2.1.325",
                        binary_path=str(binary), binary_mtime_ns=st.st_mtime_ns, binary_size=st.st_size,
                        result=evaluate_attempt(good_attempt(mode)), paste_mode=mode)


def svc_with(store: MeasurementStore) -> CliCatalogService:
    return CliCatalogService(measurements=store, include_measurements=True, platform=PLATFORM)


def test_shipped_catalog_has_nothing_verified_without_measurements(tmp_path):
    svc = svc_with(MeasurementStore(tmp_path / "none.json"))
    for cli in ("claude", "codex", "opencode", "antigravity"):
        spec = svc.get_spec(cli)
        assert not spec.capability_verified("activity", PLATFORM) and not spec.capability_verified("delivery", PLATFORM)


def test_valid_measurement_verifies_activity_and_delivery_only_for_that_cli_and_platform(tmp_path, fake_binary):
    store = MeasurementStore(tmp_path / "m.json")
    store.put(make_record(fake_binary))
    svc = svc_with(store)
    spec = svc.get_spec("claude")
    assert spec.capability_verified("activity", PLATFORM) and spec.capability_verified("delivery", PLATFORM)
    assert not spec.capability_verified("activity", "win32")
    assert spec.delivery["paste_mode"] == "bracketed" and spec.delivery["confirm_event"] == "turn_started"
    assert spec.activity["events"]["claude.Stop"] == "turn_ended"
    assert not svc.get_spec("codex").capability_verified("activity", PLATFORM)
    assert not svc.get_spec("claude").capability_verified("role_launch", PLATFORM), "solo activity y delivery"
    # el servicio por defecto (sin archivo de mediciones propio) sigue sin ver nada
    assert not CliCatalogService().get_spec("claude").capability_verified("activity", PLATFORM)


def test_catalog_with_own_repo_path_ignores_measurements_by_default(tmp_path, fake_binary):
    from src.adapters.terminal.cli_catalog import default_repo_catalog_path

    store = MeasurementStore(tmp_path / "m.json")
    store.put(make_record(fake_binary))
    svc = CliCatalogService(repo_catalog_path=default_repo_catalog_path(), measurements=store, platform=PLATFORM)
    assert not svc.get_spec("claude").capability_verified("activity", PLATFORM)


def test_changed_binary_makes_the_measurement_stale_and_inert(tmp_path, fake_binary):
    store = MeasurementStore(tmp_path / "m.json")
    store.put(make_record(fake_binary))
    fake_binary.write_text("#!/bin/sh\necho actualizado\n")
    svc = svc_with(store)
    assert not svc.get_spec("claude").capability_verified("activity", PLATFORM)
    _blocks, why = validate_record(store.get("claude", PLATFORM), svc.get_spec("claude"), PLATFORM)
    assert why == "stale"
    fake_binary.unlink()
    assert not svc_with(store).get_spec("claude").capability_verified("delivery", PLATFORM)


@pytest.mark.parametrize(
    "tweak",
    [
        lambda r: r["activity"].update(hook_install="codex_notify"),
        lambda r: r["activity"].update(events={"claude.SessionStart": "session_started"}),
        lambda r: r["activity"].update(events={"x y": "turn_ended"}),
        lambda r: r["delivery"].update(paste_mode="raw"),
        lambda r: r["delivery"].update(submit_delay_ms=5),
        lambda r: r["delivery"].update(platforms=["linux", "win32"]),
        lambda r: r["delivery"].update(status="unverified"),
        lambda r: r.update(platform="win32"),
        lambda r: r.update(schema=99),
        lambda r: r.update(cli_id="custom_shell"),
        lambda r: r.update(binary={"path": 5}),
    ],
)
def test_forged_or_malformed_records_are_ignored(tmp_path, fake_binary, tweak):
    record = make_record(fake_binary)
    tweak(record)
    store = MeasurementStore(tmp_path / "m.json")
    store.put(record)
    svc = svc_with(store)
    assert not svc.get_spec("claude").capability_verified("activity", PLATFORM)
    assert not svc.get_spec("claude").capability_verified("delivery", PLATFORM)


def test_store_roundtrip_forget_permissions_and_garbage(tmp_path, fake_binary):
    path = tmp_path / "sub" / "m.json"
    store = MeasurementStore(path)
    store.put(make_record(fake_binary))
    assert store.get("claude", PLATFORM)["cli_version"] == "2.1.325"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.forget("claude", PLATFORM) is True and store.forget("claude", PLATFORM) is False
    path.write_text("{no es json")
    assert MeasurementStore(path).records() == {}
    path.write_text(json.dumps({"schema": 1, "records": {"claude:linux": "texto"}}))
    assert MeasurementStore(path).records() == {}


def test_apply_measurements_returns_same_catalog_when_empty(tmp_path):
    svc = CliCatalogService(include_measurements=False)
    base = svc.catalog()
    assert apply_measurements(base, MeasurementStore(tmp_path / "x.json"), PLATFORM) is base


# ---------------------------------------------------------------- servicio

class FakeEnv:
    def __init__(self, tmp_path, fake_binary):
        self.store = MeasurementStore(tmp_path / "svc.json")
        self.binary = fake_binary
        self.attempts: List[Attempt] = []
        self.calls: List[str] = []
        self.installed = True
        self.gate: Optional[threading.Event] = None
        self.svc = CliMeasurementService(
            probe_run=self.probe,
            list_clis=lambda: [{"cli_id": "claude", "display_name": "Claude Code"},
                               {"cli_id": "cursor", "display_name": "Cursor"}],
            resolve=lambda cli: {"path": str(fake_binary), "version": "2.1.325"} if self.installed else None,
            stamp=binary_stamp,
            record_state=self.record_state,
            save=self.store.put,
            forget=lambda cli: self.store.forget(cli, PLATFORM),
            platform=PLATFORM,
            today=lambda: "2026-11-19",
        )

    def record_state(self, cli):
        rec = self.store.get(cli, PLATFORM)
        spec = CliCatalogService(include_measurements=False).get_spec(cli)
        if rec is None:
            return "none", None
        _b, why = validate_record(rec, spec, PLATFORM)
        return {"ok": "measured", "stale": "stale"}.get(why, "none"), rec

    def probe(self, cli_id, *, paste_mode, progress, cancel, step_offset, steps_total):
        self.calls.append(paste_mode)
        progress(f"{cli_id}:1", step_offset + 1, steps_total, "paso")
        if self.gate is not None:
            self.gate.wait(5)
        if cancel.is_set():
            return Attempt(paste_mode, True, cancelled=True)
        return self.attempts.pop(0)


@pytest.fixture
def env(tmp_path, fake_binary):
    return FakeEnv(tmp_path, fake_binary)


def item(status, cli="claude"):
    return [i for i in status["items"] if i["cli_id"] == cli][0]


def test_successful_measurement_saves_and_reports_measured_with_restart_note(env):
    env.attempts = [good_attempt()]
    assert env.svc.start("claude")["started"] is True
    assert env.svc.wait()
    st = env.svc.status()
    assert st["running"] is None
    it = item(st)
    assert it["state"] == "measured" and it["paste_mode"] == "bracketed" and it["confirm_event"] == "turn_started"
    assert it["cli_version"] == "2.1.325" and it["measured_at"] == "2026-11-19" and it["restart_needed"] is True
    assert env.calls == ["bracketed"]
    assert item(st, "cursor")["state"] == "unsupported"


def test_bracketed_failure_retries_with_single_line_and_records_that_mode(env):
    env.attempts = [good_attempt(events=(ev("claude.UserPromptSubmit", 500), ev("claude.Stop", 2000))),
                    good_attempt("single_line")]
    env.svc.start("claude")
    env.svc.wait()
    assert env.calls == ["bracketed", "single_line"]
    assert item(env.svc.status())["paste_mode"] == "single_line"


@pytest.mark.parametrize("attempt, code", [
    (Attempt("bracketed", True, events=(), enter_at_ms=1), "NO_HOOK_EVENTS"),
    (good_attempt(events=(ev("claude.SessionStart", 1),)), "NO_TURN_END"),
    (Attempt("bracketed", False), "SPAWN_FAILED"),
])
def test_failure_is_reported_without_enabling_anything(env, attempt, code):
    env.attempts = [attempt]
    env.svc.start("claude")
    env.svc.wait()
    it = item(env.svc.status())
    assert it["state"] == "failed" and it["reason_code"] == code and it["reason_text"]
    assert env.store.get("claude", PLATFORM) is None
    assert env.calls == ["bracketed"], "solo el fallo de pegado se reintenta"


def test_busy_unsupported_not_installed_and_cancel(env):
    env.gate = threading.Event()
    env.attempts = [good_attempt()]
    assert env.svc.start("claude")["success"] is True
    assert env.svc.start("claude")["code"] == "MEASUREMENT_BUSY"
    running = env.svc.status()["running"]
    assert running["cli_id"] == "claude" and running["steps_total"] == 10 and running["message"]
    env.svc.cancel()
    env.gate.set()
    env.svc.wait()
    it = item(env.svc.status())
    assert it["state"] == "failed" and it["reason_code"] == "CANCELLED"
    assert env.svc.start("cursor")["code"] == "UNSUPPORTED_CLI"
    assert env.svc.start(5)["code"] == "UNSUPPORTED_CLI"
    env.installed = False
    assert env.svc.start("claude")["code"] == "CLI_NOT_INSTALLED"
    assert item(env.svc.status())["installed"] is False


def test_probe_crash_does_not_leave_the_service_running(env):
    def boom(*a, **k):
        raise RuntimeError("se rompió")

    env.svc._probe_run = boom
    env.svc.start("claude")
    env.svc.wait()
    st = env.svc.status()
    assert st["running"] is None and item(st)["state"] == "failed"


def test_forget_removes_the_measurement(env):
    env.attempts = [good_attempt()]
    env.svc.start("claude")
    env.svc.wait()
    assert env.svc.forget("claude")["removed"] is True
    assert item(env.svc.status())["state"] == "not_measured"


def test_stale_state_is_reported_after_binary_change(env, fake_binary):
    env.attempts = [good_attempt()]
    env.svc.start("claude")
    env.svc.wait()
    fake_binary.write_text("#!/bin/sh\necho nuevo\n")
    assert item(env.svc.status())["state"] == "stale"


# ---------------------------------------------------------------- sonda con PTY real (POSIX)

posix_only = pytest.mark.skipif(os.name != "posix", reason="la sonda con un CLI falso usa un PTY POSIX")
FAKE = str(Path(__file__).resolve().parent / "helpers" / "fake_agent_cli.py")


def run_probe(mode: str):
    from src.adapters.terminal.cli_measurement import HeadlessCliProbe, ProbeTimings

    probe = HeadlessCliProbe(
        CliCatalogService(include_measurements=False),
        command_override=[sys.executable, FAKE, mode],
        timings=ProbeTimings(startup_cap_s=8, startup_quiet_s=0.8, startup_min_s=0.5, after_paste_s=0.5,
                             submit_delay_s=0.5, turn_end_cap_s=8, turn_start_grace_s=2),
    )
    steps: List[str] = []
    attempt = probe.run("claude", paste_mode="bracketed", progress=lambda s, d, t, m: steps.append(m),
                        cancel=threading.Event())
    return attempt, steps


@posix_only
def test_probe_with_a_well_behaved_fake_cli_is_ok():
    attempt, steps = run_probe("ok")
    res = evaluate_attempt(attempt)
    assert res.ok, (res, attempt)
    assert res.confirm_event == "turn_started"
    assert len(steps) == 5 and steps[-1].startswith("Cerrando")


@posix_only
def test_probe_detects_a_cli_without_bracketed_paste():
    attempt, _ = run_probe("no_paste")
    res = evaluate_attempt(attempt)
    assert not res.ok and res.reason_code in ("PASTE_NOT_SUPPORTED", "PREMATURE_SUBMIT")


@posix_only
def test_probe_reports_a_silent_cli_and_a_cli_that_never_ends_its_turn():
    silent, _ = run_probe("silent")
    assert evaluate_attempt(silent).reason_code == "NO_HOOK_EVENTS"
    from src.adapters.terminal.cli_measurement import HeadlessCliProbe, ProbeTimings

    probe = HeadlessCliProbe(
        CliCatalogService(include_measurements=False),
        command_override=[sys.executable, FAKE, "no_end"],
        timings=ProbeTimings(startup_cap_s=8, startup_quiet_s=0.8, startup_min_s=0.5, after_paste_s=0.5,
                             submit_delay_s=0.5, turn_end_cap_s=2),
    )
    attempt = probe.run("claude", paste_mode="bracketed", progress=lambda *a: None, cancel=threading.Event())
    assert evaluate_attempt(attempt).reason_code in ("TIMEOUT", "NO_TURN_END")


@posix_only
def test_probe_can_be_cancelled_and_cleans_its_temp_dir():
    import glob
    import tempfile

    from src.adapters.terminal.cli_measurement import HeadlessCliProbe, ProbeTimings

    cancel = threading.Event()
    cancel.set()
    probe = HeadlessCliProbe(CliCatalogService(include_measurements=False),
                             command_override=[sys.executable, FAKE, "ok"],
                             timings=ProbeTimings(startup_cap_s=3, startup_quiet_s=0.5, startup_min_s=0.2))
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "mcp2p-medicion-*")))
    attempt = probe.run("claude", paste_mode="bracketed", progress=lambda *a: None, cancel=cancel)
    assert attempt.cancelled
    assert set(glob.glob(os.path.join(tempfile.gettempdir(), "mcp2p-medicion-*"))) <= before


# ---------------------------------------------------------------- puente

@pytest.fixture
def bridge(tmp_path, monkeypatch):
    from src.app.api_bridge import ApiBridge
    from src.storage.db import DatabaseManager

    monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)
    monkeypatch.setenv("MCP2P_MEASUREMENTS_FILE", str(tmp_path / "bridge_meas.json"))
    b = ApiBridge(db_manager=DatabaseManager(db_path=str(tmp_path / "gui.db")))
    try:
        yield b
    finally:
        b.core_kernel.close()


def test_bridge_measure_asks_confirmation_and_never_remembers(bridge):
    res = bridge.pty_measure_cli("claude")
    assert res["success"] is False and res["code"] == "CONFIRMATION_REQUIRED"
    assert res["confirmation"]["rememberable"] is False
    assert "claude" in res["confirmation"]["title"].lower() or "Claude" in res["confirmation"]["title"]
    assert bridge.pty_measure_cli("no_existe")["code"] == "UNSUPPORTED_CLI"


def test_bridge_status_lists_catalog_clis_and_unsupported_ones(bridge):
    st = bridge.pty_get_cli_measurements()
    assert st["success"] is True and st["running"] is None
    by_id = {i["cli_id"]: i for i in st["items"]}
    assert by_id["claude"]["state"] in ("not_measured", "failed") and by_id["cursor"]["state"] == "unsupported"
    assert "custom_shell" not in by_id


def test_bridge_measured_cli_shows_verified_in_delivery_overview(bridge, tmp_path, fake_binary):
    store = bridge.extensions_ui._catalog_service().measurement_store()
    store.put(make_record(fake_binary, platform=bridge.extensions_ui._catalog_service().platform))
    overview = bridge.relay_get_delivery_overview()
    row = [r for r in overview["verified"] if r["cli_id"] == "claude" and r["platform"] == bridge.extensions_ui._catalog_service().platform]
    if bridge.extensions_ui._catalog_service().platform == PLATFORM:
        assert row[0]["verified"] is True
    assert bridge.pty_forget_cli_measurement("claude")["success"] is True
    again = bridge.relay_get_delivery_overview()
    assert not [r for r in again["verified"] if r["cli_id"] == "claude" and r["verified"]]
