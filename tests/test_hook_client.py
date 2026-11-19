"""Pruebas de `src/app/hook_client.py` y despacho temprano `--hook-post` en `src/app/main.py`."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from src.app import hook_client, main as app_main

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_CLIENT_PATH = REPO_ROOT / "src" / "app" / "hook_client.py"


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _CaptureServer:
    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        capture = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length)
                with capture._lock:
                    capture.requests.append(
                        {
                            "path": self.path,
                            "auth": self.headers.get("Authorization"),
                            "content_type": self.headers.get("Content-Type"),
                            "body": raw,
                        }
                    )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return None

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def hub_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2.0)


@pytest.fixture
def capture_server():
    srv = _CaptureServer()
    try:
        yield srv
    finally:
        srv.close()


def _clean_env(extra: Dict[str, str] | None = None) -> Dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("MCP2P_") and k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}
    }
    if extra:
        env.update(extra)
    return env


def test_never_writes_stdout_and_exits_zero_when_hub_is_down() -> None:
    port = _unused_port()
    proc = subprocess.run(
        [sys.executable, "-I", str(HOOK_CLIENT_PATH), "claude.Stop"],
        input=b'{"session_id":"s1"}',
        capture_output=True,
        env=_clean_env(
            {
                "MCP2P_HUB_URL": f"http://127.0.0.1:{port}/mcp",
                "MCP2P_SESSION_TOKEN": "tok-down",
            }
        ),
        timeout=10,
    )
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""

    proc_dbg = subprocess.run(
        [sys.executable, "-I", str(HOOK_CLIENT_PATH), "claude.Stop"],
        input=b'{"session_id":"s1"}',
        capture_output=True,
        env=_clean_env(
            {
                "MCP2P_HUB_URL": f"http://127.0.0.1:{port}/mcp",
                "MCP2P_SESSION_TOKEN": "tok-down",
                "MCP2P_HOOK_DEBUG": "1",
            }
        ),
        timeout=10,
    )
    assert proc_dbg.returncode == 0
    assert proc_dbg.stdout == b""
    assert proc_dbg.stderr != b""


def test_posts_json_with_bearer_from_env(capture_server: _CaptureServer) -> None:
    payload = {"session_id": "sess-123", "hook_event_name": "Stop"}
    proc = subprocess.run(
        [sys.executable, "-I", str(HOOK_CLIENT_PATH), "claude.Stop"],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        env=_clean_env(
            {
                "MCP2P_HUB_URL": capture_server.hub_url,
                "MCP2P_SESSION_TOKEN": "secret-token-abc",
            }
        ),
        timeout=10,
    )
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""
    assert len(capture_server.requests) == 1
    req = capture_server.requests[0]
    assert req["path"] == "/hooks/v1/claude.Stop"
    assert req["auth"] == "Bearer secret-token-abc"
    assert req["content_type"] == "application/json"
    assert json.loads(req["body"].decode("utf-8")) == payload


def test_reads_payload_from_argv_for_codex(capture_server: _CaptureServer) -> None:
    codex_json = json.dumps({"type": "agent-turn-complete", "thread-id": "thr-9"})
    proc = subprocess.run(
        [sys.executable, "-I", str(HOOK_CLIENT_PATH), "codex.notify", codex_json],
        input=b"should-ignore-stdin",
        capture_output=True,
        env=_clean_env(
            {
                "MCP2P_HUB_URL": capture_server.hub_url,
                "MCP2P_SESSION_TOKEN": "tok-codex",
            }
        ),
        timeout=10,
    )
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert len(capture_server.requests) == 1
    req = capture_server.requests[0]
    assert req["path"] == "/hooks/v1/codex.notify"
    assert json.loads(req["body"].decode("utf-8")) == {"type": "agent-turn-complete", "thread-id": "thr-9"}


def test_caps_stdin_at_64_kib(capture_server: _CaptureServer) -> None:
    big_text = "A" * (128 * 1024)
    proc = subprocess.run(
        [sys.executable, "-I", str(HOOK_CLIENT_PATH), "claude.PostToolUse"],
        input=big_text.encode("utf-8"),
        capture_output=True,
        env=_clean_env(
            {
                "MCP2P_HUB_URL": capture_server.hub_url,
                "MCP2P_SESSION_TOKEN": "tok-cap",
            }
        ),
        timeout=10,
    )
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert len(capture_server.requests) == 1
    body = capture_server.requests[0]["body"]
    assert len(body) <= 64 * 1024
    parsed = json.loads(body.decode("utf-8"))
    assert "raw" in parsed
    assert len(parsed["raw"].encode("utf-8")) <= 4096


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "https://127.0.0.1:8765/mcp",
        "http://example.com:8765/mcp",
        "http://192.168.1.10:8765/mcp",
        "http://0.0.0.0:8765/mcp",
        "http://127.0.0.2:8765/mcp",
        "http://127.0.0.1/mcp",
        "http://user:pass@127.0.0.1:8765/mcp",
        "file:///etc/passwd",
    ],
)
def test_refuses_non_loopback_hub_url(capture_server: _CaptureServer, monkeypatch: pytest.MonkeyPatch, bad_url: str) -> None:
    monkeypatch.setenv("MCP2P_HUB_URL", bad_url)
    monkeypatch.setenv("MCP2P_SESSION_TOKEN", "tok-1")
    rc = hook_client.main(["claude.Stop", "{}"])
    assert rc == 0
    assert capture_server.requests == []


@pytest.mark.parametrize(
    "bad_event",
    [
        "",
        "1bad",
        "-bad",
        ".bad",
        "claude/Stop",
        "claude Stop",
        "claude?x=1",
        "a" * 49,
        "claude.Stop\n",
    ],
)
def test_invalid_event_name_is_ignored(capture_server: _CaptureServer, monkeypatch: pytest.MonkeyPatch, bad_event: str) -> None:
    monkeypatch.setenv("MCP2P_HUB_URL", capture_server.hub_url)
    monkeypatch.setenv("MCP2P_SESSION_TOKEN", "tok-1")
    assert hook_client.main([bad_event, "{}"]) == 0
    assert hook_client.main([]) == 0
    assert capture_server.requests == []


def test_missing_or_dirty_token_is_ignored(capture_server: _CaptureServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP2P_HUB_URL", capture_server.hub_url)
    monkeypatch.delenv("MCP2P_SESSION_TOKEN", raising=False)
    assert hook_client.main(["claude.Stop", "{}"]) == 0
    monkeypatch.setenv("MCP2P_SESSION_TOKEN", "bad\ntoken")
    assert hook_client.main(["claude.Stop", "{}"]) == 0
    assert capture_server.requests == []


def test_runs_as_standalone_script_without_src_imports(capture_server: _CaptureServer, tmp_path: Path) -> None:
    # Copia hook_client.py fuera del repo para garantizar cero dependencia de `src.*` con `python -I`.
    isolated_script = tmp_path / "hook_client.py"
    isolated_script.write_text(HOOK_CLIENT_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-I", str(isolated_script), "opencode.event"],
        input=b'{"type":"session.idle"}',
        cwd=str(tmp_path),
        capture_output=True,
        env=_clean_env(
            {
                "MCP2P_HUB_URL": capture_server.hub_url,
                "MCP2P_SESSION_TOKEN": "tok-isolated",
            }
        ),
        timeout=10,
    )
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""
    assert len(capture_server.requests) == 1
    assert capture_server.requests[0]["path"] == "/hooks/v1/opencode.event"


def test_main_hook_post_dispatch_and_empty_stdin(capture_server: _CaptureServer, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("MCP2P_HUB_URL", f"http://localhost:{capture_server.port}/mcp")
    monkeypatch.setenv("MCP2P_SESSION_TOKEN", "tok-main")
    rc = app_main.main(["--hook-post", "claude.Stop", ""])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""
    assert len(capture_server.requests) == 1
    assert json.loads(capture_server.requests[0]["body"].decode("utf-8")) == {}


def test_non_json_payload_wraps_raw_and_ipv6_loopback_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP2P_HUB_URL", "http://[::1]:8765/mcp")
    url = hook_client._build_post_url("http://[::1]:8765/mcp", "claude.Stop")
    assert url == "http://[::1]:8765/hooks/v1/claude.Stop"

    encoded = hook_client._encode_payload(("Z" * 9000).encode("utf-8"))
    parsed = json.loads(encoded.decode("utf-8"))
    assert set(parsed) == {"raw"}
    assert len(parsed["raw"].encode("utf-8")) == 4096
