"""Arreglos de la revision 34 (integracion real GUI + xterm + puente)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_SIG_BITS = {
    "SIGINT": signal.SIGINT,
    "SIGQUIT": signal.SIGQUIT,
    "SIGPIPE": signal.SIGPIPE,
}


def _ignored_signals(status_text: str) -> int:
    for line in status_text.splitlines():
        if line.startswith("SigIgn:"):
            return int(line.split()[1], 16)
    raise AssertionError("sin linea SigIgn en /proc/self/status")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="lee /proc/self/status (Linux)")
def test_egm81_trampoline_restores_default_signals_before_exec() -> None:
    """El CLI del panel no hereda SIG_IGN: ni el SIGPIPE que ignora Python ni un SIGINT ignorado por quien
    lanzo la GUI (`./run.sh &` desde un script)."""

    def _ignore_like_a_background_job() -> None:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGQUIT, signal.SIG_IGN)

    env = dict(os.environ, PYTHONPATH=str(ROOT))
    proc = subprocess.run(
        [sys.executable, "-m", "src.app.main", "--pty-exec", "--", "cat", "/proc/self/status"],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        preexec_fn=_ignore_like_a_background_job,
    )
    assert proc.returncode == 0, proc.stderr
    ignored = _ignored_signals(proc.stdout)
    for name, sig in _SIG_BITS.items():
        assert not ignored & (1 << (int(sig) - 1)), f"{name} sigue ignorada en el CLI del panel"


def test_egm82_auto_confirm_env_is_ignored_outside_pytest() -> None:
    """Fuera de pytest `MCP2P_TEST_AUTO_CONFIRM=1` no desactiva la confirmacion nativa: sin ventana se
    rechaza con CONFIRMATION_UNAVAILABLE en lugar de aceptarse en silencio."""
    code = (
        "import json, sys\n"
        "from src.app.native_confirm import NativeConfirmationService\n"
        "assert 'pytest' not in sys.modules\n"
        "svc = NativeConfirmationService(lambda: None)\n"
        "print(json.dumps(svc.confirm('Instalar', 'npm install -g x')))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(ROOT), MCP2P_TEST_AUTO_CONFIRM="1")
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert '"CONFIRMATION_UNAVAILABLE"' in proc.stdout.strip().splitlines()[-1]


def test_egm82_auto_confirm_env_still_works_inside_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.native_confirm import NativeConfirmationService

    monkeypatch.setenv("MCP2P_TEST_AUTO_CONFIRM", "1")
    assert NativeConfirmationService(lambda: None).confirm("t", "m") is None


# ---------------------------------------------------------------------------------------------
# EGM-83: el Loop Breaker no contaba el trafico real del Hub (todo va a la cola "*")
# ---------------------------------------------------------------------------------------------


def _rfc_relay(max_rounds: int = 6):
    from conftest import make_test_db
    from src.a2a.bus import A2ABus
    from src.core.services.swarm_relay_service import SwarmRelayOrchestrator

    db = make_test_db()
    db.create_project(name="relay", base_path=str(Path(db.db_path).parent / "ws"), init_git=False, project_uuid="p")
    for node in ("node-a", "node-b"):
        db.register_or_update_node(node_uuid=node, developer_name=node, agent_id="claude")
    bus = A2ABus(db=db)
    return db, bus, SwarmRelayOrchestrator(bus=bus, max_relay_rounds=max_rounds)


def _proposal(bus, sender: str, session: str, card: str = "C3") -> None:
    bus.send_proposal(project_uuid="p", sender_node_uuid=sender, session_id=session, component="c",
                      intent="i", rationale="r", diff_ref="", impact="", card_id=card)


def _counter(bus, sender: str, session: str, card: str = "C3") -> None:
    bus.send_counter_proposal(project_uuid="p", sender_node_uuid=sender, session_id=session, critique="no",
                              alternative={}, verdict_signal="REJECT", card_id=card)


def test_egm83_rfc_pingpong_through_the_real_bus_trips_the_breaker() -> None:
    """Medido antes del arreglo con el Hub real por HTTP: 8 turnos RFC alternos A<->B dejaban dos claves
    `C3|*|A` y `C3|*|B` con 1 ronda cada una y el freno no saltaba nunca."""
    db, bus, orch = _rfc_relay()
    try:
        _proposal(bus, "node-a", "S1")
        for i in range(11):
            _counter(bus, "node-b" if i % 2 == 0 else "node-a", "S1")
        state = orch.get_loop_breaker_state(project_uuid="p", card_id="C3")
        assert state["status"] == "LOOP_BREAKER_TRIPPED", state
        assert state["relay_rounds"] == 6
        assert orch.list_tripped_loop_breakers() == ["p:C3|s:S1|*"]
        # el reinicio por tarjeta lo libera
        assert orch.reset_loop_breaker(project_uuid="p", card_id="C3") is True
        assert orch.get_loop_breaker_state(project_uuid="p", card_id="C3")["status"] == "ACTIVE"
    finally:
        orch.close()
        db.close()


def test_egm83_legitimate_rfc_sessions_and_unattributed_events_do_not_trip() -> None:
    db, bus, orch = _rfc_relay()
    try:
        # tres RFC completos (A propone, B contesta, A insiste) sobre la MISMA tarjeta, sesiones distintas
        for n in range(3):
            _proposal(bus, "node-a", f"S{n}")
            _counter(bus, "node-b", f"S{n}")
            _counter(bus, "node-a", f"S{n}")
        # 40 eventos de dominio sin remitente atribuible (p. ej. ediciones kanban) sobre la misma tarjeta
        for i in range(40):
            res = orch.enqueue_assignment("*", {"project_uuid": "p", "card_id": "C3", "n": i})
            assert res["success"], res
        assert orch.list_tripped_loop_breakers() == []
        assert orch.get_loop_breaker_state(project_uuid="p", card_id="C3")["status"] == "ACTIVE"
    finally:
        orch.close()
        db.close()


# ---------------------------------------------------------------------------------------------
# EGM-84: el empaquetado no reutiliza un frontend/dist viejo
# ---------------------------------------------------------------------------------------------


def _load_build_desktop():
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_desktop_under_test", ROOT / "build_desktop.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_egm84_package_always_rebuilds_existing_frontend_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bd = _load_build_desktop()
    (tmp_path / "frontend" / "dist").mkdir(parents=True)
    (tmp_path / "frontend" / "node_modules").mkdir()
    (tmp_path / "frontend" / "dist" / "index.html").write_text("<html>vieja</html>", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(bd.shutil, "which", lambda name: "/usr/bin/npm")
    monkeypatch.setattr(bd.subprocess, "check_call", lambda cmd, cwd=None: calls.append((cmd, cwd)))
    bd.ensure_frontend_built(str(tmp_path))
    assert calls == [(["/usr/bin/npm", "run", "build"], str(tmp_path / "frontend"))]


def test_egm84_without_npm_uses_existing_dist_or_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bd = _load_build_desktop()
    monkeypatch.setattr(bd.shutil, "which", lambda name: None)
    monkeypatch.setattr(bd.subprocess, "check_call", lambda *a, **k: pytest.fail("no debe llamar a npm"))
    with pytest.raises(SystemExit):
        bd.ensure_frontend_built(str(tmp_path))
    (tmp_path / "frontend" / "dist").mkdir(parents=True)
    (tmp_path / "frontend" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    bd.ensure_frontend_built(str(tmp_path))  # aviso, sin excepcion
