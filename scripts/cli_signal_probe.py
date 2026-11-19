#!/usr/bin/env python3
"""Sonda de señales y comportamiento de PTY para medir CLIs reales (v2.0.2 WP-5a / WP-5b).

Herramienta de diagnóstico para ejecutar fuera de la GUI principal:
- Todas las escrituras de artefactos ocurren exclusivamente dentro de `--out`.
- Nunca modifica configuraciones globales del usuario (`~/.claude`, `~/.codex`,
  `~/.config/opencode`, `~/.gemini`).
- El subcomando `pty` utiliza los conectores de terminales de producción de MCP2P
  (`PosixPtyHostConnector` en POSIX y `WinPtyHostConnector` en Windows) como
  herramienta de diagnóstico/medición. **Es la única herramienta del repositorio
  que escribe en un PTY fuera de `src/`** (las pruebas AST de D12 solo inspeccionan `src/`).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import hmac
import json
import os
import re
import shlex
import secrets
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.adapters.terminal.cli_catalog import load_repo_catalog
from src.adapters.terminal.hook_configs import (
    agy_settings,
    claude_settings,
    codex_notify_args,
    hook_command_argv,
    opencode_config_with_plugin,
    opencode_plugin_source,
)
from src.adapters.terminal.mcp_injection import PaneMcpConfigStore

_EVENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,47}$")
_SESSION_KEYS = frozenset(
    {
        "session_id",
        "sessionid",
        "thread_id",
        "thread-id",
        "conversation_id",
        "conversationid",
    }
)
_SUPPORTED_CLIS = ("claude", "codex", "opencode", "antigravity")
_MAX_BODY_BYTES = 64 * 1024

_EVENT_ALIASES: Dict[str, Tuple[str, ...]] = {
    "session_started": ("session_started", "claude.SessionStart"),
    "turn_started": ("turn_started", "claude.UserPromptSubmit", "agy.PreInvocation"),
    "turn_ended": ("turn_ended", "claude.Stop", "agy.Stop", "codex.notify", "session.idle"),
    "permission_requested": (
        "permission_requested",
        "claude.PermissionRequest",
        "claude.Notification.permission_prompt",
        "permission.asked",
        "permission.updated",
    ),
    "idle_notice": ("idle_notice", "claude.Notification.idle_prompt", "session.idle"),
    "tool_used": ("tool_used", "claude.PostToolUse", "agy.PostToolUse"),
    "subagent_ended": ("subagent_ended", "claude.SubagentStop"),
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _global_config_paths() -> Dict[str, Path]:
    home = Path.home()
    return {
        "claude": home / ".claude" / "settings.json",
        "codex": home / ".codex" / "config.toml",
        "opencode": home / ".config" / "opencode" / "opencode.json",
        "antigravity": home / ".gemini" / "config" / "hooks.json",
    }


def _snapshot_file_hash(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {"path": str(path), "exists": False, "sha256": None, "size": None}
        data = path.read_bytes()
        return {
            "path": str(path),
            "exists": True,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
    except OSError as exc:
        return {"path": str(path), "exists": False, "sha256": None, "size": None, "error": str(exc)}


def _extract_session_id_candidates(payload: Any, *, prefix: str = "", depth: int = 0) -> Dict[str, str]:
    found: Dict[str, str] = {}
    if depth > 4 or not isinstance(payload, Mapping):
        return found
    for key, val in payload.items():
        key_str = str(key)
        dotted = f"{prefix}.{key_str}" if prefix else key_str
        if key_str.lower() in _SESSION_KEYS and isinstance(val, str) and val.strip():
            found[dotted] = val.strip()[:128]
        elif isinstance(val, Mapping):
            found.update(_extract_session_id_candidates(val, prefix=dotted, depth=depth + 1))
    return found


def _extract_payload_type(payload: Any) -> Optional[str]:
    if not isinstance(payload, Mapping):
        return None
    for candidate in (payload.get("type"), payload.get("hook_event_name")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:64]
    inner_ev = payload.get("event")
    if isinstance(inner_ev, Mapping):
        ev_type = inner_ev.get("type")
        if isinstance(ev_type, str) and ev_type.strip():
            return ev_type.strip()[:64]
    return None


def _restrict_owner_mode(path: Path, mode: int = 0o600) -> None:
    if os.name == "posix":
        try:
            os.chmod(path, mode)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 1. listen
# ---------------------------------------------------------------------------


def start_listener(
    out_dir: Path,
    *,
    port: int = 0,
    keep_payload: bool = False,
    token: Optional[str] = None,
) -> Tuple[ThreadingHTTPServer, Dict[str, Any], threading.Thread]:
    """Arranca el servidor HTTP local de la sonda en 127.0.0.1 y devuelve `(server, info, thread)`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    probe_token = token or f"probe-{secrets.token_urlsafe(18)}"
    write_lock = threading.Lock()

    class _ProbeHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, body: Dict[str, Any]) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:  # noqa: N802
            if not self.path.startswith("/hooks/v1/"):
                self._send_json(404, {"ok": False, "code": "NOT_FOUND"})
                return
            event_name = self.path[len("/hooks/v1/") :].split("?", 1)[0]
            if _EVENT_RE.fullmatch(event_name) is None:
                self._send_json(400, {"ok": False, "code": "INVALID_EVENT"})
                return

            auth = self.headers.get("Authorization") or ""
            expected = f"Bearer {probe_token}"
            if not hmac.compare_digest(auth, expected):
                self._send_json(401, {"ok": False, "code": "UNAUTHORIZED"})
                return

            try:
                content_len = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self._send_json(400, {"ok": False, "code": "INVALID_CONTENT_LENGTH"})
                return
            if content_len < 0 or content_len > _MAX_BODY_BYTES:
                self._send_json(413, {"ok": False, "code": "PAYLOAD_TOO_LARGE"})
                return

            raw_body = self.rfile.read(content_len) if content_len > 0 else b""
            if len(raw_body) > _MAX_BODY_BYTES:
                self._send_json(413, {"ok": False, "code": "PAYLOAD_TOO_LARGE"})
                return

            stripped = raw_body.decode("utf-8", errors="replace").strip()
            if not stripped:
                payload: Any = {}
            else:
                try:
                    payload = json.loads(stripped)
                except ValueError:
                    self._send_json(400, {"ok": False, "code": "INVALID_JSON"})
                    return

            record: Dict[str, Any] = {
                "t_ms": _now_ms(),
                "event": event_name,
                "payload_keys": sorted(str(k) for k in payload.keys()) if isinstance(payload, Mapping) else [],
                "session_id_candidates": _extract_session_id_candidates(payload),
                "bytes": len(raw_body),
            }
            payload_type = _extract_payload_type(payload)
            if payload_type is not None:
                record["payload_type"] = payload_type
            if keep_payload:
                record["payload"] = payload

            line = json.dumps(record, ensure_ascii=False) + "\n"
            with write_lock:
                with events_path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.flush()

            self._send_json(200, {"ok": True})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return None

    server = ThreadingHTTPServer(("127.0.0.1", int(port)), _ProbeHandler)
    bound_port = int(server.server_address[1])
    hub_url = f"http://127.0.0.1:{bound_port}/mcp"
    info = {
        "url": hub_url,
        "port": bound_port,
        "token": probe_token,
        "events_file": str(events_path),
    }
    listener_meta = out_dir / "listener.json"
    listener_meta.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _restrict_owner_mode(listener_meta, 0o600)

    thread = threading.Thread(target=server.serve_forever, name="cli-signal-probe-listen", daemon=True)
    thread.start()
    return server, info, thread


