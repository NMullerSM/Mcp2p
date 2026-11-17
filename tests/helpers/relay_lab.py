"""Laboratorio de EGM-105 con procesos REALES: un Hub embebido en loopback y procesos «raiz de panel» que llaman al
Hub desde su arbol de procesos (hijos reales), igual que un CLI y su proxy stdio dentro de un PTY.

Portable (Linux y Windows): `sys.executable`, sin shells POSIX.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
HELPERS = Path(__file__).resolve().parent
CALL = HELPERS / "mcp_http_call.py"
PANE_ROOT = HELPERS / "pane_root.py"


class PaneRootProcess:
    """Proceso que hace de raiz del PTY de un panel; sus llamadas salen de un hijo suyo."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, str(PANE_ROOT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=str(ROOT),
        )

    @property
    def pid(self) -> int:
        return self.proc.pid

    def _ask(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps(cmd) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        assert line, "la raiz del panel termino sin responder"
        return json.loads(line)

    def call(self, url: str, token: str, tool: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        reply = self._ask({"op": "call", "url": url, "token": token, "tool": tool, "args": args or {}})
        assert reply.get("ok"), reply
        return reply["result"]

    def call_from_orphan(
        self, url: str, token: str, tool: str, args: Optional[Dict[str, Any]], out: Path, *, new_session: bool
    ) -> Dict[str, Any]:
        reply = self._ask(
            {"op": "orphan", "url": url, "token": token, "tool": tool, "args": args or {}, "out": str(out),
             "new_session": new_session}
        )
        assert reply.get("spawned"), reply
        return wait_result_file(out)

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self._ask_exit()
        finally:
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)

    def _ask_exit(self) -> None:
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(json.dumps({"op": "exit"}) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass


def wait_result_file(path: Path, timeout_s: float = 60.0) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert payload.get("ok"), payload
            return payload["result"]
        time.sleep(0.05)
    raise AssertionError(f"el cliente no escribio {path}")


def call_from_foreign_process(url: str, token: str, tool: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Un proceso AJENO al arbol del panel (hijo directo de pytest) con el token robado."""
    proc = subprocess.run(
        [sys.executable, str(CALL), url, token, tool, json.dumps(args or {})],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(ROOT),
    )
    lines = (proc.stdout or "").strip().splitlines()
    assert lines, proc.stderr[-2000:]
    payload = json.loads(lines[-1])
    assert payload.get("ok"), payload
    return payload["result"]


class RelayLab:
    """Hub real + dos paneles del mismo proyecto, cada uno ligado a su propio proceso raiz."""

    def __init__(self, tmp_path: Path, *, host: str = "127.0.0.1", pane_count: int = 2) -> None:
        from src.core.factory import ApplicationFactory
        from src.mcp.pane_registry import EmbeddedMcpHub, PaneRegistry

        self.tmp_path = tmp_path
        self.kernel = ApplicationFactory.create_mcp_kernel(db_path=str(tmp_path / "relay_lab.db"))
        self.proj = self.kernel.db.create_project("Proyecto Lab", str(tmp_path / "repo"))["project_uuid"]
        self.registry = PaneRegistry(identity_repo=self.kernel.pane_identity_repo)
        self.panes: List[Dict[str, Any]] = []
        self.roots: List[PaneRootProcess] = []
        for idx in range(1, pane_count + 1):
            pane = self.registry.register_pane(f"pane_{idx}", project_uuid=self.proj, cli_profile="claude")
            root = PaneRootProcess()
            assert self.registry.bind_process(f"pane_{idx}", root.pid) is not None
            self.panes.append(pane)
            self.roots.append(root)
        self.hub = EmbeddedMcpHub(kernel=self.kernel, pane_registry=self.registry, host=host, port=0)
        self.url = self.hub.start(timeout_s=15.0)

    def token(self, idx: int) -> str:
        return self.panes[idx - 1]["session_token"]

    def node(self, idx: int) -> str:
        return self.panes[idx - 1]["node_uuid"]

    def root(self, idx: int) -> PaneRootProcess:
        return self.roots[idx - 1]

    def close(self) -> None:
        for root in self.roots:
            try:
                root.close()
            except Exception:
                pass
        try:
            self.hub.stop(timeout_s=5.0)
        finally:
            self.kernel.close()
