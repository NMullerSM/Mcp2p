"""Cliente mínimo de *hooks* de MCP2P (`Mcp2p --hook-post <evento>` o `python -I src/app/hook_client.py <evento>`).

Solo usa biblioteca estándar de Python para arrancar rápido y poder ejecutarse de forma
aislada sin importar el paquete `src`.

Garantías:
- Nunca escribe en `stdout` (algunos CLIs inyectan `stdout` en el contexto del modelo).
- `stderr` solo recibe diagnósticos si `MCP2P_HOOK_DEBUG=1`.
- El código de salida es siempre `0` (un código distinto podría bloquear al CLI).
- Solo envía `POST` a direcciones loopback explícitas (`127.0.0.1`, `localhost`, `::1`).
"""

from __future__ import annotations

import json
import os
import re
import select
import sys
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional, Sequence

EVENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,47}$")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_STDIN_BYTES = 64 * 1024
MAX_RAW_BYTES = 4096
STDIN_TIMEOUT_S = 1.0
HTTP_TIMEOUT_S = 2.0


def _debug(message: str) -> None:
    if os.environ.get("MCP2P_HOOK_DEBUG") == "1" and sys.stderr is not None:
        try:
            sys.stderr.write(f"[mcp2p hook_client] {message}\n")
            sys.stderr.flush()
        except Exception:
            pass


def _build_post_url(hub_url: str, event: str) -> Optional[str]:
    if not hub_url:
        return None
    try:
        parsed = urllib.parse.urlsplit(hub_url)
    except ValueError:
        return None
    if parsed.scheme != "http":
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    host = parsed.hostname
    if host not in LOOPBACK_HOSTS:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None or port < 1 or port > 65535:
        return None
    netloc_host = f"[{host}]" if ":" in host else host
    return f"http://{netloc_host}:{port}/hooks/v1/{event}"


def _read_stdin_posix(fd: int, max_bytes: int, timeout_s: float) -> bytes:
    chunks = bytearray()
    deadline = time.monotonic() + timeout_s
    while len(chunks) < max_bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            ready, _, _ = select.select([fd], [], [], remaining)
        except (InterruptedError, ValueError, OSError):
            break
        if not ready:
            break
        try:
            chunk = os.read(fd, max_bytes - len(chunks))
        except InterruptedError:
            continue
        except BlockingIOError:
            continue
        except OSError:
            break
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks)


def _read_stdin_threaded(fd: int, max_bytes: int, timeout_s: float) -> bytes:
    chunks = bytearray()
    done = threading.Event()

    def _reader() -> None:
        try:
            while len(chunks) < max_bytes:
                chunk = os.read(fd, max_bytes - len(chunks))
                if not chunk:
                    break
                chunks.extend(chunk)
        except OSError:
            pass
        finally:
            done.set()

    thread = threading.Thread(target=_reader, name="mcp2p-hook-stdin", daemon=True)
    thread.start()
    done.wait(timeout=timeout_s)
    return bytes(chunks[:max_bytes])


def _read_stdin_bounded(max_bytes: int = MAX_STDIN_BYTES, timeout_s: float = STDIN_TIMEOUT_S) -> bytes:
    stdin_obj = sys.stdin
    if stdin_obj is None:
        return b""
    try:
        fd = stdin_obj.fileno()
    except (AttributeError, OSError, ValueError):
        try:
            buf = getattr(stdin_obj, "buffer", None)
            if buf is not None:
                data = buf.read(max_bytes)
                return bytes(data) if isinstance(data, (bytes, bytearray)) else b""
            text = stdin_obj.read(max_bytes)
            return text.encode("utf-8", errors="replace")[:max_bytes]
        except Exception:
            return b""

    try:
        if os.isatty(fd):
            return b""
    except OSError:
        return b""

    if os.name == "posix":
        return _read_stdin_posix(fd, max_bytes, timeout_s)
    return _read_stdin_threaded(fd, max_bytes, timeout_s)


def _truncate_utf8(text: str, max_bytes: int = MAX_RAW_BYTES) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _encode_payload(raw_bytes: bytes) -> bytes:
    text = raw_bytes[:MAX_STDIN_BYTES].decode("utf-8", errors="replace")
    stripped = text.strip()
    if not stripped:
        return b"{}"
    try:
        parsed = json.loads(stripped)
        serialized = json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(serialized) <= MAX_STDIN_BYTES:
            return serialized
    except (ValueError, TypeError):
        pass
    return json.dumps({"raw": _truncate_utf8(text, MAX_RAW_BYTES)}, ensure_ascii=False).encode("utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if not args:
            return 0
        event = args[0]
        if not isinstance(event, str) or EVENT_RE.fullmatch(event) is None:
            _debug(f"evento invalido ignorado: {event!r}")
            return 0

        token = os.environ.get("MCP2P_SESSION_TOKEN", "").strip()
        if not token or any(ch in token for ch in ("\r", "\n", "\x00")):
            _debug("sin MCP2P_SESSION_TOKEN valido; no se envia")
            return 0

        hub_url = os.environ.get("MCP2P_HUB_URL", "").strip()
        target_url = _build_post_url(hub_url, event)
        if target_url is None:
            _debug(f"MCP2P_HUB_URL no loopback o invalida: {hub_url!r}")
            return 0

        if len(args) >= 2:
            raw_bytes = str(args[1]).encode("utf-8", errors="replace")[:MAX_STDIN_BYTES]
        else:
            raw_bytes = _read_stdin_bounded(MAX_STDIN_BYTES, STDIN_TIMEOUT_S)

        body = _encode_payload(raw_bytes)
        req = urllib.request.Request(
            target_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=HTTP_TIMEOUT_S) as resp:
            resp.read(256)
    except Exception as exc:
        _debug(f"error enviando hook: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
