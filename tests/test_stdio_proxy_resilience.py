"""Proxy stdio -> Hub: timeouts, reconexion, recursos/prompts/progreso y modo `--hub-proxy-only` (EGM-97/98)."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_HUB = REPO_ROOT / "tests" / "helpers" / "fake_hub.py"
TOKEN = "fake-token"
RELAY_TOOLS = {"await_next_assignment", "list_swarm_panes", "send_pane_message"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeHub:
    """Proceso Hub falso; `kill()` lo mata de golpe (caida real) y `start()` lo levanta en el mismo puerto."""

    def __init__(self, port: Optional[int] = None, bare: bool = False) -> None:
        self.port = port or _free_port()
        self.bare = bare
        self.proc: Optional[subprocess.Popen] = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def start(self) -> "FakeHub":
        args = [sys.executable, str(FAKE_HUB), str(self.port)] + (["--bare"] if self.bare else [])
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=str(REPO_ROOT))
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        assert line.startswith("READY"), f"el Hub falso no arranco: {line!r}"
        return self

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)


@contextmanager
def running_hub(**kw: Any) -> Iterator[FakeHub]:
    hub = FakeHub(**kw).start()
    try:
        yield hub
    finally:
        hub.kill()


def _env(extra: Dict[str, str], tmp_path: Path) -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("MCP2P_HUB_URL", "MCP2P_SESSION_TOKEN", "MCP2P_DB_PATH")}
    env.update({"HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home"), "PYTHONPATH": str(REPO_ROOT)})
    env.update(extra)
    return env


def _params(argv: List[str], env: Dict[str, str]) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=argv, env=env, cwd=str(REPO_ROOT))


@asynccontextmanager
async def proxy_session(hub_url: str, tmp_path: Path, argv: Optional[List[str]] = None, token: str = TOKEN):
    params = _params(
        argv or ["-m", "src.mcp.server", "--hub-proxy-only"],
        _env({"MCP2P_HUB_URL": hub_url, "MCP2P_SESSION_TOKEN": token}, tmp_path),
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            yield session


def _run(coro, timeout: float = 60.0):
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


def _text(res) -> str:
    return res.content[0].text


def test_await_longer_than_default_http_timeout_is_not_cut(tmp_path: Path) -> None:
    """EGM-97: una respuesta de 7 s (> 5 s del timeout por defecto de httpx) llega completa."""
    with running_hub() as hub:

        async def go() -> None:
            async with proxy_session(hub.url, tmp_path) as s:
                t0 = time.monotonic()
                res = await s.call_tool("slow", {"seconds": 7})
                assert not res.is_error
                assert "slept:7" in _text(res)
                assert time.monotonic() - t0 >= 6.5

        _run(go())


def test_hub_down_returns_immediate_mcp_error_then_reconnects_on_same_url(tmp_path: Path) -> None:
    with running_hub() as hub:

        async def go() -> None:
            async with proxy_session(hub.url, tmp_path) as s:
                assert "echo:uno" in _text(await s.call_tool("echo", {"text": "uno"}))
                hub.kill()
                t0 = time.monotonic()
                res = await s.call_tool("echo", {"text": "dos"})
                elapsed = time.monotonic() - t0
                assert elapsed < 5.0, f"el proxy tardo {elapsed:.1f}s en avisar de un Hub caido"
                assert res.is_error
                body = json.loads(_text(res))
                assert body["code"] == "HUB_UNREACHABLE"
                assert body["success"] is False
                assert hub.url in body["error"]
                # El Hub vuelve en la misma URL: el siguiente intento reconecta solo, sin reiniciar el CLI.
                hub.start()
                res = await s.call_tool("echo", {"text": "tres"})
                assert not res.is_error and "echo:tres" in _text(res)

        _run(go())


def test_hub_killed_mid_call_returns_error_instead_of_hanging(tmp_path: Path) -> None:
    with running_hub() as hub:

        async def go() -> None:
            async with proxy_session(hub.url, tmp_path) as s:
                task = asyncio.create_task(s.call_tool("slow", {"seconds": 120}))
                await asyncio.sleep(1.5)
                hub.kill()
                t0 = time.monotonic()
                res = await asyncio.wait_for(task, timeout=20)
                assert time.monotonic() - t0 < 15
                assert res.is_error
                assert json.loads(_text(res))["code"] == "HUB_UNREACHABLE"

        _run(go())


def test_hub_that_changed_port_gives_clear_error_naming_the_stale_url(tmp_path: Path) -> None:
    """El CLI hereda la URL del Hub al lanzarse: si el Hub se reinicia en otro puerto no hay a donde reconectar."""
    old = FakeHub().start()
    try:

        async def go() -> None:
            async with proxy_session(old.url, tmp_path) as s:
                old.kill()
                with running_hub() as new:  # otro puerto
                    assert new.port != old.port
                    t0 = time.monotonic()
                    res = await s.call_tool("echo", {"text": "x"})
                    assert time.monotonic() - t0 < 5.0
                    body = json.loads(_text(res))
                    assert body["code"] == "HUB_UNREACHABLE"
                    assert old.url in body["error"]
                    assert "vuelve a abrir este CLI desde un panel" in body["error"]

        _run(go())
    finally:
        old.kill()


def test_wrong_token_is_reported_not_hung(tmp_path: Path) -> None:
    with running_hub() as hub:

        async def go() -> None:
            async with proxy_session(hub.url, tmp_path, token="otro-panel") as s:
                t0 = time.monotonic()
                res = await s.call_tool("echo", {"text": "x"})
                assert time.monotonic() - t0 < 8.0
                assert res.is_error
                assert json.loads(_text(res))["code"] == "HUB_UNREACHABLE"

        _run(go())


def test_proxy_forwards_resources_prompts_and_progress(tmp_path: Path) -> None:
    with running_hub() as hub:

        async def go() -> None:
            async with proxy_session(hub.url, tmp_path) as s:
                resources = await s.list_resources()
                assert [str(r.uri) for r in resources.resources] == ["fake://hello"]
                read = await s.read_resource("fake://hello")
                assert read.contents[0].text == "hola desde el hub falso"

                prompts = await s.list_prompts()
                assert [p.name for p in prompts.prompts] == ["saludo"]
                got = await s.get_prompt("saludo", {"nombre": "Ada"})
                assert "Saluda a Ada" in got.messages[0].content.text

                seen: List[tuple] = []

                async def on_progress(progress: float, total: Optional[float], message: Optional[str]) -> None:
                    seen.append((progress, total, message))

                res = await s.call_tool("with_progress", {"steps": 3}, progress_callback=on_progress)
                assert "done" in _text(res)
                assert [p for p, _, _ in seen] == [1, 2, 3]
                assert seen[0][2] == "paso 1"

        _run(go())


def test_proxy_does_not_advertise_list_changed_and_tools_only_hub_lists_are_empty(tmp_path: Path) -> None:
    """Limite documentado: las notificaciones `*/list_changed` del Hub no se reenvian, y por eso no se anuncian."""
    with running_hub(bare=True) as hub:

        async def go() -> None:
            async with proxy_session(hub.url, tmp_path) as s:
                caps = s.initialize_result.capabilities
                assert caps.tools is not None and not caps.tools.list_changed
                for cap in (caps.resources, caps.prompts):
                    assert cap is None or not cap.list_changed
                assert (await s.list_resources()).resources == []
                assert (await s.list_prompts()).prompts == []

        _run(go())


def test_hub_proxy_only_without_hub_vars_serves_only_the_relay_tools_as_hub_required(tmp_path: Path) -> None:
    db_path = tmp_path / "must_not_exist.db"

    async def go() -> None:
        env = _env({"MCP2P_DB_PATH": str(db_path)}, tmp_path)
        async with stdio_client(_params(["-m", "src.mcp.server", "--hub-proxy-only"], env)) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await s.list_tools()
                assert {t.name for t in tools.tools} == RELAY_TOOLS
                for name in sorted(RELAY_TOOLS):
                    res = await s.call_tool(name, {"project_uuid": "p"})
                    body = json.loads(_text(res))
                    assert body["code"] == "HUB_REQUIRED" and body["success"] is False
                    assert "abra este CLI desde un panel de MCP2P" in body["error"]
                res = await s.call_tool("list_projects", {})
                assert res.is_error

    _run(go())
    assert not db_path.exists()
    assert not (tmp_path / "home").exists() or not any((tmp_path / "home").rglob("*.db"))


def test_packaged_entrypoint_with_hub_proxy_only_proxies_for_real(tmp_path: Path) -> None:
    """`mcp2p --mcp-server --hub-proxy-only` (el binario empaquetado) hace proxy real; aqui via `src.app.main`."""
    with running_hub() as hub:

        async def go() -> None:
            argv = ["-m", "src.app.main", "--mcp-server", "--hub-proxy-only"]
            async with proxy_session(hub.url, tmp_path, argv=argv) as s:
                names = {t.name for t in (await s.list_tools()).tools}
                assert {"echo", "slow"} <= names
                assert "echo:hola" in _text(await s.call_tool("echo", {"text": "hola"}))

        _run(go())


def test_packaged_entrypoint_without_flag_also_proxies_when_hub_vars_present(tmp_path: Path) -> None:
    with running_hub() as hub:

        async def go() -> None:
            async with proxy_session(hub.url, tmp_path, argv=["-m", "src.app.main", "--mcp-server"]) as s:
                assert "echo:hola" in _text(await s.call_tool("echo", {"text": "hola"}))

        _run(go())


def test_packaged_entrypoint_hub_proxy_only_without_vars_is_hub_required(tmp_path: Path) -> None:
    db_path = tmp_path / "must_not_exist.db"

    async def go() -> None:
        env = _env({"MCP2P_DB_PATH": str(db_path)}, tmp_path)
        async with stdio_client(_params(["-m", "src.app.main", "--mcp-server", "--hub-proxy-only"], env)) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                assert {t.name for t in (await s.list_tools()).tools} == RELAY_TOOLS

    _run(go())
    assert not db_path.exists()


@pytest.mark.parametrize("module", ["src.mcp.server", "src.app.main"])
@pytest.mark.parametrize(
    "extra_env",
    [
        {"MCP2P_HUB_URL": "http://192.168.1.50:8765/mcp", "MCP2P_SESSION_TOKEN": "t"},
        {"MCP2P_HUB_URL": "http://127.0.0.1:8765/mcp"},
        {"MCP2P_SESSION_TOKEN": "t"},
    ],
)
def test_hub_proxy_only_with_invalid_hub_vars_exits_2_without_serving(module: str, extra_env: Dict[str, str], tmp_path: Path) -> None:
    argv = [sys.executable, "-m", module] + (["--mcp-server"] if module == "src.app.main" else []) + ["--hub-proxy-only"]
    db_path = tmp_path / "no.db"
    proc = subprocess.run(
        argv, input="", capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT),
        env=_env({**extra_env, "MCP2P_DB_PATH": str(db_path)}, tmp_path),
    )
    assert proc.returncode == 2
    assert "MCP2P" in proc.stderr
    assert not db_path.exists()


@pytest.mark.parametrize("module", ["src.mcp.server", "src.app.main"])
def test_hub_proxy_only_rejects_non_stdio_transport(module: str, tmp_path: Path) -> None:
    argv = [sys.executable, "-m", module] + (["--mcp-server"] if module == "src.app.main" else []) + [
        "--hub-proxy-only", "--transport", "streamable-http", "--port", "0",
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT), env=_env({}, tmp_path))
    assert proc.returncode == 2
    assert "stdio" in proc.stderr
