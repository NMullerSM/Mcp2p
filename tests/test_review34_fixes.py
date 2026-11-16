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