def cmd_listen(args: argparse.Namespace) -> int:
    server, info, _thread = start_listener(
        Path(args.out),
        port=args.port,
        keep_payload=bool(args.keep_payload),
        token=args.token,
    )
    print(f"MCP2P_HUB_URL={info['url']}", flush=True)
    print(f"MCP2P_SESSION_TOKEN={info['token']}", flush=True)
    print(f"EVENTS_FILE={info['events_file']}", flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


# ---------------------------------------------------------------------------
# 2. prepare & check-globals
# ---------------------------------------------------------------------------


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _render_run_sh(binary: str, argv: Sequence[str], env: Mapping[str, str]) -> str:
    lines = ["#!/usr/bin/env bash", "set -euo pipefail"]
    for k, v in env.items():
        lines.append(f"export {k}={shlex.quote(v)}")
    cmd = " ".join(shlex.quote(part) for part in [binary, *argv])
    lines.append(f"exec {cmd} \"$@\"")
    return "\n".join(lines) + "\n"


def _render_run_ps1(binary: str, argv: Sequence[str], env: Mapping[str, str]) -> str:
    lines = ["$ErrorActionPreference = 'Stop'"]
    for k, v in env.items():
        lines.append(f"$env:{k} = {_ps_quote(v)}")
    rendered_args = " ".join(_ps_quote(part) for part in argv)
    if rendered_args:
        lines.append(f"& {_ps_quote(binary)} {rendered_args} @args")
    else:
        lines.append(f"& {_ps_quote(binary)} @args")
    return "\n".join(lines) + "\n"


def prepare_cli_probe(
    *,
    cli: str,
    out_dir: Path,
    hub_url: str,
    token: str,
    frozen_exe: Optional[str] = None,
    platform: Optional[str] = None,
) -> Dict[str, Any]:
    """Prepara configuraciones por sesion, scripts `run.sh`/`run.ps1` y hashes globales en `out_dir`."""
    if cli not in _SUPPORTED_CLIS:
        raise ValueError(f"CLI no soportado: {cli!r}. Esperado uno de {_SUPPORTED_CLIS}.")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plat = platform or sys.platform

    if frozen_exe:
        hook_argv: Callable[[str], List[str]] = lambda ev: hook_command_argv(
            ev, frozen=True, executable=frozen_exe, repo_root=None
        )
    else:
        hook_argv = lambda ev: hook_command_argv(
            ev, frozen=False, executable=sys.executable, repo_root=REPO_ROOT
        )

    catalog = load_repo_catalog()
    spec = catalog.get(cli)
    binary = spec.binary if spec is not None else cli

    cli_argv: List[str] = []
    extra_env: Dict[str, str] = {}
    generated_files: List[str] = []

    if cli == "claude":
        cfg_path = out_dir / "claude_hooks.json"
        cfg_path.write_text(
            json.dumps(claude_settings(hook_argv, plat), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        cli_argv = ["--settings", str(cfg_path)]
        generated_files.append(str(cfg_path))
    elif cli == "antigravity":
        cfg_path = out_dir / "agy_hooks.json"
        cfg_path.write_text(
            json.dumps(agy_settings(hook_argv, plat), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        cli_argv = ["--settings", str(cfg_path)]
        generated_files.append(str(cfg_path))
    elif cli == "codex":
        cli_argv = codex_notify_args(hook_argv)
    elif cli == "opencode":
        plugin_path = out_dir / "mcp2p_plugin.js"
        plugin_path.write_text(opencode_plugin_source(), encoding="utf-8")
        cfg_path = out_dir / "opencode.json"
        merged = opencode_config_with_plugin({"$schema": "https://opencode.ai/config.json"}, plugin_path)
        cfg_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        extra_env["OPENCODE_CONFIG"] = str(cfg_path)
        generated_files.extend([str(plugin_path), str(cfg_path)])

    script_env = {
        "MCP2P_HUB_URL": hub_url,
        "MCP2P_SESSION_TOKEN": token,
        **extra_env,
    }
    run_sh = out_dir / "run.sh"
    run_sh.write_text(_render_run_sh(binary, cli_argv, script_env), encoding="utf-8")
    _restrict_owner_mode(run_sh, 0o700)

    run_ps1 = out_dir / "run.ps1"
    run_ps1.write_text(_render_run_ps1(binary, cli_argv, script_env), encoding="utf-8")
    _restrict_owner_mode(run_ps1, 0o600)

    global_hashes = {name: _snapshot_file_hash(path) for name, path in _global_config_paths().items()}
    hashes_path = out_dir / "global_hashes.json"
    hashes_path.write_text(json.dumps(global_hashes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    manifest = {
        "cli": cli,
        "binary": binary,
        "argv": cli_argv,
        "env": extra_env,
        "hub_url": hub_url,
        "generated_files": generated_files,
        "run_sh": str(run_sh),
        "run_ps1": str(run_ps1),
        "global_hashes_file": str(hashes_path),
    }
    manifest_path = out_dir / "prepare_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _restrict_owner_mode(manifest_path, 0o600)
    return manifest


def cmd_prepare(args: argparse.Namespace) -> int:
    manifest = prepare_cli_probe(
        cli=args.cli,
        out_dir=Path(args.out),
        hub_url=args.hub_url,
        token=args.token,
        frozen_exe=args.frozen_exe,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


def check_global_configs(out_dir: Path) -> Dict[str, Any]:
    """Compara el SHA-256 actual de las configs globales contra la instantánea guardada por `prepare`."""
    hashes_path = Path(out_dir) / "global_hashes.json"
    baseline = json.loads(hashes_path.read_text(encoding="utf-8"))
    results: Dict[str, Any] = {}
    changed: List[str] = []
    for name, expected in baseline.items():
        current = _snapshot_file_hash(Path(expected["path"]))
        same = (
            current["exists"] == expected["exists"]
            and current["sha256"] == expected["sha256"]
        )
        results[name] = {
            "path": expected["path"],
            "changed": not same,
            "before": expected["sha256"],
            "after": current["sha256"],
        }
        if not same:
            changed.append(name)
    return {"ok": len(changed) == 0, "changed": changed, "files": results}


def cmd_check_globals(args: argparse.Namespace) -> int:
    report = check_global_configs(Path(args.out))
    for name, info in report["files"].items():
        status = "CAMBIO DETECTADO" if info["changed"] else "sin cambios"
        print(f"{name}: {status} ({info['path']})")
    return 0 if report["ok"] else 1


# ---------------------------------------------------------------------------
# 3. pty (usa el conector de terminales de produccion de MCP2P)
# ---------------------------------------------------------------------------


def _build_pty_connector(out_dir: Path) -> Any:
    store = PaneMcpConfigStore(out_dir / "_mcp_session")
    if sys.platform == "win32":
        from src.adapters.terminal.winpty_host import WinPtyHostConnector

        return WinPtyHostConnector(max_panes=1, memory_probe=lambda: 10 ** 6, mcp_config_store=store)
    from src.adapters.terminal.pty_host import PosixPtyHostConnector

    return PosixPtyHostConnector(max_panes=1, memory_probe=lambda: 10 ** 6, mcp_config_store=store)


def _event_matches(target: str, record: Mapping[str, Any]) -> bool:
    ev = str(record.get("event") or "")
    ptype = str(record.get("payload_type") or "")
    if target == ev or (ptype and target == ptype):
        return True
    aliases = _EVENT_ALIASES.get(target, ())
    if ev in aliases or (ptype and ptype in aliases):
        return True
    if target == "permission_requested" and ptype.startswith("permission."):
        return True
    return False


def run_pty_probe(
    *,
    cli: str,
    cwd: Path,
    steps: Sequence[Mapping[str, Any]],
    out_dir: Path,
    hub_url: Optional[str] = None,
    token: Optional[str] = None,
    events_file: Optional[Path] = None,
    command_override: Optional[Sequence[str]] = None,
    connector: Optional[Any] = None,
    exit_grace_s: float = 0.5,
) -> Dict[str, Any]:
    """Ejecuta una secuencia de pasos de medición contra el conector de terminales de MCP2P.

    Es la única utilidad fuera de `src/` que invoca `send_input` sobre un PTY,
    reutilizando el mismo conector POSIX/ConPTY que producción para WP-5b.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cwd_path = Path(cwd)
    screen_path = out_dir / "screen.raw"
    timeline_path = out_dir / "timeline.jsonl"
    ev_path = Path(events_file) if events_file is not None else (out_dir / "events.jsonl")

    manifest_path = out_dir / "prepare_manifest.json"
    manifest: Dict[str, Any] = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if command_override is not None:
        command = [str(x) for x in command_override]
    elif manifest:
        command = [str(manifest["binary"]), *[str(x) for x in manifest.get("argv", [])]]
    else:
        spec = load_repo_catalog().get(cli)
        command = [spec.binary if spec is not None else cli]

    extra_env = dict(manifest.get("env") or {}) if manifest else {}
    effective_hub_url = hub_url or manifest.get("hub_url")

    owns_connector = connector is None
    conn = connector if connector is not None else _build_pty_connector(out_dir)
    start_ms = _now_ms()
    cursor = 0
    events_offset = ev_path.stat().st_size if ev_path.exists() else 0
    timeline_entries: List[Dict[str, Any]] = []

    def _log(kind: str, **detail: Any) -> None:
        now = _now_ms()
        entry = {"t_ms": now, "rel_ms": now - start_ms, "kind": kind, **detail}
        timeline_entries.append(entry)
        with timeline_path.open("a", encoding="utf-8") as tfh:
            tfh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            tfh.flush()

    spawn = conn.spawn_session(
        1,
        cli_profile=cli,
        command=command,
        cwd=cwd_path,
        env_overrides=extra_env or None,
        hub_url=effective_hub_url,
        session_token=token,
        use_trampoline=False,
    )
    if not spawn.get("success"):
        _log("spawn_failed", response=spawn)
        return {"success": False, "spawn": spawn, "timeline": timeline_entries}

    _log("spawned", pid=spawn.get("pid"), session_id=spawn.get("session_id"), command=command)

    try:
        with screen_path.open("ab") as screen_fh:

            def _pump() -> bool:
                nonlocal cursor
                res = conn.poll_output(1, since_seq=cursor)
                cursor = int(res.get("next_seq", cursor))
                text = res.get("text") or ""
                if text:
                    screen_fh.write(text.encode("utf-8", errors="replace"))
                    screen_fh.flush()
                return bool(res.get("alive", False))

            def _sleep_pumping(seconds: float) -> None:
                deadline = time.monotonic() + max(0.0, float(seconds))
                while time.monotonic() < deadline:
                    _pump()
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                _pump()

            for idx, step in enumerate(steps):
                if "mark" in step:
                    _pump()
                    _log("mark", step_index=idx, label=str(step["mark"]))
                elif "sleep_s" in step:
                    secs = float(step["sleep_s"])
                    _sleep_pumping(secs)
                    _log("sleep", step_index=idx, sleep_s=secs)
                elif "paste" in step:
                    mode = str(step.get("mode") or "bracketed")
                    raw_text = str(step["paste"])
                    if mode == "bracketed":
                        payload = f"\x1b[200~{raw_text}\x1b[201~".replace("\r\n", "\n")
                    else:
                        payload = raw_text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
                    send_res = conn.send_input(1, payload)
                    _log("paste", step_index=idx, mode=mode, bytes=len(payload.encode("utf-8")), ok=send_res.get("success"))
                    if "enter_after_ms" in step:
                        delay_ms = float(step["enter_after_ms"])
                        _sleep_pumping(delay_ms / 1000.0)
                        enter_res = conn.send_input(1, "\r")
                        _log("enter", step_index=idx, after_ms=delay_ms, ok=enter_res.get("success"))
                elif "enter_after_ms" in step:
                    delay_ms = float(step["enter_after_ms"])
                    _sleep_pumping(delay_ms / 1000.0)
                    enter_res = conn.send_input(1, "\r")
                    _log("enter", step_index=idx, after_ms=delay_ms, ok=enter_res.get("success"))
                elif "type" in step:
                    text = str(step["type"])
                    send_res = conn.send_input(1, text)
                    _log("type", step_index=idx, bytes=len(text.encode("utf-8")), ok=send_res.get("success"))
                elif "wait_event" in step:
                    target = str(step["wait_event"])
                    timeout_s = float(step.get("timeout_s", 120.0))
                    deadline = time.monotonic() + timeout_s
                    matched_record: Optional[Dict[str, Any]] = None
                    while time.monotonic() < deadline and matched_record is None:
                        _pump()
                        if ev_path.exists():
                            with ev_path.open("rb") as efh:
                                efh.seek(events_offset)
                                chunk = efh.read()
                                if chunk:
                                    last_nl = chunk.rfind(b"\n")
                                    if last_nl != -1:
                                        complete = chunk[: last_nl + 1]
                                        events_offset += len(complete)
                                        for line in complete.decode("utf-8", errors="replace").splitlines():
                                            if not line.strip():
                                                continue
                                            try:
                                                rec = json.loads(line)
                                            except ValueError:
                                                continue
                                            if _event_matches(target, rec):
                                                matched_record = rec
                                                break
                        if matched_record is None:
                            time.sleep(0.05)
                    if matched_record is not None:
                        _log("wait_event_matched", step_index=idx, target=target, matched=matched_record)
                    else:
                        _log("wait_event_timeout", step_index=idx, target=target, timeout_s=timeout_s)
                else:
                    _log("unknown_step", step_index=idx, step=dict(step))

            # Drenar salida restante antes de cerrar.
            drain_deadline = time.monotonic() + max(0.0, exit_grace_s)
            while time.monotonic() < drain_deadline:
                alive = _pump()
                if not alive:
                    break
                time.sleep(0.05)
            _pump()
    finally:
        try:
            conn.close_session(1)
        except Exception:
            pass
        if owns_connector and hasattr(conn, "close"):
            try:
                conn.close()
            except Exception:
                pass
        _log("closed")

    return {
        "success": True,
        "screen_raw": str(screen_path),
        "timeline": str(timeline_path),
        "entries": timeline_entries,
    }


def cmd_pty(args: argparse.Namespace) -> int:
    steps = json.loads(Path(args.steps).read_text(encoding="utf-8"))
    cmd_override = json.loads(args.command_json) if args.command_json else None
    result = run_pty_probe(
        cli=args.cli,
        cwd=Path(args.cwd),
        steps=steps,
        out_dir=Path(args.out),
        hub_url=args.hub_url,
        token=args.token,
        events_file=Path(args.events_file) if args.events_file else None,
        command_override=cmd_override,
    )
    print(json.dumps({"success": result["success"], "screen_raw": result.get("screen_raw"), "timeline": result.get("timeline")}, indent=2))
    return 0 if result["success"] else 1


# ---------------------------------------------------------------------------
# 4. timeline
# ---------------------------------------------------------------------------


def query_audit_timeline(db_path: Path, msg_id: str) -> List[Dict[str, Any]]:
    """Lee eventos de `relay_audit` para `msg_id` con milisegundos relativos."""
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT seq, ts_ms, event, msg_id, from_principal, to_principal, decision, reason_code, actor "
            "FROM relay_audit WHERE msg_id = ? ORDER BY seq ASC;",
            (msg_id,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []
    t0 = int(rows[0]["ts_ms"])
    out: List[Dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        d["rel_ms"] = int(d["ts_ms"]) - t0
        out.append(d)
    return out


def cmd_timeline(args: argparse.Namespace) -> int:
    rows = query_audit_timeline(Path(args.db), args.msg)
    if not rows:
        print(f"Sin eventos en relay_audit para msg_id={args.msg!r}")
        return 1
    for r in rows:
        print(
            f"+{r['rel_ms']} ms | seq={r['seq']} | event={r['event']} | "
            f"actor={r['actor']} | decision={r['decision']} | reason={r['reason_code']}"
        )
    return 0


# ---------------------------------------------------------------------------
# 5. summarize
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                items.append(json.loads(line))
            except ValueError:
                continue
    return items


def _infer_normalized_events(events: Sequence[Mapping[str, Any]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for ev in events:
        raw_ev = str(ev.get("event") or "")
        ptype = str(ev.get("payload_type") or "")
        key = f"{raw_ev}.{ptype}" if (raw_ev in {"codex.notify", "opencode.event"} and ptype) else raw_ev
        if not key:
            continue
        for norm, aliases in _EVENT_ALIASES.items():
            if raw_ev in aliases or (ptype and ptype in aliases):
                mapping[key] = norm
                break
        else:
            if ptype.startswith("permission."):
                mapping[key] = "permission_requested"
    return mapping


def summarize_probe(
    *,
    out_dir: Path,
    cli: str,
    os_name: str,
    cli_version: str,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    """Resume los artefactos de `--out` en una tabla Markdown y bloques JSON de catálogo."""
    out_dir = Path(out_dir)
    events = _read_jsonl(out_dir / "events.jsonl")
    timeline = _read_jsonl(out_dir / "timeline.jsonl")
    today_str = today or datetime.date.today().isoformat()

    event_counts: Dict[str, int] = {}
    session_fields: set[str] = set()
    for ev in events:
        name = str(ev.get("event") or "")
        ptype = str(ev.get("payload_type") or "")
        label = f"{name} ({ptype})" if ptype else name
        event_counts[label] = event_counts.get(label, 0) + 1
        for k in (ev.get("session_id_candidates") or {}).keys():
            session_fields.add(str(k).split(".")[-1])

    normalized_map = _infer_normalized_events(events)
    hook_install_by_cli = {
        "claude": "claude_settings",
        "antigravity": "agy_settings",
        "codex": "codex_notify",
        "opencode": "opencode_plugin",
    }
    has_events = len(events) > 0
    has_turn_ended = "turn_ended" in normalized_map.values()
    has_turn_started = "turn_started" in normalized_map.values()
    session_id_field = sorted(session_fields)[0] if session_fields else ""

    catalog_blocks = {
        "activity": {
            "status": "verified" if (has_events and has_turn_ended) else "unverified",
            "platforms": [os_name] if (has_events and has_turn_ended) else [],
            "hook_install": hook_install_by_cli.get(cli, "none"),
            "events": normalized_map,
            "session_id_field": session_id_field,
            "verified_with_cli_version": cli_version if (has_events and has_turn_ended) else "",
            "verified_on": today_str if (has_events and has_turn_ended) else "",
            "note": f"Generado por cli_signal_probe summarize ({len(events)} eventos observados).",
        },
        "delivery": {
            "status": "unverified",
            "platforms": [],
            "paste_mode": "bracketed",
            "submit_delay_ms": 300,
            "max_inject_bytes": 2048,
            "confirm_event": "turn_started" if has_turn_started else "",
            "verified_with_cli_version": "",
            "verified_on": "",
            "note": "Completar tras verificar M6/M7/M8/M13 en 56_MEDICION_DE_CLIS.md.",
        },
        "role_launch": {
            "status": "unverified",
            "platforms": [],
            "system_prompt_file_argv": [],
            "readonly_argv": [],
            "verified_with_cli_version": "",
            "verified_on": "",
            "note": "Completar tras verificar M10.",
        },
        "session": {
            "status": "unverified",
            "platforms": [],
            "assign_argv": [],
            "resume_by_id_argv": [],
            "verified_with_cli_version": "",
            "verified_on": "",
            "note": "Completar tras verificar M9.",
        },
    }

    events_summary = ", ".join(f"{k}: {v}" for k, v in sorted(event_counts.items())) or "ninguno"
    session_summary = ", ".join(sorted(session_fields)) or "ninguno"
    md_lines = [
        f"### Resumen de sonda · `{cli}` ({cli_version}) en `{os_name}`",
        "",
        "| Métrica | Valor observado |",
        "|---|---|",
        f"| CLI / versión | `{cli}` `{cli_version}` |",
        f"| Sistema | `{os_name}` |",
        f"| Eventos recibidos (`events.jsonl`) | {len(events)} ({events_summary}) |",
        f"| Pasos PTY registrados (`timeline.jsonl`) | {len(timeline)} |",
        f"| Candidatos `session_id` | `{session_summary}` |",
        "",
        "```json",
        json.dumps(catalog_blocks, indent=2, ensure_ascii=False),
        "```",
    ]
    return {"markdown": "\n".join(md_lines), "blocks": catalog_blocks, "events_count": len(events), "timeline_count": len(timeline)}


def cmd_summarize(args: argparse.Namespace) -> int:
    summary = summarize_probe(
        out_dir=Path(args.out),
        cli=args.cli,
        os_name=args.os,
        cli_version=args.cli_version,
    )
    print(summary["markdown"])
    return 0


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sonda de señales de CLIs para MCP2P (WP-5a / WP-5b)")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_listen = sub.add_parser("listen", help="Servidor HTTP local para recibir POST /hooks/v1/<evento>")
    p_listen.add_argument("--out", required=True, help="Directorio de salida")
    p_listen.add_argument("--port", type=int, default=0, help="Puerto TCP en 127.0.0.1 (por defecto 0)")
    p_listen.add_argument("--keep-payload", action="store_true", help="Guardar payload completo en events.jsonl")
    p_listen.add_argument("--token", default=None, help="Token opcional (por defecto probe-<aleatorio>)")
    p_listen.set_defaults(func=cmd_listen)

    p_prep = sub.add_parser("prepare", help="Genera configs de sesion, run.sh/run.ps1 y hashes globales")
    p_prep.add_argument("--cli", required=True, choices=_SUPPORTED_CLIS)
    p_prep.add_argument("--out", required=True)
    p_prep.add_argument("--hub-url", required=True)
    p_prep.add_argument("--token", required=True)
    p_prep.add_argument("--frozen-exe", default=None, help="Opcional: ejecutable congelado Mcp2p para --hook-post")
    p_prep.set_defaults(func=cmd_prepare)

    p_chk = sub.add_parser("check-globals", help="Verifica que las configs globales no cambiaron")
    p_chk.add_argument("--out", required=True)
    p_chk.set_defaults(func=cmd_check_globals)

    p_pty = sub.add_parser("pty", help="Ejecuta pasos de prueba usando el conector PTY/ConPTY de MCP2P")
    p_pty.add_argument("--cli", required=True)
    p_pty.add_argument("--cwd", required=True)
    p_pty.add_argument("--steps", required=True, help="Archivo JSON con la lista de pasos")
    p_pty.add_argument("--out", required=True)
    p_pty.add_argument("--hub-url", default=None)
    p_pty.add_argument("--token", default=None)
    p_pty.add_argument("--events-file", default=None)
    p_pty.add_argument("--command-json", default=None, help="Opcional: lista JSON de comando para pruebas")
    p_pty.set_defaults(func=cmd_pty)

    p_tl = sub.add_parser("timeline", help="Muestra eventos de relay_audit de un mensaje en ms relativos")
    p_tl.add_argument("--db", required=True)
    p_tl.add_argument("--msg", required=True)
    p_tl.set_defaults(func=cmd_timeline)

    p_sum = sub.add_parser("summarize", help="Genera tabla Markdown y bloques JSON para el catalogo")
    p_sum.add_argument("--out", required=True)
    p_sum.add_argument("--cli", required=True)
    p_sum.add_argument("--os", required=True, choices=("linux", "win32"))
    p_sum.add_argument("--cli-version", required=True)
    p_sum.set_defaults(func=cmd_summarize)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
