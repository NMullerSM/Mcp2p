"""«Agente» de pruebas con una sesion MCP PERSISTENTE contra el Hub (como un CLI que mantiene su conexion).

Uso: python agent_session.py <url> <token>

Lee ordenes JSON por stdin (una por linea) y responde una linea JSON por stdout:

- {"tool": ..., "args": {...}} -> {"ok": true, "result": <json de la herramienta>, "t0": <epoch>, "t1": <epoch>}
- {"op": "exit"} (o cerrar stdin) -> termina.

La primera linea de salida es {"ready": true} cuando la sesion MCP ya esta inicializada. Los tiempos son de reloj de
pared (`time.time()`), comparables entre procesos de la misma maquina. La salida es ASCII (consola de Windows).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


async def _main(url: str, token: str) -> None:
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    try:
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=120.0) as http_client:
            async with streamable_http_client(url, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    _emit({"ready": True})
                    while True:
                        line = await asyncio.to_thread(sys.stdin.readline)
                        if not line:
                            return
                        cmd = json.loads(line)
                        if cmd.get("op") == "exit":
                            return
                        t0 = time.time()
                        try:
                            res = await session.call_tool(cmd["tool"], cmd.get("args") or {})
                            _emit({"ok": True, "result": json.loads(res.content[0].text), "t0": t0, "t1": time.time()})
                        except Exception as exc:
                            _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}", "t0": t0, "t1": time.time()})
    except BaseException:
        # Un error al CERRAR la sesion (DELETE, cancelacion del grupo de tareas) no invalida lo ya respondido.
        return


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1], sys.argv[2]))
