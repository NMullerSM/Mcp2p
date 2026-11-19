"""Pruebas de `scripts/cli_signal_probe.py` (WP-5a)."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

import pytest

from scripts import cli_signal_probe as probe
from src.adapters.terminal.cli_catalog import _parse_entry
from src.adapters.terminal.pty_host import PosixPtyHostConnector
from src.storage.db import DatabaseManager

pty_required = pytest.mark.skipif(
    sys.platform == "win32" or not PosixPtyHostConnector.is_available(),
    reason="Requiere PTY POSIX disponible para probar el conector de terminales",
)


def _post_hook(url: str, event: str, token: str, payload: bytes) -> int:
    base = url.rsplit("/mcp", 1)[0]
    req = urllib.request.Request(
        f"{base}/hooks/v1/{event}",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=3.0) as resp:
        return int(resp.status)


def test_listen_records_events_jsonl(tmp_path: Path) -> None:
    out_dir = tmp_path / "listen"
    server, info, thread = probe.start_listener(out_dir, port=0)
    try:
        assert info["token"].startswith("probe-")
        assert info["url"].startswith("http://127.0.0.1:")

        status = _post_hook(
            info["url"],
            "claude.Stop",
            info["token"],
            json.dumps({"session_id": "sess-42", "prompt": "SECRETO_NO_GUARDAR", "extra": 1}).encode("utf-8"),
        )
        assert status == 200

        # Token erroneo -> 401
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _post_hook(info["url"], "claude.Stop", "wrong-token", b"{}")
        assert exc_info.value.code == 401

        # Payload > 64 KiB -> 413
        with pytest.raises(urllib.error.HTTPError) as exc_big:
            _post_hook(info["url"], "claude.Stop", info["token"], b"x" * (65 * 1024))
        assert exc_big.value.code == 413

        events_file = out_dir / "events.jsonl"
        lines = events_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["event"] == "claude.Stop"
        assert rec["payload_keys"] == ["extra", "prompt", "session_id"]
        assert rec["session_id_candidates"] == {"session_id": "sess-42"}
        assert rec["bytes"] > 0
        assert isinstance(rec["t_ms"], int)
        assert "payload" not in rec
        assert "SECRETO_NO_GUARDAR" not in lines[0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_prepare_writes_run_scripts_and_hashes_global_configs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    claude_global = fake_home / ".claude" / "settings.json"
    claude_global.parent.mkdir(parents=True)
    claude_global.write_text('{"theme":"dark"}\n', encoding="utf-8")

    for cli_id in ("claude", "codex", "opencode", "antigravity"):
        out_dir = tmp_path / f"prep_{cli_id}"
        manifest = probe.prepare_cli_probe(
            cli=cli_id,
            out_dir=out_dir,
            hub_url="http://127.0.0.1:9999/mcp",
            token="probe-tok-123",
        )
        run_sh = Path(manifest["run_sh"])
        run_ps1 = Path(manifest["run_ps1"])
        assert run_sh.exists()
        assert run_ps1.exists()
        sh_text = run_sh.read_text(encoding="utf-8")
        ps1_text = run_ps1.read_text(encoding="utf-8")
        assert "MCP2P_HUB_URL" in sh_text and "MCP2P_SESSION_TOKEN" in sh_text
        assert "MCP2P_HUB_URL" in ps1_text and "MCP2P_SESSION_TOKEN" in ps1_text

        check_ok = probe.check_global_configs(out_dir)
        assert check_ok["ok"] is True
        assert check_ok["changed"] == []
        assert check_ok["files"]["claude"]["before"] is not None

    # OpenCode debe incluir plugin + config combinada
    oc_cfg = json.loads((tmp_path / "prep_opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert "plugin" in oc_cfg and str(tmp_path / "prep_opencode" / "mcp2p_plugin.js") in oc_cfg["plugin"]

    # Mutar config global -> check-globals lo detecta
    claude_global.write_text('{"theme":"tampered"}\n', encoding="utf-8")
    check_changed = probe.check_global_configs(tmp_path / "prep_claude")
    assert check_changed["ok"] is False
    assert check_changed["changed"] == ["claude"]


def _write_fake_cli(script_path: Path) -> None:
    script_path.write_text(
        "import os, sys, time\n"
        "sys.stdout.write('\\x1b[?2004hREADY\\n')\n"
        "sys.stdout.flush()\n"
        "if os.name == 'posix':\n"
        "    import termios, tty\n"
        "    fd = sys.stdin.fileno()\n"
        "    old = termios.tcgetattr(fd)\n"
        "    tty.setraw(fd)\n"
        "else:\n"
        "    old = None\n"
        "try:\n"
        "    buf = bytearray()\n"
        "    deadline = time.monotonic() + 5.0\n"
        "    while time.monotonic() < deadline:\n"
        "        ch = os.read(sys.stdin.fileno(), 256)\n"
        "        if not ch:\n"
        "            break\n"
        "        buf.extend(ch)\n"
        "        if b'DONE' in buf:\n"
        "            break\n"
        "    sys.stdout.write('HEX:' + bytes(buf).hex() + '\\n')\n"
        "    sys.stdout.flush()\n"
        "finally:\n"
        "    if old is not None:\n"
        "        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)\n",
        encoding="utf-8",
    )


@pty_required
def test_pty_steps_with_fake_cli(tmp_path: Path) -> None:
    fake_cli = tmp_path / "fake_cli.py"
    _write_fake_cli(fake_cli)
    out_dir = tmp_path / "pty_out"
    out_dir.mkdir()
    events_file = out_dir / "events.jsonl"
    events_file.write_text("", encoding="utf-8")

    # Simular un evento llegado tras 150 ms para probar wait_event.
    def _emit_later() -> None:
        time.sleep(0.15)
        with events_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t_ms": int(time.time() * 1000), "event": "claude.Stop", "payload_keys": [], "session_id_candidates": {}, "bytes": 2}) + "\n")

    import threading

    t = threading.Thread(target=_emit_later, daemon=True)
    t.start()

    steps = [
        {"mark": "inicio"},
        {"sleep_s": 0.15},
        {"paste": "hola\nmundo", "mode": "bracketed", "enter_after_ms": 50},
        {"type": "DONE"},
        {"wait_event": "turn_ended", "timeout_s": 5},
    ]
    res = probe.run_pty_probe(
        cli="custom_shell",
        cwd=tmp_path,
        steps=steps,
        out_dir=out_dir,
        command_override=[sys.executable, str(fake_cli)],
        exit_grace_s=0.6,
    )
    t.join(timeout=2.0)

    assert res["success"] is True
    raw_bytes = (out_dir / "screen.raw").read_bytes()
    expected_paste_hex = b"\x1b[200~hola\nmundo\x1b[201~\rDONE".hex().encode("ascii")
    assert b"READY" in raw_bytes
    assert expected_paste_hex in raw_bytes

    timeline_rows = [json.loads(line) for line in (out_dir / "timeline.jsonl").read_text(encoding="utf-8").splitlines()]
    kinds = [row["kind"] for row in timeline_rows]
    assert "mark" in kinds
    assert "paste" in kinds
    assert "enter" in kinds
    assert "type" in kinds
    assert "wait_event_matched" in kinds


@pytest.mark.conpty
@pytest.mark.skipif(sys.platform != "win32", reason="ConPTY solo existe en Windows")
def test_pty_steps_with_fake_cli_conpty(tmp_path: Path) -> None:
    fake_cli = tmp_path / "fake_cli_win.py"
    fake_cli.write_text(
        "import msvcrt, sys, time\n"
        "sys.stdout.write('\\x1b[?2004hREADY\\r\\n')\n"
        "sys.stdout.flush()\n"
        "buf = bytearray()\n"
        "deadline = time.monotonic() + 10.0\n"
        "while time.monotonic() < deadline:\n"
        "    if msvcrt.kbhit():\n"
        "        buf.append(msvcrt.getch()[0])\n"
        "        if b'DONE' in buf:\n"
        "            break\n"
        "    else:\n"
        "        time.sleep(0.02)\n"
        "sys.stdout.write('HEX:' + bytes(buf).hex() + '\\r\\n')\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "pty_win"
    steps = [
        {"sleep_s": 0.3},
        {"paste": "hola\nmundo", "mode": "bracketed", "enter_after_ms": 100},
        {"type": "DONE"},
    ]
    res = probe.run_pty_probe(
        cli="custom_shell",
        cwd=tmp_path,
        steps=steps,
        out_dir=out_dir,
        command_override=[sys.executable, str(fake_cli)],
        exit_grace_s=1.0,
    )
    assert res["success"] is True
    raw_bytes = (out_dir / "screen.raw").read_bytes()
    assert b"HEX:" in raw_bytes


def test_timeline_reads_relay_audit_with_relative_ms(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    mgr = DatabaseManager(str(db_path))
    try:
        with mgr.get_connection() as conn:
            conn.execute(
                "INSERT INTO relay_audit (seq, ts_ms, event, msg_id, actor, decision, reason_code, prev_hash, row_hash) "
                "VALUES (1, 1000, 'queued', 'msg-9', 'hub', 'deliver', 'OK', '0', 'h1'), "
                "(2, 1250, 'delivered', 'msg-9', 'pane:n2', 'ok', 'OK', 'h1', 'h2');"
            )
            conn.commit()
    finally:
        mgr.close()

    rows = probe.query_audit_timeline(db_path, "msg-9")
    assert [r["rel_ms"] for r in rows] == [0, 250]
    assert [r["event"] for r in rows] == ["queued", "delivered"]


def test_summarize_outputs_markdown_and_json(tmp_path: Path) -> None:
    out_dir = tmp_path / "sum"
    out_dir.mkdir()
    events = [
        {
            "t_ms": 1000,
            "event": "claude.SessionStart",
            "payload_keys": ["session_id"],
            "session_id_candidates": {"session_id": "s-1"},
            "bytes": 24,
        },
        {
            "t_ms": 1500,
            "event": "claude.UserPromptSubmit",
            "payload_keys": ["session_id"],
            "session_id_candidates": {"session_id": "s-1"},
            "bytes": 24,
        },
        {
            "t_ms": 2200,
            "event": "claude.Stop",
            "payload_keys": ["session_id"],
            "session_id_candidates": {"session_id": "s-1"},
            "bytes": 24,
        },
    ]
    (out_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    (out_dir / "timeline.jsonl").write_text(json.dumps({"t_ms": 1000, "rel_ms": 0, "kind": "spawned"}) + "\n", encoding="utf-8")

    summary = probe.summarize_probe(
        out_dir=out_dir,
        cli="claude",
        os_name="linux",
        cli_version="2.1.325",
        today="2026-11-18",
    )
    md = summary["markdown"]
    assert "| Métrica | Valor observado |" in md
    assert "```json" in md

    blocks = summary["blocks"]
    assert blocks["activity"]["status"] == "verified"
    assert blocks["activity"]["platforms"] == ["linux"]
    assert blocks["activity"]["session_id_field"] == "session_id"
    assert blocks["activity"]["events"] == {
        "claude.SessionStart": "session_started",
        "claude.UserPromptSubmit": "turn_started",
        "claude.Stop": "turn_ended",
    }

    # Validar que los bloques generados cumplen estrictamente el esquema del catalogo.
    raw_entry = {
        "id": "claude",
        "display_name": "Claude Code",
        "icon": "claude",
        "kind": "agent_cli",
        "binary": "claude",
        "candidate_paths": {"linux": ["/usr/bin/claude"]},
        "launch_flags": [],
        "mcp": {"method": "none", "format": "none", "verified": False},
        "resume": {"argv": [], "verified": False},
        "install": {},
        **blocks,
    }
    spec = _parse_entry(raw_entry, source="repo")
    assert spec.capability_verified("activity", "linux") is True


def test_cli_subcommands_dispatch_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    prep_dir = tmp_path / "cli_prep"
    assert (
        probe.main(
            [
                "prepare",
                "--cli",
                "claude",
                "--out",
                str(prep_dir),
                "--hub-url",
                "http://127.0.0.1:8765/mcp",
                "--token",
                "probe-cli",
            ]
        )
        == 0
    )
    assert probe.main(["check-globals", "--out", str(prep_dir)]) == 0
    assert probe.main(["summarize", "--out", str(prep_dir), "--cli", "claude", "--os", "linux", "--cli-version", "2.1.325"]) == 0
    out_text = capsys.readouterr().out
    assert "sin cambios" in out_text
    assert '"status": "unverified"' in out_text

    db_path = tmp_path / "tl.db"
    mgr = DatabaseManager(str(db_path))
    try:
        with mgr.get_connection() as conn:
            conn.execute(
                "INSERT INTO relay_audit (seq, ts_ms, event, msg_id, actor, decision, reason_code, prev_hash, row_hash) "
                "VALUES (1, 5000, 'queued', 'm-1', 'hub', 'deliver', 'OK', '0', 'h1');"
            )
            conn.commit()
    finally:
        mgr.close()
    assert probe.main(["timeline", "--db", str(db_path), "--msg", "m-1"]) == 0
    assert "+0 ms" in capsys.readouterr().out
