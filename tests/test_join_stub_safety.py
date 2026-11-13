"""
Regresiones de la revisión final de la rama de solicitudes de unión P2P:

- C2: un stub de unión pendiente (creado por join_p2p_project, todavía sin aprobación ni
  clonado) nunca debe tratarse como un proyecto "normal". Con code_repo_path/gov_repo_path
  vacíos, Path("") resolvía al directorio de trabajo del proceso, y sync_three_tiers /
  publish_project_to_github operaban git/GitHub sobre ESE repositorio ajeno (p. ej. el
  checkout desde el que se lanzó ./run.sh).
- I3: retry_p2p_join solo debe aceptar las invitaciones de GitHub de los repositorios del
  proyecto que se reintenta, nunca todas las invitaciones pendientes de la cuenta.
"""

import io
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path

import pytest

from conftest import make_test_db, register_tmp_dir
from src.app.api_bridge import ApiBridge
from src.p2p.crypto import NodeCryptoManager
from src.storage.github_sync import GitHubSyncManager


def _two_bridges(owner_db=None, joiner_db=None):
    bridge_owner = ApiBridge(db_manager=owner_db or make_test_db())
    bridge_joiner = ApiBridge(db_manager=joiner_db or make_test_db())
    owner_key_dir = tempfile.mkdtemp()
    joiner_key_dir = tempfile.mkdtemp()
    register_tmp_dir(owner_key_dir)
    register_tmp_dir(joiner_key_dir)
    bridge_owner.crypto = NodeCryptoManager(key_path=Path(owner_key_dir) / "owner.key")
    bridge_joiner.crypto = NodeCryptoManager(key_path=Path(joiner_key_dir) / "joiner.key")
    proj = bridge_owner.create_project(name="Owner Project")["project"]
    bridge_owner.active_project_uuid = proj["project_uuid"]
    return bridge_owner, bridge_joiner


def _join_stub(urls=None):
    """Devuelve (bridge_owner, bridge_joiner, join_res) con un stub pendiente en el joiner."""
    bridge_owner, bridge_joiner = _two_bridges()
    p_uuid = bridge_owner.active_project_uuid
    if urls:
        with bridge_owner.db._write_lock, bridge_owner.db.get_connection() as conn:
            with conn:
                conn.execute(
                    "UPDATE projects SET code_repo_url = ?, gov_repo_url = ? WHERE project_uuid = ?;",
                    (urls[0], urls[1], p_uuid)
                )
    invite = bridge_owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    assert join_res["success"] is True
    assert join_res["status"] == "pending_approval"
    return bridge_owner, bridge_joiner, join_res


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


def _make_victim_repo():
    """Repositorio Git ajeno con remoto 'origin' (un bare local) y una rama de trabajo
    sin relación checked-out -- el equivalente al checkout de MCP2P (u otro proyecto)
    desde el que el usuario lanza la app."""
    root = Path(tempfile.mkdtemp())
    register_tmp_dir(root)
    bare = root / "origin.git"
    bare.mkdir()
    _git(["init", "--bare", "-b", "main"], bare)
    victim = root / "victim"
    subprocess.run(["git", "clone", str(bare), str(victim)], capture_output=True, check=True)
    _git(["config", "user.email", "victim@example.com"], victim)
    _git(["config", "user.name", "Victim"], victim)
    (victim / "README.md").write_text("victim\n", encoding="utf-8")
    _git(["add", "."], victim)
    _git(["commit", "-m", "victim init"], victim)
    _git(["push", "origin", "main"], victim)
    _git(["checkout", "-b", "feature/unrelated-work"], victim)
    (victim / "wip.txt").write_text("local unpushed work\n", encoding="utf-8")
    _git(["add", "."], victim)
    _git(["commit", "-m", "local wip"], victim)
    return victim, bare


