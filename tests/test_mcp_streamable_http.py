"""Gate 0d: transporte `streamable-http` disponible, con bind explícito y solo de loopback.

Arranca el servidor MCP real (`create_mcp_server` + `build_streamable_http_server`) en un puerto
efímero de 127.0.0.1 y un cliente MCP 2.x lista sus herramientas por HTTP.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from src.app.main import parse_arguments
from src.app.version import __version__
from src.mcp.server import build_streamable_http_server, create_mcp_server
from src.mcp.transport_options import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
    NonLoopbackBindError,
    resolve_http_bind,
)


@pytest.fixture
def clean_bind_env(monkeypatch):
    monkeypatch.delenv("MCP2P_MCP_HOST", raising=False)
    monkeypatch.delenv("MCP2P_MCP_PORT", raising=False)


def test_both_parsers_accept_streamable_http_and_keep_stdio_default(clean_bind_env):
    import argparse

    from src.mcp.transport_options import add_transport_arguments

    assert parse_arguments(["--mcp-server"]).transport == "stdio"
    args = parse_arguments(["--mcp-server", "--transport", "streamable-http", "--port", "0"])
    assert (args.transport, args.host, args.port) == ("streamable-http", None, 0)

    standalone = argparse.ArgumentParser()
    add_transport_arguments(standalone)
    assert standalone.parse_args([]).transport == "stdio"
    assert standalone.parse_args(["--transport", "streamable-http"]).transport == "streamable-http"
    with pytest.raises(SystemExit):
        standalone.parse_args(["--transport", "websocket"])


def test_bind_defaults_to_loopback_and_explicit_port(clean_bind_env):
    assert resolve_http_bind() == (DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT)
    assert DEFAULT_HTTP_HOST == "127.0.0.1" and DEFAULT_HTTP_PORT != 8000
    assert resolve_http_bind(port=0) == ("127.0.0.1", 0)
    assert resolve_http_bind("::1", 9001) == ("::1", 9001)
    assert resolve_http_bind("localhost", 9002) == ("localhost", 9002)


def test_bind_env_overrides_default(monkeypatch):
    monkeypatch.setenv("MCP2P_MCP_HOST", "127.0.0.2")
    monkeypatch.setenv("MCP2P_MCP_PORT", "9100")
    assert resolve_http_bind() == ("127.0.0.2", 9100)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.com", "10.0.0.1"])
def test_bind_rejects_non_loopback_hosts(clean_bind_env, monkeypatch, host):
    with pytest.raises(NonLoopbackBindError):
        resolve_http_bind(host, 0)
    monkeypatch.setenv("MCP2P_MCP_HOST", host)
    with pytest.raises(NonLoopbackBindError):
        resolve_http_bind()


@pytest.mark.parametrize("port", [-1, 65536, "abc"])
def test_bind_rejects_invalid_ports(clean_bind_env, port):
    with pytest.raises(ValueError):
        resolve_http_bind("127.0.0.1", port)


def test_real_server_lists_tools_over_streamable_http_on_ephemeral_loopback(tmp_path):
    from mcp import Client

    server = create_mcp_server(db_path=str(tmp_path / "mcp_http.db"), transport="streamable-http")
    uv_server, sock, url = build_streamable_http_server(server, "127.0.0.1", 0)
    bound_host, bound_port = sock.getsockname()[:2]
    assert bound_host == "127.0.0.1" and bound_port != 0
    assert url == f"http://127.0.0.1:{bound_port}/mcp"

    thread = threading.Thread(
        target=lambda: asyncio.run(uv_server.serve(sockets=[sock])), daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not uv_server.started:
            assert thread.is_alive() and time.monotonic() < deadline, "uvicorn no arrancó"
            time.sleep(0.05)

        async def _probe():
            async with Client(url) as client:
                tools = await client.list_tools()
                version = await client.call_tool("get_server_version", {})
                return [t.name for t in tools.tools], version

        names, version = asyncio.run(asyncio.wait_for(_probe(), timeout=30))
        assert {"list_projects", "get_kanban_board", "get_server_version"} <= set(names)
        payload = json.loads(version.content[0].text)
        assert payload["transport"] == "streamable-http"
        assert payload["server_version"] == "0.3.0"
        assert payload["app_version"] == __version__
    finally:
        uv_server.should_exit = True
        thread.join(timeout=15)
        sock.close()
    assert not thread.is_alive()


def test_mcp_api_version_is_defined_once():
    """EGM-39 (3): la versión de API MCP vive solo en `tools.MCP_API_VERSION`."""
    import re
    from pathlib import Path

    from src.mcp import server as server_mod
    from src.mcp import tools as tools_mod

    root = Path(__file__).resolve().parent.parent / "src" / "mcp"
    literal = re.escape(tools_mod.MCP_API_VERSION)
    hits = [
        (p.name, n)
        for p in root.glob("*.py")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(rf"[\"']{literal}[\"']", line)
    ]
    assert hits == [("tools.py", hits[0][1])], hits
    assert server_mod.SERVER_VERSION == tools_mod.MCP_API_VERSION
