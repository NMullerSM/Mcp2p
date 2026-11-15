"""
T5 (plan 2026-11-15, seccion 8.3): exportar/importar proyectos.
Cubre copia consistente, exclusion de secretos por defecto, reescritura de rutas (incluidas rutas de
Windows), zip-slip y demas zips hostiles, y la negativa a sobrescribir sin --merge/--force.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import stat
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

from src.core.services.project_portability_service import (
    PortabilityError,
    PortabilityLimits,
    ProjectPortabilityService,
    relative_under_root,
    rewrite_path,
)
from src.storage.db import DatabaseManager

PAT = "ghp_SUPERSECRET_TOKEN_1234567890"
URL_TOKEN = "ghs_URLEMBEDDEDSECRET"
svc = ProjectPortabilityService()


def _cards(db_file: Path) -> List[Tuple[str, str]]:
    conn = sqlite3.connect(str(db_file))
    try:
        return [tuple(r) for r in conn.execute("SELECT project_uuid, card_id FROM kanban_cards ORDER BY 1, 2;")]
    finally:
        conn.close()


def make_source(tmp_path: Path, name: str = "origen") -> Dict[str, Any]:
    root = tmp_path / name
    root.mkdir()
    db = DatabaseManager(str(root / "mcp2p_local.db"))
    p1 = db.create_project("Proyecto Uno", init_git=False)
    p2 = db.create_project("Proyecto Dos", init_git=False)
    for seed in db.list_projects():  # el gestor siembra un proyecto de arranque; los tests usan solo p1 y p2
        if seed["project_uuid"] not in (p1["project_uuid"], p2["project_uuid"]):
            db.delete_project(seed["project_uuid"])
    for p in (p1, p2):
        with db.get_connection() as conn:
            with conn:
                conn.execute(
                    "INSERT INTO kanban_cards (card_id, project_uuid, title) VALUES (?, ?, ?);",
                    (f"CARD-{p['name'][-3:]}", p["project_uuid"], f"Tarjeta de {p['name']}"),
                )
    code1 = Path(p1["code_repo_path"])
    (code1 / "README.md").write_text("hola")
    run = code1 / "run.sh"
    run.write_text("#!/bin/sh\necho hi\n")
    run.chmod(0o755)
    (code1 / ".env").write_text("API_KEY=nope")
    (code1 / "id_rsa").write_text("PRIVATE")
    (code1 / "node_modules").mkdir()
    (code1 / "node_modules" / "x.js").write_text("x")
    (code1 / "link").symlink_to("/etc/passwd")
    (code1 / ".git").mkdir()
    (code1 / ".git" / "config").write_text(f"[remote \"origin\"]\n\turl = https://user:{URL_TOKEN}@github.com/o/r.git\n")
    (Path(p1["gov_repo_path"]) / "adr.md").write_text("decision")
    db.set_setting("github_pat_token", PAT)
    db.set_setting("github_pat_username", "octocat")
    db.set_setting("ui_theme", "dark")
    db.set_extension_setting("terminal_cli", "binary_path.claude", "/home/origen/bin/claude")
    key_dir = tmp_path / f"{name}_home" / ".mcp2p"
    key_dir.mkdir(parents=True)
    key = key_dir / "node_identity.key"
    key.write_text("-----BEGIN PRIVATE KEY-----\nSECRETKEYMATERIAL\n")
    return {"root": root, "db": db, "db_file": root / "mcp2p_local.db", "ws": root / "workspace",
            "p1": p1, "p2": p2, "key": key}


def tamper(
    src: Path,
    dst: Path,
    *,
    add: List[Tuple[str, bytes, Optional[int]]] = (),  # type: ignore[assignment]
    register: bool = True,
    manifest_edit: Optional[Callable[[Dict[str, Any]], None]] = None,
    data_edit: Optional[Callable[[Dict[str, bytes]], None]] = None,
    drop: Tuple[str, ...] = (),
    compress: int = zipfile.ZIP_DEFLATED,
) -> Path:
    with zipfile.ZipFile(src) as zin:
        items = {i.filename: zin.read(i) for i in zin.infolist()}
    manifest = json.loads(items.pop("manifest.json"))
    if data_edit:
        data_edit(items)
    extra: List[Tuple[str, bytes, Optional[int]]] = list(add)
    for name in drop:
        items.pop(name, None)
        manifest["files"] = [f for f in manifest["files"] if f["path"] != name]
    if register:
        pid = manifest["projects"][0]["project_uuid"]
        for name, data, _ in extra:
            manifest["files"].append(
                {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                 "kind": "workspace", "project_uuid": pid}
            )
    # tras data_edit, recalcular tamano/sha de lo existente salvo que el test pida lo contrario
    for f in manifest["files"]:
        if f["path"] in items and not getattr(data_edit, "keep_manifest", False):
            f["size"] = len(items[f["path"]])
            f["sha256"] = hashlib.sha256(items[f["path"]]).hexdigest()
    if manifest_edit:
        manifest_edit(manifest)
    with zipfile.ZipFile(dst, "w", compress) as zout:
        zout.writestr("manifest.json", json.dumps(manifest))
        for name, data in items.items():
            zout.writestr(name, data)
        for name, data, attr in extra:
            info = zipfile.ZipInfo(name)
            info.compress_type = compress
            if attr is not None:
                info.external_attr = attr << 16
            zout.writestr(info, data)
    return dst


def export_full(src: Dict[str, Any], out: Path, **kw: Any) -> Dict[str, Any]:
    return svc.export_projects(src["db_file"], out, workspace_root=src["ws"], include_repos=True, **kw)


def assert_untouched(dest_root: Path, db_file: Path, outside: Path) -> None:
    assert not db_file.exists()
    assert not outside.exists()
    leftovers = [p for p in dest_root.rglob("*")] if dest_root.exists() else []
    assert leftovers == [], leftovers


# ---------------------------------------------------------------------------
# Rutas entre sistemas operativos
# ---------------------------------------------------------------------------


def test_path_rewrite_posix_and_windows_sources(tmp_path: Path) -> None:
    dest = tmp_path / "nuevo"
    assert rewrite_path("/home/ana/ws/proj/app", "/home/ana/ws", dest) == str(dest / "proj" / "app")
    assert rewrite_path("C:\\Users\\Ana\\mcp2p\\workspace\\proj\\app", "C:\\Users\\Ana\\mcp2p\\workspace", dest) == str(dest / "proj" / "app")
    assert rewrite_path("c:\\users\\ana\\mcp2p\\workspace\\Proj", "C:\\Users\\Ana\\mcp2p\\workspace", dest) == str(dest / "Proj")
    assert rewrite_path("/home/ana/ws", "/home/ana/ws", dest) == str(dest)
    # Fuera de la raiz o de otra familia: no se reubica
    assert rewrite_path("/home/otro/proj", "/home/ana/ws", dest) is None
    assert rewrite_path("C:\\Users\\Ana\\x", "/home/ana/ws", dest) is None
    assert rewrite_path("/home/ana/ws/proj", "C:\\Users\\Ana\\ws", dest) is None
    assert rewrite_path("/home/ana/ws/../etc", "/home/ana/ws", dest) is None
    assert rewrite_path("/home/ana/ws/p\nq", "/home/ana/ws", dest) is None
    assert relative_under_root("\\\\server\\share\\ws\\p", "\\\\server\\share\\ws") == ("p",)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_excludes_secrets_by_default_and_leaves_no_trace(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    out = tmp_path / "paquete.zip"
    res = export_full(src, out, identity_key_paths=[src["key"]])
    assert res["success"] is True and res["includes_secrets"] is False and "secrets_warning" not in res

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        raw_db = zf.read("db/mcp2p_local.db")
        manifest = json.loads(zf.read("manifest.json"))
        blob = b"".join(zf.read(n) for n in names)
    assert not any(n.startswith("secrets/") for n in names)
    assert PAT.encode() not in blob and URL_TOKEN.encode() not in blob and b"SECRETKEYMATERIAL" not in blob
    assert PAT.encode() not in out.read_bytes()
    assert not any(n.endswith("/.env") or n.endswith("/id_rsa") or "/node_modules/" in n or n.endswith("/link") for n in names)
    assert "workspace/" + src["p1"]["code_repo_path"].split("/workspace/")[1] + "/run.sh" in names

    assert manifest["format"] == "mcp2p-portable-project" and manifest["format_version"] == 1
    assert manifest["source_workspace_root"] == str(src["ws"])
    assert manifest["includes_secrets"] is False and manifest["source_platform"]
    assert {p["project_uuid"] for p in manifest["projects"]} == {src["p1"]["project_uuid"], src["p2"]["project_uuid"]}

    # La base exportada: consistente, sin PAT ni rutas de CLI, con ajustes no secretos
    dbp = tmp_path / "check.db"
    dbp.write_bytes(raw_db)
    conn = sqlite3.connect(str(dbp))
    try:
        assert conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        keys = {r[0] for r in conn.execute("SELECT key FROM app_settings;")}
        assert keys == {"ui_theme"}
        assert conn.execute("SELECT COUNT(*) FROM extension_settings WHERE namespace='terminal_cli';").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM projects;").fetchone()[0] == 2
    finally:
        conn.close()
    # El origen NO se modifico
    assert src["db"].get_setting("github_pat_token") == PAT


def test_export_with_include_secrets_carries_pat_and_identity_with_warning(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    out = tmp_path / "con_secretos.zip"
    res = export_full(src, out, include_secrets=True, identity_key_paths=[src["key"]])
    assert res["includes_secrets"] is True and "secretos" in res["secrets_warning"]
    with zipfile.ZipFile(out) as zf:
        assert "secrets/node_identity.key" in zf.namelist()
        assert b"SECRETKEYMATERIAL" in zf.read("secrets/node_identity.key")
        assert any(n.endswith("/.env") for n in zf.namelist())
        assert json.loads(zf.read("manifest.json"))["includes_secrets"] is True
        dbp = tmp_path / "c.db"
        dbp.write_bytes(zf.read("db/mcp2p_local.db"))
    conn = sqlite3.connect(str(dbp))
    try:
        assert conn.execute("SELECT value FROM app_settings WHERE key='github_pat_token';").fetchone()[0] == PAT
    finally:
        conn.close()


def test_export_subset_removes_other_projects_without_residue(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    out = tmp_path / "uno.zip"
    res = svc.export_projects(src["db_file"], out, workspace_root=src["ws"], include_repos=True,
                              project_uuids=[src["p1"]["project_uuid"]])
    assert res["projects"] == [src["p1"]["project_uuid"]]
    with zipfile.ZipFile(out) as zf:
        blob = b"".join(zf.read(n) for n in zf.namelist())
        assert not any("proyecto_dos" in n for n in zf.namelist())
        dbp = tmp_path / "u.db"
        dbp.write_bytes(zf.read("db/mcp2p_local.db"))
    assert b"Tarjeta de Proyecto Dos" not in blob and b"Proyecto Dos" not in blob
    assert _cards(dbp) == [(src["p1"]["project_uuid"], "CARD-Uno")]
    with pytest.raises(PortabilityError) as exc:
        svc.export_projects(src["db_file"], tmp_path / "x.zip", project_uuids=["no_existe"])
    assert exc.value.code == "PROJECT_NOT_FOUND"


def test_export_refuses_missing_db_and_existing_archive(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    with pytest.raises(PortabilityError) as exc:
        svc.export_projects(tmp_path / "nada.db", tmp_path / "a.zip")
    assert exc.value.code == "DB_NOT_FOUND"
    out = tmp_path / "a.zip"
    svc.export_projects(src["db_file"], out)
    with pytest.raises(PortabilityError) as exc2:
        svc.export_projects(src["db_file"], out)
    assert exc2.value.code == "ARCHIVE_EXISTS"
    assert svc.export_projects(src["db_file"], out, overwrite=True)["success"] is True


def test_export_is_a_consistent_snapshot_under_concurrent_writes(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    stop = threading.Event()
    p1 = src["p1"]["project_uuid"]

    def writer() -> None:
        i = 0
        while not stop.is_set():
            i += 1
            try:
                with src["db"].get_connection() as conn:
                    with conn:
                        conn.execute(
                            "INSERT OR IGNORE INTO kanban_cards (card_id, project_uuid, title) VALUES (?, ?, 't');",
                            (f"W-{i:05d}", p1),
                        )
            except Exception:
                pass

    th = threading.Thread(target=writer, daemon=True)
    th.start()
    try:
        time.sleep(0.05)
        out = tmp_path / "vivo.zip"
        svc.export_projects(src["db_file"], out)
    finally:
        stop.set()
        th.join(timeout=5)
    with zipfile.ZipFile(out) as zf:
        dbp = tmp_path / "v.db"
        dbp.write_bytes(zf.read("db/mcp2p_local.db"))
    conn = sqlite3.connect(str(dbp))
    try:
        assert conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check;").fetchall() == []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Import: ida y vuelta con reescritura de rutas
# ---------------------------------------------------------------------------


def test_roundtrip_rewrites_absolute_paths_and_extracts_repos(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    out = tmp_path / "p.zip"
    export_full(src, out)

    dest_ws = tmp_path / "otra maquina" / "ws"
    dest_db = tmp_path / "otra maquina" / "db" / "mcp2p_local.db"
    res = svc.import_projects(out, dest_db, dest_ws)
    assert res["success"] is True and res["db_mode"] == "created"
    assert set(res["projects_imported"]) == {src["p1"]["project_uuid"], src["p2"]["project_uuid"]}

    conn = sqlite3.connect(str(dest_db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM projects WHERE project_uuid = ?;", (src["p1"]["project_uuid"],)).fetchone()
        assert row["base_path"].startswith(str(dest_ws)) and str(src["ws"]) not in row["base_path"]
        assert row["code_repo_path"].startswith(str(dest_ws)) and row["gov_repo_path"].startswith(str(dest_ws))
        assert Path(row["code_repo_path"], "README.md").read_text() == "hola"
        assert Path(row["gov_repo_path"], "adr.md").read_text() == "decision"
        assert os.access(Path(row["code_repo_path"], "run.sh"), os.X_OK)
        assert not Path(row["code_repo_path"], ".env").exists()
        assert "github_pat_token" not in {r[0] for r in conn.execute("SELECT key FROM app_settings;")}
        assert conn.execute("SELECT COUNT(*) FROM kanban_cards;").fetchone()[0] == 2
        assert conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    finally:
        conn.close()
    # nada de temporales de importacion
    assert not [p for p in dest_ws.iterdir() if p.name.startswith(".mcp2p-import-")]
    # la base importada abre con el gestor normal
    assert len(DatabaseManager(str(dest_db)).list_projects()) == 2


def test_windows_origin_paths_are_rewritten_for_this_machine(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    out = tmp_path / "p.zip"
    export_full(src, out)
    win_root = "C:\\Users\\Ana\\mcp2p\\workspace"

    def to_windows(items: Dict[str, bytes]) -> None:
        dbp = tmp_path / "w.db"
        dbp.write_bytes(items["db/mcp2p_local.db"])
        conn = sqlite3.connect(str(dbp))
        try:
            with conn:
                for row in conn.execute("SELECT project_uuid, base_path, code_repo_path, gov_repo_path FROM projects;").fetchall():
                    rel = lambda p: p.split("/workspace/")[1].replace("/", "\\")
                    conn.execute(
                        "UPDATE projects SET base_path=?, code_repo_path=?, gov_repo_path=? WHERE project_uuid=?;",
                        (f"{win_root}\\{rel(row[1])}", f"{win_root}\\{rel(row[2])}", f"{win_root}\\{rel(row[3])}", row[0]),
                    )
        finally:
            conn.close()
        items["db/mcp2p_local.db"] = dbp.read_bytes()

    def edit(m: Dict[str, Any]) -> None:
        m["source_workspace_root"] = win_root
        m["source_platform"] = "win32"

    win_zip = tamper(out, tmp_path / "win.zip", data_edit=to_windows, manifest_edit=edit)
    dest_ws = tmp_path / "linux_ws"
    dest_db = tmp_path / "linux.db"
    res = svc.import_projects(win_zip, dest_db, dest_ws)
    conn = sqlite3.connect(str(dest_db))
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute("SELECT * FROM projects;"):
            for fld in ("base_path", "code_repo_path", "gov_repo_path"):
                assert row[fld].startswith(str(dest_ws)) and "\\" not in row[fld] and "C:" not in row[fld]
    finally:
        conn.close()
    assert any(c["old"].startswith("C:\\Users\\Ana") for c in res["rewritten_paths"])


# ---------------------------------------------------------------------------
# Import: zips hostiles
# ---------------------------------------------------------------------------

S_IFLNK = stat.S_IFLNK | 0o777


@pytest.mark.parametrize(
    "bad_name",
    [
        "../evil.txt",
        "workspace/../../evil.txt",
        "workspace/proj/../../../evil.txt",
        "/etc/cron.d/evil",
        "workspace//abs",
        "workspace\\..\\evil.txt",
        "..\\evil.txt",
        "C:\\Windows\\evil.txt",
        "C:/Windows/evil.txt",
        "workspace/ok\nname",
        "workspace/ok\x00name",
        "etc/passwd",
        "evil.txt",
        "db/other.db",
        "secrets/otro.txt",
        "workspace",
    ],
)
def test_zip_slip_and_foreign_names_are_rejected_without_side_effects(tmp_path: Path, bad_name: str) -> None:
    src = make_source(tmp_path)
    good = tmp_path / "good.zip"
    export_full(src, good)
    bad = tamper(good, tmp_path / "bad.zip", add=[(bad_name, b"pwned", None)])
    dest_ws = tmp_path / "dest" / "ws"
    dest_db = tmp_path / "dest" / "db.sqlite"
    with pytest.raises(PortabilityError) as exc:
        svc.import_projects(bad, dest_db, dest_ws)
    assert exc.value.code in {"ARCHIVE_UNSAFE", "ARCHIVE_INVALID"}
    assert_untouched(dest_ws, dest_db, tmp_path / "dest" / "evil.txt")
    assert not (tmp_path / "evil.txt").exists() and not Path("/etc/cron.d/evil").exists()


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_symlink_special_encrypted_and_duplicate_entries_are_rejected(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    good = tmp_path / "good.zip"
    export_full(src, good)
    dest_ws, dest_db = tmp_path / "d" / "ws", tmp_path / "d" / "db.sqlite"

    link = tamper(good, tmp_path / "link.zip", add=[("workspace/proj/link", b"/etc/passwd", S_IFLNK)])
    with pytest.raises(PortabilityError) as exc:
        svc.import_projects(link, dest_db, dest_ws)
    assert exc.value.code == "ARCHIVE_UNSAFE" and "simbolico" in exc.value.message
    for special in (stat.S_IFCHR | 0o666, stat.S_IFIFO | 0o666, stat.S_IFBLK | 0o666, stat.S_IFSOCK | 0o666):
        bad = tamper(good, tmp_path / "sp.zip", add=[("workspace/proj/dev", b"", special)])
        with pytest.raises(PortabilityError) as exc2:
            svc.import_projects(bad, dest_db, dest_ws)
        assert exc2.value.code == "ARCHIVE_UNSAFE"

    dup = tamper(good, tmp_path / "dup.zip", add=[("workspace/proj/a", b"1", None), ("workspace/proj/a", b"2", None)])
    with pytest.raises(PortabilityError) as exc3:
        svc.import_projects(dup, dest_db, dest_ws)
    assert exc3.value.code == "ARCHIVE_UNSAFE"
    case = tamper(good, tmp_path / "case.zip", add=[("workspace/proj/A", b"1", None), ("workspace/proj/a", b"2", None)])
    with pytest.raises(PortabilityError):
        svc.import_projects(case, dest_db, dest_ws)
    assert_untouched(dest_ws, dest_db, tmp_path / "d" / "x")


def test_manifest_and_integrity_attacks_are_rejected(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    good = tmp_path / "good.zip"
    export_full(src, good)
    dest_ws, dest_db = tmp_path / "d" / "ws", tmp_path / "d" / "db.sqlite"

    def expect(zip_path: Path, codes: Tuple[str, ...]) -> None:
        with pytest.raises(PortabilityError) as exc:
            svc.import_projects(zip_path, dest_db, dest_ws)
        assert exc.value.code in codes, exc.value.message
        assert_untouched(dest_ws, dest_db, tmp_path / "d" / "x")

    # version futura
    expect(tamper(good, tmp_path / "v.zip", manifest_edit=lambda m: m.update(format_version=2)), ("ARCHIVE_VERSION_UNSUPPORTED",))
    expect(tamper(good, tmp_path / "f.zip", manifest_edit=lambda m: m.update(format="otro")), ("ARCHIVE_INVALID",))
    # archivo no declarado en el manifiesto
    expect(tamper(good, tmp_path / "u.zip", add=[("workspace/proj/extra", b"x", None)],
                  register=False), ("ARCHIVE_INVALID",))
    # sha256 alterado
    def flip(items: Dict[str, bytes]) -> None:
        name = next(n for n in items if n.endswith("README.md"))
        items[name] = b"HOLA"
    flip.keep_manifest = True  # type: ignore[attr-defined]
    expect(tamper(good, tmp_path / "h.zip", data_edit=flip), ("ARCHIVE_INTEGRITY",))
    # tamano declarado menor que el real (zip bomb con cabecera mentirosa)
    def lie(m: Dict[str, Any]) -> None:
        for f in m["files"]:
            if f["path"].endswith("README.md"):
                f["size"] = 1
    expect(tamper(good, tmp_path / "s.zip", manifest_edit=lie), ("ARCHIVE_UNSAFE", "ARCHIVE_INTEGRITY"))
    # sin base de datos
    expect(tamper(good, tmp_path / "nodb.zip", drop=("db/mcp2p_local.db",)), ("ARCHIVE_INVALID",))
    # project_uuid invalido en el manifiesto / ruta de proyecto con control
    def bad_uuid(m: Dict[str, Any]) -> None:
        m["projects"][0]["project_uuid"] = "../../x"
    expect(tamper(good, tmp_path / "pu.zip", manifest_edit=bad_uuid), ("ARCHIVE_INVALID",))
    def bad_root(m: Dict[str, Any]) -> None:
        m["source_workspace_root"] = "/ws\nroot"
    expect(tamper(good, tmp_path / "br.zip", manifest_edit=bad_root), ("ARCHIVE_INVALID",))
    def bad_kind(m: Dict[str, Any]) -> None:
        for f in m["files"]:
            if f["path"] == "db/mcp2p_local.db":
                f["kind"] = "workspace"
    expect(tamper(good, tmp_path / "bk.zip", manifest_edit=bad_kind), ("ARCHIVE_INVALID",))
    # no es un zip / manifest ilegible
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"PK\x03\x04basura")
    expect(junk, ("ARCHIVE_INVALID",))
    with pytest.raises(PortabilityError) as nf:
        svc.import_projects(tmp_path / "nada.zip", dest_db, dest_ws)
    assert nf.value.code == "ARCHIVE_NOT_FOUND"


def test_limits_protect_against_zip_bombs_and_entry_floods(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    good = tmp_path / "good.zip"
    export_full(src, good)
    dest_ws, dest_db = tmp_path / "d" / "ws", tmp_path / "d" / "db.sqlite"

    bomb = tamper(good, tmp_path / "bomb.zip", add=[("workspace/proj/zeros.bin", b"\0" * (6 * 1024 * 1024), None)])
    with pytest.raises(PortabilityError) as exc:
        ProjectPortabilityService(limits=PortabilityLimits(max_ratio=50)).import_projects(bomb, dest_db, dest_ws)
    assert exc.value.code == "ARCHIVE_UNSAFE"
    with pytest.raises(PortabilityError) as exc2:
        ProjectPortabilityService(limits=PortabilityLimits(max_entries=3)).import_projects(good, dest_db, dest_ws)
    assert exc2.value.code == "ARCHIVE_INVALID"
    with pytest.raises(PortabilityError) as exc3:
        ProjectPortabilityService(limits=PortabilityLimits(max_total_bytes=1000)).import_projects(good, dest_db, dest_ws)
    assert exc3.value.code == "ARCHIVE_TOO_LARGE"
    with pytest.raises(PortabilityError) as exc4:
        ProjectPortabilityService(limits=PortabilityLimits(max_file_bytes=10)).import_projects(good, dest_db, dest_ws)
    assert exc4.value.code == "ARCHIVE_TOO_LARGE"
    assert_untouched(dest_ws, dest_db, tmp_path / "d" / "x")


def test_database_with_triggers_is_rejected(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    good = tmp_path / "good.zip"
    export_full(src, good)

    def evil_db(items: Dict[str, bytes]) -> None:
        dbp = tmp_path / "evil.db"
        dbp.write_bytes(items["db/mcp2p_local.db"])
        conn = sqlite3.connect(str(dbp))
        try:
            with conn:
                conn.execute("CREATE TRIGGER pwn AFTER INSERT ON projects BEGIN DELETE FROM kanban_cards; END;")
        finally:
            conn.close()
        items["db/mcp2p_local.db"] = dbp.read_bytes()

    evil = tamper(good, tmp_path / "evil.zip", data_edit=evil_db)
    dest_ws, dest_db = tmp_path / "d" / "ws", tmp_path / "d" / "db.sqlite"
    with pytest.raises(PortabilityError) as exc:
        svc.import_projects(evil, dest_db, dest_ws)
    assert exc.value.code == "ARCHIVE_UNSAFE" and "trigger" in exc.value.message
    assert_untouched(dest_ws, dest_db, tmp_path / "d" / "x")

    def other_projects(items: Dict[str, bytes]) -> None:
        dbp = tmp_path / "mismatch.db"
        dbp.write_bytes(items["db/mcp2p_local.db"])
        conn = sqlite3.connect(str(dbp))
        try:
            with conn:
                conn.execute("INSERT INTO projects (project_uuid, name, base_path) VALUES ('proj_colado', 'x', '/x');")
        finally:
            conn.close()
        items["db/mcp2p_local.db"] = dbp.read_bytes()

    with pytest.raises(PortabilityError) as exc2:
        svc.import_projects(tamper(good, tmp_path / "mm.zip", data_edit=other_projects), dest_db, dest_ws)
    assert exc2.value.code == "ARCHIVE_INVALID"


def test_import_neutralizes_git_hooks_and_external_commands_from_foreign_packages(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    good = tmp_path / "good.zip"
    export_full(src, good)
    base = "workspace/" + Path(src["p1"]["code_repo_path"]).relative_to(src["ws"]).as_posix()
    cfg = (b"[core]\n\tfsmonitor = /tmp/evil.sh\n\tsshCommand = evil\n\tbare = false\n"
           b"[alias]\n\tco = !rm -rf /\n[user]\n\tname = Ana\n")
    pkg = tamper(
        good, tmp_path / "hooks.zip",
        add=[(f"{base}/.git/hooks/pre-commit", b"#!/bin/sh\ntouch /tmp/pwned\n", (stat.S_IFREG | 0o755)),
             (f"{base}/.git/hooks/pre-commit.sample", b"# ejemplo\n", None),
             (f"{base}/.git/config", cfg, None)],
        data_edit=lambda items: [items.pop(n) for n in list(items) if n.endswith("/.git/config")],
        manifest_edit=lambda m: m.update(files=[f for f in m["files"] if f["path"].count("/.git/config") == 0
                                                  or f["path"].startswith(base) and f["size"] == len(cfg)]),
    )
    res = svc.import_projects(pkg, tmp_path / "h" / "db.sqlite", tmp_path / "h" / "ws")
    repo = Path(res["workspace_root"]) / Path(src["p1"]["code_repo_path"]).relative_to(src["ws"])
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()
    assert (repo / ".git" / "hooks" / "pre-commit.sample").is_file()
    text = (repo / ".git" / "config").read_text()
    assert "evil" not in text.lower() and "!rm" not in text and "name = Ana" in text and "bare = false" in text
    assert any("hooks de git" in w for w in res["warnings"]) and any(".git/config" in w for w in res["warnings"])


def test_failed_import_does_not_leave_the_new_workspace_root_behind(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    good = tmp_path / "good.zip"
    export_full(src, good)
    bad = tamper(good, tmp_path / "bad.zip", add=[("../x", b"1", None)])
    root = tmp_path / "nueva" / "ws"
    with pytest.raises(PortabilityError):
        svc.import_projects(bad, tmp_path / "nueva" / "db.sqlite", root)
    assert not root.exists()


def test_relative_or_empty_workspace_root_is_refused(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    good = tmp_path / "good.zip"
    export_full(src, good)
    for bad in ("", "   ", "relativa/ws"):
        with pytest.raises(PortabilityError) as exc:
            svc.import_projects(good, tmp_path / "x.db", bad)
        assert exc.value.code in {"WORKSPACE_ROOT_REQUIRED", "WORKSPACE_ROOT_INVALID"}
    assert not (tmp_path / "x.db").exists()


# ---------------------------------------------------------------------------
# Import: nunca sobrescribe sin decirlo
# ---------------------------------------------------------------------------


def _import_once(tmp_path: Path, src: Dict[str, Any], name: str = "destino") -> Tuple[Path, Path, Path]:
    out = tmp_path / f"{name}.zip"
    export_full(src, out)
    dest_ws = tmp_path / name / "ws"
    dest_db = tmp_path / name / "mcp2p_local.db"
    svc.import_projects(out, dest_db, dest_ws)
    return out, dest_ws, dest_db


def test_existing_database_requires_merge_or_force(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    out, dest_ws, dest_db = _import_once(tmp_path, src)
    before = dest_db.read_bytes()
    with pytest.raises(PortabilityError) as exc:
        svc.import_projects(out, dest_db, tmp_path / "otra_raiz")
    assert exc.value.code == "DB_EXISTS"
    assert dest_db.read_bytes() == before
    assert not (tmp_path / "otra_raiz").exists()


def test_merge_adds_new_projects_and_reports_same_uuid_without_overwriting(tmp_path: Path) -> None:
    src = make_source(tmp_path, "a")
    out, dest_ws, dest_db = _import_once(tmp_path, src, "dest")
    # un proyecto local distinto ya en el destino, mas un cambio local en uno repetido
    local = DatabaseManager(str(dest_db))
    local.update_project_sla(src["p1"]["project_uuid"], 99)
    local_only = local.create_project("Local", base_path=str(tmp_path / "dest" / "ws" / "local_x"), init_git=False)

    # paquete con un proyecto nuevo (otra maquina) + el repetido p1
    other = make_source(tmp_path, "b")
    other_pkg = tmp_path / "other.zip"
    export_full(other, other_pkg)

    res = svc.import_projects(other_pkg, dest_db, tmp_path / "dest" / "ws2", merge=True)
    assert res["db_mode"] == "merged"
    conn = sqlite3.connect(str(dest_db))
    try:
        ids = {r[0] for r in conn.execute("SELECT project_uuid FROM projects;")}
    finally:
        conn.close()
    assert local_only["project_uuid"] in ids
    # los uuid de b son distintos de los de a (uuid aleatorio): importados
    assert set(res["projects_imported"]) == {other["p1"]["project_uuid"], other["p2"]["project_uuid"]}
    assert res["projects_skipped_existing"] == []

    # Reimportar el MISMO paquete en merge: se omite e informa, no pisa el cambio local
    res2 = svc.import_projects(out, dest_db, tmp_path / "dest" / "ws3", merge=True)
    assert set(res2["projects_skipped_existing"]) == {src["p1"]["project_uuid"], src["p2"]["project_uuid"]}
    assert res2["projects_imported"] == []
    assert DatabaseManager(str(dest_db)).get_project_by_uuid(src["p1"]["project_uuid"])["default_sla_hours"] == 99
    assert not (tmp_path / "dest" / "ws3").exists() or not any(
        p for p in (tmp_path / "dest" / "ws3").iterdir() if not p.name.startswith(".mcp2p-import-")
    )

    # Con --merge --force se pisan los repetidos y se dice cual
    # (workspace nuevo: las carpetas de destino nunca se sobrescriben)
    res3 = svc.import_projects(out, dest_db, tmp_path / "dest" / "ws4", merge=True, force=True)
    assert set(res3["projects_imported"]) == {src["p1"]["project_uuid"], src["p2"]["project_uuid"]}
    assert set(res3["projects_replaced"]) == set(res3["projects_imported"])
    assert DatabaseManager(str(dest_db)).get_project_by_uuid(src["p1"]["project_uuid"])["default_sla_hours"] == 24


def test_force_replaces_database_but_keeps_a_backup(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    out, dest_ws, dest_db = _import_once(tmp_path, src)
    local = DatabaseManager(str(dest_db))
    local.create_project("SoloLocal", base_path=str(tmp_path / "x" / "solo"), init_git=False)
    res = svc.import_projects(out, dest_db, tmp_path / "dest2_ws", force=True)
    assert res["db_mode"] == "replaced" and Path(res["db_backup"]).is_file()
    assert any("copia previa" in w for w in res["warnings"])
    names = {p["name"] for p in DatabaseManager(str(dest_db)).list_projects()}
    assert "SoloLocal" not in names
    backup_names = {r[0] for r in sqlite3.connect(res["db_backup"]).execute("SELECT name FROM projects;")}
    assert "SoloLocal" in backup_names


def test_workspace_directories_are_never_overwritten(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    out = tmp_path / "p.zip"
    export_full(src, out)
    dest_ws = tmp_path / "ws"
    existing = dest_ws / Path(src["p1"]["base_path"]).name
    existing.mkdir(parents=True)
    (existing / "mio.txt").write_text("no me toques")
    dest_db = tmp_path / "d.db"
    with pytest.raises(PortabilityError) as exc:
        svc.import_projects(out, dest_db, dest_ws, force=True)
    assert exc.value.code == "WORKSPACE_CONFLICT"
    assert (existing / "mio.txt").read_text() == "no me toques"
    assert not dest_db.exists()


def test_secrets_in_package_need_explicit_flag_on_import_too(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    out = tmp_path / "s.zip"
    export_full(src, out, include_secrets=True, identity_key_paths=[src["key"]])

    # Sin flag: ni PAT en la base importada ni clave instalada
    home = tmp_path / "home1" / ".mcp2p"
    res = svc.import_projects(out, tmp_path / "n1" / "db.sqlite", tmp_path / "n1" / "ws", identity_dir=home)
    assert res["identity_files_installed"] == [] and not home.exists()
    assert any("secretos" in w for w in res["warnings"])
    conn = sqlite3.connect(str(tmp_path / "n1" / "db.sqlite"))
    try:
        assert conn.execute("SELECT COUNT(*) FROM app_settings WHERE key='github_pat_token';").fetchone()[0] == 0
    finally:
        conn.close()
    assert PAT.encode() not in (tmp_path / "n1" / "db.sqlite").read_bytes()

    # Con flag: se instalan con permisos 0600 y no se pisan identidades existentes
    home2 = tmp_path / "home2" / ".mcp2p"
    res2 = svc.import_projects(out, tmp_path / "n2" / "db.sqlite", tmp_path / "n2" / "ws",
                               include_secrets=True, identity_dir=home2)
    key = home2 / "node_identity.key"
    assert res2["identity_files_installed"] == [str(key)] and key.is_file()
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    conn = sqlite3.connect(str(tmp_path / "n2" / "db.sqlite"))
    try:
        assert conn.execute("SELECT value FROM app_settings WHERE key='github_pat_token';").fetchone()[0] == PAT
    finally:
        conn.close()
    with pytest.raises(PortabilityError) as exc:
        svc.import_projects(out, tmp_path / "n3" / "db.sqlite", tmp_path / "n3" / "ws",
                            include_secrets=True, identity_dir=home2)
    assert exc.value.code == "IDENTITY_EXISTS"
    assert key.read_text().startswith("-----BEGIN")
    assert not (tmp_path / "n3" / "db.sqlite").exists()


# ---------------------------------------------------------------------------
# CLI: mcp2p fast projects export|import
# ---------------------------------------------------------------------------


def _run_cli(argv: List[str], capsys: pytest.CaptureFixture[str]) -> Tuple[int, Dict[str, Any], str]:
    from src.app.fast_cli import run_fast_cli

    code = run_fast_cli(argv)
    cap = capsys.readouterr()
    return code, json.loads(cap.out), cap.err


def test_fast_cli_export_import_respects_db_path_and_flags(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = make_source(tmp_path)
    pkg = tmp_path / "cli.zip"
    code, out, err = _run_cli(
        ["--db-path", str(src["db_file"]), "projects", "export", str(pkg),
         "--workspace-root", str(src["ws"]), "--include-repos"], capsys)
    assert code == 0 and out["success"] is True and out["includes_secrets"] is False and err == ""
    with zipfile.ZipFile(pkg) as zf:
        assert PAT.encode() not in b"".join(zf.read(n) for n in zf.namelist())

    # no se pisa el .zip sin --overwrite
    code, out, _ = _run_cli(["--db-path", str(src["db_file"]), "projects", "export", str(pkg)], capsys)
    assert code == 1 and out["code"] == "ARCHIVE_EXISTS"

    # --include-secrets avisa por stderr
    secret_pkg = tmp_path / "sec.zip"
    code, out, err = _run_cli(["--db-path", str(src["db_file"]), "projects", "export", str(secret_pkg),
                               "--include-secrets"], capsys)
    assert code == 0 and "secretos" in err and out["includes_secrets"] is True

    # import a una base nueva: la crea (el kernel no la abre antes de decidir)
    new_db = tmp_path / "nueva" / "mcp2p_local.db"
    new_ws = tmp_path / "nueva" / "ws"
    code, out, _ = _run_cli(["--db-path", str(new_db), "projects", "import", str(pkg),
                             "--workspace-root", str(new_ws)], capsys)
    assert code == 0 and out["db_mode"] == "created" and new_db.is_file()

    # segunda importacion sin flags: se niega
    code, out, _ = _run_cli(["--db-path", str(new_db), "projects", "import", str(pkg),
                             "--workspace-root", str(tmp_path / "otra")], capsys)
    assert code == 1 and out["code"] == "DB_EXISTS"

    # --workspace-root es obligatorio en import
    from src.app.fast_cli import run_fast_cli

    assert run_fast_cli(["--db-path", str(new_db), "projects", "import", str(pkg)]) == 2
    capsys.readouterr()

    # `projects` sin accion sigue listando
    code, listing, _ = _run_cli(["--db-path", str(new_db), "projects"], capsys)
    assert code == 0 and len(listing["projects"]) == 2