def _repo_snapshot(victim, bare):
    return {
        "heads": _git(["for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"], victim),
        "head": _git(["rev-parse", "HEAD"], victim),
        "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"], victim),
        "worktrees": _git(["worktree", "list", "--porcelain"], victim),
        "status": _git(["status", "--porcelain"], victim),
        "origin_refs": _git(["for-each-ref", "--format=%(refname) %(objectname)"], bare),
        "remotes": _git(["remote", "-v"], victim),
    }


# ==========================
# C2.1: rutas futuras persistidas en el stub
# ==========================

def test_join_stub_persists_future_nonexistent_paths_matching_retry_clone_target():
    _, bridge_joiner, join_res = _join_stub()
    stub = bridge_joiner.db.get_project_by_uuid(join_res["project"]["project_uuid"])

    _, expected_app, expected_gov = bridge_joiner._join_stub_repo_dirs(stub["name"], stub["project_uuid"], stub["base_path"])
    assert stub["code_repo_path"] == str(expected_app)
    assert stub["gov_repo_path"] == str(expected_gov)
    for key in ("code_repo_path", "gov_repo_path"):
        p = Path(stub[key])
        assert p.is_absolute()
        assert not p.exists()
        assert p.resolve() != Path.cwd().resolve()
        # Debe colgar del workspace propio de la app, nunca del cwd del proceso.
        assert bridge_joiner.db.db_path.parent in p.parents


# ==========================
# C2.3: el stub nunca pasa a ser el proyecto activo
# ==========================

def test_join_does_not_make_the_pending_stub_the_active_project():
    _, bridge_joiner = _two_bridges()
    active_before = bridge_joiner.active_project_uuid
    assert active_before is not None  # proyecto semilla

    owner, _ = _two_bridges()
    invite = owner.generate_p2p_invite()["token"]
    res = bridge_joiner.join_p2p_project(invite)
    assert res["status"] == "pending_approval"
    assert bridge_joiner.active_project_uuid == active_before
    assert bridge_joiner.active_project_uuid != res["project"]["project_uuid"]


def test_ensure_active_project_skips_pending_stub_even_when_it_is_the_newest():
    _, bridge_joiner, join_res = _join_stub()
    stub_uuid = join_res["project"]["project_uuid"]
    # El stub es el proyecto más reciente (updated_at DESC) -- antes, _ensure_active_project
    # lo escogía al reiniciar la app.
    assert bridge_joiner.db.list_projects()[0]["project_uuid"] == stub_uuid

    restarted = ApiBridge(db_manager=bridge_joiner.db)
    assert restarted.active_project_uuid is not None
    assert restarted.active_project_uuid != stub_uuid


def test_ensure_active_project_leaves_none_when_only_pending_stubs_exist():
    _, bridge_joiner, join_res = _join_stub()
    stub_uuid = join_res["project"]["project_uuid"]
    for proj in bridge_joiner.db.list_projects():
        if proj["project_uuid"] != stub_uuid:
            assert bridge_joiner.delete_project(proj["project_uuid"])["success"] is True

    assert [p["project_uuid"] for p in bridge_joiner.db.list_projects()] == [stub_uuid]
    assert bridge_joiner.active_project_uuid is None

    restarted = ApiBridge(db_manager=bridge_joiner.db)
    assert restarted.active_project_uuid is None
    # Y las operaciones "sobre el proyecto activo" fallan limpiamente, sin tocar nada.
    assert restarted.sync_three_tiers()["success"] is False
    assert restarted.generate_p2p_invite()["success"] is False


# ==========================
# C2.2: guards por join_status
# ==========================

def test_select_project_refuses_pending_stub():
    _, bridge_joiner, join_res = _join_stub()
    active_before = bridge_joiner.active_project_uuid
    res = bridge_joiner.select_project(join_res["project"]["project_uuid"])
    assert res["success"] is False
    assert res["status"] == "join_pending"
    assert "pendiente" in res["error"]
    assert bridge_joiner.active_project_uuid == active_before


