"""Cliente MCP minimo en un PROCESO aparte (pruebas de EGM-105): llama a una herramienta del Hub por HTTP.

Uso: python mcp_http_call.py <url> <token> <tool> <json_args> [--out FILE] [--wait-orphan PID]

Imprime (o escribe en FILE) una linea JSON: {"ok": true, "pid": ..., "result": <json de la herramienta>} o
{"ok": false, "pid": ..., "error": "..."}. Con --wait-orphan espera a que su padre PID haya terminado (proceso
huerfano / demonizado) ANTES de conectar, para que la prueba no dependa de una carrera.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time


def _wait_orphan(parent_pid: int) -> None:
    import psutil

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        parent = psutil.Process().parent()
        if parent is None or parent.pid != parent_pid:
            return
        time.sleep(0.05)


async def _call(url: str, token: str, tool: str, args: dict) -> dict:
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30.0) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(tool, args)
                return json.loads(res.content[0].text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("token")
    parser.add_argument("tool")
    parser.add_argument("args")
    parser.add_argument("--out")
    parser.add_argument("--wait-orphan", type=int, default=0)
    ns = parser.parse_args()
    if ns.wait_orphan:
        _wait_orphan(ns.wait_orphan)
    try:
        payload = {"ok": True, "pid": os.getpid(), "result": asyncio.run(_call(ns.url, ns.token, ns.tool, json.loads(ns.args)))}
    except Exception as exc:  # la prueba decide; aqui solo se informa
        payload = {"ok": False, "pid": os.getpid(), "error": f"{type(exc).__name__}: {exc}"}
    line = json.dumps(payload, ensure_ascii=False)
    if ns.out:
        tmp = ns.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(line)
        os.replace(tmp, ns.out)
    else:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
