"""Pruebas WP-6a (v2.0.2): `HookEndpoint`, `HooksRouter` y `hooks_launch`."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.adapters.terminal import hooks_launch, pty_host
from src.adapters.terminal.cli_catalog import CliCatalogService, CliSpec, _parse_entry
from src.adapters.terminal.launch_augment import LaunchContext, LaunchServices
from src.adapters.terminal.mcp_injection import PaneMcpConfigStore
from src.adapters.terminal.pty_host import PosixPtyHostConnector
from src.core.domain.agent_activity import AgentState
from src.core.ports.identity import PEER_MISMATCH, PEER_VERIFIED
from src.core.services.agent_activity_registry import AgentActivityRegistry
from src.mcp.hook_endpoint import MAX_HOOK_BODY_BYTES, HookEndpoint, HooksRouter
from src.mcp.pane_registry import PaneRegistry
from src.mcp.peer_binding import PeerBinding, peer_binding_context
from src.mcp.server import _PaneTokenAuthMiddleware


def _spec_with_activity(
    cli_id: str,
    *,
    hook_install: str,
    events: Dict[str, str],
    session_id_field: str = "session_id",
    status: str = "verified",
    platforms: Tuple[str, ...] = ("linux",),
    mcp: Optional[Dict[str, Any]] = None,
) -> CliSpec:
    return _parse_entry(
        {
            "id": cli_id,
            "display_name": cli_id.title(),
            "icon": cli_id,
            "kind": "agent_cli",
            "binary": cli_id,
            "candidate_paths": {"linux": [f"/usr/bin/{cli_id}"], "win32": [f"C:\\bin\\{cli_id}.exe"]},
            "launch_flags": [],
            "mcp": mcp or {"method": "none", "format": "none", "verified": False},
            "resume": {"argv": [], "verified": False},
            "install": {},
            "activity": {
                "status": status,
                "platforms": list(platforms),
                "hook_install": hook_install,
                "events": events,
                "session_id_field": session_id_field,
                "verified_with_cli_version": "1.0.0" if status == "verified" else "",
                "verified_on": "2026-11-18" if status == "verified" else "",
                "note": "prueba",
            },
        },
        source="repo",
    )


async def _asgi_call(
    app: Any,
    *,
    method: str = "POST",
    path: str = "/hooks/v1/claude.Stop",
    headers: Optional[List[Tuple[bytes, bytes]]] = None,
    body: bytes = b"{}",
    chunk_size: Optional[int] = None,
    client: Optional[Tuple[str, int]] = ("127.0.0.1", 55000),
    server: Optional[Tuple[str, int]] = ("127.0.0.1", 8765),
) -> Tuple[int, Dict[str, Any], bytes]:
    responses: List[Dict[str, Any]] = []
    chunks: List[bytes]
    if chunk_size and len(body) > chunk_size:
        chunks = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)]
    else:
        chunks = [body]
    idx = 0

    async def _recv() -> Dict[str, Any]:
        nonlocal idx
        if idx >= len(chunks):
            return {"type": "http.request", "body": b"", "more_body": False}
        part = chunks[idx]
        idx += 1
        return {"type": "http.request", "body": part, "more_body": idx < len(chunks)}

    async def _send(msg: Dict[str, Any]) -> None:
        responses.append(msg)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [],
        "client": client,
        "server": server,
    }
    await app(scope, _recv, _send)
    start = next(r for r in responses if r["type"] == "http.response.start")
    raw_body = b"".join(r.get("body", b"") for r in responses if r["type"] == "http.response.body")
    parsed = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    return int(start["status"]), parsed, raw_body


def _build_stack(
    *,
    cli_profile: str = "claude",
    events: Optional[Dict[str, str]] = None,
    session_id_field: str = "session_id",
    peer_status: str = PEER_VERIFIED,
    with_activity: bool = True,
    clock_box: Optional[List[float]] = None,
):
    reg = PaneRegistry()
    pane = reg.register_pane("pane_1", cli_profile=cli_profile, project_uuid="proj-1")
    token = pane["session_token"]
    node_uuid = pane["node_uuid"]

    activity = AgentActivityRegistry(clock_ms=lambda: 5000) if with_activity else None
    spec = _spec_with_activity(
        cli_profile,
        hook_install="claude_settings",
        events=events
        if events is not None
        else {
            "claude.SessionStart": "session_started",
            "claude.Stop": "turn_ended",
            "claude.UserPromptSubmit": "turn_started",
            "claude.Notification.permission_prompt": "permission_requested",
            "codex.notify.agent-turn-complete": "turn_ended",
            "opencode.event.session.idle": "turn_ended",
            "opencode.event.permission.asked": "permission_requested",
        },
        session_id_field=session_id_field,
    )

    clock_fn = (lambda: clock_box[0]) if clock_box is not None else None
    endpoint = HookEndpoint(
        activity,
        catalog_lookup=lambda cid: spec if cid == cli_profile else None,
        clock=clock_fn,
        clock_ms=lambda: 5000,
    )

    async def _mcp_fallback(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"mcp":true}', "more_body": False})

    router = HooksRouter(_mcp_fallback, endpoint)

    class _FakeVerifier:
        def verify(self, record, client, server):
            return PeerBinding(peer_status, record.pane_id, "test")

    app = _PaneTokenAuthMiddleware(router, reg, _FakeVerifier())
    auth_headers = [(b"authorization", f"Bearer {token}".encode("ascii"))]
    return app, endpoint, activity, reg, node_uuid, auth_headers


def test_requires_pane_token() -> None:
    app, *_ = _build_stack()
    status_missing, body_missing, _ = asyncio.run(_asgi_call(app))
    assert status_missing == 401
    assert body_missing["code"] == "MISSING_PANE_SESSION_TOKEN"

    status_bad, body_bad, _ = asyncio.run(
        _asgi_call(app, headers=[(b"authorization", b"Bearer wrong-token")])
    )
    assert status_bad == 401
    assert body_bad["code"] == "INVALID_PANE_SESSION_TOKEN"


def test_rejects_peer_outside_pane_tree() -> None:
    app, _, activity, _, node_uuid, headers = _build_stack(peer_status=PEER_MISMATCH)
    status, body, _ = asyncio.run(_asgi_call(app, headers=headers))
    assert status == 403
    assert body["code"] == "PEER_PROCESS_MISMATCH"
    assert activity.snapshot(node_uuid).state == AgentState.UNKNOWN


def test_body_over_64kib_is_413() -> None:
    app, _, _, _, _, headers = _build_stack()
    oversized = b'{"x":"' + (b"a" * MAX_HOOK_BODY_BYTES) + b'"}'
    assert len(oversized) > MAX_HOOK_BODY_BYTES

    # Sin Content-Length, por lectura acotada en streaming
    status, body, _ = asyncio.run(_asgi_call(app, headers=headers, body=oversized, chunk_size=8192))
    assert status == 413
    assert body["code"] == "PAYLOAD_TOO_LARGE"

    # Con Content-Length > 64 KiB rechaza de inmediato
    cl_headers = [*headers, (b"content-length", str(MAX_HOOK_BODY_BYTES + 1).encode("ascii"))]
    status_cl, body_cl, _ = asyncio.run(_asgi_call(app, headers=cl_headers, body=b"{}"))
    assert status_cl == 413
    assert body_cl["code"] == "PAYLOAD_TOO_LARGE"


def test_bad_event_name_is_400() -> None:
    app, _, _, _, _, headers = _build_stack()
    for bad_path in ("/hooks/v1/", "/hooks/v1/bad$event", "/hooks/v1/a/b", "/hooks/v1/1starts_digit"):
        status, body, _ = asyncio.run(_asgi_call(app, path=bad_path, headers=headers))
        assert status == 400, (bad_path, status, body)
        assert body["code"] == "INVALID_EVENT"


def test_unknown_event_is_ignored_202() -> None:
    app, _, activity, _, node_uuid, headers = _build_stack()
    status, body, _ = asyncio.run(
        _asgi_call(app, path="/hooks/v1/claude.FutureHook", headers=headers, body=b"{}")
    )
    assert status == 202
    assert body == {"ok": True, "ignored": True}
    assert activity.snapshot(node_uuid).state == AgentState.UNKNOWN


def test_rate_limit_429() -> None:
    clock_box = [100.0]
    app, _, _, _, _, headers = _build_stack(clock_box=clock_box)
    for idx in range(50):
        st, _, _ = asyncio.run(_asgi_call(app, headers=headers, body=b"{}"))
        assert st == 200, f"fallo en evento #{idx}"

    st_51, body_51, _ = asyncio.run(_asgi_call(app, headers=headers, body=b"{}"))
    assert st_51 == 429
    assert body_51["code"] == "RATE_LIMITED"

    # Al avanzar más de 1s la ventana vuelve a admitir eventos
    clock_box[0] = 101.1
    st_after, _, _ = asyncio.run(_asgi_call(app, headers=headers, body=b"{}"))
    assert st_after == 200


def test_response_never_contains_decision_reason_or_continue() -> None:
    forbidden = {"decision", "reason", "continue"}
    observed_bodies: List[Dict[str, Any]] = []

    def _check(status: int, body: Dict[str, Any]) -> None:
        observed_bodies.append(body)
        assert not (forbidden & set(body.keys())), f"campos prohibidos en {status}: {body}"

    # 200, 202, 400 (evento/json/content-length/array), 401, 413, 429
    clock_box = [10.0]
    app, endpoint, _, _, _, headers = _build_stack(clock_box=clock_box)

    _check(*asyncio.run(_asgi_call(app, headers=headers, body=b""))[:2])  # cuerpo vacío -> 200
    _check(*asyncio.run(_asgi_call(app, path="/hooks/v1/claude.Unknown", headers=headers))[:2])  # 202
    _check(*asyncio.run(_asgi_call(app, path="/hooks/v1/bad!ev", headers=headers))[:2])  # 400 event
    _check(*asyncio.run(_asgi_call(app, headers=headers, body=b"{not-json"))[:2])  # 400 json
    _check(*asyncio.run(_asgi_call(app, headers=headers, body=b"[1,2]"))[:2])  # 400 non-dict
    _check(*asyncio.run(_asgi_call(app, headers=[*headers, (b"content-length", b"abc")]))[:2])  # 400 CL
    _check(*asyncio.run(_asgi_call(app))[:2])  # 401
    _check(*asyncio.run(_asgi_call(app, headers=headers, body=b"a" * (MAX_HOOK_BODY_BYTES + 10)))[:2])  # 413

    for _ in range(49):
        _check(*asyncio.run(_asgi_call(app, headers=headers, body=b"{}"))[:2])
    _check(*asyncio.run(_asgi_call(app, headers=headers, body=b"{}"))[:2])  # 429

    # 403 (peer mismatch)
    app_403, *_, h403 = _build_stack(peer_status=PEER_MISMATCH)
    _check(*asyncio.run(_asgi_call(app_403, headers=h403))[:2])

    # 503 (sin registro de actividad)
    app_503, *_, h503 = _build_stack(with_activity=False)
    _check(*asyncio.run(_asgi_call(app_503, headers=h503))[:2])

    # 405 y 401 directo en HookEndpoint sin middleware
    _check(*asyncio.run(_asgi_call(endpoint, method="GET", path="/hooks/v1/claude.Stop"))[:2])
    with peer_binding_context(None):
        _check(*asyncio.run(_asgi_call(endpoint, method="POST", path="/hooks/v1/claude.Stop"))[:2])

    assert len(observed_bodies) >= 13


def test_maps_raw_events_with_catalog_and_extracts_session_id() -> None:
    # 1. Claude: SessionStart con session_id y Notification.permission_prompt
    app, _, activity, _, node_uuid, headers = _build_stack(cli_profile="claude")
    seen_sids: List[Tuple[str, str]] = []
    activity.on_cli_session_id(lambda n, sid: seen_sids.append((n, sid)))

    st, body, _ = asyncio.run(
        _asgi_call(
            app,
            path="/hooks/v1/claude.SessionStart",
            headers=headers,
            body=json.dumps({"session_id": "claude-sess_01"}).encode("utf-8"),
        )
    )
    assert st == 200 and body == {"ok": True}
    assert activity.snapshot(node_uuid).state == AgentState.IDLE
    assert seen_sids == [(node_uuid, "claude-sess_01")]

    st_perm, _, _ = asyncio.run(
        _asgi_call(app, path="/hooks/v1/claude.Notification.permission_prompt", headers=headers, body=b"")
    )
    assert st_perm == 200
    assert activity.snapshot(node_uuid).state == AgentState.PERMISSION

    # 2. Codex: codex.notify + payload["type"]
    app_cx, _, act_cx, _, node_cx, h_cx = _build_stack(cli_profile="codex", session_id_field="thread-id")
    seen_cx: List[Tuple[str, str]] = []
    act_cx.on_cli_session_id(lambda n, sid: seen_cx.append((n, sid)))
    st_cx, _, _ = asyncio.run(
        _asgi_call(
            app_cx,
            path="/hooks/v1/codex.notify",
            headers=h_cx,
            body=json.dumps({"type": "agent-turn-complete", "thread-id": "thr_99"}).encode("utf-8"),
        )
    )
    assert st_cx == 200
    assert act_cx.snapshot(node_cx).state == AgentState.IDLE
    assert seen_cx == [(node_cx, "thr_99")]

    # 3. OpenCode: opencode.event + event.type
    app_oc, _, act_oc, _, node_oc, h_oc = _build_stack(cli_profile="opencode", session_id_field="event.sessionID")
    seen_oc: List[Tuple[str, str]] = []
    act_oc.on_cli_session_id(lambda n, sid: seen_oc.append((n, sid)))
    st_oc, _, _ = asyncio.run(
        _asgi_call(
            app_oc,
            path="/hooks/v1/opencode.event",
            headers=h_oc,
            body=json.dumps({"event": {"type": "permission.asked", "sessionID": "oc_123"}}).encode("utf-8"),
        )
    )
    assert st_oc == 200
    assert act_oc.snapshot(node_oc).state == AgentState.PERMISSION
    assert seen_oc == [(node_oc, "oc_123")]


def test_session_id_with_bad_chars_is_dropped() -> None:
    app, _, activity, _, node_uuid, headers = _build_stack(cli_profile="claude")
    seen: List[Tuple[str, str]] = []
    activity.on_cli_session_id(lambda n, sid: seen.append((n, sid)))

    for bad_sid in ("../etc/passwd", "with space", "a" * 129, "semi;colon", 12345):
        st, body, _ = asyncio.run(
            _asgi_call(
                app,
                path="/hooks/v1/claude.SessionStart",
                headers=headers,
                body=json.dumps({"session_id": bad_sid}).encode("utf-8"),
            )
        )
        assert st == 200 and body == {"ok": True}
    assert seen == []
    assert activity.snapshot(node_uuid).state == AgentState.IDLE


def test_hooks_launch_installs_only_when_verified_for_platform(tmp_path: Path) -> None:
    services = LaunchServices(hook_argv=lambda ev: ["/opt/mcp2p/Mcp2p", "--hook-post", ev])
    spec_linux_only = _spec_with_activity(
        "claude",
        hook_install="claude_settings",
        events={"claude.Stop": "turn_ended"},
        status="verified",
        platforms=("linux",),
    )

    # Plataforma no verificada -> no instala ni archivos ni flags
    ctx_win = LaunchContext(
        spec=spec_linux_only,
        platform="win32",
        pane_slot="pane_1",
        session_id="pane_1-s1",
        hub_url="http://127.0.0.1:8765/mcp",
        request={},
        session_file=lambda n: tmp_path / n,
        services=services,
    )
    out_win = hooks_launch.augment(ctx_win)
    assert out_win.argv == () and out_win.files == {} and out_win.env == {}
    assert out_win.meta == {"hooks": "none"}

    # Catálogo real actual (unverified) -> tampoco instala nada
    ctx_repo = LaunchContext(
        spec=CliCatalogService(include_user_catalog=False).get_spec("claude"),  # type: ignore[arg-type]
        platform="linux",
        pane_slot="pane_1",
        session_id="pane_1-s1",
        hub_url="http://127.0.0.1:8765/mcp",
        request={},
        session_file=lambda n: tmp_path / n,
        services=services,
    )
    out_repo = hooks_launch.augment(ctx_repo)
    assert out_repo.argv == () and out_repo.files == {} and out_repo.meta == {"hooks": "none"}

    # Verificado en la plataforma actual + codex_notify
    spec_codex = _spec_with_activity(
        "codex",
        hook_install="codex_notify",
        events={"codex.notify.agent-turn-complete": "turn_ended"},
        status="verified",
        platforms=("linux",),
    )
    ctx_codex = LaunchContext(
        spec=spec_codex,
        platform="linux",
        pane_slot="pane_1",
        session_id="pane_1-s1",
        hub_url="http://127.0.0.1:8765/mcp",
        request={},
        session_file=lambda n: tmp_path / n,
        services=services,
    )
    out_codex = hooks_launch.augment(ctx_codex)
    assert out_codex.argv[0] == "-c" and out_codex.argv[1].startswith("notify=[")
    assert out_codex.meta == {"hooks": "installed"}


def _build_fake_winpty_connector(
    monkeypatch: pytest.MonkeyPatch,
    *,
    svc: CliCatalogService,
    store: PaneMcpConfigStore,
    which_path: str,
    captured_cmds: List[List[str]],
    captured_envs: List[Dict[str, str]],
):
    from src.adapters.terminal import winpty_host
    from src.adapters.terminal.winpty_host import WinPtyHostConnector

    class _FakeWinPty:
        def __init__(self, cols: int, rows: int, backend: Any = 0) -> None:
            self.pid = 2002

        def spawn(self, appname: str, cmdline: str = "", cwd: Any = None, env: Any = None) -> bool:
            parsed_env: Dict[str, str] = {}
            if isinstance(env, str):
                for entry in env.split("\x00"):
                    if "=" in entry:
                        k, v = entry.split("=", 1)
                        parsed_env[k] = v
            captured_envs.append(parsed_env)
            return True

        def isalive(self) -> bool:
            return False

        def read(self, n: int = 4096) -> str:
            return ""

    win_host = WinPtyHostConnector(
        catalog_service=svc,
        mcp_config_store=store,
        winpty_module=SimpleNamespace(Backend=SimpleNamespace(ConPTY=0), PTY=_FakeWinPty),
    )
    win_host.launch_services = LaunchServices(hook_argv=lambda ev: ["C:\\Mcp2p\\Mcp2p.exe", "--hook-post", ev])
    monkeypatch.setattr(winpty_host, "is_windows_platform", lambda: True)
    monkeypatch.setattr(win_host, "_which_executable", lambda b, cwd=None: which_path)
    monkeypatch.setattr(
        winpty_host,
        "build_windows_launch",
        lambda cmd, comspec=None: (
            captured_cmds.append(list(cmd)),
            SimpleNamespace(appname=cmd[0], cmdline=" ".join(cmd), display=list(cmd), kind="exe"),
        )[1],
    )
    monkeypatch.setattr(win_host, "_reader_loop", lambda sess: None)
    return win_host


def test_claude_hooks_file_is_0600_and_argv_points_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_claude = _spec_with_activity(
        "claude",
        hook_install="claude_settings",
        events={"claude.Stop": "turn_ended"},
        status="verified",
        platforms=("linux", "win32"),
    )
    fake_bin = tmp_path / "claude"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(0o755)

    if os.name == "posix":
        svc = CliCatalogService(include_user_catalog=False, which=lambda n: str(fake_bin), platform="linux")
        monkeypatch.setattr(svc, "get_spec", lambda cid: spec_claude if cid == "claude" else None)
        store = PaneMcpConfigStore(base_dir=tmp_path / "store")
        host = PosixPtyHostConnector(catalog_service=svc, mcp_config_store=store)
        host.launch_services = LaunchServices(hook_argv=lambda ev: ["/opt/mcp2p/Mcp2p", "--hook-post", ev])

        captured: List[List[str]] = []
        monkeypatch.setattr(
            pty_host.pty,
            "openpty",
            lambda: (os.open(os.devnull, os.O_RDONLY), os.open(os.devnull, os.O_RDONLY)),
        )
        monkeypatch.setattr(host, "_set_winsize", lambda fd, c, r: None)
        monkeypatch.setattr(host, "_set_nonblocking", lambda fd: None)
        monkeypatch.setattr(
            pty_host.subprocess,
            "Popen",
            lambda cmd, **kw: (captured.append(list(cmd)), SimpleNamespace(pid=9999, poll=lambda: None))[1],
        )
        monkeypatch.setattr(host, "_reader_loop", lambda sess: None)

        try:
            res = host.spawn_session(1, cli_profile="claude", use_trampoline=False)
            assert res["success"] is True
            assert res["launch_meta"].get("hooks") == "installed"

            hooks_files = list((tmp_path / "store").rglob("hooks.json"))
            assert len(hooks_files) == 1
            assert stat.S_IMODE(hooks_files[0].stat().st_mode) == 0o600

            cmd = captured[0]
            idx = cmd.index("--settings")
            assert cmd[idx + 1] == str(hooks_files[0])
            parsed = json.loads(hooks_files[0].read_text(encoding="utf-8"))
            assert "Stop" in parsed["hooks"] and "Notification" in parsed["hooks"]
        finally:
            host.close()

    # Variante portable win32 (corre en Linux y Windows sin skip)
    win_svc = CliCatalogService(include_user_catalog=False, which=lambda n: str(fake_bin), platform="win32")
    monkeypatch.setattr(win_svc, "get_spec", lambda cid: spec_claude if cid == "claude" else None)
    win_store = PaneMcpConfigStore(base_dir=tmp_path / "store_win")
    win_cmds: List[List[str]] = []
    win_envs: List[Dict[str, str]] = []
    win_host = _build_fake_winpty_connector(
        monkeypatch, svc=win_svc, store=win_store, which_path=str(fake_bin), captured_cmds=win_cmds, captured_envs=win_envs
    )
    try:
        win_res = win_host.spawn_session(1, cli_profile="claude", use_trampoline=False)
        assert win_res["success"] is True
        assert win_res["launch_meta"].get("hooks") == "installed"

        win_hooks = list((tmp_path / "store_win").rglob("hooks.json"))
        assert len(win_hooks) == 1
        win_cmd = win_cmds[0]
        w_idx = win_cmd.index("--settings")
        assert win_cmd[w_idx + 1] == str(win_hooks[0])
        win_parsed = json.loads(win_hooks[0].read_text(encoding="utf-8"))
        assert "Stop" in win_parsed["hooks"] and "Notification" in win_parsed["hooks"]
    finally:
        win_host.close()


def test_opencode_combined_config_keeps_mcp_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_oc = _spec_with_activity(
        "opencode",
        hook_install="opencode_plugin",
        events={"opencode.event.session.idle": "turn_ended"},
        status="verified",
        platforms=("linux", "win32"),
        mcp={
            "method": "env",
            "format": "opencode_json",
            "env": {"OPENCODE_CONFIG": "{mcp_config_file}"},
            "verified": True,
        },
    )
    fake_bin = tmp_path / "opencode"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(0o755)

    if os.name == "posix":
        svc = CliCatalogService(include_user_catalog=False, which=lambda n: str(fake_bin), platform="linux")
        monkeypatch.setattr(svc, "get_spec", lambda cid: spec_oc if cid == "opencode" else None)
        store = PaneMcpConfigStore(base_dir=tmp_path / "store_oc")
        host = PosixPtyHostConnector(catalog_service=svc, mcp_config_store=store)
        host.launch_services = LaunchServices(hook_argv=lambda ev: ["/opt/mcp2p/Mcp2p", "--hook-post", ev])

        captured_env: List[Dict[str, str]] = []
        monkeypatch.setattr(
            pty_host.pty,
            "openpty",
            lambda: (os.open(os.devnull, os.O_RDONLY), os.open(os.devnull, os.O_RDONLY)),
        )
        monkeypatch.setattr(host, "_set_winsize", lambda fd, c, r: None)
        monkeypatch.setattr(host, "_set_nonblocking", lambda fd: None)
        monkeypatch.setattr(
            pty_host.subprocess,
            "Popen",
            lambda cmd, **kw: (captured_env.append(dict(kw.get("env") or {})), SimpleNamespace(pid=9998, poll=lambda: None))[1],
        )
        monkeypatch.setattr(host, "_reader_loop", lambda sess: None)

        try:
            res = host.spawn_session(
                1,
                cli_profile="opencode",
                hub_url="http://127.0.0.1:8765/mcp",
                session_token="tok-oc-1",
                use_trampoline=False,
            )
            assert res["success"] is True
            assert res["launch_meta"].get("hooks") == "installed"

            oc_configs = list((tmp_path / "store_oc").rglob("opencode.json"))
            plugin_files = list((tmp_path / "store_oc").rglob("mcp2p_plugin.js"))
            assert len(oc_configs) == 1 and len(plugin_files) == 1
            assert captured_env[0]["OPENCODE_CONFIG"] == str(oc_configs[0])

            combined = json.loads(oc_configs[0].read_text(encoding="utf-8"))
            assert combined["mcp"]["mcp2p"]["url"] == "http://127.0.0.1:8765/mcp"
            assert combined["mcp"]["mcp2p"]["headers"]["Authorization"] == "Bearer tok-oc-1"
            assert combined["plugin"] == [str(plugin_files[0])]
        finally:
            host.close()

    # Variante portable win32 (corre en Linux y Windows sin skip)
    win_svc = CliCatalogService(include_user_catalog=False, which=lambda n: str(fake_bin), platform="win32")
    monkeypatch.setattr(win_svc, "get_spec", lambda cid: spec_oc if cid == "opencode" else None)
    win_store = PaneMcpConfigStore(base_dir=tmp_path / "store_oc_win")
    win_cmds: List[List[str]] = []
    win_envs: List[Dict[str, str]] = []
    win_host = _build_fake_winpty_connector(
        monkeypatch, svc=win_svc, store=win_store, which_path=str(fake_bin), captured_cmds=win_cmds, captured_envs=win_envs
    )
    try:
        win_res = win_host.spawn_session(
            1,
            cli_profile="opencode",
            hub_url="http://127.0.0.1:8765/mcp",
            session_token="tok-oc-win",
            use_trampoline=False,
        )
        assert win_res["success"] is True
        assert win_res["launch_meta"].get("hooks") == "installed"

        win_oc_configs = list((tmp_path / "store_oc_win").rglob("opencode.json"))
        win_plugin_files = list((tmp_path / "store_oc_win").rglob("mcp2p_plugin.js"))
        assert len(win_oc_configs) == 1 and len(win_plugin_files) == 1
        assert win_envs[0]["OPENCODE_CONFIG"] == str(win_oc_configs[0])

        win_combined = json.loads(win_oc_configs[0].read_text(encoding="utf-8"))
        assert win_combined["mcp"]["mcp2p"]["url"] == "http://127.0.0.1:8765/mcp"
        assert win_combined["mcp"]["mcp2p"]["headers"]["Authorization"] == "Bearer tok-oc-win"
        assert win_combined["plugin"] == [str(win_plugin_files[0])]
    finally:
        win_host.close()
