#!/usr/bin/env python3
"""
Comprobacion de arranque de la GUI real (plan 2026-11-15, seccion 1).

Lanza `./run.sh` en un subproceso con una base (`MCP2P_DB_PATH`) y un `HOME` desechables y
`PYWEBVIEW_LOG=debug`, espera unos segundos, mata el arbol de procesos y FALLA si el log contiene
`EvalError`, `Refused to`, `Content Security Policy` o `Traceback`, o si la ventana nunca llego a abrirse e inyectar la API de PyWebView.

Regresion que cubre: la CSP de EGM-53 dejo `window.pywebview.api` vacia (PyWebView la crea con
`new Function`) y ningun test lo detecto porque ninguno abria la ventana real.

Codigos de salida: 0 = ok, 1 = fallo, 77 = omitido (no hay `DISPLAY` ni `WAYLAND_DISPLAY`).

Uso:
    python scripts/check_gui_boot.py [--seconds 20] [--log-file RUTA] [--allow-frontend-build]

Por defecto fija `MCP2P_SKIP_FRONTEND_BUILD=1` para que `run.sh` no ejecute `npm` (la comprobacion
valida el `frontend/dist` ya construido); `--allow-frontend-build` lo desactiva.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Mapping, Optional, Sequence

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_SKIP = 77

FORBIDDEN_PATTERNS: Sequence["re.Pattern[str]"] = (
    re.compile(r"EvalError"),
    re.compile(r"Refused to"),
    re.compile(r"Content Security Policy", re.IGNORECASE),
    re.compile(r"Traceback"),
)
WINDOW_MARKER = "Iniciando ventana de escritorio"
# Con PYWEBVIEW_LOG=debug, PyWebView registra estos eventos cuando inyecta `window.pywebview.api`.
REQUIRED_MARKERS: Sequence[str] = (WINDOW_MARKER, "_pywebviewready event fired", "loaded event fired")
DEFAULT_SECONDS = 20.0
REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class BootCheckResult:
    exit_code: int
    reason: str
    findings: List[str] = field(default_factory=list)
    log: str = ""


def has_display(env: Mapping[str, str]) -> bool:
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def find_forbidden(log: str) -> List[str]:
    """Lineas del log que contienen alguno de los patrones prohibidos."""
    hits: List[str] = []
    for line in log.splitlines():
        if any(p.search(line) for p in FORBIDDEN_PATTERNS):
            hits.append(line.strip()[:300])
    return hits


def _kill_tree_without_killpg(proc: "subprocess.Popen[str]") -> None:
    """Windows no tiene grupos de procesos POSIX: se recorre el arbol con psutil."""
    import psutil

    try:
        parent = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        return
    victims = parent.children(recursive=True) + [parent]
    for p in victims:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(victims, timeout=5)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _kill_tree(proc: "subprocess.Popen[str]") -> None:
    if proc.poll() is not None:
        return
    if not hasattr(os, "killpg"):
        _kill_tree_without_killpg(proc)
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + 5.0
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def run_boot_check(
    *,
    seconds: float = DEFAULT_SECONDS,
    repo_root: Path = REPO_ROOT,
    command: Optional[Sequence[str]] = None,
    base_env: Optional[Mapping[str, str]] = None,
    allow_frontend_build: bool = False,
    required_markers: Sequence[str] = REQUIRED_MARKERS,
) -> BootCheckResult:
    env_in = dict(os.environ if base_env is None else base_env)
    if not has_display(env_in):
        return BootCheckResult(EXIT_SKIP, "sin DISPLAY ni WAYLAND_DISPLAY: no hay donde abrir la ventana")

    cmd = list(command) if command else [str(repo_root / "run.sh")]
    tmp = Path(tempfile.mkdtemp(prefix="mcp2p-gui-boot-"))
    try:
        home = tmp / "home"
        home.mkdir()
        env = dict(env_in)
        real_home = env_in.get("HOME", "")
        # Con otro HOME, X11 no encontraria la cookie de autenticacion por defecto.
        if "XAUTHORITY" not in env and real_home and (Path(real_home) / ".Xauthority").is_file():
            env["XAUTHORITY"] = str(Path(real_home) / ".Xauthority")
        env.update(
            {
                "HOME": str(home),
                "MCP2P_DB_PATH": str(tmp / "mcp2p_gui_boot.db"),
                "PYWEBVIEW_LOG": "debug",
                "PYTHONUNBUFFERED": "1",
            }
        )
        if not allow_frontend_build:
            env["MCP2P_SKIP_FRONTEND_BUILD"] = "1"
        # No heredar identidad ni credenciales del entorno real.
        for name in ("MCP2P_NODE_UUID", "MCP2P_SESSION_TOKEN"):
            env.pop(name, None)

        proc = subprocess.Popen(
            cmd,
            cwd=str(repo_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        chunks: List[str] = []

        def _reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                chunks.append(line)

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        deadline = time.monotonic() + max(1.0, seconds)
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.2)
        exited_early = proc.poll() is not None
        early_code = proc.returncode
        _kill_tree(proc)
        reader.join(timeout=5)
        log = "".join(chunks)

        findings = find_forbidden(log)
        if findings:
            return BootCheckResult(EXIT_FAIL, "el log de arranque contiene errores prohibidos", findings, log)
        if exited_early and early_code not in (0, None):
            return BootCheckResult(EXIT_FAIL, f"el proceso termino antes de tiempo con codigo {early_code}", [], log)
        missing = [m for m in required_markers if m not in log]
        if missing:
            return BootCheckResult(
                EXIT_FAIL, f"la ventana no llego a iniciarse o a inyectar la API (faltan eventos: {missing})", [], log
            )
        return BootCheckResult(EXIT_OK, "arranque limpio", [], log)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS, help="Segundos de espera antes de matar la GUI")
    parser.add_argument("--log-file", type=str, default=None, help="Guardar el log completo en esta ruta")
    parser.add_argument("--repo-root", type=str, default=str(REPO_ROOT), help="Raiz del repo que contiene run.sh")
    parser.add_argument("--allow-frontend-build", action="store_true", help="Permitir que run.sh compile el frontend (npm)")
    args = parser.parse_args(argv)

    result = run_boot_check(
        seconds=args.seconds,
        repo_root=Path(args.repo_root),
        allow_frontend_build=args.allow_frontend_build,
    )
    if args.log_file and result.log:
        Path(args.log_file).write_text(result.log, encoding="utf-8")

    if result.exit_code == EXIT_SKIP:
        print(f"[check_gui_boot] OMITIDO: {result.reason}")
    elif result.exit_code == EXIT_OK:
        print(f"[check_gui_boot] OK: {result.reason}")
    else:
        print(f"[check_gui_boot] FALLO: {result.reason}")
        for line in result.findings[:20]:
            print(f"  > {line}")
        tail = "\n".join(result.log.splitlines()[-25:])
        if tail:
            print("--- ultimas lineas del log ---")
            print(tail)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
