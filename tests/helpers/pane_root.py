"""Proceso «raiz de panel» para las pruebas de EGM-105 (sustituye al CLI que corre en el PTY).

Lee ordenes JSON por stdin, una por linea, y responde una linea JSON por stdout:

- {"op": "call", "url", "token", "tool", "args"}: lanza un HIJO (descendiente real) que llama al Hub y devuelve
  su resultado.
- {"op": "orphan", "url", "token", "tool", "args", "out", "new_session": bool}: lanza un intermedio que lanza el
  cliente (con setsid si new_session y POSIX) y TERMINA en el acto: el cliente queda huerfano fuera del arbol,
  como un proceso demonizado. El resultado lo escribe el cliente en `out`.
- {"op": "exit"}.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CALL = os.path.join(HERE, "mcp_http_call.py")


def _client_argv(cmd: dict) -> list:
    return [sys.executable, CALL, cmd["url"], cmd["token"], cmd["tool"], json.dumps(cmd.get("args") or {})]


def _spawn_orphan(cmd: dict) -> int:
    """Modo intermedio: lanza el cliente y sale sin esperarlo."""
    argv = _client_argv(cmd) + ["--out", cmd["out"], "--wait-orphan", str(os.getpid())]
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if cmd.get("new_session") and os.name == "posix":
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)
    return 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--spawn-orphan":
        return _spawn_orphan(json.loads(sys.argv[2]))
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        cmd = json.loads(raw)
        op = cmd.get("op")
        if op == "exit":
            return 0
        if op == "call":
            proc = subprocess.run(_client_argv(cmd), capture_output=True, text=True, timeout=120)
            line = (proc.stdout or "").strip().splitlines()
            reply = line[-1] if line else json.dumps({"ok": False, "error": proc.stderr[-2000:]})
        elif op == "orphan":
            subprocess.run([sys.executable, os.path.abspath(__file__), "--spawn-orphan", json.dumps(cmd)], timeout=60)
            reply = json.dumps({"ok": True, "spawned": True})
        else:
            reply = json.dumps({"ok": False, "error": f"op desconocida: {op}"})
        sys.stdout.write(reply + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
