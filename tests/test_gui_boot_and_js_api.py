"""
T6 (plan 2026-11-15, seccion 1): la GUI real arranca limpia y `window.pywebview.api` NO queda vacia.

Regresion real: la CSP de EGM-53 prohibio `new Function`, que PyWebView usa para crear
`window.pywebview.api`; la API quedo vacia y ningun test lo vio porque ninguno abria la ventana.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from conftest import make_test_db

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_gui_boot.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_gui_boot", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_gui_boot"] = mod
    spec.loader.exec_module(mod)
    return mod


gui = _load_script()
ONLY_WINDOW = (gui.WINDOW_MARKER,)
FAKE_ENV = {"PATH": os.environ.get("PATH", ""), "DISPLAY": ":fake-for-tests"}


def _py(code: str) -> List[str]:
    return [sys.executable, "-c", code]


# ---------------------------------------------------------------------------
# Logica del script (con comandos falsos: no abre ninguna ventana)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_line",
    [
        "EvalError: Refused to evaluate a string as JavaScript",
        "Refused to apply inline style",
        "violates the following Content Security Policy directive",
        "content security policy blocked it",
        "Traceback (most recent call last):",
    ],
)
def test_check_fails_on_each_forbidden_pattern(bad_line: str) -> None:
    code = f"print('Iniciando ventana de escritorio'); print({bad_line!r})"
    res = gui.run_boot_check(seconds=1.5, command=_py(code), base_env=FAKE_ENV, required_markers=ONLY_WINDOW)
    assert res.exit_code == gui.EXIT_FAIL and res.findings, res


def test_check_passes_on_clean_boot_and_kills_the_process_tree() -> None:
    code = (
        "import os, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print('Iniciando ventana de escritorio', flush=True)\n"
        "print('CHILD', child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    res = gui.run_boot_check(seconds=1.5, command=_py(code), base_env=FAKE_ENV, required_markers=ONLY_WINDOW)
    assert res.exit_code == gui.EXIT_OK, res
    child_pid = int(next(l for l in res.log.splitlines() if l.startswith("CHILD")).split()[1])
    import time as _t
    deadline = _t.monotonic() + 5
    alive = True
    while _t.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            alive = False
            break
        try:  # un zombie aun responde a kill(0): comprobar su estado real
            if "Z" in Path(f"/proc/{child_pid}/stat").read_text().split(") ")[-1][:2]:
                alive = False
                break
        except OSError:
            alive = False
            break
        _t.sleep(0.1)
    assert alive is False, "el hijo del arranque sobrevivio al kill del grupo"


def test_check_uses_disposable_home_and_db_and_debug_logging() -> None:
    code = (
        "import os; print('Iniciando ventana de escritorio');"
        "print('HOME=' + os.environ['HOME']); print('DB=' + os.environ['MCP2P_DB_PATH']);"
        "print('LOG=' + os.environ['PYWEBVIEW_LOG']); print('SKIP=' + os.environ.get('MCP2P_SKIP_FRONTEND_BUILD',''))"
    )
    env = dict(FAKE_ENV, HOME="/home/real-user", MCP2P_NODE_UUID="node_real", MCP2P_DB_PATH="/real/db.sqlite")
    res = gui.run_boot_check(seconds=3, command=_py(code), base_env=env, required_markers=ONLY_WINDOW)
    kv = dict(l.split("=", 1) for l in res.log.splitlines() if "=" in l)
    assert kv["HOME"] != "/home/real-user" and "mcp2p-gui-boot-" in kv["HOME"]
    assert kv["DB"] != "/real/db.sqlite" and "mcp2p-gui-boot-" in kv["DB"]
    assert kv["LOG"] == "debug" and kv["SKIP"] == "1"
    assert not Path(kv["HOME"]).exists()  # se borra al terminar
    allow = gui.run_boot_check(seconds=3, command=_py(code), base_env=env, allow_frontend_build=True, required_markers=ONLY_WINDOW)
    assert "SKIP=" in allow.log and "SKIP=1" not in allow.log


def test_check_fails_when_window_never_starts_or_process_dies() -> None:
    silent = gui.run_boot_check(seconds=1.2, command=_py("import time; time.sleep(30)"), base_env=FAKE_ENV)
    assert silent.exit_code == gui.EXIT_FAIL and "ventana" in silent.reason
    # la ventana abre pero PyWebView nunca inyecta la API: tambien es un fallo
    half = gui.run_boot_check(
        seconds=1.5, command=_py("print('Iniciando ventana de escritorio', flush=True); import time; time.sleep(30)"),
        base_env=FAKE_ENV,
    )
    assert half.exit_code == gui.EXIT_FAIL and "_pywebviewready" in half.reason
    full = gui.run_boot_check(
        seconds=1.5,
        command=_py("print('Iniciando ventana de escritorio\\n_pywebviewready event fired\\nloaded event fired', flush=True); import time; time.sleep(30)"),
        base_env=FAKE_ENV,
    )
    assert full.exit_code == gui.EXIT_OK, full
    dies = gui.run_boot_check(seconds=5, command=_py("import sys; print('boom'); sys.exit(3)"), base_env=FAKE_ENV)
    assert dies.exit_code == gui.EXIT_FAIL and "codigo 3" in dies.reason


def test_check_is_skipped_without_display() -> None:
    res = gui.run_boot_check(seconds=1, command=_py("print('no deberia ejecutarse')"), base_env={"PATH": "/usr/bin"})
    assert res.exit_code == gui.EXIT_SKIP == 77
    assert res.log == ""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--seconds", "1"],
        env={"PATH": os.environ.get("PATH", "")}, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 77 and "OMITIDO" in proc.stdout


def test_run_sh_supports_skipping_the_frontend_build() -> None:
    assert "MCP2P_SKIP_FRONTEND_BUILD" in (ROOT / "run.sh").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Arranque de la GUI real
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.environ.get("MCP2P_SKIP_GUI_BOOT") == "1", reason="MCP2P_SKIP_GUI_BOOT=1")
def test_real_gui_boots_without_csp_or_js_errors() -> None:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        pytest.skip("sin DISPLAY ni WAYLAND_DISPLAY")
    if not (ROOT / "run.sh").is_file() or not (ROOT / "frontend" / "dist" / "index.html").is_file():
        pytest.skip("no hay run.sh o frontend/dist construido")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--seconds", "20"],
        capture_output=True, text=True, timeout=180, cwd=str(ROOT),
    )
    if proc.returncode == 77:
        pytest.skip(proc.stdout.strip())
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# Lo que PyWebView expone realmente (get_functions del SDK instalado)
# ---------------------------------------------------------------------------


def _exposed_functions(js_api: Any) -> Dict[str, List[str]]:
    """Ejecuta `webview.util.inject_pywebview` (el SDK real) sobre una ventana simulada."""
    import webview.util as wutil

    scripts: List[str] = []
    finished = threading.Event()
    window = MagicMock()
    window._js_api = js_api
    window._functions = {}
    window._expose_lock = threading.Lock()
    window.uid = "master"
    window.js_api_endpoint = ""
    window.state = {}

    def _run_js(code: str) -> None:
        scripts.append(code)

    window.run_js.side_effect = _run_js
    window.events.loaded.set.side_effect = finished.set
    wutil.inject_pywebview("gtk", window)
    assert finished.wait(timeout=20), "PyWebView no termino de inyectar la API"
    marker = '[{"func":'
    decoder = json.JSONDecoder()
    for code in scripts:
        idx = code.find(marker)
        if idx != -1:
            funcs, _ = decoder.raw_decode(code[idx:])
            return {f["func"]: f["params"] for f in funcs}
    raise AssertionError("PyWebView no emitio la lista de funciones")


def test_pywebview_sdk_exposes_the_public_api_to_js() -> None:
    from src.app.api_bridge import ApiBridge

    bridge = ApiBridge(db_manager=make_test_db())
    try:
        exposed = _exposed_functions(bridge.as_public_js_api())
    finally:
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()

    assert len(exposed) > 50, "la API expuesta esta (casi) vacia"
    for required in ("list_projects", "get_github_status"):
        assert required in exposed, required
    pty_methods = sorted(
        name for name in dir(ApiBridge) if name.startswith("pty_") and callable(getattr(ApiBridge, name))
    )
    assert {"pty_get_matrix_status", "pty_spawn_pane", "pty_poll_output", "pty_list_cli_catalog",
            "pty_set_cli_path", "pty_prepare_install", "pty_install_cli"} <= set(pty_methods)
    missing = [m for m in pty_methods if m not in exposed]
    assert not missing, f"metodos pty_* ausentes de window.pywebview.api: {missing}"

    # Superficie minima: nada privado, nada interno y ningun objeto anidado.
    assert not [n for n in exposed if n.startswith("_") or "." in n]
    for internal in ("as_public_js_api", "set_window", "set_transport", "ensure_local_node_registered"):
        assert internal not in exposed
    # Los argumentos de los metodos nuevos llegan al JS (no solo el nombre)
    assert "pty_install_cli" in exposed
