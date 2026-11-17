"""Tests para el proxy automático `stdio -> EmbeddedMcpHub HTTP`."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from src.core.factory import ApplicationFactory
from src.mcp.pane_registry import EmbeddedMcpHub, PaneRegistry
from src.mcp.stdio_proxy import should_use_hub_proxy

REPO_ROOT = Path(__file__).resolve().parents[1]


def _j(raw: str) -> Dict[str, Any]:
    return json.loads(raw)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (
            {
                "MCP2P_HUB_URL": "http://127.0.0.1:8765/mcp",
                "MCP2P_SESSION_TOKEN": "tok-123",
            },
            ("http://127.0.0.1:8765/mcp", "tok-123"),
        ),
        (
            {
                "MCP2P_HUB_URL": "http://localhost:9001/mcp",
                "MCP2P_SESSION_TOKEN": "tok-abc",
            },
            ("http://localhost:9001/mcp", "tok-abc"),
        ),
        (
            {
                "MCP2P_HUB_URL": "http://[::1]:8765/mcp",
                "MCP2P_SESSION_TOKEN": "tok-v6",
            },
            ("http://[::1]:8765/mcp", "tok-v6"),
        ),
        ({"MCP2P_HUB_URL": "http://127.0.0.1:8765/mcp"}, None),
        ({"MCP2P_HUB_URL": "http://127.0.0.1:8765/mcp", "MCP2P_SESSION_TOKEN": "   "}, None),
        ({"MCP2P_SESSION_TOKEN": "tok-123"}, None),
        (
            {
                "MCP2P_HUB_URL": "http://192.168.1.50:8765/mcp",
                "MCP2P_SESSION_TOKEN": "tok-123",
            },
            None,
        ),
        (
            {
                "MCP2P_HUB_URL": "http://0.0.0.0:8765/mcp",
                "MCP2P_SESSION_TOKEN": "tok-123",
            },
            None,
        ),
        (
            {
                "MCP2P_HUB_URL": "http://example.com:8765/mcp",
                "MCP2P_SESSION_TOKEN": "tok-123",
            },
            None,
        ),
        (
            {
                "MCP2P_HUB_URL": "https://127.0.0.1:8765/mcp",
                "MCP2P_SESSION_TOKEN": "tok-123",
            },
            None,
        ),
    ],
)
def test_should_use_hub_proxy_validates_loopback_and_token(
    env: Dict[str, str],
    expected: tuple[str, str] | None,
) -> None:
    assert should_use_hub_proxy(env) == expected


def test_stdio_subprocess_proxies_to_live_embedded_hub_and_delivers_pane_message(
    tmp_path: Path,
) -> None:
    desktop_kernel = ApplicationFactory.create_desktop_kernel(
        db_path=str(tmp_path / "stdio_proxy_hub.db")
    )
    p_uuid = desktop_kernel.db.list_projects()[0]["project_uuid"]
    reg = PaneRegistry(collab_repo=desktop_kernel.collab_repo)

    p1 = reg.register_pane(1, node_uuid="node_agy", agent_name="Agy", cli_profile="agy")
    p2 = reg.register_pane(2, node_uuid="node_opencode", agent_name="OpenCode", cli_profile="opencode")
    desktop_kernel.db.add_or_request_collaborator(p_uuid, "node_agy", status="approved")
    desktop_kernel.db.add_or_request_collaborator(p_uuid, "node_opencode", status="approved")

    hub = EmbeddedMcpHub(kernel=desktop_kernel, pane_registry=reg, host="127.0.0.1", port=0)
    try:
        hub_url = hub.start(timeout_s=10.0)

        async def _run_e2e() -> None:
            server_params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "src.mcp.server", "--transport", "stdio"],
                env={
                    **os.environ,
                    "MCP2P_HUB_URL": hub_url,
                    "MCP2P_SESSION_TOKEN": p1["session_token"],
                },
                cwd=str(REPO_ROOT),
            )

            async with stdio_client(server_params) as (stdio_read, stdio_write):
                async with ClientSession(stdio_read, stdio_write) as agy_session:
                    await agy_session.initialize()

                    tools_res = await agy_session.list_tools()
                    assert len(tools_res.tools) == 30
                    tool_names = {t.name for t in tools_res.tools}
                    assert {"list_swarm_panes", "send_pane_message", "await_next_assignment"} <= tool_names

                    panes_res = await agy_session.call_tool(
                        "list_swarm_panes",
                        {"project_uuid": p_uuid},
                    )
                    panes_data = _j(panes_res.content[0].text)
                    assert panes_data["success"] is True
                    pane_ids = {p["pane_id"]: p for p in panes_data["panes"]}
                    assert "pane_2" in pane_ids
                    assert pane_ids["pane_2"]["cli_profile"] == "opencode"
                    assert pane_ids["pane_2"]["node_uuid"] == "node_opencode"

                    send_res = await agy_session.call_tool(
                        "send_pane_message",
                        {
                            "project_uuid": p_uuid,
                            "target_pane_id": "pane_2",
                            "message": "Hola OpenCode desde agy via stdio proxy",
                        },
                    )
                    send_data = _j(send_res.content[0].text)
                    assert send_data["success"] is True

            # Verificar que pane_2 recibe el mensaje en await_next_assignment conectado al mismo Hub
            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {p2['session_token']}"}
            ) as http_client:
                async with streamable_http_client(hub_url, http_client=http_client) as (r2, w2):
                    async with ClientSession(r2, w2) as opencode_session:
                        await opencode_session.initialize()
                        next_res = await opencode_session.call_tool(
                            "await_next_assignment",
                            {"project_uuid": p_uuid, "timeout_ms": 1000},
                        )
                        next_data = _j(next_res.content[0].text)
                        assert next_data["success"] is True
                        # F0: el mensaje directo llega por la bandeja, con la procedencia aparte del texto.
                        assert next_data["has_messages"] is True
                        (msg,) = next_data["messages"]
                        assert msg["text"] == "Hola OpenCode desde agy via stdio proxy"
                        assert msg["from"]["node_uuid"] == "node_agy"
                        assert msg["from"]["pane_id"] == "pane_1"

        asyncio.run(asyncio.wait_for(_run_e2e(), timeout=25.0))
    finally:
        hub.stop(timeout_s=5.0)
        desktop_kernel.db.close()