def test_sync_three_tiers_refuses_pending_stub_explicitly_and_as_active_project():
    _, bridge_joiner, join_res = _join_stub()
    stub_uuid = join_res["project"]["project_uuid"]
    calls = []
    for name in ("git_pull", "git_push_branch", "git_fetch", "has_remote"):
        setattr(bridge_joiner.github, name, lambda *a, _n=name, **k: calls.append(_n))
    bridge_joiner.ensure_orphan_worktree = lambda *a, **k: calls.append("ensure_orphan_worktree")

    res = bridge_joiner.sync_three_tiers(stub_uuid)
    assert res["success"] is False
    assert res["status"] == "join_pending"

    # Aunque algo forzara el stub como activo (p. ej. una BD legada), la llamada sin
    # argumentos que usa el webhook LAN de main.py también se rechaza.
    bridge_joiner.active_project_uuid = stub_uuid
    res2 = bridge_joiner.sync_three_tiers()
    assert res2["success"] is False
    assert res2["status"] == "join_pending"
    assert calls == []


def test_sync_three_tiers_refuses_active_project_with_empty_gov_path():
    """Defensa en profundidad: una ruta vacía jamás debe resolverse al cwd."""
    bridge = ApiBridge(db_manager=make_test_db())
    p_uuid = bridge.create_project(name="Legacy")["project"]["project_uuid"]
    with bridge.db._write_lock, bridge.db.get_connection() as conn:
        with conn:
            conn.execute("UPDATE projects SET gov_repo_path = '', code_repo_path = '' WHERE project_uuid = ?;", (p_uuid,))
    called = []
    bridge.ensure_orphan_worktree = lambda *a, **k: called.append(a)
    res = bridge.sync_three_tiers(p_uuid)
    assert res["success"] is False
    assert called == []


@pytest.mark.parametrize("repo_target", ["both", "app", "gov"])
def test_publish_project_to_github_refuses_pending_stub(repo_target):
    _, bridge_joiner, join_res = _join_stub()
    calls = []
    bridge_joiner.github.auto_create_and_publish_repo = lambda *a, **k: calls.append(k) or {"success": True, "repo_url": "x"}
    res = bridge_joiner.publish_project_to_github(join_res["project"]["project_uuid"], repo_target=repo_target)
    assert res["success"] is False
    assert res["status"] == "join_pending"
    assert calls == []


def test_publish_both_never_passes_an_empty_repo_path():
    bridge = ApiBridge(db_manager=make_test_db())
    p_uuid = bridge.create_project(name="Legacy")["project"]["project_uuid"]
    with bridge.db._write_lock, bridge.db.get_connection() as conn:
        with conn:
            conn.execute("UPDATE projects SET gov_repo_path = '', code_repo_path = '' WHERE project_uuid = ?;", (p_uuid,))
    calls = []
    bridge.github.auto_create_and_publish_repo = lambda *a, **k: calls.append(k) or {"success": True}
    res = bridge.publish_project_to_github(p_uuid, repo_target="both")
    assert res["success"] is False
    assert calls == []


def test_generate_p2p_invite_refuses_pending_stub():
    _, bridge_joiner, join_res = _join_stub()
    res = bridge_joiner.generate_p2p_invite(join_res["project"]["project_uuid"])
    assert res["success"] is False
    assert res["status"] == "join_pending"
    assert "token" not in res


def test_ingest_join_request_refuses_to_register_into_a_pending_stub():
    _, bridge_joiner, join_res = _join_stub()
    stub_uuid = join_res["project"]["project_uuid"]
    rows_before = bridge_joiner.db.get_project_collaborators(stub_uuid)
    res = bridge_joiner.ingest_join_request(join_res["request_code"], stub_uuid)
    assert res["success"] is False
    assert bridge_joiner.db.get_project_collaborators(stub_uuid) == rows_before


