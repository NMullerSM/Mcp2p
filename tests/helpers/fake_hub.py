"""Hub falso en loopback para probar el proxy stdio (`python tests/helpers/fake_hub.py PORT [--bare]`).

Imprime `READY <url>` en stdout cuando escucha. Se mata como proceso (como una caida real del Hub), no se
apaga con gracia. Exige `Authorization: Bearer fake-token`. `--bare` solo expone herramientas (sin recursos ni
prompts), como el Hub real.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import anyio  # noqa: E402
from mcp.server.mcpserver import Context, MCPServer  # noqa: E402


TOKEN = "fake-token"


def build(bare: bool) -> MCPServer:
    server = MCPServer("fake-hub")

    @server.tool()
    def echo(text: str = "") -> str:
        return f"echo:{text}"

    @server.tool()
    async def slow(seconds: float = 1.0) -> str:
        await asyncio.sleep(seconds)
        return f"slept:{seconds}"

    @server.tool()
    async def with_progress(ctx: Context, steps: int = 3) -> str:
        for i in range(steps):
            await ctx.report_progress(i + 1, steps, f"paso {i + 1}")
        return "done"

    if not bare:

        @server.resource("fake://hello")
        def hello() -> str:
            return "hola desde el hub falso"

        @server.prompt()
        def saludo(nombre: str = "mundo") -> str:
            return f"Saluda a {nombre}"

    return server


class _Bearer:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if headers.get(b"authorization") != f"Bearer {TOKEN}".encode():
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


async def main(port: int, bare: bool) -> None:
    import socket

    import uvicorn

    server = build(bare)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    app = _Bearer(server.streamable_http_app(host="127.0.0.1"))
    uv = uvicorn.Server(uvicorn.Config(app, log_level="warning", log_config=None))
    print(f"READY http://127.0.0.1:{sock.getsockname()[1]}/mcp", flush=True)
    await uv.serve(sockets=[sock])


if __name__ == "__main__":
    anyio.run(main, int(sys.argv[1]), "--bare" in sys.argv)
