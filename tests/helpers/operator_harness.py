"""«Harness» de prueba del canal del operador (Ola 4): hace de Claude Code / Claude Desktop.

Lanza el proxy REAL (`python -m src.mcp.operator_proxy ...`) como servidor MCP por stdio (es su hijo, igual que en el
harness de verdad), negocia MCP y obedece ordenes JSON por stdin, una por linea, respondiendo una linea JSON (ASCII):

- {"op": "tools"} -> {"ok": true, "tools": [...]}
- {"op": "call", "tool": "...", "args": {...}} -> {"ok": true, "result": <json de la herramienta>, "progress": n}
- {"op": "exit"}

Uso: python operator_harness.py <json con la lista argv del proxy>
"""

from __future__ import annotations

import json
import os
import sys

import anyio


async def _main(proxy_argv: list) -> int:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=proxy_argv[0], args=proxy_argv[1:], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            _say({"ready": True, "pid": os.getpid()})
            while True:
                line = await anyio.to_thread.run_sync(sys.stdin.readline)
                if not line:
                    return 0
                line = line.strip()
                if not line:
                    continue
                cmd = json.loads(line)
                op = cmd.get("op")
                if op == "exit":
                    return 0
                try:
                    if op == "tools":
                        listed = await session.list_tools()
                        _say({"ok": True, "tools": sorted(t.name for t in listed.tools)})
                    elif op == "call":
                        seen = []

                        async def _progress(progress, total, message, _seen=seen):
                            _seen.append(progress)

                        res = await session.call_tool(cmd["tool"], cmd.get("args") or {}, progress_callback=_progress)
                        text = res.content[0].text if res.content else ""
                        try:
                            value = json.loads(text)
                        except ValueError:
                            value = {"raw": text, "is_error": bool(getattr(res, "is_error", False))}
                        _say({"ok": True, "result": value, "progress": len(seen)})
                    else:
                        _say({"ok": False, "error": f"op desconocida: {op}"})
                except Exception as exc:  # la prueba decide
                    _say({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def _say(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> int:
    try:
        return anyio.run(_main, json.loads(sys.argv[1]))
    except BaseException as exc:  # sin Hub o con el proxy caido: se informa y se sale
        _say({"ready": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