def test_retry_still_activates_and_syncs_after_approval_despite_guards():
    """Los guards no deben romper el camino feliz: retry_p2p_join marca 'active' ANTES de
    llamar a sync_three_tiers, y deja el proyecto ya unido como activo."""
    root = Path(tempfile.mkdtemp())
    register_tmp_dir(root)
    bare_code, bare_gov = root / "code.git", root / "gov.git"
    for bare in (bare_code, bare_gov):
        bare.mkdir()
        _git(["init", "--bare", "-b", "main"], bare)
        seed = root / f"seed_{bare.stem}"
        subprocess.run(["git", "clone", str(bare), str(seed)], capture_output=True, check=True)
        _git(["config", "user.email", "s@example.com"], seed)
        _git(["config", "user.name", "S"], seed)
        (seed / "README.md").write_text("seed\n", encoding="utf-8")
        _git(["add", "."], seed)
        _git(["commit", "-m", "seed"], seed)
        _git(["push", "origin", "main"], seed)

    bridge_owner, bridge_joiner, join_res = _join_stub(urls=(str(bare_code), str(bare_gov)))
    stub_uuid = join_res["project"]["project_uuid"]
    bridge_owner.ingest_join_request(join_res["request_code"])
    row = bridge_owner.db.get_collaborator_by_pubkey(stub_uuid, bridge_joiner.crypto.public_key_base64)
    assert bridge_owner.approve_collaborator(stub_uuid, row["node_uuid"])["success"] is True

    synced = []
    real_sync = bridge_joiner.sync_three_tiers
    bridge_joiner.sync_three_tiers = lambda uuid=None: synced.append(real_sync(uuid)) or synced[-1]
    res = bridge_joiner.retry_p2p_join(stub_uuid)
    assert res["success"] is True
    proj = bridge_joiner.db.get_project_by_uuid(stub_uuid)
    assert proj["join_status"] == "active"
    assert (Path(proj["gov_repo_path"]) / ".git").exists()
    assert bridge_joiner.active_project_uuid == stub_uuid
    assert synced and synced[0]["success"] is True
    assert bridge_joiner.select_project(stub_uuid)["success"] is True
    assert bridge_joiner.generate_p2p_invite(stub_uuid)["success"] is True


# ==========================
# Regresión estilo "repro del revisor": app lanzada desde un repo ajeno
# ==========================

def test_pending_stub_never_touches_the_process_cwd_repository(monkeypatch):
    """
    Reproduce el escenario confirmado en la revisión final: el proceso corre con cwd
    dentro de un repositorio Git ajeno con remoto 'origin' (p. ej. ./run.sh desde el
    checkout de MCP2P). El stub pendiente es el proyecto más reciente. Ninguna de las
    operaciones expuestas (sync manual / webhook LAN, publicación, invitación, selección,
    reinicio de la app) debe modificar ese repositorio ni su remoto, ni publicar nada.
    """
    victim, bare = _make_victim_repo()
    before = _repo_snapshot(victim, bare)
    monkeypatch.chdir(victim)

    _, bridge_joiner, join_res = _join_stub()
    stub_uuid = join_res["project"]["project_uuid"]
    stub = bridge_joiner.db.get_project_by_uuid(stub_uuid)
    for key in ("code_repo_path", "gov_repo_path"):
        assert Path(stub[key]).resolve() != victim.resolve()
        assert victim.resolve() not in Path(stub[key]).resolve().parents

    publish_calls = []

    def fake_publish(*args, **kwargs):
        publish_calls.append(kwargs.get("repo_path"))
        return {"success": False, "error": "stubbed in test"}

    # Simular el reinicio de la app (el escenario donde _ensure_active_project elegía al
    # stub por ser el más reciente) y luego todos los disparadores conocidos.
    for bridge in (bridge_joiner, ApiBridge(db_manager=bridge_joiner.db)):
        bridge.github.auto_create_and_publish_repo = fake_publish
        bridge.sync_three_tiers()                        # webhook LAN de main.py
        bridge.sync_three_tiers(stub_uuid)               # botón Sync del hub
        bridge.publish_project_to_github(stub_uuid, "both")
        bridge.publish_project_to_github(stub_uuid, "app")
        bridge.publish_project_to_github(stub_uuid, "gov")
        bridge.generate_p2p_invite(stub_uuid)
        bridge.select_project(stub_uuid)
        # Aun forzando el stub como activo (BD legada / bug futuro):
        bridge.active_project_uuid = stub_uuid
        bridge.sync_three_tiers()
        bridge.publish_project_to_github()

    after = _repo_snapshot(victim, bare)
    assert after == before
    assert not any(p and Path(p).resolve() == victim.resolve() for p in publish_calls)
    assert publish_calls == []
    # Las rutas futuras del stub siguen sin existir (nada se creó "por adelantado").
    assert not Path(stub["gov_repo_path"]).exists()
    assert not Path(stub["code_repo_path"]).exists()


