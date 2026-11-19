"""CLI de agente FALSO para probar la medición automática con un PTY real (sin red ni modelo).

Uso: `python fake_agent_cli.py <modo>`; modos: ok | no_paste | silent | no_end.
Imita lo esencial de un CLI con hooks: pide el pegado entre corchetes, avisa a `MCP2P_HUB_URL` (`/hooks/v1/<evento>` con
`MCP2P_SESSION_TOKEN`), «responde LISTO» al enviar y avisa del fin del turno.
"""

from __future__ import annotations

import json
import os
import sys
import termios
import time
import tty
import urllib.request

MODE = sys.argv[1] if len(sys.argv) > 1 else "ok"
HUB = os.environ.get("MCP2P_HUB_URL", "")
TOKEN = os.environ.get("MCP2P_SESSION_TOKEN", "")


def post(event: str, payload=None) -> None:
    if MODE == "silent" or not HUB:
        return
    base = HUB.rsplit("/mcp", 1)[0]
    req = urllib.request.Request(
        f"{base}/hooks/v1/{event}",
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass


def main() -> None:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    out = sys.stdout
    try:
        if MODE != "no_paste":
            out.write("\x1b[?2004h")
        out.write("fake-agent listo\r\n")
        out.flush()
        post("claude.SessionStart", {"session_id": "fake-session-1"})
        buf = ""
        in_paste = False
        pending = ""
        while True:
            ch = os.read(fd, 1).decode("utf-8", errors="replace")
            if not ch:
                return
            pending += ch
            if pending.startswith("\x1b") and len(pending) < 6 and MODE != "no_paste":
                if "\x1b[200~".startswith(pending):
                    continue
                if "\x1b[201~".startswith(pending):
                    continue
            if pending == "\x1b[200~":
                in_paste, pending = True, ""
                continue
            if pending == "\x1b[201~":
                in_paste, pending = False, ""
                continue
            text, pending = pending, ""
            if in_paste:
                buf += text
                out.write(text.replace("\n", "\r\n"))
                out.flush()
                continue
            if text in ("\r", "\n"):
                if buf.strip() or MODE == "no_paste":
                    post("claude.UserPromptSubmit", {"session_id": "fake-session-1"})
                    out.write("\r\nLISTO\r\n")
                    out.flush()
                    time.sleep(0.2)
                    if MODE != "no_end":
                        post("claude.Stop", {"session_id": "fake-session-1"})
                    buf = ""
                continue
            buf += text
            out.write(text)
            out.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


if __name__ == "__main__":
    main()