def test_dev_mode_layout_fallback_active_project_never_touches_enclosing_repo(monkeypatch):
    """
    Variante del repro con la disposición REAL de modo desarrollo: la BD y workspace/
    viven dentro del checkout desde el que se lanza ./run.sh (get_user_data_dir() ==
    raíz del repo). Con el stub excluido, el proyecto activo de reserva es el proyecto
    semilla, creado con init_git=False -- su carpeta de gobernanza NO es raíz de un repo,
    y git, al subir por los directorios padre, operaba sobre el checkout contenedor
    (rama huérfana + worktree creados en él y empujados a su origin). Ni el stub ni el
    semilla deben tocar ese repositorio.
    """
    victim, bare = _make_victim_repo()
    before = _repo_snapshot(victim, bare)
    monkeypatch.chdir(victim)

    from src.storage.db import DatabaseManager
    joiner_db = DatabaseManager(str(victim / "mcp2p_local.db"))
    _, bridge_joiner = _two_bridges(joiner_db=joiner_db)
    owner, _ = _two_bridges()
    invite = owner.generate_p2p_invite()["token"]
    join_res = bridge_joiner.join_p2p_project(invite)
    stub_uuid = join_res["project"]["project_uuid"]

    for bridge in (bridge_joiner, ApiBridge(db_manager=joiner_db)):
        bridge.github.auto_create_and_publish_repo = lambda *a, **k: {"success": False, "error": "stubbed"}
        assert bridge.active_project_uuid != stub_uuid
        bridge.sync_three_tiers()                         # webhook LAN: proyecto activo de reserva
        bridge.sync_three_tiers(stub_uuid)
        bridge.publish_project_to_github(None, "both")
        bridge.publish_project_to_github(stub_uuid, "both")

    after = _repo_snapshot(victim, bare)
    # 'status' cambia legítimamente (mcp2p_local.db y workspace/ sin trackear, igual que
    # en el checkout real donde están en .gitignore); refs, worktrees y remoto, no.
    for key in ("heads", "head", "branch", "worktrees", "origin_refs", "remotes"):
        assert after[key] == before[key], key


# ==========================
# Regeneración del request_code para un stub reabierto desde el hub
# ==========================

def test_get_pending_join_request_code_regenerates_an_ingestable_code_without_side_effects():
    bridge_owner, bridge_joiner, join_res = _join_stub()
    stub_uuid = join_res["project"]["project_uuid"]
    joiner_rows_before = bridge_joiner.db.get_project_collaborators(stub_uuid)

    res = bridge_joiner.get_pending_join_request_code(stub_uuid)
    assert res["success"] is True
    is_valid, payload, _ = NodeCryptoManager.decode_and_verify_invite_token(res["request_code"])
    assert is_valid
    assert payload["project_uuid"] == stub_uuid
    assert payload["public_key"] == bridge_joiner.crypto.public_key_base64
    assert bridge_joiner.db.get_project_collaborators(stub_uuid) == joiner_rows_before

    ingest = bridge_owner.ingest_join_request(res["request_code"], stub_uuid)
    assert ingest["success"] is True
    assert ingest["status"] == "pending_approval"


def test_get_pending_join_request_code_refuses_non_pending_projects():
    bridge = ApiBridge(db_manager=make_test_db())
    p_uuid = bridge.create_project(name="Normal")["project"]["project_uuid"]
    assert bridge.get_pending_join_request_code(p_uuid)["success"] is False
    assert bridge.get_pending_join_request_code("proj_missing")["success"] is False


# ==========================
# I3: aceptación de invitaciones de GitHub acotada al proyecto
# ==========================

class _FakeResp:
    def __init__(self, body=b"", status=200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_invitations(monkeypatch, invitations):
    patched = []

    def fake_urlopen(req, timeout=None):
        if req.get_method() == "PATCH":
            patched.append(int(req.full_url.rsplit("/", 1)[-1]))
            return _FakeResp(status=204)
        return _FakeResp(json.dumps(invitations).encode("utf-8"))

    monkeypatch.setattr("src.storage.github_sync.urllib.request.urlopen", fake_urlopen)
    return patched


INVITATIONS = [
    {"id": 1, "repository": {"full_name": "owner/mcp2p-app"}},
    {"id": 2, "repository": {"full_name": "owner/mcp2p-app-okf"}},
    {"id": 3, "repository": {"full_name": "stranger/totally-unrelated"}},
    {"id": 4, "repository": {"full_name": "Owner/MCP2P-APP"}},
]


def test_accept_pending_invitations_filters_by_repo_full_name(monkeypatch):
    gh = GitHubSyncManager(db_manager=make_test_db())
    patched = _patch_invitations(monkeypatch, INVITATIONS)
    res = gh.accept_pending_invitations(token="t", repo_full_names={"owner/mcp2p-app", "owner/mcp2p-app-okf"})
    assert res["success"] is True
    assert sorted(patched) == [1, 2, 4]  # 4: comparación sin distinguir mayúsculas
    assert 3 not in patched


def test_accept_pending_invitations_empty_filter_accepts_nothing(monkeypatch):
    gh = GitHubSyncManager(db_manager=make_test_db())
    patched = _patch_invitations(monkeypatch, INVITATIONS)
    res = gh.accept_pending_invitations(token="t", repo_full_names=set())
    assert res["success"] is True
    assert res["accepted_count"] == 0
    assert patched == []


def test_accept_pending_invitations_without_filter_keeps_legacy_behavior(monkeypatch):
    gh = GitHubSyncManager(db_manager=make_test_db())
    patched = _patch_invitations(monkeypatch, INVITATIONS)
    res = gh.accept_pending_invitations(token="t")
    assert res["success"] is True
    assert sorted(patched) == [1, 2, 3, 4]


def test_retry_p2p_join_only_accepts_invitations_for_the_stubs_own_repos(monkeypatch):
    _, bridge_joiner, join_res = _join_stub(urls=(
        "https://github.com/owner/mcp2p-app.git",
        "git@github.com:owner/mcp2p-app-okf.git",
    ))
    patched = _patch_invitations(monkeypatch, INVITATIONS)
    monkeypatch.setattr(bridge_joiner.github, "_get_active_token", lambda: "t")
    bridge_joiner.github.git_clone = lambda *a, **k: {"success": False, "status": "pending_access"}

    res = bridge_joiner.retry_p2p_join(join_res["project"]["project_uuid"])
    assert res["status"] == "still_pending"
    assert sorted(patched) == [1, 2, 4]
    assert 3 not in patched


def test_retry_p2p_join_skips_invitation_acceptance_for_non_github_urls():
    _, bridge_joiner, join_res = _join_stub(urls=("/tmp/local/code.git", "/tmp/local/gov.git"))
    calls = []
    bridge_joiner.github.accept_pending_invitations = lambda *a, **k: calls.append((a, k))
    bridge_joiner.github.git_clone = lambda *a, **k: {"success": False, "status": "pending_access"}
    bridge_joiner.retry_p2p_join(join_res["project"]["project_uuid"])
    assert calls == []
